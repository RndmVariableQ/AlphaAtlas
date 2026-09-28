import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest
from test_pipeline import project as project

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import read_json
from alpha_atlas.config import load_toml
from alpha_atlas.contracts import Candidate, DateRange
from alpha_atlas.model import evaluate_models, fit_models, predict, transform, validate_config
from alpha_atlas.runner import _check_config, run, run_model


@pytest.fixture
def config():
    result = load_toml(Path(__file__).resolve().parents[1] / "configs/model.toml")
    result["lightgbm"].update(num_boost_round=60, min_data_in_leaf=5, num_threads=1)
    return result


def test_linear_exact_fit_collinearity_missing_columns_and_serialization(config):
    x = np.linspace(-2, 2, 120)
    train = pl.DataFrame(
        {
            "a": x,
            "b": x * 2,
            "constant": np.ones(120),
            "missing": [None] * 120,
            "target": 0.2 + x * 3,
        }
    )
    fitted = fit_models(train, ["a", "b", "constant", "missing"], config)
    assert fitted["preprocessing"]["dropped"] == ["constant", "missing"]
    assert fitted["models"]["linear"]["rank"] == 1
    restored = json.loads(json.dumps(fitted, allow_nan=False))
    values, valid = transform(train, restored["preprocessing"])
    assert valid.all()
    assert predict(restored["models"]["linear"], values, threads=1) == pytest.approx(
        train["target"]
    )
    assert predict(restored["models"]["lightgbm"], values, threads=1) == pytest.approx(
        predict(fitted["models"]["lightgbm"], values, threads=1)
    )


def test_preprocessing_uses_train_statistics_and_excludes_all_missing_rows(config):
    train = pl.DataFrame(
        {
            "a": [1.0, 2.0, 3.0, None],
            "b": [2.0, None, 6.0, None],
            "target": [1.0, 2.0, 3.0, 100000.0],
        }
    )
    fitted = fit_models(train, ["a", "b"], config)
    assert fitted["training_rows"] == 3
    before = copy.deepcopy(fitted)
    val = pl.DataFrame({"a": [1000.0, None, None], "b": [None, 1000.0, None]})
    values, valid = transform(val, fitted["preprocessing"])
    assert valid.tolist() == [True, True, False]
    assert fitted["preprocessing"]["mean"] == [2.0, 4.0]
    assert values[0, 1] == 0 and values[1, 0] == 0
    assert fitted == before


def test_lightgbm_learns_nonlinearity_and_is_repeatable(config):
    x = np.linspace(-2, 2, 300)
    train = pl.DataFrame({"a": x, "target": x**2})
    first = fit_models(train, ["a"], config)
    second = fit_models(train, ["a"], config)
    assert first == second
    values, _ = transform(train, first["preprocessing"])
    tree = predict(first["models"]["lightgbm"], values, threads=1)
    linear = predict(first["models"]["linear"], values, threads=1)
    assert np.mean((tree - x**2) ** 2) < np.mean((linear - x**2) ** 2) * 0.1


@pytest.mark.parametrize("problem", ["empty", "constant_factors", "missing_factors", "constant_y"])
def test_unusable_training_data_fails(config, problem):
    a = [1.0, 2.0, 3.0]
    target = [1.0, 2.0, 3.0]
    if problem == "constant_factors":
        a = [1.0] * 3
    if problem == "missing_factors":
        a = [None] * 3
    if problem == "constant_y":
        target = [1.0] * 3
    train = pl.DataFrame({"a": a, "target": target})
    if problem == "empty":
        train = train.head(0)
    with pytest.raises(ValueError):
        fit_models(train, ["a"], config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("num_threads", 0),
        ("num_leaves", True),
        ("learning_rate", float("nan")),
        ("lambda_l2", -1e-301),
    ],
)
def test_invalid_model_settings_fail(config, key, value):
    config["lightgbm"][key] = value
    with pytest.raises(ValueError):
        validate_config(config)


class PriceMethod:
    def run(self, session):
        assert session.evaluate(Candidate("$adj_close")).accepted


@pytest.fixture
def frozen_run(project):
    path = project / "configs/model.toml"
    path.write_text(path.read_text().replace("200", "12").replace("100", "5"), encoding="utf-8")
    return run(project, "ashare", "fold1", "price", 42, 1, method_impl=PriceMethod())


def test_model_pipeline_separates_test_and_preserves_search(project, frozen_run, monkeypatch):
    original = {str(p): p.read_bytes() for p in frozen_run.rglob("*") if p.is_file()}
    ends = []
    load = ParqMarketData.load_features

    def checked_load(self, **kwargs):
        ends.append(kwargs["end"])
        if kwargs["end"] > date(2020, 12, 31):
            assert (frozen_run / "model/fit.json").exists()
        return load(self, **kwargs)

    monkeypatch.setattr(ParqMarketData, "load_features", checked_load)
    with pytest.raises(ValueError, match="before"):
        run_model(project, frozen_run, test=True)
    assert ends == []
    first = run_model(project, frozen_run)
    assert ends == [date(2020, 12, 31)]
    assert first["reports"] == {}
    assert not (frozen_run / "model/test.json").exists()
    fitted_bytes = (frozen_run / "model/fit.json").read_bytes()
    assert run_model(project, frozen_run) == first
    assert len(ends) == 1

    def no_refit(*args, **kwargs):
        raise AssertionError("test must not fit or call LightGBM.train")

    monkeypatch.setattr("alpha_atlas.model.fit_models", no_refit)
    monkeypatch.setattr(lgb, "train", no_refit)
    result = run_model(project, frozen_run, test=True)
    assert set(result["reports"]) == {"test"}
    assert ends[-1] == date(2022, 12, 31)
    assert run_model(project, frozen_run, test=True) == result
    assert len(ends) == 2
    assert (frozen_run / "model/fit.json").read_bytes() == fitted_bytes
    assert all(Path(p).read_bytes() == data for p, data in original.items())
    report = (frozen_run / "model/report.md").read_text(encoding="utf-8")
    assert "linear" in report and "lightgbm" in report and "| test |" in report


def test_old_frozen_library_is_an_explicit_new_experiment(project, frozen_run, monkeypatch):
    spec = read_json(frozen_run / "run.json")
    monkeypatch.setattr("alpha_atlas.runner.source_fingerprint", lambda: "new-model-source")
    _check_config(project, spec)
    run_model(project, frozen_run)
    model_spec = read_json(frozen_run / "model/fit.json")["spec"]
    assert not model_spec["source_matches_search"]
    assert model_spec["search_source_fingerprint"] == spec["source_fingerprint"]
    assert model_spec["source_fingerprint"] == "new-model-source"
    assert read_json(frozen_run / "run.json") == spec


def test_saved_model_reuse_and_test_do_not_check_current_source(project, frozen_run, monkeypatch):
    first = run_model(project, frozen_run)
    fit_path = frozen_run / "model/fit.json"
    saved = fit_path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("saved model must not scan current source or refit")

    monkeypatch.setattr("alpha_atlas.runner.source_fingerprint", forbidden)
    monkeypatch.setattr("alpha_atlas.model.fit_models", forbidden)
    assert run_model(project, frozen_run) == first
    result = run_model(project, frozen_run, test=True)
    assert set(result["reports"]) == {"test"}
    assert run_model(project, frozen_run, test=True) == result
    assert fit_path.read_bytes() == saved
    assert read_json(frozen_run / "model/test.json")["spec"] == read_json(fit_path)["spec"]


@pytest.mark.parametrize("change", ["config", "snapshot", "library", "run"])
def test_saved_model_checks_cannot_be_bypassed_by_cache(project, frozen_run, monkeypatch, change):
    run_model(project, frozen_run)
    if change == "config":
        path = project / "configs/model.toml"
        path.write_text(path.read_text().replace("0.05", "0.1"), encoding="utf-8")
    elif change == "snapshot":
        monkeypatch.setattr(ParqMarketData, "snapshot_id", lambda _: "changed")
    elif change == "library":
        with (frozen_run / "frozen/library.json").open("a", encoding="utf-8") as stream:
            stream.write(" ")
    else:
        spec = read_json(frozen_run / "run.json")
        spec["seed"] += 1
        atomic_json(frozen_run / "run.json", spec)
    with pytest.raises(ValueError, match="changed|modified"):
        run_model(project, frozen_run)


def test_nonfrozen_and_empty_runs_rejected(project, frozen_run):
    spec = read_json(frozen_run / "run.json")
    spec["status"] = "failed"
    atomic_json(frozen_run / "run.json", spec)
    with pytest.raises(ValueError, match="frozen run"):
        run_model(project, frozen_run)

    class EmptyMethod:
        def run(self, session):
            session.evaluate(Candidate("1"))

    empty = run(project, "ashare", "fold1", "empty", 1, 1, method_impl=EmptyMethod())
    with pytest.raises(ValueError, match="nonempty"):
        run_model(project, empty)


def test_missing_prediction_coverage_and_split_target_boundary(config):
    day = date(2020, 1, 1)
    train = pl.DataFrame({"a": np.arange(10.0), "target": np.arange(10.0)})
    fitted = fit_models(train, ["a"], config)
    panel = pl.DataFrame(
        {
            "a": [1.0, 2.0, 3.0, 4.0, 5.0, None, 100.0],
            "target": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0],
            "eligible": [True] * 7,
            "trading_day": [day] * 7,
            "target_end": [day] * 6 + [date(2020, 1, 2)],
        }
    )
    result = evaluate_models(
        panel,
        fitted,
        {"metric": "cross_sectional_spearman"},
        panel,
        DateRange(day, day),
        "val",
        config,
    )
    for report in result.values():
        assert report["eligible_rows"] == 6 and report["predicted_rows"] == 5
        assert report["coverage"] == pytest.approx(5 / 6)
    assert result["linear"]["metrics"][0]["value"] == pytest.approx(1.0)


def test_futures_models_use_shared_product_metrics_and_contract_targets(project):
    rows = []
    for year in range(2015, 2023):
        for product, amount in (("P", 1.0), ("Q", 9.0)):
            for i in range(50):
                rows.append(
                    {
                        "timestamp": datetime(year, 1, 1, 9) + timedelta(minutes=5 * i),
                        "trading_day": date(year, 1, 1),
                        "exchange": "XSGE",
                        "product": product,
                        "instrument_id": f"{product}{year}",
                        "close": float(100 * np.exp(0.00001 * i**2)),
                        "amount": amount,
                    }
                )
    atomic_parq(with_row_id(pl.DataFrame(rows)), project / "data/futures/bars/fixture.parq")
    atomic_json(
        project / "data/futures/manifest.json",
        {"status": "complete", "snapshot_id": "model-futures", "rows": len(rows)},
    )

    class CloseMethod:
        def run(self, session):
            assert session.evaluate(Candidate("$close")).accepted

    path = run(
        project,
        "futures",
        "fold1",
        "close",
        42,
        1,
        field_names=["close"],
        method_impl=CloseMethod(),
    )
    fit = run_model(project, path)
    result = run_model(project, path, test=True)
    assert fit["reports"] == {}
    for stage in (result["reports"]["test"],):
        for report in stage.values():
            assert report["eligible_rows"] == 2 * 2 * (50 - 12)
            assert len(report["metrics"]) == 4
            assert report["metrics"][0]["name"] == "time_series_weighted_pearson_ic"
            assert report["metrics"][0]["n_groups"] == 2
