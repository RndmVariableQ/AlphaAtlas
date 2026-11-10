"""Concurrent candidate evaluation: computation overlaps, admission stays serial in trial order."""

import itertools
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_evaluation_library import evaluator
from test_pipeline import project as project
from test_v1_async import AsyncModels

from alpha_atlas.checkpoint import read_json
from alpha_atlas.contracts import Candidate
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.runner import _check_checkpoint, resume, run
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


class DistinctMiner(AsyncModels):
    """Every Miner reply is a new expression, so trials exercise admission, not deduplication."""

    def __init__(self):
        super().__init__(proposal_count=3)
        self.windows = itertools.count(2)

    def chat_once(self, messages, **kwargs):
        text, usage = super().chat_once(messages, **kwargs)
        if "Factor Miner Agent" in messages[0]["content"]:
            raw = json.loads(text)
            with self.lock:
                window = next(self.windows)
            raw["expression"] = f"TS_MEAN({raw['expression']}, {window})"
            text = json.dumps(raw)
        return text, usage


def method(evaluations=3):
    return V1AsyncSearch(
        42,
        V1AsyncConfig(candidates_per_plan=2, parallel_plans=3, parallel_evaluations=evaluations),
        models=DistinctMiner(),
    )


@pytest.fixture
def overlap(monkeypatch):
    """Record the peak number of concurrent measure() calls; each holds briefly to overlap."""
    state = {"active": 0, "peak": 0, "lock": threading.Lock()}
    original = SearchSession.measure

    def measure(self, candidate):
        with state["lock"]:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.05)
            return original(self, candidate)
        finally:
            with state["lock"]:
                state["active"] -= 1

    monkeypatch.setattr(SearchSession, "measure", measure)
    return state


def committed(directory):
    trials = RunStore(directory).trials()
    assert [t["factor_trial_index"] for t in trials] == list(range(1, len(trials) + 1))
    # Library versions grow only with admissions, in trial order.
    versions = [t["feedback"]["library_version"] for t in trials]
    accepted = list(itertools.accumulate(int(t["feedback"]["accepted"]) for t in trials))
    assert versions == accepted
    return trials


def test_config_bounds_and_serial_runs_keep_their_recorded_configuration():
    with pytest.raises(ValueError, match="parallel_evaluations"):
        V1AsyncConfig(parallel_plans=2, parallel_evaluations=3)
    with pytest.raises(ValueError, match="parallel_evaluations"):
        V1AsyncConfig(parallel_evaluations=0)
    assert "parallel_evaluations" not in method(1).configuration
    assert method(1).evaluation_slots == 1 and "evaluating" not in method(1).dump_state()
    assert method(3).configuration["parallel_evaluations"] == 3


def test_concurrent_evaluation_overlaps_computation_and_admits_in_trial_order(project, overlap):
    directory = run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method())
    spec = read_json(directory / "run.json")
    assert spec["status"] == "frozen" and spec["method_config"]["parallel_evaluations"] == 3
    assert overlap["peak"] > 1
    trials = committed(directory)
    assert len(trials) == 6 and read_json(directory / "checkpoint.json")["pending"] is None
    accepted = {
        t["feedback"]["report"]["expression_id"] for t in trials if t["feedback"]["accepted"]
    }
    frozen = read_json(directory / "frozen/library.json")["factors"]
    assert {f["report"]["expression_id"] for f in frozen} == accepted


@pytest.mark.parametrize("stage", ["before_tell", "after_tell"])
def test_interrupt_with_candidates_in_flight_resumes_without_lost_or_repeated_trials(
    project, overlap, monkeypatch, stage
):
    # before_tell: trial 2 is committed but its feedback is not told; after_tell: it is told.
    interrupted = method()
    original = interrupted.tell

    def tell(results):
        if stage == "after_tell":
            original(results)
        if results[0].factor_trial_index == 2:
            raise KeyboardInterrupt
        if stage == "before_tell":
            original(results)

    monkeypatch.setattr(interrupted, "tell", tell)
    with pytest.raises(KeyboardInterrupt):
        run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=interrupted)
    directory = next((project / "artifacts/runs").iterdir())
    checkpoint = read_json(directory / "checkpoint.json")
    assert isinstance(checkpoint["pending"], list)
    assert [e["factor_trial_index"] for e in checkpoint["pending"]] == list(
        range(checkpoint["completed"] + 1, checkpoint["completed"] + 1 + len(checkpoint["pending"]))
    )
    # Trial 2 is committed before tell in both cases; only before_tell leaves it in flight.
    in_flight = {e["factor_trial_index"] for e in checkpoint["pending"]}
    assert len(RunStore(directory).trials()) >= 2 and (2 in in_flight) == (stage == "before_tell")
    restored = method()
    resume(project, directory, method_impl=restored)
    assert read_json(directory / "run.json")["status"] == "frozen"
    assert len(committed(directory)) == 6 and restored.completed_attempts == 6
    assert len(restored.factor_trial_records) == 6 and not restored.evaluating


def test_admission_uses_the_measured_observation_after_cache_eviction(project, monkeypatch):
    # Regression: with many candidates in flight, the bounded caches had evicted the head's
    # observation before its admission ("observation reference is not available in this run").
    from alpha_atlas.evaluation import EvaluationService

    def evicted(self, reference):
        raise ValueError("observation reference is not available in this run")

    monkeypatch.setattr(EvaluationService, "observation", evicted)
    directory = run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method())
    assert read_json(directory / "run.json")["status"] == "frozen"
    trials = committed(directory)
    assert len(trials) == 6 and any(t["feedback"]["accepted"] for t in trials)


def test_checkpoint_accepts_only_consecutive_in_flight_trials_with_a_committed_prefix():
    def trial(index, expression):
        return {
            "factor_trial_index": index,
            "feedback": {
                "candidate": {"expression": expression},
                "reason": "quality_threshold",
                "library_version": 0,
            },
        }

    def entry(index, expression):
        return {"factor_trial_index": index, "candidate": {"expression": expression}}

    trials = [trial(1, "$a"), trial(2, "$b")]
    checkpoint = {"completed": 1, "attempts": 1, "library_version": 0}
    _check_checkpoint({**checkpoint, "pending": [entry(2, "$b"), entry(3, "$c")]}, trials, 6)
    with pytest.raises(ValueError, match="index mismatch"):
        _check_checkpoint({**checkpoint, "pending": [entry(2, "$b"), entry(4, "$c")]}, trials, 6)
    with pytest.raises(ValueError, match="differs from committed"):
        _check_checkpoint({**checkpoint, "pending": [entry(2, "$x")]}, trials, 6)
    with pytest.raises(ValueError, match="beyond checkpoint"):
        _check_checkpoint({**checkpoint, "pending": []}, trials, 6)
    with pytest.raises(ValueError, match="exceeds attempt budget"):
        _check_checkpoint({**checkpoint, "pending": [entry(2, "$b"), entry(3, "$c")]}, trials, 2)


def test_threaded_evaluations_match_serial_results_and_share_one_cache():
    expressions = ["NEG($x)", "$x * $close", "TS_MEAN($x, 2)", "$close - $x", "NEG($x)"]
    serial = [evaluator().evaluate(Candidate(e)) for e in expressions]
    service = evaluator()
    with ThreadPoolExecutor(max_workers=4) as pool:
        threaded = list(pool.map(lambda e: service.evaluate(Candidate(e)), expressions))
    for a, b in zip(serial, threaded, strict=True):
        assert (a.expression_id, a.status, a.direction) == (b.expression_id, b.status, b.direction)
        assert [m.value for m in a.metrics] == pytest.approx([m.value for m in b.metrics])
        assert service.observation(b.observation_ref).frame.height > 0
