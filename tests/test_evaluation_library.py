from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from alpha_atlas.config import load_toml
from alpha_atlas.contracts import (
    Candidate,
    DateRange,
    EvaluationReport,
    Expression,
    FactorValues,
    Fold,
    Metric,
)
from alpha_atlas.evaluation import (
    FUTURES_METRICS,
    EvaluationService,
    build_targets,
    metric,
    metrics,
    product_weights,
)
from alpha_atlas.library import FactorLibrary
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import ArrowCache, RunStore


def market_frame():
    rows = []
    for day in range(20):
        for instrument in range(6):
            rows.append(
                {
                    "row_id": len(rows),
                    "timestamp": datetime(2020, 1, 1) + timedelta(days=day),
                    "trading_day": date(2020, 1, 1) + timedelta(days=day),
                    "exchange": "X",
                    "instrument_id": str(instrument),
                    "eligible": True,
                    "close": float(10 * (1 + 0.01 * instrument) ** day),
                    "x": float(instrument),
                }
            )
    return pl.DataFrame(rows)


def evaluator(data=None, **kwargs):
    fold = Fold(
        "f",
        DateRange(date(2020, 1, 1), date(2020, 1, 10)),
        DateRange(date(2020, 1, 11), date(2020, 1, 20)),
        DateRange(date(2020, 2, 1), date(2020, 2, 10)),
    )
    profile = {
        "price_field": "close",
        "target_horizon_bars": 2,
        "metric": "cross_sectional_spearman",
    }
    return EvaluationService(
        market_frame() if data is None else data, fold, profile, {"x", "close"}, **kwargs
    )


def test_direction_metrics_cache_and_context():
    service = evaluator()
    report = service.evaluate(Candidate("NEG($x)"))
    assert report.status == "success" and report.direction == -1
    linear = np.corrcoef(np.arange(6), np.log1p(0.01 * np.arange(6)))[0, 1]
    assert [m.value for m in report.metrics] == pytest.approx([-1, 1, -1, -linear, linear, -linear])
    assert report.metrics[0].n_obs == 48 and report.metrics[0].n_groups == 8
    values = service.observation(report.observation_ref)
    assert values.snapshot_id == service.snapshot_id
    cached = service.evaluate(Candidate("-$x", name="different name"))
    assert cached.cache_hit and cached.evaluation_id == report.evaluation_id
    other = evaluator(context={"universe": "other"}).evaluate(Candidate("NEG($x)"))
    assert (
        other.expression_id == report.expression_id and other.evaluation_id != report.evaluation_id
    )
    constant = evaluator().evaluate(Candidate("1"))
    assert constant.direction is None and constant.status == "constant_factor"
    assert evaluator().evaluate(Candidate("$target")).status == "compile_error"


def test_empty_validation_and_alignment_errors():
    data = market_frame().filter(pl.col("trading_day") <= date(2020, 1, 10))
    report = evaluator(data).evaluate(Candidate("$x"))
    assert report.coverage is None and report.status == "undefined_val_metric"
    with pytest.raises(ValueError, match="duplicate row"):
        evaluator(pl.concat([data, data.head(1)]))
    with pytest.raises(ValueError, match="targets"):
        evaluator(data.with_columns(pl.lit(1).alias("target_return")))
    with pytest.raises(ValueError, match="OOS"):
        evaluator(data.with_columns(pl.lit(date(2021, 1, 1)).alias("trading_day")))


def test_targets_do_not_cross_segments_or_invalid_endpoints():
    data = market_frame().filter(pl.col("instrument_id") == "1").head(8)
    data = data.with_columns(
        pl.Series("segment_id", [0, 0, 0, 1, 1, 1, 1, 1]),
        pl.Series("target_eligible", [True] * 6 + [False, True]),
    )
    result = build_targets(data, "close", 2)["target"].to_list()
    assert result[0] is not None
    assert result[1:3] == [None, None]
    assert result[3] is not None
    assert result[4:] == [None] * 4


def average_rank(values):
    values = np.asarray(values)
    return (
        1
        + (values[:, None] > values).sum(axis=1)
        + ((values[:, None] == values).sum(axis=1) - 1) / 2
    )


def test_futures_metric_pools_contracts_before_ranking():
    rows = []
    for product, contract, count, sign in [("P", "A", 5, 1), ("P", "B", 10, -1), ("Q", "C", 30, 1)]:
        rows.extend(
            {
                "product": product,
                "exchange": "X",
                "instrument_id": contract,
                "value": float(i),
                "target": float(i * sign),
            }
            for i in range(count)
        )
    actual = metric(pl.DataFrame(rows), "time_series_equal_spearman_ic", "val")
    x = np.r_[np.arange(5), np.arange(10)]
    y = np.r_[np.arange(5), -np.arange(10)]
    pooled = np.corrcoef(average_rank(x), average_rank(y))[0, 1]
    assert actual.value == pytest.approx((pooled + 1) / 2)
    assert actual.value != pytest.approx(
        0.5
    )  # Averaging separate contract ICs is a different metric.
    assert actual.n_obs == 45 and actual.n_groups == 2


def test_futures_weights_use_full_split_amount_before_square_root():
    day = date(2020, 1, 1)
    frame = pl.DataFrame(
        {
            "product": ["P"] * 8 + ["Q", "Q", "zero", "missing"],
            "trading_day": [day] * 7 + [day + timedelta(days=1)] + [day] * 4,
            "eligible": [True] * 6 + [False, True] + [True] * 4,
            "amount": [
                9.0,
                16.0,
                None,
                float("nan"),
                float("inf"),
                -100.0,
                10000.0,
                10000.0,
                1.0,
                3.0,
                0.0,
                None,
            ],
            # Factor/target availability must not affect the period's product weights.
            "value": [None] * 12,
            "target": [None] * 12,
        }
    )
    result = product_weights(frame, DateRange(day, day)).sort("product")
    assert result.to_dicts() == [{"product": "P", "weight": 5.0}, {"product": "Q", "weight": 2.0}]


def test_futures_two_aggregates_handle_missing_ties_and_undefined_products():
    rows = []
    for product, values, targets in [
        ("P", [1, 2, 2, 4, 5, None, float("inf")], [5, 3, 3, 2, 1, 9, 9]),
        ("Q", [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, None]),
        ("constant", [1] * 5, [1, 2, 3, 4, 5]),
        ("constant_target", [1, 2, 3, 4, 5], [1] * 5),
        ("short", [1, 2, 3, 4], [1, 2, 3, 4]),
        ("zero", [1, 2, 3, 4, 5], [1, 2, 3, 4, 5]),
    ]:
        rows.extend(
            {"product": product, "value": x, "target": y}
            for x, y in zip(values, targets, strict=True)
        )
    frame = pl.DataFrame(rows, schema_overrides={"value": pl.Float64, "target": pl.Float64})
    weights = pl.DataFrame(
        {"product": ["P", "Q", "constant", "short"], "weight": [3.0, 1.0, 100.0, 100.0]}
    )
    measured = metrics(frame, FUTURES_METRICS[1], "val", weights=weights)
    assert [m.name for m in measured] == [
        "time_series_weighted_pearson_ic",
        "time_series_equal_pearson_ic",
        "time_series_weighted_spearman_ic",
        "time_series_equal_spearman_ic",
    ]
    for name in FUTURES_METRICS:
        reordered = metrics(frame, name, "val", weights=weights)
        expected = next(m for m in measured if m.name == name)
        assert reordered[0] == metric(frame, name, "val", weights=weights) == expected
        assert {m.name: m.value for m in reordered} == {m.name: m.value for m in measured}
    weighted, equal = measured[:2]
    pearson = np.corrcoef([1, 2, 2, 4, 5], [5, 3, 3, 2, 1])[0, 1]
    assert weighted.value == pytest.approx((3 * pearson + 1) / 4)
    assert equal.value == pytest.approx((pearson + 2) / 3)
    assert measured[2].value == pytest.approx(-0.5)
    assert (weighted.n_obs, weighted.n_groups) == (10, 2)
    assert measured[3].value == pytest.approx(1 / 3)
    assert (equal.n_obs, equal.n_groups) == (15, 3)
    shuffled = metrics(
        frame.sample(fraction=1, shuffle=True, seed=8), FUTURES_METRICS[1], "val", weights=weights
    )
    assert [m.value for m in shuffled] == pytest.approx([m.value for m in measured])
    assert [(m.name, m.n_obs, m.n_groups) for m in shuffled] == [
        (m.name, m.n_obs, m.n_groups) for m in measured
    ]
    empty = metrics(frame.head(0), FUTURES_METRICS[1], "val", weights=weights)
    assert all(m.value is None and m.n_obs == m.n_groups == 0 for m in empty)
    no_turnover = metrics(frame, FUTURES_METRICS[1], "val", weights=weights.head(0))
    assert no_turnover[0].value is None and no_turnover[1] == equal


def test_large_constant_groups_do_not_contribute_roundoff_correlations():
    rng = np.random.default_rng(92)
    n = 131071
    frame = pl.DataFrame(
        {
            "product": ["constant"] * n + ["variable"] * 5,
            "value": [0.0] * n + list(range(5)),
            "target": [*rng.normal(size=n), 0, 1, 2, 3, 4],
        }
    )
    for correlation in ("spearman", "pearson"):
        result = metric(frame, FUTURES_METRICS[0], "val", correlation=correlation)
        assert result.value == pytest.approx(1)
        assert result.n_obs == 5 and result.n_groups == 1


def futures_frame():
    rows = []
    for day in (1, 2):
        for product, sign, amount in [("P", -1, 16.0), ("Q", 1, 1.0), ("R", 1, 1.0)]:
            for contract in ("A", "B"):
                for i in range(40):
                    rows.append(
                        {
                            "row_id": len(rows),
                            "product": product,
                            "exchange": "X",
                            "instrument_id": product + contract,
                            "segment_id": day,
                            "timestamp": datetime(2020, 1, day, 9) + timedelta(minutes=5 * i),
                            "trading_day": date(2020, 1, day),
                            "eligible": True,
                            "close": float(100 * np.exp(sign * 0.0001 * i**2)),
                            "x": float(i),
                            "amount": amount,
                        }
                    )
    return pl.DataFrame(rows)


def test_futures_primary_direction_admission_and_fixed_split_weights():
    from alpha_atlas.methods.baselines import reward

    frame = futures_frame()
    fold = Fold(
        "f",
        DateRange(date(2020, 1, 1), date(2020, 1, 1)),
        DateRange(date(2020, 1, 2), date(2020, 1, 2)),
        DateRange(date(2020, 1, 3), date(2020, 1, 3)),
    )
    profile = {"price_field": "close", "target_horizon_bars": 12, "metric": FUTURES_METRICS[1]}
    service = EvaluationService(frame, fold, profile, {"x", "close"}, retain_training=True)
    report = service.evaluate(Candidate("$x"))
    assert report.status == "success" and report.direction == -1
    names = [FUTURES_METRICS[1]] * 3 + [FUTURES_METRICS[0]] * 3
    assert [m.name for m in report.metrics] == names + [
        n.replace("pearson", "spearman") for n in names
    ]
    assert [m.value for m in report.metrics] == pytest.approx(
        [-1 / 3, 1 / 3, -1 / 3, 1 / 3, -1 / 3, 1 / 3] * 2
    )
    assert all(m.n_obs == 168 and m.n_groups == 3 for m in report.metrics)
    library = FactorLibrary(RULES, min_train_val_ic=0.005)
    feedback = library.consider(
        Candidate("$x"), report, service.observation(report.observation_ref)
    )
    assert feedback.accepted and reward(feedback) == pytest.approx(1 + 1 / 3)
    assert len(library.view().list(min_val_ic=0.3)) == 1
    assert library.view().list(min_val_ic=0.4) == ()
    # Secondary IC cannot rescue a failing primary IC or a library-view threshold.
    c, r, v = evaluated("other", np.arange(200, dtype=float))
    primary = replace(r.metrics[1], name=FUTURES_METRICS[1], value=0.005)
    secondary = replace(primary, name=FUTURES_METRICS[0], value=0.9)
    bad = replace(r, metrics=(r.metrics[0], primary, secondary))
    assert (
        FactorLibrary(RULES, min_train_val_ic=0.005).consider(c, bad, v).reason
        == "quality_threshold"
    )
    accepted = replace(bad, metrics=(r.metrics[0], replace(primary, value=0.02), secondary))
    other_library = FactorLibrary(RULES, min_train_val_ic=0.005)
    assert other_library.consider(c, accepted, v).accepted
    assert other_library.view().list(min_val_ic=0.5) == ()
    assert service.evaluate(Candidate("$x")).cache_hit
    assert service.evaluate(Candidate("$amount")).status == "compile_error"
    changed = frame.with_columns(
        pl.when(pl.col("trading_day") == fold.val.start)
        .then(pl.col("amount") * 100)
        .otherwise(pl.col("amount"))
        .alias("amount")
    )
    assert (
        product_weights(changed, fold.train)
        .sort("product")
        .equals(service.weights["train"].sort("product"))
    )
    # Removing a factor's final values leaves the full-period weights unchanged.
    service.evaluate(Candidate("IF_THEN_ELSE($x < 25, $x, LOG(-1))"))
    assert service.weights["val"].sort("product")["weight"].to_list() == pytest.approx(
        np.sqrt([16 * 80, 80, 80])
    )
    changed = frame.with_columns(
        pl.when((pl.col("trading_day") == fold.val.start) & (pl.col("product") == "P"))
        .then(pl.col("amount") / 256)
        .otherwise(pl.col("amount"))
        .alias("amount")
    )
    opposite_val = EvaluationService(changed, fold, profile, {"x"}).evaluate(Candidate("$x"))
    assert opposite_val.direction == -1
    assert [m.value for m in opposite_val.metrics[:3]] == pytest.approx([-1 / 3, -7 / 9, 7 / 9])
    zero_train = frame.with_columns(
        pl.when(pl.col("trading_day") == fold.train.start)
        .then(0.0)
        .otherwise(pl.col("amount"))
        .alias("amount")
    )
    undefined = EvaluationService(zero_train, fold, profile, {"x"}).evaluate(Candidate("$x"))
    assert undefined.direction is None and undefined.status == "undefined_train_metric"
    assert undefined.metrics[3].value == pytest.approx(1 / 3)
    assert all(m.value is None for m in undefined.metrics if m.split == "val")
    # Opposite signs: Pearson chooses the shared direction, including reported Spearman.
    opposed = frame.with_columns(
        (100 * (0.0001 * pl.col("x").pow(2)).exp()).alias("close"),
        pl.when(pl.col("x") == 27).then(1e6).otherwise(-pl.col("x")).alias("x"),
    )
    dual = EvaluationService(opposed, fold, profile, {"x"}).evaluate(Candidate("$x"))
    assert dual.direction == 1 and dual.metrics[0].value > 0
    assert dual.metrics[6].value < 0  # Raw training Spearman.
    assert dual.metrics[7].value == dual.metrics[8].value < 0  # Shared frozen direction.


def test_futures_label_is_twelve_native_bars_with_contract_and_segment_boundaries():
    frame = futures_frame().sample(fraction=1, shuffle=True, seed=3)
    actual = frame.join(build_targets(frame, "close", 12), on="row_id").sort("row_id")
    first = actual.row(0, named=True)
    assert first["target"] == pytest.approx(np.log(np.exp(-0.0001 * 12**2)))
    assert actual.filter(pl.col("x") >= 28)["target"].null_count() == 12 * 12
    assert actual.filter(pl.col("x") < 28)["target"].null_count() == 0


RULES = {"min_abs_val_ic": 0.01, "min_coverage": 0.8, "max_abs_corr": 0.9, "min_corr_overlap": 100}


def evaluated(name, array, snapshot="S", context="C"):
    candidate = Candidate(Expression("field", value=name))
    identity = candidate.expression.expression_id
    report = EvaluationReport(
        identity,
        (Metric("ic", "train", 0.1, 200, "test"), Metric("ic", "val", 0.1, 200, "test")),
        1,
        1.0,
        0.0,
    )
    values = FactorValues(
        identity, snapshot, pl.DataFrame({"row_id": range(len(array)), "value": array}), context
    )
    return candidate, report, values


def test_library_context_readonly_full_nearest_and_atomicity(tmp_path):
    rng = np.random.default_rng(18)
    a, b = rng.normal(size=(2, 200))
    library = FactorLibrary(RULES, directory=tmp_path / "members")
    first = evaluated("a", a)
    assert library.consider(*first).accepted
    assert library.consider(*evaluated("b", b)).accepted
    rejected = library.consider(*evaluated("c", -b))
    assert rejected.reason == "behavior_duplicate"
    assert rejected.nearest_factor == evaluated("b", b)[1].expression_id
    assert rejected.max_abs_corr == pytest.approx(1) and rejected.comparison_complete
    assert library.consider(*evaluated("d", a, "other")).reason == "observation_context_mismatch"
    view = library.view()
    with pytest.raises(FrozenInstanceError):
        view.version = 999
    view.stats()["members"] = 999
    assert len(library.view().members) == 2

    def fail(_):
        raise OSError("disk full")

    with pytest.raises(OSError):
        library.consider(*evaluated("fresh", rng.normal(size=200)), commit=fail)
    assert library.view().version == 2


def test_library_rejection_boundaries():
    library = FactorLibrary(RULES)
    a = evaluated("a", np.arange(200, dtype=float))
    assert library.consider(*a).accepted
    assert library.consider(*a).reason == "expression_duplicate"
    b = evaluated("b", np.arange(200, dtype=float))
    assert (
        library.consider(b[0], replace(b[1], direction=None), b[2]).reason
        == "undefined_train_metric"
    )
    assert library.consider(b[0], replace(b[1], coverage=0.79), b[2]).reason == "low_coverage"
    short = evaluated("c", np.arange(99, dtype=float))
    result = library.consider(*short)
    assert result.reason == "insufficient_corr_overlap" and not result.comparison_complete
    assert library.consider(*evaluated("d", np.ones(200))).reason == "constant_factor"
    duplicate = replace(b[2], frame=pl.concat([b[2].frame, b[2].frame.head(1)]))
    assert library.consider(b[0], b[1], duplicate).reason == "duplicate_observation_row"


@pytest.mark.parametrize(
    "train, val, accepted",
    [
        (0.006, 0.006, True),
        (-0.006, -0.006, True),
        (0.005, 0.1, False),
        (-0.005, -0.1, False),
        (0.1, 0.005, False),
        (-0.1, -0.005, False),
        (0.004, 0.1, False),
        (0.1, 0.004, False),
        (0.1, -0.1, False),
        (-0.1, 0.1, False),
        (0.0, 0.1, False),
        (float("nan"), 0.1, False),
        (float("inf"), 0.1, False),
        (0.1, float("nan"), False),
        (0.1, float("inf"), False),
    ],
)
def test_futures_both_split_quality_boundaries(train, val, accepted):
    candidate, report, values = evaluated("x", np.arange(200, dtype=float))
    direction = -1 if train < 0 else 1
    primary = FUTURES_METRICS[1]
    report = replace(
        report,
        direction=direction,
        metrics=(
            replace(report.metrics[0], name=primary, value=train),
            replace(report.metrics[1], name=primary, value=direction * val),
            replace(report.metrics[1], name=primary, split="val_raw", value=val),
            # Strong secondary metrics must not rescue either primary split.
            replace(report.metrics[0], name=FUTURES_METRICS[0], value=0.9),
            replace(report.metrics[1], name=FUTURES_METRICS[0], value=0.9),
        ),
    )
    library = FactorLibrary(RULES, min_train_val_ic=0.005)
    feedback = library.consider(candidate, report, values)
    assert feedback.accepted is accepted
    assert feedback.reason == ("accepted" if accepted else "quality_threshold")


def test_stock_quality_retains_validation_only_inclusive_threshold():
    candidate, report, values = evaluated("x", np.arange(200, dtype=float))
    report = replace(
        report,
        metrics=(replace(report.metrics[0], value=0.001), replace(report.metrics[1], value=0.01)),
    )
    assert FactorLibrary(RULES).consider(candidate, report, values).accepted


@pytest.mark.parametrize("cutoff, accepted", [(0.6, False), (0.7, False), (0.8, True)])
@pytest.mark.parametrize("sign", [-1, 1])
def test_configurable_correlation_cutoff(cutoff, accepted, sign):
    rules = load_toml(Path(__file__).resolve().parents[1] / "configs/benchmark.toml")
    assert rules["max_abs_corr"] == 0.7
    library = FactorLibrary({**rules, "min_corr_overlap": 5, "max_abs_corr": cutoff})
    assert library.consider(*evaluated("first", np.arange(5, dtype=float))).accepted
    feedback = library.consider(*evaluated("next", sign * np.array([0.0, 1.0, 3.0, 4.0, 2.0])))
    assert feedback.max_abs_corr == pytest.approx(0.7)
    assert feedback.accepted is accepted
    assert feedback.reason == ("accepted" if accepted else "behavior_duplicate")


def test_batch_record_order_and_readback(tmp_path):
    store = RunStore(tmp_path)
    service = evaluator(registry=OperatorRegistry(record=store.record_operator))
    library = FactorLibrary(
        {**RULES, "min_corr_overlap": 5},
        snapshot_id=service.snapshot_id,
        context_id=service.context_id,
        directory=tmp_path / "cache/members",
    )
    session = SearchSession(service, library, store, 3)
    feedback = session.evaluate_many(
        [Candidate("$x"), Candidate("$x"), Candidate("$oops"), Candidate("$close")]
    )
    assert [f.reason for f in feedback] == [
        "accepted",
        "expression_duplicate",
        "compile_error",
        "budget_rejected",
    ]
    assert [f.trial_index for f in feedback] == [1, 2, 3, 4]
    assert feedback[1].report.cache_hit
    assert session.remaining_budget() == 0
    assert store.library_view() == library.view()
    assert len(store.trials()) == 4
    assert not list(tmp_path.rglob("*.duckdb"))
    with pytest.raises(ValueError, match="restore committed library"):
        SearchSession(service, FactorLibrary(RULES), store, 3)


def test_evaluation_disk_budget_counts_files_after_restart(tmp_path):
    service = evaluator(cache_dir=tmp_path)
    first = service.evaluate(Candidate("$x"))
    second = service.evaluate(Candidate("NEG($x)"))
    limit = max(p.stat().st_size for p in tmp_path.glob("*.arrow"))
    restarted = evaluator(cache_dir=tmp_path, cache_bytes=limit)
    assert sum(p.stat().st_size for p in tmp_path.glob("*.arrow")) <= limit
    for formula in ("$x", "NEG($x)", "$close"):
        report = restarted.evaluate(Candidate(formula))
        assert report.status == "success"
        assert restarted.observation(report.observation_ref).frame.height == 48
        assert sum(p.stat().st_size for p in tmp_path.glob("*.arrow")) <= limit
    assert first.metrics[1].value == second.metrics[1].value


@pytest.mark.parametrize("limit", [0, 1])
def test_oversized_cache_result_is_returned_without_retaining_disk_file(tmp_path, limit):
    service = evaluator(cache_dir=tmp_path, cache_bytes=limit)
    report = service.evaluate(Candidate("$x"))
    assert report.status == "success"
    assert service.observation(report.observation_ref).frame.height == 48
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("restore", [False, True])
def test_shared_member_budget_rebuilds_only_when_needed(tmp_path, restore):
    paths = (tmp_path / "evaluations", tmp_path / "members")
    saved = [
        evaluated(f"x{i}", a) for i, a in enumerate(np.random.default_rng(10).normal(size=(3, 200)))
    ]
    limit = max(v.frame.write_ipc(None, compression="zstd").getbuffer().nbytes for _, _, v in saved)
    cache = ArrowCache(paths, limit)
    rebuilt = []
    observations = {r.expression_id: v for _, r, v in saved}

    def rebuild(entry):
        rebuilt.append(entry.factor_id)
        return observations[entry.factor_id]

    library = FactorLibrary(RULES, directory=paths[1], disk_cache=cache, rebuild=rebuild)
    for item in saved:
        assert library.consider(*item).accepted
        assert sum(p.stat().st_size for p in tmp_path.rglob("*.arrow")) <= limit
    view = library.view()
    # Evaluation writes share the same budget and can evict every member file.
    cache.write(paths[0] / "evaluation.arrow", saved[0][2].frame)
    assert not list(paths[1].glob("*.arrow"))
    if restore:
        cache = ArrowCache(paths, limit)
        library = FactorLibrary(RULES, directory=paths[1], disk_cache=cache, rebuild=rebuild)
        rebuilt.clear()
        library.restore(view)
        assert rebuilt == []
    rebuilt.clear()
    duplicate = evaluated("inverse", -saved[0][2].frame["value"].to_numpy())
    feedback = library.consider(*duplicate)
    assert feedback.reason == "behavior_duplicate" and feedback.comparison_complete
    assert feedback.nearest_factor == saved[0][1].expression_id
    assert feedback.max_abs_corr == pytest.approx(1)
    assert set(rebuilt) == set(observations)
    assert library.view() == view
    assert sum(p.stat().st_size for p in tmp_path.rglob("*.arrow")) <= limit


def test_cache_index_only_reads_metadata_and_failed_write_keeps_previous(tmp_path, monkeypatch):
    path = tmp_path / "a.arrow"
    frame = pl.DataFrame({"value": [1.0, 2.0]})
    frame.write_ipc(path, compression="zstd")
    original = path.read_bytes()
    untouched = tmp_path / "evidence.json"
    untouched.write_text("keep")

    def fail_read(*args, **kwargs):
        raise AssertionError("cache indexing must not decode IPC")

    monkeypatch.setattr(pl, "read_ipc", fail_read)
    cache = ArrowCache((tmp_path,), len(original))

    def fail_write(self, file, **kwargs):
        file.write_bytes(b"partial")
        raise OSError("synthetic disk error")

    monkeypatch.setattr(pl.DataFrame, "write_ipc", fail_write)
    with pytest.raises(OSError, match="disk error"):
        cache.write(path, frame)
    assert path.read_bytes() == original
    assert not path.with_suffix(".tmp").exists()
    ArrowCache((tmp_path,), 0)
    assert not path.exists()
    assert untouched.read_text() == "keep"


@pytest.mark.parametrize("state", ["present", "missing", "unreadable"])
def test_member_cache_read_or_rebuild_does_not_change_admission(tmp_path, state):
    item = evaluated("x", np.arange(200, dtype=float))
    rebuilt = []

    def rebuild(entry):
        rebuilt.append(entry.factor_id)
        return item[2]

    library = FactorLibrary(
        RULES, directory=tmp_path, disk_cache=ArrowCache((tmp_path,)), rebuild=rebuild
    )
    assert library.consider(*item).accepted
    path = tmp_path / f"{item[1].expression_id}.arrow"
    if state == "missing":
        path.unlink()
    elif state == "unreadable":
        path.write_bytes(b"broken IPC")
    before = library.view()
    result = library.consider(*evaluated("inverse", -np.arange(200, dtype=float)))
    assert result.reason == "behavior_duplicate" and result.comparison_complete
    assert library.view() == before
    assert len(rebuilt) == (0 if state == "present" else 1)


def test_polars_panic_records_failed_trial_and_allows_next_candidate(tmp_path, monkeypatch):
    import alpha_atlas.evaluation as module

    session = SearchSession(evaluator(), FactorLibrary(RULES), RunStore(tmp_path), 2)
    original = module.execute

    def panic(*args, **kwargs):
        raise pl.exceptions.PanicException("synthetic Polars panic")

    monkeypatch.setattr(module, "execute", panic)
    failed = session.evaluate(Candidate("TS_LINEAR_DECAY($x,3)"))
    assert failed.report.status == "compute_error" and not failed.accepted
    assert failed.report.failure_reason == "synthetic Polars panic"
    monkeypatch.setattr(module, "execute", original)
    assert session.evaluate(Candidate("$x")).report.status == "success"
    assert len(RunStore(tmp_path).trials()) == 2 and session.remaining_budget() == 0


def test_evaluation_does_not_swallow_keyboard_interrupt(monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("alpha_atlas.evaluation.execute", interrupt)
    with pytest.raises(KeyboardInterrupt):
        evaluator().evaluate(Candidate("$x"))
