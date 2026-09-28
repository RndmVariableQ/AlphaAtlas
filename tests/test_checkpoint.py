import json
import os
import subprocess
import sys

import pytest
from test_pipeline import project as project

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import Checkpoint, read_json, run_lock
from alpha_atlas.contracts import (
    Candidate,
    EvaluationReport,
    Metric,
    SearchContext,
    TargetDefinition,
    TrialFeedback,
)
from alpha_atlas.methods import make_method
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


def json_state(method):
    return json.loads(json.dumps(method.dump_state()))


@pytest.mark.parametrize("name", ["random", "gp", "mcts", "atlas"])
def test_method_json_state_including_pending_feedback(name):
    original = make_method(name, 42)
    context = SearchContext("futures", "5m", TargetDefinition("close", 12), "ic", 100)
    for step in range(40):
        candidate = original.ask(context)[0]
        restored = make_method(name, 999)
        restored.load_state(json_state(original))
        report = EvaluationReport(
            "id", (Metric("ic", "val", 0.03 * (step % 5), 100, "test"),), 1, 1.0, 0.0
        )
        result = TrialFeedback(candidate, report, step % 3 == 0, "test")
        original.tell([result])
        restored.tell([result])
        assert json_state(original) == json_state(restored)
        assert original.ask(context) == restored.ask(context)


def interrupted_run(project, monkeypatch, method="random", boundary="after_commit", attempts=8):
    """Stop around trial 5: GP already has a population and MCTS has a nonempty tree."""
    if boundary in {"before_commit", "after_commit"}:
        original = RunStore.record

        def record(self, attempt, *args, **kwargs):
            if attempt == 5 and boundary == "before_commit":
                raise KeyboardInterrupt("injected before commit")
            original(self, attempt, *args, **kwargs)
            if attempt == 5:
                raise KeyboardInterrupt("injected after commit")

        monkeypatch.setattr(RunStore, "record", record)
    elif boundary == "before_evaluate":
        original = SearchSession.evaluate

        def evaluate(self, *args, **kwargs):
            if self.trial_count == 4:
                raise KeyboardInterrupt("injected before evaluation")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(SearchSession, "evaluate", evaluate)
    elif boundary == "tell":
        cls = type(make_method(method, 42))
        original = cls.tell

        def tell(self, feedback):
            original(self, feedback)
            if feedback[0].trial_index == 5:
                raise KeyboardInterrupt("injected after feedback consumption")

        monkeypatch.setattr(cls, "tell", tell)
    else:
        original = Checkpoint.save

        def save(self, method, completed, attempts, version, pending=None):
            if completed == 5 and pending is None and boundary == "before_checkpoint":
                raise KeyboardInterrupt("injected before checkpoint commit")
            original(self, method, completed, attempts, version, pending)
            if completed == 5 and pending is None:
                raise KeyboardInterrupt("injected after checkpoint commit")

        monkeypatch.setattr(Checkpoint, "save", save)
    with pytest.raises(KeyboardInterrupt, match="injected"):
        run(project, "ashare", "fold1", method, 42, attempts=attempts)
    return next((project / "artifacts/runs").iterdir())


def logical_trials(directory):
    results = []
    for trial in RunStore(directory).trials():
        feedback = trial["feedback"]
        report = feedback["report"]
        results.append(
            (
                feedback["candidate"],
                feedback["accepted"],
                feedback["reason"],
                feedback["trial_index"],
                feedback["library_version"],
                feedback["nearest_factor"],
                report["direction"],
                report["status"],
                report["coverage"],
            )
        )
    return results


@pytest.mark.parametrize("method", ["random", "gp", "mcts", "atlas"])
@pytest.mark.parametrize(
    "boundary",
    [
        "before_evaluate",
        "before_commit",
        "after_commit",
        "tell",
        "before_checkpoint",
        "after_checkpoint",
    ],
)
def test_resume_matches_uninterrupted_run(project, monkeypatch, method, boundary):
    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch, method, boundary)
    assert read_json(path / "run.json")["status"] == "interrupted"
    assert read_json(path / "failures.json")[-1]["type"] == "KeyboardInterrupt"
    committed = {p: p.read_bytes() for p in (path / "trials").glob("*.json")}
    calls = []
    original = SearchSession.evaluate

    def evaluate(self, candidate, **kwargs):
        calls.append(candidate)
        return original(self, candidate, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SearchSession, "evaluate", evaluate)
        assert resume(project, path) == path
    assert len(calls) == 8 - len(committed)
    assert all(p.read_bytes() == data for p, data in committed.items())
    checkpoint = read_json(path / "checkpoint.json")
    assert checkpoint["completed"] == checkpoint["attempts"] == 8
    assert checkpoint["pending"] is None
    reference = run(project, "ashare", "fold1", method, 42, attempts=8)
    assert logical_trials(path) == logical_trials(reference)
    assert checkpoint["method_state"] == read_json(reference / "checkpoint.json")["method_state"]
    assert read_json(path / "run.json")["library_size"] == checkpoint["library_version"]
    assert all(r["status"] == "success" for r in evaluate_frozen(project, path)["results"])
    with pytest.raises(ValueError, match="already frozen"):
        resume(project, path)


@pytest.mark.parametrize("cache_state", ["present", "missing", "unreadable"])
def test_last_trial_and_cache_resume_without_readmission(project, monkeypatch, cache_state):
    from alpha_atlas.evaluation import EvaluationService

    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch, "atlas", attempts=5)
    members = RunStore(path).library_view().members
    assert members
    for file in (path / "cache").rglob("*.arrow"):
        if cache_state == "missing":
            file.unlink()
        elif cache_state == "unreadable":
            file.write_bytes(b"unreadable IPC")
    computed = []
    original = EvaluationService.evaluate

    def evaluate(self, candidate):
        computed.append(candidate)
        return original(self, candidate)

    monkeypatch.setattr(EvaluationService, "evaluate", evaluate)
    before = logical_trials(path)
    resume(project, path)
    # No candidates remain: freezing only needs definitions, not evicted member values.
    assert computed == []
    assert logical_trials(path) == before
    assert read_json(path / "checkpoint.json")["attempts"] == 5


def test_zero_disk_cache_resume_preserves_trials_and_oos(project, monkeypatch):
    from alpha_atlas.storage import ArrowCache

    monkeypatch.setattr("alpha_atlas.runner.ArrowCache", lambda paths: ArrowCache(paths, 0))
    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch, "atlas", attempts=8)
    committed = {p.name: p.read_bytes() for p in (path / "trials").glob("*.json")}
    resume(project, path)
    assert not list((path / "cache").rglob("*.arrow"))
    assert all((path / "trials" / name).read_bytes() == data for name, data in committed.items())
    reference = run(project, "ashare", "fold1", "atlas", 42, attempts=8)
    assert logical_trials(path) == logical_trials(reference)
    assert RunStore(path).library_view().members
    assert (
        evaluate_frozen(project, path)["results"] == evaluate_frozen(project, reference)["results"]
    )


@pytest.mark.parametrize(
    "change, message",
    [
        ("config", "configuration was modified"),
        ("snapshot", "dataset changed"),
        ("checkpoint", "budget or library version mismatch"),
        ("operators", "registered operator identity mismatch"),
        ("trial", "pending candidate differs"),
    ],
)
def test_resume_rejects_incompatible_evidence(project, monkeypatch, change, message):
    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch)
    if change == "operators":
        atomic_json(
            path / "operators/00000001.json",
            {
                "definition": {"name": "CHANGED", "kind": "composite", "scope": "ts"},
                "feedback": {
                    "accepted": True,
                    "operator_id": "original",
                    "elapsed_seconds": 0.0,
                    "error": None,
                },
            },
        )
    else:
        target = {
            "config": path / "run.json",
            "snapshot": project / "data/ashare/manifest.json",
            "checkpoint": path / "checkpoint.json",
            "trial": path / "trials/00000005.json",
        }[change]
        data = read_json(target)
        if change == "config":
            data["attempts"] += 1
        elif change == "snapshot":
            data["snapshot_id"] = "changed"
        elif change == "checkpoint":
            data["attempts"] += 1
        else:
            data["feedback"]["candidate"]["hypothesis"] = "different"
        atomic_json(target, data)
    with pytest.raises(ValueError, match=message):
        resume(project, path)
    assert read_json(path / "run.json")["status"] == "failed"
    assert read_json(path / "failures.json")[-1]["stage"] == (
        "setup" if change == "operators" else "resume_checks"
    )


def test_resume_and_oos_do_not_check_current_source(project, monkeypatch):
    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch, "atlas")
    source = read_json(path / "run.json")["source_fingerprint"]
    committed = {p: p.read_bytes() for p in (path / "trials").glob("*.json")}

    def no_source_scan():
        pytest.fail("resume and OOS must not scan current source")

    monkeypatch.setattr("alpha_atlas.runner.source_fingerprint", no_source_scan)
    resume(project, path)
    assert read_json(path / "run.json")["source_fingerprint"] == source
    assert len(RunStore(path).trials()) == 8
    assert all(p.read_bytes() == content for p, content in committed.items())
    result = evaluate_frozen(project, path)
    assert result["results"] and all(r["status"] == "success" for r in result["results"])
    saved = (path / "oos.json").read_bytes()
    assert evaluate_frozen(project, path) == result
    assert (path / "oos.json").read_bytes() == saved


def test_freeze_failure_resumes_without_more_search(project, monkeypatch):
    import alpha_atlas.runner as runner

    original = runner.atomic_json

    def write(path, value):
        if path.name == "library.json":
            raise OSError("disk full")
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(runner, "atomic_json", write)
        with pytest.raises(OSError, match="disk full"):
            run(project, "ashare", "fold1", "random", 42, attempts=2)
    path = next((project / "artifacts/runs").iterdir())
    assert read_json(path / "run.json")["status"] == "failed"
    assert read_json(path / "failures.json")[-1]["stage"] == "freeze"
    with pytest.raises(ValueError, match="requires a frozen"):
        evaluate_frozen(project, path)
    before = logical_trials(path)
    resume(project, path)
    assert logical_trials(path) == before
    assert read_json(path / "run.json")["status"] == "frozen"


def test_single_writer_lock_and_release_after_process_death(tmp_path):
    code = (
        "from pathlib import Path\n"
        "from alpha_atlas.checkpoint import run_lock\n"
        "import os, sys\n"
        "with run_lock(Path(sys.argv[1])):\n"
        " print('locked', flush=True)\n"
        " input()\n"
        " os._exit(23)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match="another writer"), run_lock(tmp_path):
            pass
        with pytest.raises(RuntimeError, match="another writer"):
            evaluate_frozen(tmp_path, tmp_path)
    finally:
        child.communicate("exit\n", timeout=10)
    assert child.returncode == 23
    with run_lock(tmp_path):
        pass


def test_stale_running_status_is_recorded_on_recovery(project, monkeypatch):
    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch)
    data = read_json(path / "run.json")
    data["status"] = "running"
    atomic_json(path / "run.json", data)
    resume(project, path)
    assert "unrecorded active time is unknown" in read_json(path / "failures.json")[-1]["message"]


def test_run_plugin_is_not_silently_restarted_and_setup_failure_is_recorded(project, monkeypatch):
    class Plugin:
        def run(self, session):
            session.evaluate(Candidate("1"))
            raise RuntimeError("token=should-not-be-recorded")

    with pytest.raises(RuntimeError):
        run(project, "ashare", "fold1", "custom", 1, attempts=2, method_impl=Plugin())
    path = next((project / "artifacts/runs").iterdir())
    assert not read_json(path / "run.json")["resumable"]
    assert "should-not-be-recorded" not in (path / "failures.json").read_text()
    with pytest.raises(ValueError, match="no resumable"):
        resume(project, path)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic data load failure")

    monkeypatch.setattr("alpha_atlas.runner.ParqMarketData.load_features", fail)
    with pytest.raises(RuntimeError, match="data load"):
        run(project, "ashare", "fold1", "random", 1, attempts=2)
    second = next(p for p in (project / "artifacts/runs").iterdir() if p != path)
    assert read_json(second / "run.json")["status"] == "failed"
    assert read_json(second / "failures.json")[-1]["stage"] == "load_data"


def test_process_abrupt_exit_then_cli_resume(project):
    script = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from alpha_atlas.runner import run\n"
        "from alpha_atlas.storage import RunStore\n"
        "original = RunStore.record\n"
        "def crash(self, attempt, *args, **kwargs):\n"
        " original(self, attempt, *args, **kwargs)\n"
        " if attempt == 2: os._exit(23)\n"
        "RunStore.record = crash\n"
        "run(Path(sys.argv[1]), 'ashare', 'fold1', 'mcts', 42, attempts=4)\n"
    )
    crashed = subprocess.run([sys.executable, "-c", script, str(project)], timeout=30)
    assert crashed.returncode == 23
    path = next((project / "artifacts/runs").iterdir())
    assert read_json(path / "run.json")["status"] == "running"
    assert not (path / "failures.json").exists()
    committed = (path / "trials/00000002.json").read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "alpha_atlas.cli", "--root", str(project), "resume", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["run_dir"] == str(path)
    assert (path / "trials/00000002.json").read_bytes() == committed
    assert len(RunStore(path).trials()) == 4
    assert read_json(path / "checkpoint.json")["attempts"] == 4
    assert read_json(path / "run.json")["status"] == "frozen"
    assert "unknown" in read_json(path / "failures.json")[-1]["message"]


def test_failed_checkpoint_write_keeps_previous_snapshot(project, monkeypatch):
    from alpha_atlas import checkpoint as module

    original = module.atomic_json

    def fail(path, value):
        if path.name == "checkpoint.json" and value["completed"] == 1:
            raise OSError("checkpoint write failed")
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_json", fail)
        with pytest.raises(OSError, match="checkpoint write"):
            run(project, "ashare", "fold1", "gp", 42, attempts=3)
    path = next((project / "artifacts/runs").iterdir())
    checkpoint = read_json(path / "checkpoint.json")
    assert checkpoint["completed"] == 0 and checkpoint["pending"]["trial_index"] == 1
    assert len(RunStore(path).trials()) == 1
    resume(project, path)
    assert read_json(path / "checkpoint.json")["attempts"] == 3


def test_cache_io_error_is_a_run_failure_and_can_retry(project, monkeypatch):
    from alpha_atlas.evaluation import EvaluationService

    def fail(*args, **kwargs):
        raise OSError("cache write failed")

    with monkeypatch.context() as patch:
        patch.setattr(EvaluationService, "_remember", fail)
        with pytest.raises(OSError, match="cache write"):
            run(project, "ashare", "fold1", "random", 42, attempts=2)
    path = next((project / "artifacts/runs").iterdir())
    assert RunStore(path).trials() == []
    failure = read_json(path / "failures.json")[-1]
    assert failure["stage"] == "evaluate" and failure["trial_index"] == 1
    resume(project, path)
    assert len(RunStore(path).trials()) == 2


def test_json_state_rejects_nonserializable_values_without_replacing_checkpoint(tmp_path):
    class State:
        value = {"step": 1}

        def dump_state(self):
            return self.value

    state = State()
    checkpoint = Checkpoint(tmp_path, {"config_fingerprint": "fixture"})
    checkpoint.save(state, 0, 0, 0)
    before = (tmp_path / "checkpoint.json").read_bytes()
    for value in ({"nan": float("nan")}, {"object": object()}):
        state.value = value
        with pytest.raises((TypeError, ValueError)):
            checkpoint.save(state, 1, 1, 0)
        assert (tmp_path / "checkpoint.json").read_bytes() == before


def test_registered_definitions_restore_without_duplicate_records(project, monkeypatch):
    from alpha_atlas.operators import OperatorDefinition, OperatorRegistry

    original = RunStore.__init__

    def setup(self, directory):
        original(self, directory)
        if not (directory / "operators").exists():
            registry = OperatorRegistry(record=self.record_operator)
            definition = OperatorDefinition("DOUBLE", (("x", "series"),), "ADD(x, x)")
            assert registry.register(definition).accepted
            assert not registry.register(definition).accepted

    with monkeypatch.context() as patch:
        patch.setattr(RunStore, "__init__", setup)
        path = interrupted_run(project, patch)
    records = {p: p.read_bytes() for p in (path / "operators").glob("*.json")}
    resume(project, path)
    assert len(records) == len(list((path / "operators").glob("*.json"))) == 2
    assert all(p.read_bytes() == data for p, data in records.items())
    assert [op["name"] for op in read_json(path / "frozen/library.json")["operators"]] == ["DOUBLE"]
