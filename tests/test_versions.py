import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest
from test_pipeline import project as project

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import read_json
from alpha_atlas.config import asset_config, load_fold
from alpha_atlas.experiment import finish_experiment
from alpha_atlas.runner import _check_config, resume, run, run_model
from alpha_atlas.storage import RunStore
from alpha_atlas.versions import load_version


@pytest.fixture
def version(project):
    value = read_json(project / "configs/versions/rq8_fold2_v1.json")
    with ParqMarketData(project / "data/ashare", "union1800") as market:
        snapshot = market.snapshot_id()
    value.update(
        id="synthetic_v1",
        asset="ashare",
        universe="union1800",
        profile=asset_config(project, "ashare"),
        fold=asdict(load_fold(project, "fold1")),
        fields=["adj_close", "close", "volume", "amount"],
        data={
            "adapter": "parquet",
            "sources": {
                "base": {
                    "path": "data/ashare",
                    "snapshot_id": snapshot,
                    "allow_incomplete": False,
                }
            },
        },
    )
    atomic_json(project / "configs/versions/synthetic_v1.json", value)
    return "synthetic_v1"


def test_version_run_finish_uses_saved_rules_and_model_config(project, version, monkeypatch):
    calls = []
    load = ParqMarketData.load_features

    def checked_load(self, **kwargs):
        calls.append(kwargs["end"])
        if kwargs["end"] > date(2020, 12, 31):
            path = next((project / "artifacts/runs").glob("*/run.json"))
            assert read_json(path)["status"] == "frozen"
            assert (path.parent / "model/fit.json").exists()
        return load(self, **kwargs)

    monkeypatch.setattr(ParqMarketData, "load_features", checked_load)
    directory = run(project, "ashare", "fold1", "atlas", 42, attempts=12, version=version)
    spec = read_json(directory / "run.json")
    assert spec["library_size"] >= 1
    assert calls == [date(2020, 12, 31)]
    assert spec["experiment_version"] == load_version(project, version)
    # No mutable root config is consulted by a versioned run, resume, model or finish.
    for path in (project / "configs").glob("*.toml"):
        path.write_text("invalid = [")
    (project / "configs/assets/ashare.toml").write_text("invalid = [")
    _check_config(project, spec)
    finish_experiment(project, directory)
    assert calls == [date(2020, 12, 31)] * 2 + [date(2022, 12, 31)]
    fitted = read_json(directory / "model/fit.json")
    assert fitted["spec"]["config"] == spec["experiment_version"]["model"]
    assert set(read_json(directory / "model/test.json")["reports"]["test"]) == {
        "linear",
        "lightgbm",
    }
    run_model(project, directory, test=True)
    assert len(calls) == 3
    # It also reruns from the version alone after global settings have changed.
    rerun = run(project, "ashare", "fold1", "atlas", 42, attempts=12, version=version)
    first = read_json(directory / "frozen/library.json")["factors"]
    second = read_json(rerun / "frozen/library.json")["factors"]
    assert [f["compiled"] for f in first] == [f["compiled"] for f in second]
    assert [f["report"]["direction"] for f in first] == [f["report"]["direction"] for f in second]


def test_version_resume_matches_uninterrupted_and_checks_changes(project, version, monkeypatch):
    original = RunStore.record

    def interrupted(self, attempt, *args, **kwargs):
        original(self, attempt, *args, **kwargs)
        if attempt == 3:
            raise KeyboardInterrupt("injected")

    with monkeypatch.context() as patch:
        patch.setattr(RunStore, "record", interrupted)
        with pytest.raises(KeyboardInterrupt):
            run(project, "ashare", "fold1", "random", 42, attempts=5, version=version)
    directory = next((project / "artifacts/runs").iterdir())
    resume(project, directory)
    assert read_json(directory / "run.json")["status"] == "frozen"
    clean = run(project, "ashare", "fold1", "random", 42, attempts=5, version=version)
    a, b = RunStore(directory).trials(), RunStore(clean).trials()
    assert [t["feedback"]["candidate"] for t in a] == [t["feedback"]["candidate"] for t in b]
    assert [t["feedback"]["accepted"] for t in a] == [t["feedback"]["accepted"] for t in b]
    path = project / "configs/versions/synthetic_v1.json"
    value = read_json(path)
    value["rules"]["min_coverage"] = 0.123
    atomic_json(path, value)
    with pytest.raises(ValueError, match="version changed"):
        _check_config(project, read_json(directory / "run.json"))


def test_version_rejects_drift_before_loading_features(project, version, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a changed snapshot must fail before loading data")

    monkeypatch.setattr(ParqMarketData, "load_features", forbidden)
    manifest = project / "data/ashare/manifest.json"
    value = read_json(manifest)
    value["snapshot_id"] = "changed"
    atomic_json(manifest, value)
    with pytest.raises(ValueError, match="snapshot mismatch"):
        run(project, "ashare", "fold1", "random", 42, attempts=1, version=version)
    with pytest.raises(ValueError, match="field override"):
        run(project, "ashare", "fold1", "random", 42, field_names=["close"], version=version)


def test_published_versions_are_self_contained_and_recipe_bound():
    root = Path(__file__).parents[1]
    base = load_version(root, "rq8_fold2_v1")
    enriched = load_version(root, "rq8_zk26_fold2_v2")
    assert len(base["fields"]) == 8 and len(enriched["fields"]) == 34
    assert len(base["operator_versions"]) == 60
    assert len(enriched["operator_versions"]) == 66
    assert enriched["data"]["sources"]["curve"]["allow_incomplete"]
    assert len(enriched["data_recipe"]["factors"]) == 26
    assert base["fold"] == enriched["fold"]
    assert base["model"] == enriched["model"]
    assert json.loads(json.dumps(enriched)) == enriched


def test_prepared_field_context_has_semantics_without_version_or_dates(tmp_path):
    from test_evaluation_library import RULES, evaluator

    from alpha_atlas.library import FactorLibrary
    from alpha_atlas.operators import OperatorRegistry
    from alpha_atlas.session import SearchSession

    root = Path(__file__).parents[1]
    version = load_version(root, "rq8_zk26_fold2_v2")
    spec = {"experiment_version": version, "fields": ["close", "zk_mom12"]}
    service = evaluator(registry=OperatorRegistry(versions=version["operator_versions"]))
    current = SearchSession(service, FactorLibrary(RULES), RunStore(tmp_path), 1, run_spec=spec)
    context = current.query("get_context", {})
    prepared = context["expression_rules"]["prepared_fields"]
    assert set(prepared["fields"]) == {"zk_mom12"}
    assert prepared["fields"]["zk_mom12"]["calculation_expression"] == "TS_PROD(1 + $R, 12) - 1"
    text = json.dumps(context)
    for forbidden in (version["id"], "fold2", "2019-01-01", "2024-01-01", "data/futures"):
        assert forbidden not in text
    prepared["fields"]["zk_mom12"]["description"] = "changed"
    assert current.query("get_context", {}) != context
