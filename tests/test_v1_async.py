import copy
import json
import threading
from concurrent.futures import wait
from dataclasses import replace

import pytest
from test_annealing import setup_method
from test_pipeline import project as project
from test_v1 import proposals

from alpha_atlas.checkpoint import read_json
from alpha_atlas.contracts import EvaluationReport, TrialFeedback
from alpha_atlas.methods.models import HTTPModels, _RetryableError
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.storage import candidate_from_dict


class AsyncModels:
    def __init__(self, handler=None):
        self.calls = []
        self.lock = threading.Lock()
        self.handler = handler
        self.explorers = 0

    def chat_once(self, messages, *, temperature=None):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        stage = (
            "explorer"
            if "Atlas Explorer Agent" in system
            else "analyst"
            if "Research Analyst Agent" in system
            else "miner"
        )
        with self.lock:
            self.calls.append((stage, copy.deepcopy(payload), copy.deepcopy(messages)))
            if stage == "explorer":
                self.explorers += 1
            batch_index = self.explorers
        if self.handler:
            self.handler(stage, payload, messages)
        if stage == "explorer":
            result = proposals()
            for item in result["proposals"]:
                item["hypothesis"] += f" mechanism {batch_index}"
                item["objective"] += f" question {batch_index}"
        elif stage == "analyst":
            result = {
                "hypothesis": payload["exploration"]["proposal"]["hypothesis"],
                "allowed_fields": payload["research_context"]["fields"],
                "measurement_plan": "Compare conditional deviations",
                "miner_instruction": "Use permitted fields",
            }
        else:
            result = {
                "expression": "$" + payload["plan"]["allowed_fields"][0],
                "name": payload["plan"]["hypothesis"],
            }
        return json.dumps(result), {"prompt_tokens": 5, "completion_tokens": 3}


@pytest.fixture
def setup_async(tmp_path):
    methods = []

    def setup(*, attempts=6, config=None, models=None):
        previous, current = setup_method(tmp_path / str(len(methods)), attempts=attempts)
        method = V1AsyncSearch(
            42, config or V1AsyncConfig(candidates_per_plan=2), models=models or AsyncModels()
        )
        method.set_session(current)
        method.set_atlas_context(previous._atlas_context)
        methods.append(method)
        return method, current

    yield setup
    for method in methods:
        method.close()


def context(current):
    return replace(current.base.get_context(), remaining_attempts=current.remaining)


def tell(method, current, accepted, index):
    current.remaining -= 1
    method.tell(
        [
            TrialFeedback(
                candidate_from_dict(method.pending),
                EvaluationReport(None, (), None, None, 0.0),
                accepted,
                "accepted" if accepted else "quality_threshold",
                trial_index=index,
            )
        ]
    )


def drain(method, current):
    while current.remaining:
        method.ask(context(current))
        tell(method, current, True, method.completed_attempts + 1)


def test_three_analysts_overlap_and_only_coordinator_writes_state(setup_async):
    analysts = threading.Barrier(3, timeout=5)
    owner = threading.get_ident()

    def handler(stage, payload, messages):
        assert threading.get_ident() != owner
        if stage == "analyst":
            analysts.wait()

    models = AsyncModels(handler)
    method, current = setup_async(models=models)
    checkpoints = []

    def save():
        assert threading.get_ident() == owner
        checkpoints.append(json.loads(json.dumps(method.dump_state())))

    method.set_checkpoint(save)
    drain(method, current)
    assert method.completed_attempts == 6
    assert len([c for c in models.calls if c[0] == "analyst"]) == 3
    assert method.usage == {
        "chat_requests": 10,
        "unknown_usage_requests": 0,
        "prompt_tokens": 50,
        "completion_tokens": 30,
    }
    assert checkpoints[-1]["groups"] == {}
    assert checkpoints[-1]["pending"] is None
    for stage, payload, messages in models.calls:
        if stage != "miner":
            assert "remaining_attempts" not in json.dumps(payload)
        else:
            feedback = [
                json.loads(m["content"])["platform_evaluation"]
                for m in messages[2:]
                if m["role"] == "user" and "platform_evaluation" in m["content"]
            ]
            for result in feedback:
                assert result["candidate"]["hypothesis"] == payload["plan"]["hypothesis"]
    assert len([s for s in method.steps if "annealing_result" in s]) == 3


def test_fast_group_refills_while_slow_analyst_is_still_running(setup_async):
    release = threading.Event()
    slow_started = threading.Event()
    refill_started = threading.Event()

    def handler(stage, payload, messages):
        if stage == "analyst" and payload["exploration"]["region_id"] == "std":
            slow_started.set()
            assert release.wait(10)
        if stage == "explorer" and payload["active_proposals"]:
            refill_started.set()

    method, current = setup_async(
        attempts=4, config=V1AsyncConfig(candidates_per_plan=1), models=AsyncModels(handler)
    )
    try:
        candidate = method.ask(context(current))[0]
        assert slow_started.is_set()
        assert candidate.region_ids != ("std",)
        tell(method, current, True, 1)
        method.ask(context(current))
        assert refill_started.wait(5)
        assert not release.is_set()
        tell(method, current, False, 2)
    finally:
        release.set()
    drain(method, current)
    assert method.round_index == 4


@pytest.mark.parametrize("attempts", [1, 2, 4, 7, 16])
def test_budget_tail_reserves_exactly_the_available_attempts(setup_async, attempts):
    models = AsyncModels()
    method, current = setup_async(attempts=attempts, models=models)

    def save():
        state = method.dump_state()
        assert len(state["groups"]) <= 3
        reserved = sum(
            g["round"]["candidate_limit"] - len(g["round"]["outcomes"])
            for g in state["groups"].values()
        ) + sum((state["explorer"] or {}).get("quotas", []))
        assert reserved <= attempts - state["completed_attempts"]

    method.set_checkpoint(save)
    drain(method, current)
    assert len([c for c in models.calls if c[0] == "miner"]) == attempts
    assert not method.groups and method.explorer is None and not method._requests
    assert sum(s["attempts"] for s in method.region_scores.values()) == attempts


@pytest.mark.parametrize("stage", ["explorer", "analyst", "miner", "pending"])
def test_saved_reply_or_pending_candidate_is_reused_after_interrupt(setup_async, stage):
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1)
    method, current = setup_async(attempts=1, config=config)
    captured = None

    def save():
        nonlocal captured
        state = method.dump_state()
        records = []
        if stage == "explorer" and state["explorer"]:
            records = state["explorer"]["round"]["responses"]["explorer"]
        for group in state["groups"].values():
            if stage == "analyst":
                records = group["round"]["responses"]["analyst"]
            elif stage == "miner":
                records = (group["work"] or {}).get("responses", [])
        if records or (stage == "pending" and state["pending"]):
            captured = json.loads(json.dumps(state))
            raise KeyboardInterrupt

    method.set_checkpoint(save)
    with pytest.raises(KeyboardInterrupt):
        method.ask(context(current))
    method.close()
    models = AsyncModels()
    restored, _ = setup_async(attempts=1, config=config, models=models)
    restored.set_session(current)
    restored.load_state(captured)
    drain(restored, current)
    order = ["explorer", "analyst", "miner", "pending"]
    assert all(order.index(call[0]) > order.index(stage) for call in models.calls)
    assert restored.completed_attempts == 1
    assert restored.usage["chat_requests"] == 3


def test_inflight_retry_is_counted_and_never_writes_from_worker(setup_async, monkeypatch):
    failed = False

    def handler(stage, payload, messages):
        nonlocal failed
        if stage == "explorer" and not failed:
            failed = True
            raise _RetryableError("simulated transient failure")

    # Advance monotonic deadlines without waiting for real retry backoff.
    ticks = iter(range(10000))
    monkeypatch.setattr("alpha_atlas.methods.v1_async.time.monotonic", lambda: next(ticks))
    method, current = setup_async(attempts=1, models=AsyncModels(handler))
    drain(method, current)
    assert method.usage["chat_requests"] == 4
    assert method.usage["unknown_usage_requests"] == 1


def test_resume_reissues_only_unsaved_stages_and_workers_cannot_change_checkpoint(setup_async):
    release = threading.Event()
    started = threading.Event()

    def handler(stage, payload, messages):
        if stage == "analyst" and payload["exploration"]["region_id"] == "std":
            started.set()
            assert release.wait(10)

    config = V1AsyncConfig(candidates_per_plan=1)
    method, current = setup_async(attempts=3, config=config, models=AsyncModels(handler))
    try:
        method.ask(context(current))
        assert started.is_set()
        state = json.loads(json.dumps(method.dump_state()))
        futures = [job["future"] for job in method._requests.values()]
        method.close()
    finally:
        release.set()
    assert not wait(futures, timeout=5).not_done
    assert json.loads(json.dumps(method.dump_state())) == state
    restored, _ = setup_async(attempts=3, config=config)
    restored.set_session(current)
    restored.load_state(state)
    drain(restored, current)
    assert not any(call[0] == "explorer" for call in restored._transport.calls)
    analysts = [
        call[1]["exploration"]["region_id"]
        for call in restored._transport.calls
        if call[0] == "analyst"
    ]
    missing = [
        group["round"]["exploration"]["region_id"]
        for group in state["groups"].values()
        if not group["round"]["responses"]["analyst"]
    ]
    assert sorted(analysts) == sorted(missing)
    assert restored.usage["unknown_usage_requests"] == state["usage"]["unknown_usage_requests"]


def test_active_proposal_duplicate_requires_repair_before_registering_regions(setup_async):
    method, _ = setup_async()
    raw = proposals()
    method.round_state = {"active_proposals": [raw["proposals"][0]]}
    _, catalog = method._catalog()
    with pytest.raises(ValueError, match="active research direction"):
        method._validate_proposals(raw, catalog, set())
    assert not method.proposed_regions


def test_chat_once_uses_one_silent_attempt_without_retry_callback(monkeypatch):
    models = HTTPModels(V1AsyncConfig(chat_model="mock"))
    attempts = []

    def fail(kind, route, body, *, on_delta):
        assert kind == "chat" and route == "/chat/completions"
        assert on_delta is None and "stream" not in body
        attempts.append(body)
        raise _RetryableError("simulated connection failure")

    monkeypatch.setattr(models, "_post_once", fail)
    monkeypatch.setattr(models, "on_retry", lambda _: pytest.fail("worker must not retry"))
    with pytest.raises(_RetryableError):
        models.chat_once([{"role": "user", "content": "mock request"}], temperature=0.3)
    assert len(attempts) == 1 and attempts[0]["temperature"] == 0.3


def test_new_region_partial_trials_are_not_counted_twice(project, monkeypatch):
    release = threading.Event()

    def handler(stage, payload, messages):
        if stage == "analyst" and "Inventory" in payload["exploration"]["direction"]:
            assert release.wait(10)

    class NewRegions(AsyncModels):
        def chat_once(self, messages, **kwargs):
            text, usage = super().chat_once(messages, **kwargs)
            raw = json.loads(text)
            for item in raw.get("proposals", []):
                del item["region_id"]
                item["objective"] = item["hypothesis"]
                item["new_region"] = {
                    "hypothesis": item["hypothesis"],
                    "continent_ids": ["price_volume"],
                }
            return json.dumps(raw), usage

    method = V1AsyncSearch(42, V1AsyncConfig(candidates_per_plan=2), models=NewRegions(handler))
    original = method.tell

    def feedback(results):
        original(results)
        release.set()

    monkeypatch.setattr(method, "tell", feedback)
    try:
        run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method)
    finally:
        release.set()
    assert len(method.proposed_regions) == 3
    assert sum(stats["attempts"] for stats in method.region_scores.values()) == 6
    assert all(method.region_scores[r]["attempts"] == 2 for r in method.proposed_regions)


def test_miner_repairs_stay_with_its_plan_and_failure_uses_one_attempt(setup_async):
    class BadMiner(AsyncModels):
        def chat_once(self, messages, **kwargs):
            text, usage = super().chat_once(messages, **kwargs)
            if "Factor Miner Agent" in messages[0]["content"]:
                text = json.dumps({"expression": "$missing_field"})
            return text, usage

    method, current = setup_async(
        attempts=3,
        config=V1AsyncConfig(candidates_per_plan=1, max_formula_repairs=1),
        models=BadMiner(),
    )
    drain(method, current)
    assert method.completed_attempts == 3
    assert all(step["candidate"]["expression"] == "" for step in method.steps)
    miners = [call for call in method._transport.calls if call[0] == "miner"]
    assert len(miners) == 6
    assert len([c for c in miners if "validation_error" in c[2][-1]["content"]]) == 3


def test_transient_failures_stop_after_four_requests_without_spending_budget(
    setup_async, monkeypatch
):
    def fail(*args):
        raise _RetryableError("simulated timeout")

    ticks = iter(range(10000))
    monkeypatch.setattr("alpha_atlas.methods.v1_async.time.monotonic", lambda: 10 * next(ticks))
    method, current = setup_async(attempts=3, models=AsyncModels(fail))
    with pytest.raises(RuntimeError, match="failed after 4 requests"):
        method.ask(context(current))
    assert current.remaining == 3 and method.completed_attempts == 0
    assert method.usage["chat_requests"] == method.usage["unknown_usage_requests"] == 4


@pytest.mark.parametrize("parallel_plans", [0, -1, True, 1.5])
def test_invalid_concurrency_config_is_rejected(parallel_plans):
    with pytest.raises(ValueError, match="parallel_plans"):
        V1AsyncConfig(parallel_plans=parallel_plans)


def test_native_runner_freezes_async_config(project, monkeypatch):
    models = AsyncModels()
    monkeypatch.setattr("alpha_atlas.methods.annealing.HTTPModels", lambda config: models)
    directory = run(project, "ashare", "fold1", "v1_async", 42, attempts=4)
    spec = read_json(directory / "run.json")
    assert spec["implementation"] == "atlas_explorer_v1_async"
    assert spec["method_config"]["max_dialogue_rounds"] == 40
    assert spec["status"] == "frozen"
    state = read_json(directory / "checkpoint.json")["method_state"]
    assert state["completed_attempts"] == 4 and state["groups"] == {}
    path = project / "configs/v1_async.toml"
    path.write_text(path.read_text().replace("parallel_plans = 3", "parallel_plans = 2"))
    with pytest.raises(ValueError, match="v1_async configuration changed"):
        evaluate_frozen(project, directory)


def test_resume_committed_trial_routes_feedback_to_its_group(project, monkeypatch):
    config = V1AsyncConfig(candidates_per_plan=2)
    models = AsyncModels()
    method = V1AsyncSearch(42, config, models=models)
    original = method.tell

    def interrupt(results):
        if results[0].trial_index == 2:
            raise KeyboardInterrupt
        original(results)

    monkeypatch.setattr(method, "tell", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method)
    directory = next((project / "artifacts/runs").iterdir())
    saved = read_json(directory / "checkpoint.json")
    assert saved["pending"]["trial_index"] == 2
    restored = V1AsyncSearch(42, config, models=AsyncModels())
    resume(project, directory, method_impl=restored)
    assert restored.completed_attempts == 6
    assert [step["trial_index"] for step in restored.steps] == list(range(1, 7))
    assert len({step["round_index"] for step in restored.steps}) == 3
    assert not restored.groups
