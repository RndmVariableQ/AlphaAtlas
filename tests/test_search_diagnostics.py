from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from test_evaluation_library import evaluator, market_frame

from alpha_atlas.contracts import Candidate, DateRange
from alpha_atlas.evaluation import search_diagnostics


def stock_panel():
    return pl.DataFrame(
        [
            {
                "trading_day": date(2020, 1, 1) + timedelta(days=day),
                "target_end": date(2020, 1, 1) + timedelta(days=day),
                "exchange": "X",
                "instrument_id": str(i),
                "eligible": True,
                "value": float(i if day == 0 else 4 - i),
                "target": float(i),
            }
            for day in range(3)
            for i in range(5)
        ]
    )


PERIOD = DateRange(date(2020, 1, 1), date(2020, 1, 3))


def test_stock_icir_and_rank_holdings_hand_calculation():
    result = search_diagnostics(stock_panel(), PERIOD, "cross_sectional_spearman", 1)
    assert result["icir"] == pytest.approx(np.mean([1, -1, -1]) / np.std([1, -1, -1], ddof=1))
    # Fully reverse unit-gross positions once, then unchanged: half L1 is 1 then 0.
    assert result["turnover"] == pytest.approx(0.5)
    assert result["turnover_observations"] == 2
    assert result["icir_days"] == 3
    directed = search_diagnostics(stock_panel(), PERIOD, "cross_sectional_spearman", -1)
    assert directed["icir"] == -result["icir"]
    assert directed["turnover"] == result["turnover"]


def test_pit_exit_entry_count_and_missing_snapshots_do_not_bridge():
    panel = stock_panel().with_columns(
        pl.when(pl.col("trading_day") > date(2020, 1, 1))
        .then(pl.col("instrument_id") + "new")
        .otherwise(pl.col("instrument_id"))
        .alias("instrument_id")
    )
    assert search_diagnostics(panel, PERIOD, "cross_sectional_spearman", 1)[
        "turnover"
    ] == pytest.approx(0.5)
    expected = search_diagnostics(panel, PERIOD, "cross_sectional_spearman", 1)
    for seed in range(5):
        shuffled = panel.sample(fraction=1, shuffle=True, seed=seed)
        assert search_diagnostics(shuffled, PERIOD, "cross_sectional_spearman", 1) == expected
    missing = panel.with_columns(
        pl.when(pl.col("trading_day") == date(2020, 1, 2))
        .then(None)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    result = search_diagnostics(missing, PERIOD, "cross_sectional_spearman", 1)
    assert result["turnover"] is None and result["turnover_observations"] == 0
    empty_day = panel.with_columns((pl.col("trading_day") != date(2020, 1, 2)).alias("eligible"))
    assert search_diagnostics(empty_day, PERIOD, "cross_sectional_spearman", 1)["turnover"] is None


def test_degenerate_diagnostics_are_missing_not_invented_quality():
    panel = stock_panel().with_columns(pl.lit(1.0).alias("value"))
    result = search_diagnostics(panel, PERIOD, "cross_sectional_spearman", 1)
    assert result["icir"] is None
    assert result["turnover"] == 0  # A constant rank portfolio is flat, not an undefined holding.
    assert search_diagnostics(panel, PERIOD, "cross_sectional_spearman", None)["turnover"] is None
    one_day = stock_panel().filter(pl.col("trading_day") == PERIOD.start)
    result = search_diagnostics(one_day, PERIOD, "cross_sectional_spearman", 1)
    assert result["icir"] is None and result["turnover"] is None


def futures_panel():
    rows = []
    for day in range(3):
        for product in ("A", "B"):
            for bar in range(6):
                rows.append(
                    {
                        "trading_day": date(2020, 1, 1) + timedelta(days=day),
                        "target_end": date(2020, 1, 1) + timedelta(days=day),
                        "timestamp": datetime(2020, 1, 1) + timedelta(days=day, minutes=5 * bar),
                        "exchange": "X",
                        "instrument_id": product + str(day),
                        "segment_id": day,
                        "product": product,
                        "eligible": True,
                        "value": float(bar - 2.5),
                        "target": float(bar if product == "B" or day == 0 else -bar),
                    }
                )
    return pl.DataFrame(rows)


def test_futures_daily_icir_weights_and_native_contract_turnover():
    panel = futures_panel()
    weights = pl.DataFrame({"product": ["A", "B"], "weight": [1.0, 3.0]})
    name = "time_series_weighted_pearson_ic"
    result = search_diagnostics(panel, PERIOD, name, 1, weights)
    assert result["icir"] == pytest.approx(np.mean([1, 0.5, 0.5]) / np.std([1, 0.5, 0.5], ddof=1))
    assert result["turnover"] == pytest.approx(
        0.2
    )  # One sign flip in five transitions per contract.
    assert result["turnover_observations"] == 30  # No artificial roll from +1 back to -1.
    assert result["turnover_unit"] == "native_bar_unit_position"
    split = panel.with_columns(
        pl.when(pl.col("value") > 0).then(99).otherwise(pl.col("segment_id")).alias("segment_id")
    )
    assert search_diagnostics(split, PERIOD, name, 1, weights)["turnover"] == 0
    missing = panel.with_columns(
        pl.when(pl.col("value") == -0.5).then(None).otherwise(pl.col("value")).alias("value")
    )
    assert search_diagnostics(missing, PERIOD, name, 1, weights)["turnover"] == 0


def test_public_report_diagnostics_ignore_validation_and_preserve_primary_metrics():
    data = market_frame().with_columns(pl.Series("x", np.random.default_rng(4).normal(size=120)))
    ordinary = evaluator(data).evaluate(Candidate("$x"))
    enhanced = evaluator(data, diagnostics="icir_turnover_v1").evaluate(Candidate("$x"))
    assert enhanced.metrics == ordinary.metrics and enhanced.direction == ordinary.direction
    assert enhanced.diagnostics["icir"] is not None
    assert enhanced.diagnostics["turnover"] is not None
    altered = data.with_columns(
        pl.when(pl.col("trading_day") > date(2020, 1, 10))
        .then(1e6)
        .otherwise(pl.col("x"))
        .alias("x")
    )
    other = evaluator(altered, diagnostics="icir_turnover_v1").evaluate(Candidate("$x"))
    assert enhanced.diagnostics == other.diagnostics
    cached_service = evaluator(data, diagnostics="icir_turnover_v1")
    first = cached_service.evaluate(Candidate("$x"))
    again = cached_service.evaluate(Candidate("$x"))
    assert again.diagnostics == first.diagnostics and again.cache_hit
    assert replace(enhanced, diagnostics={}).metrics == ordinary.metrics
