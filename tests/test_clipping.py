from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from test_checkpoint import interrupted_run
from test_evaluation_library import average_rank
from test_pipeline import project as project
from test_search_diagnostics import PERIOD, futures_panel

from alpha_atlas.assets.common import atomic_parq
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import read_json
from alpha_atlas.contracts import Candidate, DateRange, Fold
from alpha_atlas.evaluation import (
    FUTURES_METRICS,
    EvaluationService,
    apply_clipping,
    build_targets,
    search_diagnostics,
    split_panel,
)
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen


def raw_series(day):
    values = np.arange(102, dtype=float)
    values[[0, 100, 101]] = [-1e6, 1e6, -1e15]
    returns = np.sin(np.arange(101) * 0.2) * 0.005
    returns[[0, 100]] = [-0.5, 0.5]
    if day == 0:
        return values * 1e6, returns * 10
    if day == 2:
        return np.linspace(-500, 500, 102), np.roll(returns, 7) * 3
    return values, returns


@pytest.fixture
def clip_market():
    rows = []
    for day in range(3):
        values, returns = raw_series(day)
        prices = 100 * np.exp(np.r_[0, np.cumsum(returns)])
        start = datetime(2019, 12, 31, 9) + timedelta(days=day)
        for bar, (value, price) in enumerate(zip(values, prices, strict=True)):
            rows.append(
                {
                    "row_id": len(rows),
                    "timestamp": start + timedelta(minutes=5 * bar),
                    "trading_day": start.date(),
                    "exchange": "X",
                    "instrument_id": "P1",
                    "product": "P",
                    "segment_id": 0,
                    "eligible": True,
                    "target_eligible": True,
                    "bar": bar,
                    "x": value,
                    "close": price,
                    "amount": 1.0,
                }
            )
    fold = Fold(
        "synthetic",
        DateRange(date(2020, 1, 1), date(2020, 1, 1)),
        DateRange(date(2020, 1, 2), date(2020, 1, 2)),
        DateRange(date(2020, 1, 3), date(2020, 1, 3)),
    )
    return pl.DataFrame(rows), fold


def service(frame, fold, **kwargs):
    profile = {"price_field": "close", "target_horizon_bars": 1, "metric": FUTURES_METRICS[0]}
    return EvaluationService(frame, fold, profile, {"x", "close"}, **kwargs)


def test_disabled_clipping_keeps_raw_metrics_observations_and_rebuilt_training(
    clip_market, monkeypatch
):
    def forbid_fit(*args, **kwargs):
        raise AssertionError("unclipped evaluation must not fit quantile bounds")

    monkeypatch.setattr("alpha_atlas.evaluation.clip_bounds", forbid_fit)
    evaluator = service(
        *clip_market, clip_quantiles=None, retain_training=True, training_cache_factors=1
    )
    candidate = Candidate("$x")
    report = evaluator.evaluate(candidate)
    assert report.status == "success" and report.clipping == {}
    for split, day in (("train", 1), ("val_raw", 2), ("val", 2)):
        x, y = raw_series(day)
        for correlation in ("pearson", "spearman"):
            expected = (
                np.corrcoef(x[:-1], y)[0, 1]
                if correlation == "pearson"
                else np.corrcoef(average_rank(x[:-1]), average_rank(y))[0, 1]
            )
            if split == "val":
                expected *= report.direction
            for measured in (
                m for m in report.metrics if m.split == split and correlation in m.name
            ):
                assert measured.value == pytest.approx(expected, abs=1e-12)
                assert measured.n_obs == 101
    observation = evaluator.observation(report.observation_ref).frame
    np.testing.assert_allclose(observation["value"], raw_series(2)[0][:-1])
    evaluator.evaluate(Candidate("NEG($x)"))
    assert report.expression_id not in evaluator._training
    rebuilt = evaluator._training_values(report.expression_id, candidate)
    np.testing.assert_allclose(rebuilt["value"], raw_series(1)[0][:-1])


@pytest.mark.parametrize("quantiles", [(0.01, 0.99), (0.1, 0.9)])
def test_train_fitted_factor_and_label_clipping_match_independent_metrics(clip_market, quantiles):
    frame, fold = clip_market
    evaluator = service(frame, fold, clip_quantiles=quantiles)
    report = evaluator.evaluate(Candidate("$x"))
    train_x, train_y = raw_series(1)
    val_x, val_y = raw_series(2)
    x_bounds = np.quantile(train_x[:-1], quantiles)
    y_bounds = np.quantile(train_y, quantiles)
    assert report.status == "success"
    assert report.clipping["quantiles"] == list(quantiles)
    assert report.clipping["value"] == pytest.approx(x_bounds)
    assert report.clipping["target"] == pytest.approx(y_bounds)
    clipped = [
        (np.clip(x[:-1], *x_bounds), np.clip(y, *y_bounds))
        for x, y in ((train_x, train_y), (val_x, val_y))
    ]
    for correlation in ("pearson", "spearman"):
        expected = [
            np.corrcoef(x, y)[0, 1]
            if correlation == "pearson"
            else np.corrcoef(average_rank(x), average_rank(y))[0, 1]
            for x, y in clipped
        ]
        for measured in (m for m in report.metrics if correlation in m.name):
            value = {
                "train": expected[0],
                "val_raw": expected[1],
                "val": expected[1] * report.direction,
            }[measured.split]
            assert measured.value == pytest.approx(value, abs=1e-12)
            assert measured.n_obs == 101 and measured.n_groups == 1
    observation = evaluator.observation(report.observation_ref).frame
    np.testing.assert_allclose(observation["value"], np.clip(val_x[:-1], *x_bounds))
    raw_ic = np.corrcoef(train_x[:-1], train_y)[0, 1]
    assert abs(report.metrics[0].value - raw_ic) > 0.1
    assert report.coverage == 1.0


def test_validation_values_and_labels_cannot_change_training_bounds_or_direction(clip_market):
    frame, fold = clip_market
    original = service(frame, fold).evaluate(Candidate("$x"))
    validation = pl.col("trading_day") == fold.val.start
    altered = frame.with_columns(
        pl.when(validation).then(pl.col("x") * -1e9).otherwise(pl.col("x")).alias("x"),
        pl.when(validation)
        .then((pl.col("bar") % 2 * 20 - 10).exp())
        .otherwise(pl.col("close"))
        .alias("close"),
    )
    other = service(altered, fold).evaluate(Candidate("$x"))
    assert original.clipping == other.clipping
    assert original.direction == other.direction
    assert [m for m in original.metrics if m.split == "train"] == [
        m for m in other.metrics if m.split == "train"
    ]
    assert original.metrics[2].value != pytest.approx(other.metrics[2].value)


def test_fit_excludes_warmup_ineligible_cross_split_and_nonfinite_rows(clip_market):
    frame, fold = clip_market
    train = pl.col("trading_day") == fold.train.start
    invalid_factor = pl.col("bar").is_in([30, 31, 32])
    frame = frame.with_columns(
        (~(train & (pl.col("bar") == 7))).alias("eligible"),
        (~(train & (pl.col("bar") == 50))).alias("target_eligible"),
        pl.when(invalid_factor & (pl.col("bar") == 30))
        .then(float("nan"))
        .when(invalid_factor & (pl.col("bar") == 31))
        .then(float("inf"))
        .when(invalid_factor)
        .then(None)
        .otherwise(pl.col("x"))
        .alias("x"),
    )
    evaluator = service(frame, fold)
    report = evaluator.evaluate(Candidate("$x"))
    x, y = raw_series(1)
    label_rows = np.ones(101, dtype=bool)
    label_rows[[7, 49, 50]] = False
    factor_rows = label_rows.copy()
    factor_rows[[30, 31, 32]] = False
    assert report.clipping["value"] == pytest.approx(np.quantile(x[:-1][factor_rows], [0.01, 0.99]))
    assert report.clipping["target"] == pytest.approx(np.quantile(y[label_rows], [0.01, 0.99]))
    # Missingness of another factor cannot change the shared label thresholds.
    complete = evaluator.evaluate(Candidate("$close"))
    assert complete.clipping["target"] == report.clipping["target"]
    assert evaluator.observation(report.observation_ref).frame["value"].null_count() == 3
    assert report.coverage == pytest.approx(98 / 101)


def test_clipping_preserves_missing_values_in_both_columns():
    frame = pl.DataFrame(
        {
            "value": [-1e6, 1e6, None, float("nan"), float("inf"), -float("inf")],
            "target": [1e6, -1e6, None, float("inf"), -float("inf"), float("nan")],
        }
    )
    clipped = apply_clipping(frame, {"value": [-3.0, 3.0], "target": [-1.0, 1.0]})
    assert clipped.to_dict(as_series=False) == {
        "value": [-3.0, 3.0, None, None, None, None],
        "target": [1.0, -1.0, None, None, None, None],
    }


@pytest.mark.parametrize("problem", ["empty_train", "missing_factor", "constant_factor"])
def test_unusable_training_bounds_never_fall_back_to_validation(clip_market, problem):
    frame, fold = clip_market
    train = pl.col("trading_day") == fold.train.start
    if problem == "empty_train":
        frame = frame.filter(~train)
    else:
        frame = frame.with_columns(
            pl.when(train)
            .then(5.0 if problem == "constant_factor" else None)
            .otherwise(pl.col("x"))
            .alias("x")
        )
    evaluator = service(frame, fold)
    report = evaluator.evaluate(Candidate("$x"))
    assert report.direction is None
    assert all(m.value is None for m in report.metrics)
    if problem == "constant_factor":
        assert report.clipping["value"] == [5.0, 5.0]
        assert report.status == "constant_factor"
    else:
        assert report.clipping["value"] is None
        assert evaluator.observation(report.observation_ref).frame["value"].null_count() == 101
    if problem == "empty_train":
        assert report.clipping["target"] is None


@pytest.mark.parametrize(
    "quantiles",
    [
        "1-99",
        [],
        [0.1],
        [0.1, 0.5, 0.9],
        [-0.1, 0.9],
        [0.1, 1.1],
        [0.9, 0.1],
        [0.5, 0.5],
        [True, 0.99],
        [0.01, float("nan")],
        [-float("inf"), 0.99],
    ],
)
def test_invalid_clipping_configuration_is_rejected(clip_market, quantiles):
    with pytest.raises(ValueError, match="clip_quantiles"):
        service(*clip_market, clip_quantiles=quantiles)


def test_clipping_configuration_changes_evaluation_context_not_factor_identity(clip_market):
    left = service(*clip_market)
    right = service(*clip_market, clip_quantiles=(0.1, 0.9))
    a, b = (evaluator.evaluate(Candidate("$x")) for evaluator in (left, right))
    assert a.expression_id == b.expression_id
    assert left.context_id != right.context_id and a.evaluation_id != b.evaluation_id
    assert a.clipping != b.clipping


def test_training_period_is_part_of_clipped_observation_context(clip_market):
    frame, fold = clip_market
    extended = replace(fold, train=DateRange(date(2019, 12, 31), fold.train.end))
    left, right = service(frame, fold), service(frame, extended)
    a, b = (evaluator.evaluate(Candidate("$x")) for evaluator in (left, right))
    assert a.expression_id == b.expression_id
    assert a.clipping != b.clipping and left.context_id != right.context_id


def test_training_correlation_cache_rebuild_preserves_clipped_values(clip_market):
    evaluator = service(*clip_market, retain_training=True, training_cache_factors=1)
    first = evaluator.evaluate(Candidate("$x"))
    expected = evaluator._training[first.expression_id].clone()
    second = evaluator.evaluate(Candidate("NEG($x)"))
    assert first.expression_id not in evaluator._training
    result = evaluator.factor_correlations(
        [(first.expression_id, second.expression_id)],
        {first.expression_id: Candidate("$x"), second.expression_id: Candidate("NEG($x)")},
        5,
    )[0]
    assert result.value == pytest.approx(-1) and result.n_obs == 101
    rebuilt = evaluator._training_values(first.expression_id, Candidate("$x"))
    assert rebuilt.equals(expected)
    assert evaluator.evaluate(Candidate("$x")).clipping == first.clipping


def test_icir_uses_clipped_scores_and_turnover_keeps_original_holdings():
    panel = futures_panel().with_columns(
        pl.when((pl.col("trading_day") == PERIOD.start) & (pl.col("value") == 2.5))
        .then(1000.0)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    clipping = {
        name: np.quantile(panel[name].to_numpy(), [0.1, 0.9]).tolist()
        for name in ("value", "target")
    }
    daily = []
    for day in sorted(panel["trading_day"].unique()):
        correlations = []
        for product in ("A", "B"):
            group = panel.filter((pl.col("trading_day") == day) & (pl.col("product") == product))
            x = np.clip(group["value"].to_numpy(), *clipping["value"])
            y = np.clip(group["target"].to_numpy(), *clipping["target"])
            correlations.append(np.corrcoef(x, y)[0, 1])
        daily.append(np.mean(correlations))
    raw = search_diagnostics(panel, PERIOD, FUTURES_METRICS[0], 1)
    actual = search_diagnostics(panel, PERIOD, FUTURES_METRICS[0], 1, clipping=clipping)
    assert actual["icir"] == pytest.approx(np.mean(daily) / np.std(daily, ddof=1))
    assert actual["icir"] != pytest.approx(raw["icir"])
    assert actual["turnover"] == raw["turnover"]
    assert actual["turnover_observations"] == raw["turnover_observations"]


def test_oos_reuses_frozen_bounds_without_fitting_and_checks_configuration(project, monkeypatch):
    path = project / "data/ashare/bars/fixture.parq"
    frame = pl.read_parquet(path).with_columns(
        pl.when(
            (pl.col("trading_day") == date(2021, 1, 1))
            & (pl.col("instrument_id").str.starts_with("000000"))
        )
        .then(1e8)
        .otherwise(pl.col("adj_close"))
        .alias("adj_close")
    )
    atomic_parq(frame, path)

    class CloseMethod:
        def run(self, session):
            assert session.evaluate(Candidate("$adj_close")).accepted

    directory = run(
        project,
        "ashare",
        "fold1",
        "clipping",
        42,
        attempts=1,
        field_names=["adj_close"],
        method_impl=CloseMethod(),
    )
    spec = read_json(directory / "run.json")
    frozen_path = directory / "frozen/library.json"
    frozen_bytes = frozen_path.read_bytes()
    item = read_json(frozen_path)["factors"][0]
    clipping = item["report"]["clipping"]
    assert (
        clipping == read_json(directory / "trials/00000001.json")["feedback"]["report"]["clipping"]
    )
    assert spec["rules"]["clip_quantiles"] == [0.01, 0.99]
    with ParqMarketData(project / "data/ashare", "union1800") as market:
        features = market.load_features(fields=["adj_close"], end=date(2022, 12, 31))
    targets = build_targets(features, "adj_close", 5)
    raw = split_panel(
        features.join(targets, on="row_id"), DateRange(date(2021, 1, 1), date(2022, 12, 31))
    ).sort("row_id")
    assert raw["adj_close"].max() > clipping["value"][1]
    assert raw["target"].min() < clipping["target"][0]
    from alpha_atlas import runner

    original_metrics = runner.metrics
    calls = []

    def measured(panel, *args, **kwargs):
        ordered = panel.sort("row_id")
        assert ordered["row_id"].equals(raw["row_id"])
        np.testing.assert_allclose(
            ordered["value"],
            np.clip(raw["adj_close"], *clipping["value"]) * item["report"]["direction"],
        )
        np.testing.assert_allclose(ordered["target"], np.clip(raw["target"], *clipping["target"]))
        calls.append(True)
        return original_metrics(panel, *args, **kwargs)

    def forbid_fit(*args, **kwargs):
        raise AssertionError("OOS must read frozen training bounds without fitting quantiles")

    monkeypatch.setattr("alpha_atlas.evaluation.clip_bounds", forbid_fit)
    monkeypatch.setattr(runner, "metrics", measured)
    result = evaluate_frozen(project, directory)
    assert calls == [True] and result["results"][0]["status"] == "success"
    assert evaluate_frozen(project, directory) == result and calls == [True]
    assert frozen_path.read_bytes() == frozen_bytes
    benchmark = project / "configs/benchmark.toml"
    benchmark.write_text(benchmark.read_text().replace("[0.01, 0.99]", "[0.1, 0.9]"))
    with pytest.raises(ValueError, match="evaluation configuration changed"):
        evaluate_frozen(project, directory)


def test_unclipped_configuration_resumes_and_tests_without_fitting_bounds(project, monkeypatch):
    benchmark = project / "configs/benchmark.toml"
    configured = benchmark.read_text()
    benchmark.write_text(configured.replace("clip_quantiles = [0.01, 0.99]\n", ""))

    def forbid_fit(*args, **kwargs):
        raise AssertionError("unclipped run must not fit quantile bounds")

    monkeypatch.setattr("alpha_atlas.evaluation.clip_bounds", forbid_fit)
    with monkeypatch.context() as patch:
        directory = interrupted_run(project, patch, attempts=6)
    committed = {path: path.read_bytes() for path in (directory / "trials").glob("*.json")}
    resume(project, directory)
    assert all(path.read_bytes() == content for path, content in committed.items())
    assert read_json(directory / "checkpoint.json")["completed"] == 6
    frozen = read_json(directory / "frozen/library.json")
    assert frozen["factors"]
    assert all(not item["report"].get("clipping") for item in frozen["factors"])
    result = evaluate_frozen(project, directory)
    assert len(result["results"]) == len(frozen["factors"])
    assert all(item["status"] == "success" for item in result["results"])
    benchmark.write_text(configured)
    with pytest.raises(ValueError, match="evaluation configuration changed"):
        evaluate_frozen(project, directory)
