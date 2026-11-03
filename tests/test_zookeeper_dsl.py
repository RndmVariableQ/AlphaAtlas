from datetime import date

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_zookeeper import daily as daily

from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.factor_libraries.zookeeper.compute import align_monthly
from alpha_atlas.factor_libraries.zookeeper.compute import compute_monthly as legacy_monthly
from alpha_atlas.factor_libraries.zookeeper.dsl import compute_monthly, recipe
from alpha_atlas.operators import OperatorRegistry


@pytest.mark.parametrize("gaps", [False, True])
def test_all_26_dsl_factors_match_independent_legacy_calculation(daily, gaps):
    base, curve = daily
    if gaps:
        base = base.filter(
            ~((pl.col("product") == "0") & (pl.col("trading_day") == date(2018, 4, 12)))
        ).filter(
            ~(
                (pl.col("product") == "1")
                & (pl.col("trading_day").dt.truncate("1mo") == date(2018, 6, 1))
            )
        )
        curve = curve.with_columns(
            pl.when(pl.col("trading_day") >= date(2018, 9, 13))
            .then(pl.lit("rolled"))
            .otherwise(pl.col("leg0"))
            .alias("leg0"),
            pl.when(pl.col("trading_day") == date(2018, 12, 31))
            .then(None)
            .otherwise(pl.col("F1"))
            .alias("F1"),
        )
    actual = compute_monthly(base, curve)
    expected = legacy_monthly(base, curve).select(actual.columns)
    assert_frame_equal(actual, expected, rel_tol=1e-8, abs_tol=1e-10)
    for field in actual.columns[3:]:
        assert actual[field].is_not_null().sum() > 0


def test_dsl_publication_ignores_partial_month_and_future(daily):
    base, curve = daily
    full = compute_monthly(base, curve)
    cutoff = date(2019, 6, 14)
    prefix = compute_monthly(*(f.filter(pl.col("trading_day") <= cutoff) for f in daily))
    features = (
        base.select("exchange", "product", "trading_day")
        .filter(pl.col("trading_day").is_between(date(2019, 4, 1), cutoff))
        .with_row_index("row_id")
    )
    assert_frame_equal(align_monthly(features, full), align_monthly(features, prefix))
    names = {r["name"] for r in recipe()["factors"]}
    assert len(names) == 26
    assert all(r["description"] and r["expression"] for r in recipe()["factors"])


def test_endpoint_lag_does_not_change_strict_delay_semantics():
    frame = pl.DataFrame(
        {
            "row_id": [0, 1, 2, 3],
            "exchange": ["X"] * 4,
            "instrument_id": ["A"] * 4,
            "timestamp": [1, 2, 3, 4],
            "segment_id": [0, 0, 0, 1],
            "x": [7.0, None, 9.0, 11.0],
        }
    )
    assert execute("OBS_LAG($x, 2)", frame, {"x"})["value"].to_list() == [None, None, 7.0, None]
    assert execute("DELAY($x, 2)", frame, {"x"})["value"].null_count() == 4


def test_residual_std_rank_missing_and_independent_numpy():
    rng = np.random.default_rng(9)
    x = rng.normal(size=(30, 3))
    y = x @ [1.0, 2.0, 3.0] + rng.normal(size=30)
    frame = pl.DataFrame(
        {
            "row_id": range(30),
            "timestamp": range(30),
            "exchange": ["X"] * 30,
            "instrument_id": ["A"] * 30,
            "y": y,
            "x1": x[:, 0],
            "x2": x[:, 1],
            "x3": x[:, 2],
        }
    )
    fields = {"y", "x1", "x2", "x3"}
    expr = "TS_RESID_STD3($y, $x1, $x2, $x3, 12)"
    actual = execute(expr, frame, fields)["value"]
    design = np.column_stack((np.ones(12), x[-12:]))
    residual = y[-12:] - design @ np.linalg.lstsq(design, y[-12:], rcond=None)[0]
    assert actual[-1] == pytest.approx(np.std(residual, ddof=1))
    assert actual[:11].null_count() == 11
    singular = frame.with_columns(pl.col("x1").alias("x3"))
    assert execute(expr, singular, fields)["value"].null_count() == 30
    missing = frame.with_columns(
        pl.when(pl.col("row_id") == 20).then(None).otherwise(pl.col("y")).alias("y")
    )
    assert execute(expr, missing, fields)["value"][-1] is None


def test_lagged_holdings_do_not_replace_missing_and_do_not_use_current_signal():
    rows = []
    for t in range(2):
        for i in range(8):
            if t == 1 and i == 7:
                continue
            rows.append(
                dict(
                    row_id=len(rows),
                    timestamp=t,
                    exchange="X",
                    instrument_id=str(i),
                    eligible=True,
                    signal=float(i if t == 0 else -i),
                    r=float(i),
                    member=1.0,
                )
            )
    frame = pl.DataFrame(rows)
    fields = {"signal", "r", "member"}
    expr = "CS_LAGGED_SPREAD($r, $signal, GT($member, 0), 0.25, 0.8)"
    assert execute(expr, frame, fields)["value"].null_count() == frame.height
    # With 50% coverage the original high tail contains only observed member 6, not member 5.
    values = execute(expr.replace("0.8", "0.5"), frame, fields).join(
        frame.select("row_id", "timestamp"), on="row_id"
    )
    assert values.filter(pl.col("timestamp") == 1)["value"].to_list() == [5.5] * 7
    for bad in [
        "CS_LAGGED_MEAN($r, GT($member, 0), 4, 0)",
        "CS_LAGGED_SPREAD($r, $signal, GT($member, 0), 0.6, 0.8)",
    ]:
        with pytest.raises(ValueError):
            compile_factor(bad, fields)


def test_pinned_operator_catalog_rejects_unavailable_or_unlisted_operators():
    registry = OperatorRegistry(versions={"ADD": "1"})
    compile_factor("$x + 1", {"x"}, registry)
    with pytest.raises(ValueError, match="unknown operator"):
        compile_factor("TS_STD($x, 2)", {"x"}, registry)
    with pytest.raises(ValueError, match="unavailable operator version"):
        OperatorRegistry(versions={"ADD": "changed"})
