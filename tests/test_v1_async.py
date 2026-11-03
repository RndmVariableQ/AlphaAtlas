import copy
import io
import json
import logging
import threading
from concurrent.futures import wait
from dataclasses import replace

import pytest
from test_annealing import commit_records, setup_method
from test_checkpoint import legacy_field_names
from test_pipeline import project as project
from test_v1 import V1Models, memory_steps, proposals

from alpha_atlas.checkpoint import config_fingerprint, read_json
from alpha_atlas.contracts import EvaluationReport, TrialFeedback
from alpha_atlas.methods.models import HTTPModels, _RetryableError
from alpha_atlas.methods.v1 import ExplorerUnresolved
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.storage import RunStore, candidate_from_dict


class AsyncModels:
    def __init__(self, handler=None, *, proposal_count=3):
        self.calls = []
        self.lock = threading.Lock()
        self.handler = handler
        self.explorers = 0
        self.proposal_count = proposal_count

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
            result = proposals(self.proposal_count)
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


class FencedModels(AsyncModels):
    def chat_once(self, messages, **kwargs):
        text, usage = super().chat_once(messages, **kwargs)
        raw = json.loads(text)
        if "Research Analyst Agent" in messages[0]["content"]:
            raw["miner_instruction"] = {
                "instructions": raw["miner_instruction"],
                "rules": ["字段边界"],
            }
        return "```json\n" + json.dumps(raw, ensure_ascii=False) + "\n```", usage


class WaitingModels(AsyncModels):
    def chat_once(self, messages, **kwargs):
        text, usage = super().chat_once(messages, **kwargs)
        if "Atlas Explorer Agent" in messages[0]["content"]:
            payload = json.loads(messages[1]["content"])
            if payload["active_proposals"]:
                return json.dumps(
                    {
                        "summary": "Current evidence is insufficient",
                        "unresolved": "Need active results",
                    }
                ), usage
        return text, usage


class BlockedAsyncModels(AsyncModels):
    def __init__(self, stage="analyst", *, all_initial=False):
        super().__init__(proposal_count=2)
        self.stage, self.all_initial = stage, all_initial

    def chat_once(self, messages, **kwargs):
        text, usage = super().chat_once(messages, **kwargs)
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if self.stage == "analyst" and "Research Analyst Agent" in system:
            question = payload["exploration"]["direction"]
        elif self.stage == "miner" and "Factor Miner Agent" in system:
            question = payload["plan"]["direction"]
        else:
            return text, usage
        if question.endswith("question 1") and (self.all_initial or "pressure" in question):
            return '{"blocked":"Missing adj_close_p1 for calendar-spread test"}', usage
        return text, usage


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
    feedback = TrialFeedback(
        candidate_from_dict(method.pending),
        EvaluationReport(None, (), None, None, 0.0),
        accepted,
        "accepted" if accepted else "quality_threshold",
        factor_trial_index=index,
    )
    current.base._store.record(index, feedback, 0, 0, 0, feedback.library_version)
    method.tell([feedback])


def drain(method, current):
    while current.remaining:
        method.ask(context(current))
        tell(method, current, True, method.completed_attempts + 1)


def test_async_logs_each_request_once_without_reporting_waits_as_failures(setup_async, caplog):
    models = AsyncModels(proposal_count=2)
    method, current = setup_async(
        attempts=2,
        models=models,
        config=V1AsyncConfig(parallel_plans=2, candidates_per_plan=1),
    )
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        drain(method, current)
    messages = [record.getMessage() for record in caplog.records]
    for stage in ("explorer", "analyst", "miner"):
        action = {"explorer": "探索方向", "analyst": "制定计划", "miner": "生成因子"}[stage]
        requests = [message for message in messages if f"· {stage.title()}] {action} ·" in message]
        assert len(requests) == sum(call[0] == stage for call in models.calls)
        assert all(message.startswith("  [计划 #") for message in requests)
    assert sum("· Explorer] 方向已提交\n" in message for message in messages) == 2
    assert sum("· Analyst] 计划已提交\n" in message for message in messages) == 2
    assert "模型请求失败" not in caplog.text


@pytest.mark.parametrize("stage", ["analyst", "miner"])
def test_blocked_group_releases_quota_and_other_groups_continue(setup_async, stage):
    models = BlockedAsyncModels(stage)
    method, current = setup_async(attempts=6, models=models)
    drain(method, current)
    assert method.completed_attempts == 6 and len(method.factor_trial_records) == 6
    assert len(method.blocked_plans) == 1
    blocked = method.blocked_plans[0]
    assert not any(
        row["research_plan_index"] == blocked["research_plan_index"]
        for row in method.factor_trial_records
    )
    assert "adj_close_p1" in blocked["plan"]["blocked"]
    assert method.pending is None and method.pending_group is None and not method.groups
    assert any(call[1]["atlas"]["blocked_plans"] for call in models.calls if call[0] == "explorer")
    if stage == "analyst":
        assert not any(
            "pressure" in call[1]["plan"]["direction"]
            and call[1]["plan"]["direction"].endswith("question 1")
            for call in models.calls
            if call[0] == "miner"
        )


def test_all_initial_plans_block_then_choose_again_without_fake_trials(setup_async):
    method, current = setup_async(
        attempts=4,
        config=V1AsyncConfig(parallel_plans=2, candidates_per_plan=2),
        models=BlockedAsyncModels(all_initial=True),
    )
    drain(method, current)
    assert len(method.blocked_plans) == 2
    assert len(current.base._store.trials()) == method.completed_attempts == 4
    assert all("question 1" not in row["direction"] for row in method.factor_trial_records)


def test_async_recovery_after_block_archived_releases_group_once(setup_async):
    models = BlockedAsyncModels(all_initial=True)
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=2)
    method, current = setup_async(attempts=2, config=config, models=models)
    saved = None

    def interrupt():
        nonlocal saved
        if method.blocked_plans:
            saved = json.loads(json.dumps(method.dump_state()))
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    with pytest.raises(KeyboardInterrupt):
        method.ask(context(current))
    assert saved["groups"] == {} and saved["pending_group"] is None
    restored, _ = setup_async(attempts=2, config=config, models=models)
    restored.set_session(current)
    restored.load_state(saved)
    drain(restored, current)
    assert len(restored.blocked_plans) == 1 and restored.completed_attempts == 2
    assert all("question 1" not in row["direction"] for row in restored.factor_trial_records)


def test_partial_block_keeps_its_trial_and_finishes_the_correct_interleaved_plan(setup_async):
    class PartialBlock(AsyncModels):
        def chat_once(self, messages, **kwargs):
            text, usage = super().chat_once(messages, **kwargs)
            if "Factor Miner Agent" in messages[0]["content"] and len(messages) > 2:
                question = json.loads(messages[1]["content"])["plan"]["direction"]
                if "pressure" in question and question.endswith("question 1"):
                    return '{"blocked":"Planned horizon control unavailable"}', usage
            return text, usage

    method, current = setup_async(
        attempts=6,
        config=V1AsyncConfig(parallel_plans=2, candidates_per_plan=2),
        models=PartialBlock(),
    )
    drain(method, current)
    assert len(method.blocked_plans) == 1
    index = method.blocked_plans[0]["research_plan_index"]
    rows = [r for r in method.factor_trial_records if r["research_plan_index"] == index]
    assert len(rows) == 1 and "explorer_proposals" in rows[0]
    result = method._plan_results({index})[0]
    assert result["completed"] and result["blocked"] == "Planned horizon control unavailable"
    assert (
        len(result["trials"]) == 1
        and result["trials"][0]["ref"] == f"trial:{rows[0]['factor_trial_index']}"
    )


def test_deferred_explorer_after_all_blocked_has_no_trial_to_attach_to(setup_async):
    method, _ = setup_async()
    method.blocked_plans = [
        {"research_plan_index": 1, "plan": {"blocked": "Missing far leg"}, "completed_attempts": 0}
    ]
    records = [{"unresolved": "Await active results"}] * 2
    method.explorer = {
        "research_plan_state": {
            "research_plan_index": 2,
            "active_proposals": [{}],
            "responses": {"explorer": records},
        }
    }
    method._retire_explorer()
    assert method.explorer is None and method.factor_trial_records == []
    assert method.blocked_plans[0]["deferred_explorer"]["responses"] == records


def test_async_block_limit_applies_with_other_groups_still_active(setup_async):
    class AlwaysBlocked(AsyncModels):
        def chat_once(self, messages, **kwargs):
            text, usage = super().chat_once(messages, **kwargs)
            if "Research Analyst Agent" in messages[0]["content"]:
                return '{"blocked":"Missing evaluation capability"}', usage
            return text, usage

    config = V1AsyncConfig(parallel_plans=2, max_dialogue_rounds=2)
    method, current = setup_async(config=config, models=AlwaysBlocked())
    with pytest.raises(ValueError, match="blocked plan limit"):
        method.ask(context(current))
    assert method.completed_attempts == 0 and len(method.blocked_plans) >= 2
    restored, _ = setup_async(config=config, models=AlwaysBlocked())
    restored.set_session(current)
    restored.load_state(json.loads(json.dumps(method.dump_state())))
    active = restored._new_round(context(current), 999)
    active["candidate_limit"] = 2
    restored.groups["999"] = {"research_plan_state": active, "work": None}
    with pytest.raises(ValueError, match="blocked plan limit"):
        restored.ask(context(current))
    assert restored._transport.calls == []


def test_fenced_roles_and_structured_plan_need_no_repairs_or_extra_attempts(setup_async):
    method, current = setup_async(
        attempts=2,
        config=V1AsyncConfig(parallel_plans=1, candidates_per_plan=2, max_formula_repairs=0),
        models=FencedModels(),
    )
    first = method.ask(context(current))[0]
    group = next(iter(method.groups.values()))
    records = group["research_plan_state"]["responses"]
    assert records["explorer"][0]["text"].startswith("```json")
    assert records["analyst"][0]["text"].startswith("```json")
    plan = group["research_plan_state"]["plan"]
    expression = "$" + plan["allowed_fields"][0]
    assert first.expression == expression
    assert json.loads(plan["miner_instruction"])["rules"] == ["字段边界"]
    messages = next(c[2] for c in method._transport.calls if c[0] == "miner")
    assert json.loads(messages[1]["content"])["plan"] == plan
    assert current.remaining == 2 and current.base.attempts_used == 0
    tell(method, current, True, 1)
    drain(method, current)
    assert method.usage["chat_requests"] == 4
    assert method.completed_attempts == 2
    assert all(
        current.get_trial(step["factor_trial_index"])["candidate"]["expression"] == expression
        for step in method.factor_trial_records
    )


def test_concurrent_analysts_share_one_plan_without_unrelated_batch_evidence(setup_async):
    batch = proposals()
    for proposal, ref in zip(
        batch["proposals"], ("trial:194", "trial:196", "trial:1000"), strict=True
    ):
        proposal["references"] = [ref]
    models = V1Models([batch, batch])
    method, current = setup_async(
        attempts=2,
        config=V1AsyncConfig(parallel_plans=2, candidates_per_plan=1),
        models=models,
    )
    method.context = current.query("get_context", {})
    method.factor_trial_records = commit_records(
        current, memory_steps(method.context, control_visible=True)
    )
    method.next_research_plan_index = 102
    before = copy.deepcopy(method.factor_trial_records)
    drain(method, current)
    analysts = [
        payload for system, payload, _ in models.calls if "Research Analyst Agent" in system
    ]
    assert len(analysts) == 2
    for payload in analysts:
        exploration = payload["exploration"]
        (source,) = exploration["sources"]
        assert source["ref"] == exploration["proposal"]["references"][0]
        assert source["value"]["research_plan_index"] == 39
        assert [row["ref"] for row in source["value"]["trials"]] == [
            f"trial:{i}" for i in (190, 192, 194, 196, 197)
        ]
    assert {payload["exploration"]["sources"][0]["ref"] for payload in analysts} == {
        "trial:194",
        "trial:196",
    }
    assert len(method.factor_trial_records[-1]["explorer_proposals"]["sources"]) == 2
    assert method.factor_trial_records[: len(before)] == before
    assert method.completed_attempts == 2 and current.remaining_budget() == 0
    assert sum("Atlas Explorer Agent" in call[0] for call in models.calls) == 2


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
    assert len([s for s in method.factor_trial_records if "annealing_result" in s]) == 3


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
    assert method.completed_research_plan_count == 4


@pytest.mark.parametrize("attempts", [1, 2, 4, 7, 16])
def test_budget_tail_reserves_exactly_the_available_attempts(setup_async, attempts):
    models = AsyncModels()
    method, current = setup_async(attempts=attempts, models=models)

    def save():
        state = method.dump_state()
        assert len(state["groups"]) <= 3
        reserved = sum(
            g["research_plan_state"]["candidate_limit"] - len(g["research_plan_state"]["outcomes"])
            for g in state["groups"].values()
        ) + sum((state["explorer"] or {}).get("quotas", []))
        assert reserved <= attempts - state["completed_attempts"]

    method.set_checkpoint(save)
    drain(method, current)
    assert len([c for c in models.calls if c[0] == "miner"]) == attempts
    assert not method.groups and method.explorer is None and not method._requests
    assert sum(s["attempts"] for s in method.region_scores.values()) == attempts


@pytest.mark.parametrize("stage", ["explorer", "analyst", "miner", "pending"])
@pytest.mark.parametrize("fenced", [False, True])
def test_saved_reply_or_pending_candidate_is_reused_after_interrupt(setup_async, stage, fenced):
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1)
    method, current = setup_async(
        attempts=1, config=config, models=FencedModels() if fenced else AsyncModels()
    )
    captured = None

    def save():
        nonlocal captured
        state = method.dump_state()
        records = []
        if stage == "explorer" and state["explorer"]:
            records = state["explorer"]["research_plan_state"]["responses"]["explorer"]
        for group in state["groups"].values():
            if stage == "analyst":
                records = group["research_plan_state"]["responses"]["analyst"]
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
        group["research_plan_state"]["exploration"]["region_id"]
        for group in state["groups"].values()
        if not group["research_plan_state"]["responses"]["analyst"]
    ]
    assert sorted(analysts) == sorted(missing)
    assert restored.usage["unknown_usage_requests"] == state["usage"]["unknown_usage_requests"]


def test_active_proposal_duplicate_requires_repair_before_registering_regions(setup_async):
    method, _ = setup_async()
    raw = proposals()
    method.research_plan_state = {"active_proposals": [raw["proposals"][0]]}
    _, catalog = method._catalog()
    with pytest.raises(ValueError, match="active research direction"):
        method._validate_proposals(raw, catalog, set())
    assert not method.proposed_regions


@pytest.mark.parametrize("count", [2, 3, 4])
def test_async_batch_creates_only_validated_groups(setup_async, count):
    method, current = setup_async(attempts=12, config=V1AsyncConfig(parallel_plans=4))
    method.context = current.query("get_context", {})
    method._start_explorer(context(current))
    raw = proposals(count)
    method.explorer["research_plan_state"]["responses"]["explorer"] = [{"text": json.dumps(raw)}]
    before = method.rng.getstate()
    method._advance_explorer(context(current))
    assert method.explorer is None
    assert len(method.groups) == count
    assert not method._requests
    expected = ["std", "mean", "delta", "return"][:count]
    assert [
        g["research_plan_state"]["exploration"]["region_id"] for g in method.groups.values()
    ] == expected
    assert method.rng.getstate() == before
    assert current.remaining_budget() == 12


def test_four_direction_batch_resumes_pending_fourth_group(setup_async):
    config = V1AsyncConfig(parallel_plans=4, candidates_per_plan=1)
    method, current = setup_async(attempts=4, config=config, models=AsyncModels(proposal_count=4))
    for index in range(1, 4):
        method.ask(context(current))
        tell(method, current, False, index)
    candidate = method.ask(context(current))[0]
    assert method.pending_group == "4"
    saved = json.loads(json.dumps(method.dump_state()))
    method.close()
    restored, _ = setup_async(attempts=4, config=config, models=AsyncModels(proposal_count=4))
    restored.set_session(current)
    restored.load_state(saved)
    assert candidate_from_dict(restored.pending) == candidate
    tell(restored, current, False, 4)
    assert restored.completed_attempts == 4 and current.remaining_budget() == 0
    assert not restored.groups and restored.pending_group is None
    assert restored.factor_trial_records[-1]["research_plan_index"] == 4
    assert restored.factor_trial_records[-1]["region_id"] == "return"
    assert not restored._transport.calls


@pytest.mark.parametrize("policy", ["random", "boltzmann"])
def test_async_selection_is_saved_with_groups_and_not_repeated_on_resume(setup_async, policy):
    config = V1AsyncConfig(selection_policy=policy, parallel_plans=2, candidates_per_plan=1)
    method, current = setup_async(attempts=2, config=config)
    method.context = current.query("get_context", {})
    method._start_explorer(context(current))
    state = method.explorer["research_plan_state"]
    state["responses"]["explorer"] = [{"text": json.dumps(proposals())}]
    method._advance_explorer(context(current))
    selected = {
        int(key): g["research_plan_state"]["explorer_proposals"]["selected"]
        for key, g in method.groups.items()
    }
    assert len(set(selected.values())) == 2
    saved = json.loads(json.dumps(method.dump_state()))
    method.close()
    restored, _ = setup_async(attempts=2, config=config)
    restored.set_session(current)
    restored.load_state(saved)

    def unexpected(*args):
        raise AssertionError("existing groups must retain their selections")

    restored._select_proposals = unexpected
    before = restored.rng.getstate()
    drain(restored, current)
    assert restored.rng.getstate() == before
    assert {
        r["research_plan_index"]: r["explorer_proposals"]["selected"]
        for r in restored.factor_trial_records
    } == selected
    assert restored.completed_research_plan_count == 2
    assert all(p["completed"] for p in restored._plan_results())
    assert not restored.groups
    assert not any(call[0] == "explorer" for call in restored._transport.calls)


def test_saved_unresolved_with_one_free_slot_selects_one_from_two_directions(setup_async):
    config = V1AsyncConfig(candidates_per_plan=2)
    method, current = setup_async(attempts=12, config=config)
    method.context = current.query("get_context", {})
    method._start_explorer(context(current))
    batch = proposals()
    batch["proposals"] = batch["proposals"][:2]
    method.explorer["research_plan_state"]["responses"]["explorer"] = [{"text": json.dumps(batch)}]
    method._advance_explorer(context(current))
    method._start_explorer(context(current))
    assert len(method.explorer["quotas"]) == 1
    unresolved = {"summary": "Two groups active", "unresolved": "Cannot invent a third direction"}
    record = {"text": json.dumps(unresolved), "processed": True, **unresolved}
    method.explorer["research_plan_state"]["responses"]["explorer"] = [record]
    saved = copy.deepcopy(method.dump_state())
    complete = proposals(4)
    complete["proposals"] = complete["proposals"][2:]
    models = V1Models([complete])
    restored, target = setup_async(attempts=12, config=config, models=models)
    restored.load_state(saved)
    for _ in range(5):
        restored._advance_explorer(context(target))
        if restored.explorer is None:
            break
        wait([job["future"] for job in restored._requests.values()], timeout=5)
    assert restored.explorer is None
    assert len(restored.groups) == 3
    for key, group in saved["groups"].items():
        assert restored.groups[key] == group
    calls = [call for call in models.calls if "Atlas Explorer Agent" in call[0]]
    assert len(calls) == 1
    assert calls[0][1]["available_slots"] == 1
    assert len(calls[0][1]["active_proposals"]) == 2
    assert "two to four" in calls[0][0]
    assert restored.usage["chat_requests"] == saved["usage"]["chat_requests"] + 1
    assert restored.completed_attempts == saved["completed_attempts"]
    assert target.remaining_budget() == 12


def seed_two_active_plans(method, current):
    method.context = current.query("get_context", {})
    method._start_explorer(context(current))
    batch = proposals()
    batch["proposals"] = batch["proposals"][:2]
    method.explorer["research_plan_state"]["responses"]["explorer"] = [{"text": json.dumps(batch)}]
    method._advance_explorer(context(current))
    assert len(method.groups) == 2
    method._start_explorer(context(current))
    assert method.explorer["quotas"] == [2]


@pytest.mark.parametrize("origin", ["live", "saved", "resumed"])
@pytest.mark.parametrize("limit", [1, 40])
def test_unresolved_refill_drains_active_plans_then_restarts_with_new_evidence(
    setup_async, origin, limit
):
    config = V1AsyncConfig(candidates_per_plan=2, max_dialogue_rounds=limit)
    models = WaitingModels()
    method, current = setup_async(attempts=6, config=config, models=models)
    seed_two_active_plans(method, current)
    records = method.explorer["research_plan_state"]["responses"]["explorer"]
    if origin == "live":
        for _ in range(8):
            method._advance_explorer(context(current))
            if len(records) == min(2, limit) and records[-1].get("processed"):
                break
            wait([job["future"] for job in method._requests.values()], timeout=5)
        assert len(records) == min(2, limit) and records[-1].get("processed")
    else:
        raw = {"summary": "Need evidence", "unresolved": "Wait for active plans"}
        records.extend(
            {"text": json.dumps(raw), "processed": True, **raw} for _ in range(min(2, limit))
        )
    saved = json.loads(json.dumps(method.dump_state()))
    if origin == "resumed":
        method.close()
        models = WaitingModels()
        method, _ = setup_async(attempts=6, config=config, models=models)
        method.set_session(current)
        method.load_state(saved)
    # Re-reading a terminal reply neither repeats model requests nor changes the checkpoint.
    before_calls = len(models.calls)
    for _ in range(3):
        method._advance_explorer(context(current))
    assert len(models.calls) == before_calls
    assert json.loads(json.dumps(method.dump_state())) == saved
    for index in range(1, 5):
        method.ask(context(current))
        assert not any(call[0] == "explorer" for call in models.calls[before_calls:])
        tell(method, current, False, index)
    assert not method.groups and method.explorer is None
    deferred = method.factor_trial_records[-1]["deferred_explorer"]
    assert deferred == {
        "research_plan_index": saved["explorer"]["research_plan_state"]["research_plan_index"],
        "responses": saved["explorer"]["research_plan_state"]["responses"]["explorer"],
    }
    assert current.remaining == 2
    drain(method, current)
    fresh = [call[1] for call in models.calls[before_calls:] if call[0] == "explorer"]
    assert len(fresh) == 1 and fresh[0]["active_proposals"] == []
    assert sum(region["statistics"]["attempts"] for region in fresh[0]["atlas"]["regions"]) == 4
    assert method.completed_attempts == 6 and current.remaining == 0
    assert not method.groups and method.explorer is None and not method._requests
    assert (
        method.usage["chat_requests"]
        == len(models.calls) + saved["usage"]["chat_requests"] - before_calls
    )


def test_late_unresolved_reply_uses_new_results_when_snapshot_plans_already_finished(setup_async):
    release = threading.Event()
    started = threading.Event()

    def handler(stage, payload, messages):
        if stage == "explorer" and payload["active_proposals"]:
            started.set()
            assert release.wait(10)

    models = WaitingModels(handler)
    method, current = setup_async(
        attempts=6, config=V1AsyncConfig(candidates_per_plan=2), models=models
    )
    seed_two_active_plans(method, current)
    method._advance_explorer(context(current))
    assert started.wait(5)
    try:
        for index in range(1, 5):
            method.ask(context(current))
            tell(method, current, False, index)
        assert not method.groups and method.explorer is not None
        release.set()
        drain(method, current)
    finally:
        release.set()
    assert method.completed_attempts == 6
    assert len(method.factor_trial_records[3]["deferred_explorer"]["responses"]) == 2
    fresh = [call[1] for call in models.calls if call[0] == "explorer"][-1]
    assert fresh["active_proposals"] == []
    assert sum(region["statistics"]["attempts"] for region in fresh["atlas"]["regions"]) == 4


def test_unresolved_without_active_plans_stops_without_retries_or_fake_trials(setup_async):
    method, current = setup_async(attempts=3)
    method.context = current.query("get_context", {})
    method._start_explorer(context(current))
    snapshot, _ = method._catalog()
    method.explorer["research_plan_state"]["explorer_input"] = {
        "atlas": method._explorer_atlas(snapshot)
    }
    raw = {"summary": "No available test", "unresolved": "No justified direction"}
    method.explorer["research_plan_state"]["responses"]["explorer"] = [
        {"text": json.dumps(raw), "processed": True, **raw} for _ in range(2)
    ]
    saved = copy.deepcopy(method.dump_state())
    with pytest.raises(ExplorerUnresolved, match="No justified direction"):
        method.ask(context(current))
    assert method.dump_state() == saved
    assert (
        not method._transport.calls and not method.factor_trial_records and current.remaining == 3
    )


@pytest.mark.parametrize("origin", ["live", "saved", "resumed"])
@pytest.mark.parametrize("last_kind", ["invalid_json", "query"])
def test_exhausted_refill_drains_plans_then_uses_new_evidence(setup_async, origin, last_kind):
    config = V1AsyncConfig(candidates_per_plan=2)
    method, current = setup_async(attempts=6, config=config)
    seed_two_active_plans(method, current)
    records = [
        {"text": "{", "processed": True, "error": "Invalid JSON"}
        for _ in range(config.max_dialogue_rounds - 1)
    ]
    last = (
        {"text": "{"}
        if last_kind == "invalid_json"
        else {"text": json.dumps({"tool": "library_stats", "arguments": {}})}
    )
    if origin != "live":
        last.update(
            {"processed": True, "error": "Invalid JSON"}
            if last_kind == "invalid_json"
            else {"processed": True, "query": {"tool": "library_stats"}, "result": {}}
        )
    records.append(last)
    method.explorer["research_plan_state"]["responses"]["explorer"] = records
    if origin == "resumed":
        saved = json.loads(json.dumps(method.dump_state()))
        method.close()
        method, _ = setup_async(attempts=6, config=config)
        method.set_session(current)
        method.load_state(saved)
    method._advance_explorer(context(current))
    saved = copy.deepcopy(method.dump_state())
    for _ in range(3):
        method._advance_explorer(context(current))
    assert method.dump_state() == saved
    assert len(method.groups) == 2 and not method._transport.calls and current.remaining == 6
    for index in range(1, 5):
        method.ask(context(current))
        assert not any(call[0] == "explorer" for call in method._transport.calls)
        tell(method, current, False, index)
    assert method.explorer is None and not method.groups
    assert method.factor_trial_records[-1]["deferred_explorer"] == {
        "research_plan_index": saved["explorer"]["research_plan_state"]["research_plan_index"],
        "responses": saved["explorer"]["research_plan_state"]["responses"]["explorer"],
    }
    assert current.remaining == 2
    drain(method, current)
    fresh = [call[1] for call in method._transport.calls if call[0] == "explorer"]
    assert len(fresh) == 1 and fresh[0]["active_proposals"] == []
    assert sum(r["statistics"]["attempts"] for r in fresh[0]["atlas"]["regions"]) == 4
    assert method.completed_attempts == 6 and current.remaining == 0


def test_exhausted_explorer_without_active_snapshot_still_fails(setup_async):
    method, current = setup_async(attempts=3)
    method.context = current.query("get_context", {})
    method._start_explorer(context(current))
    method.explorer["research_plan_state"]["responses"]["explorer"] = [
        {"text": "{", "processed": True, "error": "Invalid JSON"}
        for _ in range(method.config.max_dialogue_rounds)
    ]
    with pytest.raises(ValueError, match="dialogue limit exhausted"):
        method._advance_explorer(context(current))
    assert not method.groups and not method._transport.calls and current.remaining == 3


@pytest.mark.parametrize("completed", [0, 1])
def test_runner_freezes_results_when_breadth_has_no_executable_direction(project, completed):
    class NoMoreDirections(AsyncModels):
        def chat_once(self, messages, **kwargs):
            text, usage = super().chat_once(messages, **kwargs)
            if "Atlas Explorer Agent" in messages[0]["content"] and self.explorers > completed:
                return json.dumps(
                    {
                        "summary": "Reviewed the whole map with available fields",
                        "unresolved": "No executable direction with current capabilities",
                    }
                ), usage
            return text, usage

    (project / "configs/v1_async.toml").write_text(
        "parallel_plans = 1\ncandidates_per_plan = 1\nmax_dialogue_rounds = 4\n"
    )
    method = V1AsyncSearch(
        42,
        V1AsyncConfig(parallel_plans=1, candidates_per_plan=1, max_dialogue_rounds=4),
        models=NoMoreDirections(),
    )
    directory = run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method)
    spec = read_json(directory / "run.json")
    checkpoint = read_json(directory / "checkpoint.json")
    assert spec["status"] == "frozen" and spec["attempts"] == 6
    assert "No executable direction" in spec["search_stop_reason"]
    assert spec["config_fingerprint"] == config_fingerprint(spec)
    assert checkpoint["completed"] == checkpoint["attempts"] == completed
    assert checkpoint["pending"] is None and len(RunStore(directory).trials()) == completed
    assert not (directory / "failures.json").exists()
    report = (directory / "report.md").read_text()
    assert "提前结束原因" in report and "No executable direction" in report
    assert len(evaluate_frozen(project, directory)["results"]) == spec["library_size"]


@pytest.mark.parametrize("failure", ["transport", "format"])
def test_runner_does_not_hide_real_explorer_failures(project, failure):
    class BrokenExplorer(AsyncModels):
        def chat_once(self, messages, **kwargs):
            text, usage = super().chat_once(messages, **kwargs)
            if "Atlas Explorer Agent" in messages[0]["content"]:
                if failure == "transport":
                    raise RuntimeError("simulated transport failure")
                return '{"bad":"reply"}', usage
            return text, usage

    method = V1AsyncSearch(
        42, V1AsyncConfig(parallel_plans=1, max_dialogue_rounds=2), models=BrokenExplorer()
    )
    with pytest.raises((RuntimeError, ValueError)):
        run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method)
    directory = next((project / "artifacts/runs").iterdir())
    spec = read_json(directory / "run.json")
    assert spec["status"] == "failed" and "search_stop_reason" not in spec
    assert not (directory / "frozen/library.json").exists()
    assert read_json(directory / "failures.json")[-1]["stage"] == "ask"


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
                text = '```json\n{"expression":"$missing_field"}\n```'
            return text, usage

    method, current = setup_async(
        attempts=3,
        config=V1AsyncConfig(candidates_per_plan=1, max_formula_repairs=1),
        models=BadMiner(),
    )
    drain(method, current)
    assert method.completed_attempts == 3
    assert all(
        current.get_trial(step["factor_trial_index"])["candidate"]["expression"] == ""
        for step in method.factor_trial_records
    )
    assert all(
        "last validation error: invalid feature field: missing_field"
        in current.get_trial(step["factor_trial_index"])["candidate"]["hypothesis"]
        for step in method.factor_trial_records
    )
    miners = [call for call in method._transport.calls if call[0] == "miner"]
    assert len(miners) == 6
    assert len([c for c in miners if "validation_error" in c[2][-1]["content"]]) == 3


@pytest.mark.parametrize(
    "backoff,retry_after,expected", [(1, 0, [1, 2, 4]), (30, 75, [75, 75, 120])]
)
def test_transient_failures_stop_after_four_requests_without_spending_budget(
    setup_async, monkeypatch, backoff, retry_after, expected
):
    def fail(*args):
        raise _RetryableError("simulated failure", backoff=backoff, retry_after=retry_after)

    waits = []
    ticks = iter(range(10000))
    monkeypatch.setattr("alpha_atlas.methods.v1_async.time.monotonic", lambda: 1000 * next(ticks))
    monkeypatch.setattr(
        "alpha_atlas.methods.v1_async.terminal_progress",
        lambda title, **values: waits.append(values["等待秒"]) if "等待秒" in values else None,
    )
    method, current = setup_async(attempts=3, models=AsyncModels(fail))
    with pytest.raises(RuntimeError, match="failed after 4 requests"):
        method.ask(context(current))
    assert current.remaining == 3 and method.completed_attempts == 0
    assert method.usage["chat_requests"] == method.usage["unknown_usage_requests"] == 4
    assert waits == expected


def test_empty_http_200_response_retries_in_coordinator_without_spending_budget(
    setup_async, monkeypatch
):
    calls = []

    def request(*args, **kwargs):
        calls.append(threading.get_ident())
        return io.BytesIO(b'{"choices":[{"message":{"content":null},"finish_reason":"stop"}]}')

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    ticks = iter(range(10000))
    monkeypatch.setattr("alpha_atlas.methods.v1_async.time.monotonic", lambda: 10 * next(ticks))
    config = V1AsyncConfig(chat_model="synthetic")
    method, current = setup_async(attempts=3, config=config, models=HTTPModels(config))
    with pytest.raises(RuntimeError, match="empty message content; failed after 4 requests"):
        method.ask(context(current))
    assert len(calls) == 4 and threading.get_ident() not in calls
    assert method.usage["chat_requests"] == method.usage["unknown_usage_requests"] == 4
    assert current.remaining == 3 and method.completed_attempts == 0


@pytest.mark.parametrize("parallel_plans", [0, -1, True, 1.5])
def test_invalid_concurrency_config_is_rejected(parallel_plans):
    with pytest.raises(ValueError, match="parallel_plans"):
        V1AsyncConfig(parallel_plans=parallel_plans)


def test_native_runner_freezes_async_config(project, monkeypatch, caplog):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr(
        logging.getLogger("alpha_atlas.progress"), "handlers", [logging.StreamHandler(terminal)]
    )
    models = AsyncModels()
    monkeypatch.setattr("alpha_atlas.methods.annealing.HTTPModels", lambda config: models)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        directory = run(project, "ashare", "fold1", "v1_async", 42, attempts=4)
    spec = read_json(directory / "run.json")
    assert spec["implementation"] == "atlas_explorer_v1_async"
    assert spec["method_config"]["max_dialogue_rounds"] == 40
    assert spec["status"] == "frozen"
    state = read_json(directory / "checkpoint.json")["method_state"]
    messages = [record.getMessage() for record in caplog.records]
    for record in state["factor_trial_records"]:
        prefix = f"  [计划 #{record['research_plan_index']} · 评估]"
        matching = [
            message
            for message in messages
            if message.startswith(prefix) and f" · {record['factor_trial_index']}/4 · " in message
        ]
        assert len(matching) == 1
    for role, action in (("Explorer", "探索方向"), ("Analyst", "制定计划"), ("Miner", "生成因子")):
        assert f"· {role}] {action}" in terminal.getvalue()
    assert state["completed_attempts"] == 4 and state["groups"] == {}
    assert "steps" not in state
    assert all(
        not {"candidate", "evaluation", "accepted"} & record.keys()
        for record in state["factor_trial_records"]
    )
    path = project / "configs/v1_async.toml"
    path.write_text(path.read_text().replace("parallel_plans = 3", "parallel_plans = 2"))
    with pytest.raises(ValueError, match="v1_async configuration changed"):
        evaluate_frozen(project, directory)


@pytest.mark.parametrize("legacy_names", [False, True])
@pytest.mark.parametrize("legacy_directory", [False, True])
def test_resume_committed_trial_routes_feedback_to_its_group(
    project, monkeypatch, legacy_names, legacy_directory
):
    config = V1AsyncConfig(candidates_per_plan=2)
    models = AsyncModels()
    method = V1AsyncSearch(42, config, models=models)
    original = method.tell

    def interrupt(results):
        if results[0].factor_trial_index == 2:
            raise KeyboardInterrupt
        original(results)

    monkeypatch.setattr(method, "tell", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(project, "ashare", "fold1", "v1_async", 42, 6, method_impl=method)
    directory = next((project / "artifacts/runs").iterdir())
    saved = read_json(directory / "checkpoint.json")
    assert saved["pending"]["factor_trial_index"] == 2
    if legacy_names:
        for path in [directory / "checkpoint.json", *(directory / "factor_trials").glob("*.json")]:
            path.write_text(json.dumps(legacy_field_names(read_json(path))))
    if legacy_directory:
        (directory / "factor_trials").rename(directory / "trials")
    trial_directory = RunStore(directory).factor_trials_directory
    committed = {path: path.read_bytes() for path in trial_directory.glob("*.json")}
    restored = V1AsyncSearch(42, config, models=AsyncModels())
    resume(project, directory, method_impl=restored)
    assert all(path.read_bytes() == original for path, original in committed.items())
    assert not (directory / ("factor_trials" if legacy_directory else "trials")).exists()
    assert restored.completed_attempts == 6
    assert [step["factor_trial_index"] for step in restored.factor_trial_records] == list(
        range(1, 7)
    )
    assert len({step["research_plan_index"] for step in restored.factor_trial_records}) == 3
    assert not restored.groups
