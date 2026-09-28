from datetime import date, datetime

import numpy as np
import pandas as pd
import polars as pl
import pytest

from alpha_atlas.assets.ashare_rq import classify_missing_members, shift_factor_dates
from alpha_atlas.assets.futures import normalize_bars
from alpha_atlas.contracts import Candidate, EvaluationReport, Expression, FactorValues, Metric
from alpha_atlas.library import FactorLibrary


def report(identity):
    return EvaluationReport(
        identity,
        (Metric("ic", "train", 0.1, 200, "test"), Metric("ic", "val", 0.1, 200, "test")),
        1,
        1.0,
        0.01,
    )


def test_negative_duplicate_is_rejected_and_row_alignment_is_explicit():
    library = FactorLibrary(
        {"min_coverage": 0.8, "min_abs_val_ic": 0.01, "min_corr_overlap": 100, "max_abs_corr": 0.9}
    )
    a = Candidate(Expression("field", value="a"))
    b = Candidate(Expression("field", value="b"))
    values = pl.DataFrame({"row_id": range(200), "value": np.arange(200, dtype=float)})
    assert library.consider(
        a,
        report(a.expression.expression_id),
        FactorValues(a.expression.expression_id, "snapshot", values),
    ).accepted
    inverse = values.with_columns(-pl.col("value")).reverse()
    rejected = library.consider(
        b,
        report(b.expression.expression_id),
        FactorValues(b.expression.expression_id, "snapshot", inverse),
    )
    assert rejected.reason == "behavior_duplicate"
    assert rejected.max_abs_corr == pytest.approx(1.0)


def test_fundamentals_shift_over_weekend():
    calendar = pl.DataFrame({"trading_day": [date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)]})
    factors = pl.DataFrame({"source_day": [date(2020, 1, 3)], "roe": [0.1]})
    result = shift_factor_dates(factors, calendar)
    assert result["available_day"][0] == date(2020, 1, 6)
    assert result["source_day"][0] < result["available_day"][0]


def test_missing_members_inside_listing_lifetime_remain_unresolved():
    missing = pl.DataFrame(
        {
            "instrument_id": ["a", "a", "a", "a", "unknown"],
            "trading_day": [date(2020, 1, d) for d in [1, 2, 3, 4, 4]],
        }
    )
    instruments = pl.DataFrame(
        {"order_book_id": ["a"], "listed_date": ["2020-01-02"], "de_listed_date": ["2020-01-04"]}
    )
    result = classify_missing_members(missing, instruments)
    assert result["reason"].to_list() == [
        "before_listing",
        "unresolved",
        "unresolved",
        "on_or_after_delisting",
        "unresolved",
    ]


def test_native_futures_night_session_keeps_trading_day_and_real_contract():
    raw = pd.DataFrame(
        {
            "order_book_id": ["RB2005", "RB2010"],
            "datetime": [datetime(2020, 1, 2, 21, 5)] * 2,
            "trading_date": [datetime(2020, 1, 3)] * 2,
            "close": [3500.0, 3600.0],
            "total_turnover": [1e6, 2e6],
        }
    )
    raw = raw.set_index(["order_book_id", "datetime"])
    mapping = pl.DataFrame(
        {"trading_day": [date(2020, 1, 3)], "instrument_id": ["RB2005"], "product": ["RB"]}
    )
    instruments = pl.DataFrame(
        {
            "instrument_id": ["RB2005", "RB2010"],
            "exchange": ["SHFE"] * 2,
            "contract_multiplier": [10, 10],
        }
    )
    result = normalize_bars(raw, mapping, instruments)
    assert result.height == 1
    assert result["instrument_id"][0] == "RB2005"
    assert result["timestamp"][0].date() == date(2020, 1, 2)
    assert result["trading_day"][0] == date(2020, 1, 3)
