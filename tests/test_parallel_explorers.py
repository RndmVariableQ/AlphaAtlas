"""Concurrent Explorer dialogues fill free plan slots; claimed slots and quotas are never shared."""

import json
import threading

import pytest
from test_parallel_evaluation import DistinctMiner, committed
from test_pipeline import project as project
from test_v1 import proposals
from test_v1_async import context
from test_v1_async import setup_async as setup_async

from alpha_atlas.checkpoint import read_json
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.runner import resume, run


class OverlappingExplorers(DistinctMiner):
    """The first two Explorer requests wait for each other: they can only pass if concurrent."""

    def __init__(self):
        super().__init__()
        self.barrier = threading.Barrier(2, timeout=20)
        self.waiting = 2

    def chat_once(self, messages, **kwargs):
        if "Atlas Explorer Agent" in messages[0]["content"]:
            with self.lock:
                first, self.waiting = self.waiting > 0, self.waiting - 1
            if first:
                self.barrier.wait()
        return super().chat_once(messages, **kwargs)


def config(**values):
    return V1AsyncConfig(
        **{
            "candidates_per_plan": 1,
            "parallel_plans": 6,
            "parallel_explorers": 2,
            "parallel_evaluations": 3,
            **values,
        }
    )


def test_config_bounds_and_serial_runs_keep_their_recorded_state():
    with pytest.raises(ValueError, match="parallel_explorers"):
        V1AsyncConfig(parallel_plans=2, parallel_explorers=3)
    serial = V1AsyncSearch(42, V1AsyncConfig(), models=DistinctMiner())
    assert "parallel_explorers" not in serial.configuration
    assert "explorer" in serial.dump_state() and "explorers" not in serial.dump_state()
    concurrent = V1AsyncSearch(42, config(), models=DistinctMiner())
    assert concurrent.configuration["parallel_explorers"] == 2
    assert "explorers" in concurrent.dump_state() and "explorer" not in concurrent.dump_state()


def test_second_explorer_claims_only_the_slots_and_quota_the_first_left(setup_async):
    method, current = setup_async(attempts=20, config=config(candidates_per_plan=2))
    method._start_explorer(context(current))
    # Six slots: the first Explorer opens at most four, the second the remaining two.
    quotas = [explorer["quotas"] for explorer in method.explorers.values()]
    assert list(method.explorers) == ["explorer", "explorer-2"]
    assert [len(q) for q in quotas] == [4, 2] and sum(map(sum, quotas)) == 12
    assert (
        len({e["research_plan_state"]["research_plan_index"] for e in method.explorers.values()})
        == 2
    )
    # A budget the first Explorer claims entirely leaves no slot for a second one.
    tight, tight_current = setup_async(attempts=7, config=config(candidates_per_plan=2))
    tight._start_explorer(context(tight_current))
    assert list(tight.explorers) == ["explorer"] and sum(tight.explorer["quotas"]) == 7
    saved = json.loads(json.dumps(method.dump_state()))
    restored = V1AsyncSearch(42, config(candidates_per_plan=2), models=DistinctMiner())
    restored.set_session(current)
    restored.load_state(saved)
    assert json.loads(json.dumps(restored.explorers)) == saved["explorers"]


def test_a_direction_opened_by_another_explorer_is_an_active_duplicate(setup_async):
    method, current = setup_async(config=config())
    raw = proposals(2)
    catalog = {"mean": {"id": "mean", "continent_ids": ["price_volume"]}}
    for item in raw["proposals"]:
        item["region_id"] = "mean"
    method.research_plan_state = {"active_proposals": []}
    method.groups = {
        "9": {"research_plan_state": {"exploration": {"proposal": raw["proposals"][1]}}}
    }
    with pytest.raises(ValueError, match="duplicates an active research direction"):
        method._validate_proposals(raw, catalog, set())


def test_runner_overlaps_explorers_and_resumes_with_their_saved_dialogues(project, monkeypatch):
    method = V1AsyncSearch(42, config(), models=OverlappingExplorers())
    original = method.tell

    def tell(results):
        original(results)
        if results[0].factor_trial_index == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(method, "tell", tell)
    with pytest.raises(KeyboardInterrupt):
        run(project, "ashare", "fold1", "v1_async", 42, 8, method_impl=method)
    directory = next((project / "artifacts/runs").iterdir())
    # Both first Explorer requests passed the two-party barrier, so they were in flight together.
    assert method._transport.waiting <= 0
    assert "explorers" in read_json(directory / "checkpoint.json")["method_state"]
    restored = V1AsyncSearch(42, config(), models=DistinctMiner())
    resume(project, directory, method_impl=restored)
    assert read_json(directory / "run.json")["status"] == "frozen"
    assert len(committed(directory)) == 8 and restored.completed_attempts == 8
