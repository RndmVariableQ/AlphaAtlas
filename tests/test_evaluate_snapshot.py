import importlib.util
import json
from pathlib import Path

import pytest

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import config_fingerprint, read_json
from alpha_atlas.storage import FORMAT_VERSION


@pytest.fixture
def helper(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/evaluate_snapshot.py"
    spec = importlib.util.spec_from_file_location("evaluate_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def check_config(root, run):
        if config_fingerprint(run) != run["config_fingerprint"]:
            raise ValueError("run configuration was modified")

    monkeypatch.setattr(module, "check_config", check_config)
    return module


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "artifacts/comparisons/example/workspace/artifacts/runs/demo-42-abc"
    spec = {
        "format_version": FORMAT_VERSION,
        "run_id": directory.name,
        "status": "running",
        "attempts": 10,
        "resumable": True,
        "runtime": {},
        "experiment_version": {"id": "example_version"},
    }
    spec["config_fingerprint"] = config_fingerprint(spec)
    atomic_json(directory / "run.json", spec)
    atomic_json(
        directory / "checkpoint.json",
        {"completed": 5, "library_version": 3, "config_fingerprint": spec["config_fingerprint"]},
    )
    count = 0
    for index in range(1, 7):
        accepted = index in {1, 3, 5, 6}
        count += accepted
        atomic_json(
            directory / "factor_trials" / f"{index:08d}.json",
            {
                "factor_trial_index": index,
                "feedback": {
                    "accepted": accepted,
                    "library_version": count,
                    "candidate": {"name": f"candidate{index}"},
                    "report": {"direction": -1, "clipping": {"factor": [-2.0, 3.0]}},
                },
                "compiled": {"factor_id": f"factor{index}"},
                "library_size": count,
                "cumulative_seconds": index * 2.0,
            },
        )
    return directory


def test_latest_uses_committed_checkpoint_and_preserves_source(helper, source):
    before = {p: p.read_bytes() for p in source.rglob("*.json")}
    destination = helper.freeze_prefix(source)
    frozen = read_json(destination / "frozen/library.json")
    assert destination.name.endswith("-prefix5")
    assert [row["compiled"]["factor_id"] for row in frozen["factors"]] == [
        "factor1",
        "factor3",
        "factor5",
    ]
    assert all(row["report"]["direction"] == -1 for row in frozen["factors"])
    assert all(row["report"]["clipping"] == {"factor": [-2.0, 3.0]} for row in frozen["factors"])
    run = read_json(destination / "run.json")
    assert run["attempts"] == 5 and not run["resumable"] and run["status"] == "frozen"
    assert run["config_fingerprint"] == config_fingerprint(run)
    assert before == {p: p.read_bytes() for p in source.rglob("*.json")}
    assert not (destination / "checkpoint.json").exists()
    assert not (destination / "factor_trials/00000006.json").exists()
    for index in range(1, 6):
        trial = Path("factor_trials") / f"{index:08d}.json"
        assert (destination / trial).read_bytes() == (source / trial).read_bytes()


def test_exact_factor_count_stops_before_next_admission_and_reuses_prefix(helper, source):
    destination = helper.freeze_prefix(source, factors=1)
    assert destination.name.endswith("-prefix2")
    assert read_json(destination / "run.json")["total_seconds"] == 4.0
    before = {p: p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    assert helper.freeze_prefix(source, factors=1) == destination
    assert before == {p: p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    frozen = read_json(destination / "frozen/library.json")
    frozen["factors"][0]["report"]["direction"] = 1
    atomic_json(destination / "frozen/library.json", frozen)
    with pytest.raises(ValueError, match="different frozen members"):
        helper.freeze_prefix(source, factors=1)


def test_exact_attempt_count(helper, source):
    destination = helper.freeze_prefix(source, attempts=3)
    assert destination.name.endswith("-prefix3")
    assert len(read_json(destination / "frozen/library.json")["factors"]) == 2


@pytest.mark.parametrize("cutoff", [{"factors": 4}, {"attempts": 6}, {"factors": 0}])
def test_unavailable_or_invalid_cutoff_does_not_create_prefix(helper, source, cutoff):
    with pytest.raises(ValueError):
        helper.freeze_prefix(source, **cutoff)
    assert list(source.parent.glob("*-prefix*")) == []


def test_inconsistent_checkpoint_is_rejected(helper, source):
    path = source / "checkpoint.json"
    checkpoint = read_json(path)
    checkpoint["library_version"] = 4
    atomic_json(path, checkpoint)
    with pytest.raises(ValueError, match="checkpoint library size"):
        helper.freeze_prefix(source)
    assert list(source.parent.glob("*-prefix*")) == []


def test_resolver_supports_identifiers_and_rejects_ambiguity(helper, source, tmp_path):
    for selector in (str(source), source.name, "abc", "example_version", "example"):
        assert helper.resolve_run(tmp_path, selector) == source
    helper.freeze_prefix(source, factors=1)
    assert helper.resolve_run(tmp_path, "example_version") == source
    other = source.with_name("demo-43-def")
    atomic_json(other / "run.json", json.loads((source / "run.json").read_text()))
    with pytest.raises(ValueError, match="expected one source run"):
        helper.resolve_run(tmp_path, "example_version")
