import json
from dataclasses import asdict

import pytest
from test_pipeline import project as project
from test_versions import version as version

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import Checkpoint, read_json
from alpha_atlas.contracts import Candidate
from alpha_atlas.evaluation import EvaluationService
from alpha_atlas.expressions import compile_factor
from alpha_atlas.incremental import assemble_arms
from alpha_atlas.library import FactorLibrary, LibraryEntry, LibraryView
from alpha_atlas.runner import resume, run
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore
from alpha_atlas.versions import load_version


def seed_version(project, version):
    path = project / "configs/versions" / f"{version}.json"
    value = read_json(path)
    candidate = Candidate("$close", name="fixed baseline", hypothesis="Existing baseline")
    identity = compile_factor(candidate.expression, set(value["fields"])).factor_id
    value["initial_library"] = {
        "source": "fixed synthetic baseline",
        "factors": [{"candidate": asdict(candidate), "factor_id": identity}],
    }
    atomic_json(path, value)
    return identity


class DuplicateSearch:
    def __init__(self):
        self.index = 0
        self.pending = None

    def set_session(self, session):
        self.session = session

    def ask(self, context, count=1):
        if self.index == 0:
            assert self.session.factor_trial_count == 0
            assert context.remaining_attempts == 2
            assert self.session.query("library_stats", {}) == {
                "members": 1,
                "version": 1,
                "initial_members": 1,
                "new_members": 0,
            }
            listed = self.session.query("library_list", {})
            assert listed["members"][0]["origin"] == "initial"
            assert self.session.query("get_context", {})["initial_library"]["members"] == 1
        self.pending = Candidate(["$close", "2 * $close"][self.index])
        return [self.pending]

    def tell(self, feedback):
        assert feedback[0].reason == ["expression_duplicate", "behavior_duplicate"][self.index]
        assert feedback[0].library_version == 1
        self.index += 1
        self.pending = None

    def dump_state(self):
        return {"index": self.index, "pending": asdict(self.pending) if self.pending else None}

    def load_state(self, state):
        self.index = state["index"]
        self.pending = Candidate(**state["pending"]) if state["pending"] else None


def seeded_run(project, version):
    return run(
        project,
        "ashare",
        "fold1",
        "random",
        42,
        attempts=2,
        version=version,
        method_impl=DuplicateSearch(),
    )


def test_initial_library_is_queryable_and_blocks_exact_and_numeric_duplicates(
    project, version, monkeypatch
):
    identity = seed_version(project, version)
    ends = []
    original = ParqMarketData.load_features

    def load(self, **kwargs):
        ends.append(kwargs["end"].isoformat())
        return original(self, **kwargs)

    monkeypatch.setattr(ParqMarketData, "load_features", load)
    directory = seeded_run(project, version)
    spec = read_json(directory / "run.json")
    assert ends == [spec["fold"]["val"]["end"]]
    assert spec["library_size"] == 1
    assert len(spec["initial_library"]["members"]) == 1
    assert spec["initial_library"]["elapsed_seconds"] >= 0
    trials = RunStore(directory).trials()
    assert len(trials) == 2
    assert trials[1]["feedback"]["max_abs_corr"] == pytest.approx(1)
    assert trials[1]["feedback"]["nearest_factor_id"] == identity
    checkpoint = read_json(directory / "checkpoint.json")
    assert checkpoint["attempts"] == checkpoint["completed"] == 2
    assert checkpoint["library_version"] == 1
    frozen = read_json(directory / "frozen/library.json")
    assert len(frozen["factors"]) == 1 and frozen["factors"][0]["initial"]
    report = (directory / "report.md").read_text()
    assert "| 初始成员 | 1 |" in report and "| 新增入库 | 0 |" in report
    assert "冻结因子库为空" not in report


@pytest.mark.parametrize("boundary", ["initial_measurement", "initial_checkpoint", "trial_commit"])
def test_seeded_resume_preserves_initial_members_and_search_budget(
    project, version, monkeypatch, boundary
):
    seed_version(project, version)
    with monkeypatch.context() as patch:
        if boundary == "initial_measurement":
            original = EvaluationService.evaluate

            def evaluate(self, candidate):
                if candidate.name == "fixed baseline":
                    raise KeyboardInterrupt("initial measurement")
                return original(self, candidate)

            patch.setattr(EvaluationService, "evaluate", evaluate)
        elif boundary == "initial_checkpoint":
            patch.setattr(
                Checkpoint,
                "save",
                lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("initial checkpoint")),
            )
        else:
            original = RunStore.record

            def record(self, *args, **kwargs):
                original(self, *args, **kwargs)
                raise KeyboardInterrupt("trial committed")

            patch.setattr(RunStore, "record", record)
        with pytest.raises(KeyboardInterrupt):
            seeded_run(project, version)
    directory = next((project / "artifacts/runs").iterdir())
    before = read_json(directory / "run.json").get("initial_library")
    committed = {p: p.read_bytes() for p in (directory / "factor_trials").glob("*.json")}
    resume(project, directory, method_impl=DuplicateSearch())
    after = read_json(directory / "run.json")
    assert after["status"] == "frozen" and after["library_size"] == 1
    if before is not None:
        assert after["initial_library"] == before
    assert all(p.read_bytes() == content for p, content in committed.items())
    assert [t["feedback"]["reason"] for t in RunStore(directory).trials()] == [
        "expression_duplicate",
        "behavior_duplicate",
    ]


def test_version_initial_library_does_not_accept_test_or_other_preloaded_metrics(project, version):
    seed_version(project, version)
    path = project / "configs/versions" / f"{version}.json"
    value = read_json(path)
    value["initial_library"]["factors"][0]["test_ic"] = 0.9
    atomic_json(path, value)
    with pytest.raises(ValueError, match="definitions, not preloaded metrics"):
        load_version(project, version)


def test_initial_members_are_not_new_admissions_in_paired_prefixes():
    def factor(identity, initial=False):
        return {"compiled": {"factor_id": identity}, "initial": initial}

    trial = {
        "factor_trial_index": 1,
        "feedback": {"accepted": True, "reason": "accepted", "report": {"expression_id": "new"}},
    }
    factors, _, arms = assemble_arms(
        [factor("base")],
        [("method", {"factors": [factor("base", True), factor("new")]}, [trial], {})],
        [1],
    )
    assert len(factors) == 2
    assert arms["method_1"]["admitted_count"] == 1
    assert arms["method_1"]["columns"] == [0, 1]


def test_mcts_diversity_includes_initial_library_without_inventing_prior_method_rewards(tmp_path):
    from test_mcts_llm import FakeModels, search_for
    from test_mcts_llm import session as empty_session

    original = empty_session(tmp_path)
    service = original._evaluator
    candidate = Candidate("$x", name="baseline")
    report = service.evaluate(candidate)
    initial = {"members": [{"candidate": asdict(candidate), "report": asdict(report)}]}
    atomic_json(tmp_path / "run.json", {"initial_library": initial})
    library = FactorLibrary(
        original._library._rules,
        snapshot_id=service.snapshot_id,
        context_id=service.context_id,
    )
    library.restore(
        LibraryView((LibraryEntry(report.expression_id, candidate, report, 1),), 1),
        lambda entry: service.observation(entry.report.observation_ref),
    )
    current = SearchSession(
        service,
        library,
        RunStore(tmp_path),
        2,
        run_spec={"initial_library": initial, "rules": library._rules},
    )
    search = search_for(current, FakeModels(["$x * $close"]))
    proposed = search.ask(current.get_context())[0]
    assert search.work["library_before"] == [report.expression_id]
    feedback = current.evaluate(proposed)
    search.tell([feedback])
    correlation = current.factor_correlations(
        [(feedback.report.expression_id, report.expression_id)]
    )
    assert search.steps[-1]["raw"]["diversity"] == pytest.approx(1 - abs(correlation[0].value))
    assert search.steps[-1]["reward"] is not None
    assert report.expression_id not in search.members
    json.dumps(search.dump_state(), allow_nan=False)
