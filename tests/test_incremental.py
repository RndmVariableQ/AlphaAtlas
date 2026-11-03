import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import config_fingerprint
from alpha_atlas.contracts import DateRange, Expression, Fold
from alpha_atlas.evaluation import paired_block_bootstrap, paired_prediction_metrics
from alpha_atlas.expressions import compile_factor
from alpha_atlas.incremental import (
    _frozen,
    _load_study,
    assemble_arms,
    evaluate_incremental,
    fit_arms,
    prediction_panel,
)
from alpha_atlas.operators import OperatorRegistry


@pytest.fixture
def config():
    return {
        "seed": 42,
        "preprocessing": {"clip_quantiles": [0.01, 0.99], "product_demean": True},
        "linear": {"rcond": 1e-12},
        "lightgbm": {
            "num_boost_round": 4,
            "learning_rate": 0.05,
            "num_leaves": 5,
            "min_data_in_leaf": 5,
            "lambda_l2": 1.0,
            "num_threads": 1,
        },
    }


def test_paired_error_uses_product_weights_not_row_counts():
    y = np.r_[np.arange(5.0), np.arange(10.0)]
    frame = pl.DataFrame(
        {
            "row_id": np.arange(15),
            "product": ["A"] * 5 + ["B"] * 10,
            "target": y,
            "baseline": y + np.r_[np.full(5, 3), np.ones(10)],
            "prediction": y + np.r_[np.full(5, 2), np.full(10, 0.5)],
        }
    )
    weights = pl.DataFrame({"product": ["A", "B"], "weight": [1.0, 3.0]})
    result = paired_prediction_metrics(frame, weights)
    assert result["baseline_mse"] == pytest.approx(3.0)
    assert result["mse"] == pytest.approx(1.1875)
    assert result["relative_mse_reduction"] == pytest.approx(1 - 1.1875 / 3)
    assert result["metrics"]["weighted_pearson"]["delta"] == pytest.approx(0)
    assert result["ic_products"] == ["A", "B"]
    # A constant prediction has undefined IC, but remains in the error comparison.
    constant_a = frame.with_columns(
        pl.when(pl.col("product") == "A")
        .then(0.0)
        .otherwise(pl.col("prediction"))
        .alias("prediction")
    )
    result = paired_prediction_metrics(constant_a, weights)
    assert result["ic_products"] == ["B"]
    assert result["products"] == 2 and result["rows"] == 15
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_paired_errors_refuse_missing_instead_of_silently_changing_sample(bad):
    frame = pl.DataFrame(
        {
            "row_id": [1, 2],
            "product": ["A", "A"],
            "target": [1.0, 2.0],
            "baseline": [0.0, 0.0],
            "prediction": [1.0, bad],
        }
    )
    with pytest.raises(ValueError, match="finite common rows"):
        paired_prediction_metrics(frame, pl.DataFrame({"product": ["A"], "weight": [1.0]}))


def test_bootstrap_matches_independent_raw_row_resampling():
    rng = np.random.default_rng(4)
    days = [date(year, 1, day) for year in (2024, 2025) for day in (2, 3, 4)]
    rows = []
    for day in days:
        for product in ("A", "B"):
            for _ in range(6):
                target = float(rng.normal())
                rows.append(
                    {
                        "row_id": len(rows),
                        "trading_day": day,
                        "product": product,
                        "target": target,
                        "baseline": target + rng.normal(),
                        "prediction": target + 0.4 * rng.normal(),
                    }
                )
    frame = pl.DataFrame(rows)
    weights = pl.DataFrame({"product": ["A", "B"], "weight": [1.0, 3.0]})
    actual = paired_block_bootstrap(frame, weights, days=2, repeats=30, seed=91)
    # Independently reconstruct actual raw rows, preserving both products and complete days.
    rng = np.random.default_rng(91)
    samples = [[] for _ in range(30)]
    for year in (2024, 2025):
        year_days = [d for d in days if d.year == year]
        for sample in samples:
            starts = rng.integers(0, 2, size=2)
            picked = [d for start in starts for d in year_days[start : start + 2]][:3]
            sample.extend(row for d in picked for row in rows if row["trading_day"] == d)
    delta, reductions = [], []
    for sample in samples:
        errors, correlations = [], []
        for product in ("A", "B"):
            values = np.array(
                [
                    [r[k] for k in ("target", "baseline", "prediction")]
                    for r in sample
                    if r["product"] == product
                ]
            )
            correlations.append(
                np.corrcoef(values[:, 0], values[:, 2])[0, 1]
                - np.corrcoef(values[:, 0], values[:, 1])[0, 1]
            )
            errors.append(np.mean((values[:, 0, None] - values[:, 1:]) ** 2, axis=0))
        delta.append(np.average(correlations, weights=[1, 3]))
        b, p = np.average(errors, weights=[1, 3], axis=0)
        reductions.append(1 - p / b)
    assert actual["delta_weighted_pearson"]["interval_95"] == pytest.approx(
        np.quantile(delta, [0.025, 0.975])
    )
    assert actual["relative_mse_reduction"]["interval_95"] == pytest.approx(
        np.quantile(reductions, [0.025, 0.975])
    )
    same = frame.with_columns(pl.col("baseline").alias("prediction"))
    zero = paired_block_bootstrap(same, weights, days=20, repeats=8)
    assert zero["delta_weighted_pearson"]["interval_95"] == pytest.approx([0, 0])
    assert zero["relative_mse_reduction"]["interval_95"] == pytest.approx([0, 0])


def test_constant_predictions_keep_error_but_never_create_zero_ic():
    frame = pl.DataFrame(
        {
            "row_id": range(10),
            "product": ["A"] * 10,
            "trading_day": [date(2024, 1, 2)] * 10,
            "target": np.arange(10.0),
            "baseline": [1.0] * 10,
            "prediction": [1.0] * 10,
        }
    )
    weights = pl.DataFrame({"product": ["A"], "weight": [1.0]})
    result = paired_prediction_metrics(frame, weights)
    assert result["metrics"]["weighted_pearson"]["delta"] is None
    assert result["relative_mse_reduction"] == 0
    boot = paired_block_bootstrap(frame, weights, repeats=10)
    assert boot["delta_weighted_pearson"]["valid_replicates"] == 0
    assert boot["delta_weighted_pearson"]["interval_95"] is None


@pytest.mark.parametrize("include_union", [True, False])
def test_prefixes_keep_all_admissions_and_only_deduplicate_exact_identities(include_union):
    def item(identity):
        return {"compiled": {"factor_id": identity}, "report": {"direction": 1}}

    def trial(i, identity=None, reason="accepted"):
        return {
            "factor_trial_index": i,
            "feedback": {
                "accepted": reason == "accepted",
                "reason": reason,
                "report": {"expression_id": identity},
            },
        }

    source_a = (
        "A",
        {"factors": [item("base"), item("new")]},
        [trial(1, "base"), trial(2, reason="quality_threshold"), trial(3, "new")],
        {},
    )
    source_b = (
        "B",
        {"factors": [item("new"), item("different")]},
        [trial(1, "new"), trial(2, "different")],
        {},
    )
    factors, origins, arms = assemble_arms(
        [item("base")], [source_a, source_b], [2, 3], include_union=include_union
    )
    assert [f["compiled"]["factor_id"] for f in factors] == ["base", "new", "different"]
    assert arms["A_2"]["columns"] == [0]
    assert arms["A_3"]["columns"] == [0, 1]
    assert arms["B_3"]["actual_attempts"] == 2
    assert not arms["B_3"]["budget_reached"]
    if include_union:
        assert arms["union_3"]["columns"] == [0, 1, 2]
        assert arms["union_3"]["duplicate_count"] == 2
    else:
        assert set(arms) == {"baseline", "A_2", "A_3", "B_2", "B_3"}
    assert [o["source"] for o in origins[1]] == ["A", "B"]


def test_fitting_and_prediction_anchor_to_baseline_and_ignore_new_coverage(config):
    rng = np.random.default_rng(4)
    n = 100
    base = rng.normal(size=n)
    new = rng.normal(size=n)
    y = 2 * base + new
    base[-10:] = np.nan
    y[-10:] = 1e9  # Those rows must not change either arm's label clipping.
    training = pl.DataFrame(
        {"row_id": range(n), "product": ["A"] * n, "factor_0": base, "factor_1": new, "target": y}
    )
    arms = {"baseline": {"columns": [0]}, "new": {"columns": [0, 1]}, "empty": {"columns": [0]}}
    trained = fit_arms(training, arms, config)
    assert trained["training_rows"] == 90
    assert trained["arms"]["empty"] == {"reuse": "baseline"}
    for arm in ("baseline", "new"):
        prep = trained["arms"][arm]["preprocessing"]
        assert prep["label_bounds"] == pytest.approx(np.quantile(y[:90], [0.01, 0.99]))
    a, b = (trained["arms"][arm]["preprocessing"] for arm in ("baseline", "new"))
    for key in ("lower", "upper", "mean", "scale"):
        assert a[key][0] == b[key][0]
    scored = training.with_columns(pl.lit(date(2024, 1, 2)).alias("trading_day"))
    predictions = prediction_panel(scored, trained, config)
    assert predictions["row_id"].to_list() == list(range(90))
    assert predictions["empty_linear"].equals(predictions["baseline_linear"])
    assert predictions["empty_lightgbm"].equals(predictions["baseline_lightgbm"])
    assert trained["arms"]["new"]["training_rows"] == 90


def test_frozen_import_checks_commit_and_refuses_unfinished_search(tmp_path):
    spec = {"status": "running"}
    atomic_json(tmp_path / "run.json", spec)
    with pytest.raises(ValueError, match="must be frozen"):
        _frozen(tmp_path)
    atomic_json(tmp_path / "frozen/library.json", {"factors": []})
    spec.update(
        status="frozen",
        library_sha256=hashlib.sha256((tmp_path / "frozen/library.json").read_bytes()).hexdigest(),
    )
    spec["config_fingerprint"] = config_fingerprint(spec)
    atomic_json(tmp_path / "run.json", spec)
    assert _frozen(tmp_path)[1] == {"factors": []}
    atomic_json(tmp_path / "frozen/library.json", {"factors": ["modified"]})
    with pytest.raises(ValueError, match="frozen library changed"):
        _frozen(tmp_path)


def test_test_phase_refuses_to_load_market_without_all_saved_models(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alpha_atlas.incremental._load_study", lambda *a: ({}, {}, None, None, [], [])
    )
    monkeypatch.setattr(
        "alpha_atlas.incremental._market", lambda *a: pytest.fail("Test market must not be opened")
    )
    with pytest.raises(ValueError, match="before Test"):
        evaluate_incremental(tmp_path, tmp_path / "scope.json", [], tmp_path / "out", test=True)


@pytest.mark.parametrize("include_union", [True, False])
def test_paired_pipeline_freezes_all_models_before_test_and_reuses_saved_results(
    tmp_path, monkeypatch, config, include_union
):
    registry = OperatorRegistry()
    fields = ["adj_close", "volume", "amount"]
    factors = []
    for field in fields[:2]:
        compiled = compile_factor(Expression("field", value=field), set(fields), registry)
        factors.append(
            {
                "compiled": asdict(compiled),
                "report": {"direction": 1},
                "candidate": {"hypothesis": "synthetic"},
            }
        )
    fold = Fold(
        "fold2",
        DateRange(date(2019, 1, 1), date(2019, 12, 31)),
        DateRange(date(2022, 1, 1), date(2022, 12, 31)),
        DateRange(date(2024, 1, 1), date(2025, 12, 31)),
    )
    study = {
        "scope": {"id": "synthetic", "seeds": [42], "evaluation_nodes": [2]},
        "plan": {"uncertainty": {"block_length_days": 20, "replicates": 5, "seed": 4}},
        "model_config": config,
        "factor_ids": [f["compiled"]["factor_id"] for f in factors],
        "arms": {
            "baseline": {"columns": [0], "method": "baseline", "budget": None},
            "search_2": {"columns": [0, 1], "method": "search", "budget": 2},
            "union_2": {"columns": [0, 1], "method": "union", "budget": 2},
        },
    }
    if not include_union:
        del study["arms"]["union_2"]
    spec = {
        "snapshot_id": "synthetic",
        "fields": fields,
        "profile": {
            "price_field": "adj_close",
            "target_horizon_bars": 1,
            "metric": "time_series_weighted_pearson_ic",
            "panel": "product",
        },
        "rules": {"max_expression_nodes": 100, "max_expression_depth": 20},
        "warmup": {"start": "2018-01-01"},
    }
    rng = np.random.default_rng(8)
    rows = []
    for year in (2019, 2022, 2024, 2025):
        for product in ("A", "B"):
            price = 100.0
            for bar in range(60):
                price *= np.exp(rng.normal(scale=0.002))
                rows.append(
                    {
                        "row_id": len(rows),
                        "product": product,
                        "exchange": "TEST",
                        "instrument_id": f"{product}{year}",
                        "timestamp": datetime(year, 1, 2) + timedelta(minutes=5 * bar),
                        "trading_day": date(year, 1, 2),
                        "eligible": True,
                        "target_eligible": True,
                        "segment_id": year,
                        "product_segment_id": year,
                        "adj_close": price,
                        "volume": float(rng.uniform(1, 100)),
                        "amount": 100.0,
                    }
                )
    features = pl.DataFrame(rows)
    output = tmp_path / "comparison"
    calls = []

    class Market:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def snapshot_id(self):
            return "synthetic"

        def load_features(self, *, fields, start, end):
            calls.append(end)
            if end >= fold.test.start:
                fitted = json.loads((output / "fit.json").read_text())
                assert set(fitted["fitted"]["arms"]) == set(study["arms"])
                assert all(
                    set(arm["models"]) == {"linear", "lightgbm"}
                    for arm in fitted["fitted"]["arms"].values()
                )
            return features.filter(pl.col("trading_day").is_between(start, end))

    monkeypatch.setattr(
        "alpha_atlas.incremental._load_study",
        lambda *a: (study, spec, fold, registry, factors, [[], []]),
    )
    monkeypatch.setattr("alpha_atlas.incremental._market", lambda *a: Market())
    evaluate_incremental(tmp_path, tmp_path / "scope.json", [], output)
    assert calls == [fold.val.end]
    original_fit = (output / "fit.json").read_bytes()
    assert not (output / "test.json").exists()
    evaluate_incremental(tmp_path, tmp_path / "scope.json", [], output, test=True)
    assert calls == [fold.val.end, fold.test.end]
    assert (output / "fit.json").read_bytes() == original_fit
    result = json.loads((output / "test.json").read_text())
    assert len(result["results"]) == (4 if include_union else 2)
    assert result["common_rows"] == result["eligible_rows"]
    assert (tmp_path / result["predictions"]).is_file()
    assert all(row["natural_model_coverage"] == 1 for row in result["results"])
    assert evaluate_incremental(tmp_path, tmp_path / "scope.json", [], output, test=True)["cached"]
    assert calls == [fold.val.end, fold.test.end]
    from alpha_atlas.incremental import read_json

    def no_test_read(path):
        assert path.name != "test.json", "fit-only invocation must not read Test results"
        return read_json(path)

    monkeypatch.setattr("alpha_atlas.incremental.read_json", no_test_read)
    reused = evaluate_incremental(tmp_path, tmp_path / "scope.json", [], output)
    assert reused["cached"] and reused["output"] == str(output / "fit.json")


@pytest.fixture
def refit_study(tmp_path, monkeypatch, config):
    source_version = {
        "id": "search_v4",
        "asset": "futures",
        "universe": "all",
        "profile": {"asset_id": "futures"},
        "fold": {"id": "fold2"},
        "fields": ["close"],
        "data": {"sources": {k: {"snapshot_id": k} for k in ("base", "curve")}},
        "data_recipe": {},
        "operator_versions": {},
        "initial_library": {"factors": [{"factor_id": "base"}]},
        "model": deepcopy(config),
    }
    target_version = deepcopy(source_version)
    target_version["id"] = "model_v5"
    target_version["model"]["lightgbm"]["min_data_in_leaf"] = 20
    spec = {
        "method": "search",
        "seed": 42,
        "attempts": 1,
        "experiment_version": source_version,
        "snapshot_id": "synthetic",
        "profile": source_version["profile"],
        "fold": source_version["fold"],
        "config_fingerprint": "synthetic_config",
        "library_sha256": "synthetic_library",
        "run_id": "search_run",
    }
    baseline_spec = {
        **spec,
        "method": "baseline",
        "run_id": "baseline_run",
        "data_provenance": {"base_snapshot": "base", "curve_snapshot": "curve"},
    }
    initial = {"compiled": {"factor_id": "base"}, "initial": True}
    frozen = {
        "factors": [initial, {"compiled": {"factor_id": "new"}}],
        "runtime": {},
        "operators": [],
    }
    monkeypatch.setattr(
        "alpha_atlas.incremental._frozen",
        lambda path: (
            (baseline_spec, {**frozen, "factors": [initial]})
            if path.name == "baseline"
            else (spec, frozen)
        ),
    )
    monkeypatch.setattr("alpha_atlas.incremental._check_config", lambda *a: (None, None))
    monkeypatch.setattr("alpha_atlas.incremental._runtime", lambda *a: None)
    monkeypatch.setattr("alpha_atlas.incremental._registry", lambda *a: OperatorRegistry())
    monkeypatch.setattr("alpha_atlas.incremental.load_version", lambda *a: target_version)
    monkeypatch.setattr(
        "alpha_atlas.incremental.RunStore.trials",
        lambda self: [
            {
                "factor_trial_index": 1,
                "feedback": {
                    "reason": "accepted",
                    "accepted": True,
                    "report": {"expression_id": "new"},
                },
            }
        ],
    )
    atomic_json(tmp_path / "search/checkpoint.json", {"method_state": {}})
    atomic_json(
        tmp_path / "baseline_fit.json",
        {"spec": {"config": target_version["model"]}, "factor_ids": ["base"]},
    )
    atomic_json(
        tmp_path / "plan.json",
        {
            "experiment_version": "model_v5",
            "baseline_model_config": "baseline_fit.json",
            "baseline_frozen_library": "baseline/frozen/library.json",
        },
    )
    scope = {
        "parent_plan": "plan.json",
        "experiment_version": "model_v5",
        "source_experiment_version": "search_v4",
        "include_union": False,
        "methods": ["search"],
        "seeds": [42],
        "attempts_per_method": 1,
        "evaluation_nodes": [1],
    }
    atomic_json(tmp_path / "scope.json", scope)
    return scope, spec, target_version


def test_refit_changes_only_downstream_model_and_excludes_union(tmp_path, refit_study):
    _, original, target = refit_study
    before = deepcopy(original)
    study, spec, *_ = _load_study(tmp_path, tmp_path / "scope.json", [tmp_path / "search"])
    assert study["model_config"] == target["model"]
    assert study["model_config"] != original["experiment_version"]["model"]
    assert original == spec == before
    assert set(study["arms"]) == {"baseline", "search_1"}
    assert study["factor_ids"] == ["base", "new"]


@pytest.mark.parametrize("key", ["data", "fields", "fold", "operator_versions", "initial_library"])
def test_refit_rejects_changed_data_or_factor_semantics(tmp_path, refit_study, key):
    refit_study[2][key] = {"changed": True}
    with pytest.raises(ValueError, match=f"refit version changes source {key}"):
        _load_study(tmp_path, tmp_path / "scope.json", [tmp_path / "search"])


def test_refit_rejects_undeclared_source_version_and_invalid_union_setting(tmp_path, refit_study):
    scope, *_ = refit_study
    del scope["source_experiment_version"]
    atomic_json(tmp_path / "scope.json", scope)
    with pytest.raises(ValueError, match="planned method/seed/budget/version"):
        _load_study(tmp_path, tmp_path / "scope.json", [tmp_path / "search"])
    scope["include_union"] = "false"
    atomic_json(tmp_path / "scope.json", scope)
    with pytest.raises(ValueError, match="include_union must be boolean"):
        _load_study(tmp_path, tmp_path / "scope.json", [tmp_path / "search"])
