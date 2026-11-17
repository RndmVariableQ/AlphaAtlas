from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from alpha_atlas.config import asset_config, load_toml
from alpha_atlas.contracts import AssetProfile, DateRange, Fold, validate_target_transform
from alpha_atlas.evaluation import (
    EvaluationService,
    build_scoring_targets,
    compute_factor_values,
)
from alpha_atlas.expressions import compile_factor
from alpha_atlas.library import FactorLibrary
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


@pytest.fixture
def transform_market():
    raw = np.array([0.0, 0.01, 0.02, 0.03, 0.20, 0.10, 0.12, 0.13, 0.15, 0.90])
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    frame = (
        pl.DataFrame(
            [(day, str(i), 10 if i < 5 else 20, x) for day in days for i, x in enumerate(raw)],
            schema=["trading_day", "instrument_id", "ggroup", "signal"],
            orient="row",
        )
        .with_row_index("row_id")
        .with_columns(
            pl.col("trading_day").cast(pl.Datetime).alias("timestamp"),
            pl.lit("US").alias("exchange"),
            pl.lit(True).alias("eligible"),
            pl.col("signal").alias("total_return"),
        )
    )
    profile = {
        "target_return_field": "total_return",
        "target_horizon_bars": 1,
        "transform_groupby": ["ggroup"],
        "factor_transform": {"method": "z_score"},
        "target_transform": {"method": "sector_demean"},
    }
    return frame, raw, DateRange(*days), profile


@pytest.mark.parametrize("method", ["sector_demean", "sector_demedian", "sector_zscore"])
@pytest.mark.parametrize("factor_clip", ["omitted", None, (0.25, 0.75)])
@pytest.mark.parametrize("target_clip", ["omitted", None, (0.25, 0.75)])
def test_shared_grouping_and_independent_clipping(
    transform_market, method, factor_clip, target_clip
):
    frame, raw, period, profile = transform_market
    profile["target_transform"]["method"] = method
    for name, clip in (("factor_transform", factor_clip), ("target_transform", target_clip)):
        if clip != "omitted":
            profile[name]["clip_quantiles"] = clip
    factor_expected, target_expected = [], []
    for values in np.split(raw, 2):
        x = np.clip(values, values[1], values[3]) if isinstance(factor_clip, tuple) else values
        factor_expected.extend((x - x.mean()) / x.std(ddof=1))
        y = np.clip(values, values[1], values[3]) if isinstance(target_clip, tuple) else values
        center = np.median(y) if method == "sector_demedian" else y.mean()
        target_expected.extend((y - center) / (y.std(ddof=1) if method == "sector_zscore" else 1))
    registry = OperatorRegistry()
    factors = compute_factor_values(
        compile_factor("$signal", {"signal"}, registry), frame, {"signal"}, registry, profile
    ).sort("row_id")
    assert factors["value"].to_numpy() == pytest.approx(np.tile(factor_expected, 2))
    targets = build_scoring_targets(frame, profile, period).sort("row_id")
    assert targets["target"].head(10).to_numpy() == pytest.approx(target_expected)
    assert targets["target_raw"].head(10).to_numpy() == pytest.approx(raw)
    assert targets["target"].tail(10).null_count() == 10


def test_date_only_grouping_and_split_isolation(transform_market):
    frame, raw, period, profile = transform_market
    profile["transform_groupby"] = []
    targets = build_scoring_targets(frame, profile, period).sort("row_id")
    assert targets["target"].head(10).to_numpy() == pytest.approx(raw - raw.mean())
    targets = build_scoring_targets(frame, profile, DateRange(period.start, period.start))
    assert targets["target"].null_count() == frame.height


def test_missing_and_ineligible_group_members(transform_market):
    frame, raw, period, profile = transform_market
    frame = frame.with_columns(
        pl.when(pl.col("row_id") != 0).then(pl.col("ggroup")).alias("ggroup"),
        (pl.col("row_id") != 1).alias("eligible"),
    )
    targets = build_scoring_targets(frame, profile, period).sort("row_id")
    assert targets["target"].head(2).to_list() == [None, None]
    assert targets["target"][2] == pytest.approx(raw[2] - raw[2:5].mean())
    profile["transform_groupby"] = ["gind"]
    with pytest.raises(ValueError, match="grouping columns"):
        build_scoring_targets(frame, profile, period)


@pytest.mark.parametrize(
    "options",
    [
        {"method": "unknown"},
        {"method": "sector_demedian", "clip_quantiles": [0.9, 0.1]},
        {"method": "sector_demean", "clip_quantiles": [0, float("nan")]},
        {"method": "sector_demean", "clip_quantiles": False},
    ],
)
def test_invalid_target_transform(options):
    with pytest.raises(ValueError, match="target_transform"):
        validate_target_transform(options)


@pytest.mark.parametrize("groups", ["ggroup", ["ggroup", "ggroup"], [None]])
def test_invalid_transform_groups(groups):
    profile = asset_config(Path(__file__).resolve().parents[1], "us_equity")
    with pytest.raises(ValueError, match="transform_groupby"):
        AssetProfile.from_mapping(profile | {"transform_groupby": groups})


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf")])
def test_factor_transform_excludes_nonfinite_groups(transform_market, missing):
    frame, raw, period, profile = transform_market
    frame = frame.with_columns(
        pl.when(pl.col("row_id") % 10 >= 5)
        .then(pl.col("ggroup").cast(pl.Float64))
        .otherwise(pl.lit(missing, dtype=pl.Float64))
        .alias("ggroup")
    )
    registry = OperatorRegistry()
    factors = compute_factor_values(
        compile_factor("$signal", {"signal"}, registry), frame, {"signal"}, registry, profile
    ).sort("row_id")
    assert factors.filter(pl.col("row_id") % 10 < 5)["value"].null_count() == 10
    expected = (raw[5:] - raw[5:].mean()) / raw[5:].std(ddof=1)
    assert factors.filter(pl.col("row_id") % 10 >= 5)["value"].to_numpy() == pytest.approx(
        np.tile(expected, 2)
    )
    targets = build_scoring_targets(frame, profile, period)
    assert targets.filter(pl.col("row_id") % 10 < 5)["target"].null_count() == 10


def test_research_context_explains_factor_and_target_transforms(transform_market, tmp_path):
    frame, _, period, profile = transform_market
    profile = profile | {"metric": "cross_sectional_spearman", "frequency": "1d"}
    profile["factor_transform"]["clip_quantiles"] = [0.01, 0.99]
    next_day = period.end + timedelta(days=1)
    fold = Fold(
        "synthetic",
        DateRange(period.start, period.start),
        DateRange(period.end, period.end),
        DateRange(next_day, next_day),
    )
    evaluator = EvaluationService(frame, fold, profile, {"signal"})
    rules = load_toml(Path(__file__).resolve().parents[1] / "configs/benchmark.toml")
    session = SearchSession(evaluator, FactorLibrary(rules), RunStore(tmp_path), 1)
    context = session.research_context()
    factor = context["evaluation_rules"]["factor_transform"]
    target = context["evaluation_rules"]["target_transform"]
    assert factor["method"] == "z_score"
    assert factor["clip_quantiles"] == [0.01, 0.99]
    assert factor["groupby"] == target["groupby"] == ["ggroup"]
    assert "every expression" in factor["semantics"]
    assert "remaining_attempts" not in context
    assert "synthetic" not in str(context)
