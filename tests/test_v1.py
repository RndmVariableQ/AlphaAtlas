import copy
import json
import logging
import math
import random
import threading
from dataclasses import asdict

import pytest
from test_annealing import AgentModels, commit_records, setup_method, tell
from test_checkpoint import legacy_field_names
from test_pipeline import project as project

from alpha_atlas.checkpoint import read_json
from alpha_atlas.contracts import Candidate
from alpha_atlas.methods.annealing import AnnealingLLMConfig, AnnealingLLMSearch
from alpha_atlas.methods.v1 import (
    EXPLORER_PROMPT,
    EXPLORER_REMINDER,
    ExplorerUnresolved,
    V1Config,
    V1Search,
)
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.storage import RunStore, candidate_from_dict


def proposals(count=3):
    return {
        "summary": "Falsifiable mechanisms with different observable predictions",
        "proposals": [
            {
                "region_id": region,
                "objective": objective,
                "hypothesis": hypothesis,
                "conditions": condition,
                "prediction": prediction,
                "alternative": alternative,
                "references": [],
                "review": {
                    "scores": {
                        "importance": score,
                        "decision": 3,
                        "difference": 4,
                        "readiness": 5,
                    },
                    "importance": "Unresolved research mechanism",
                    "information_gain": "Predictions distinguish competing explanations",
                    "difference": "Different observable prediction",
                    "readiness": "Available fields; actual coverage remains untested",
                },
            }
            for region, objective, hypothesis, condition, prediction, alternative, score in (
                (
                    "mean",
                    "Does pressure reverse?",
                    "Inventory pressure",
                    "High volume",
                    "Reversal after volume shocks",
                    "Persistent information",
                    3,
                ),
                (
                    "std",
                    "Does uncertainty predict drift?",
                    "Delayed discovery",
                    "High volatility",
                    "Drift during uncertainty",
                    "Measurement noise",
                    5,
                ),
                (
                    "delta",
                    "Does inactivity precede jumps?",
                    "Attention latency",
                    "Low volume",
                    "Jumps after inactivity",
                    "Liquidity premium",
                    2,
                ),
                (
                    "return",
                    "Does a slow trend change short-term reversal?",
                    "Trend-dependent liquidity",
                    "Strong slow trend",
                    "Short-term reversal weakens during a strong trend",
                    "Unconditional reversal",
                    1,
                ),
            )
        ][:count],
    }


class V1Models(AgentModels):
    def __init__(self, responses=None, **kwargs):
        super().__init__(**kwargs)
        self.responses = copy.deepcopy(responses or [proposals()])

    def chat_once(self, messages, *, temperature=None):
        system = messages[0]["content"]
        if "Factor Miner Agent" in system:
            return self.chat_messages(messages, temperature=temperature)
        return self.chat(system, json.loads(messages[1]["content"]), temperature=temperature)

    def _response(self, system, payload):
        if "Atlas Explorer Agent" in system:
            raw = self.responses.pop(0) if self.responses else proposals()
            return json.dumps(raw), {"prompt_tokens": 5, "completion_tokens": 3}
        return super()._response(system, payload)


def setup_v1(tmp_path, *, models=None, config=None, attempts=4, asynchronous=False):
    previous, current = setup_method(tmp_path, config=config, attempts=attempts)
    cls = V1AsyncSearch if asynchronous else V1Search
    method = cls(42, previous.config, models=models or V1Models())
    method.set_session(current)
    method.set_atlas_context(previous._atlas_context)
    return method, current


class QueryModels(V1Models):
    def __init__(self, stage, replies, *, fenced=False):
        super().__init__()
        self.stage = stage
        self.query_replies = copy.deepcopy(replies)
        self.fenced = fenced

    def _response(self, system, payload):
        role = "analyst" if "Research Analyst Agent" in system else "miner"
        if "Atlas Explorer Agent" not in system and role == self.stage and self.query_replies:
            text = json.dumps(self.query_replies.pop(0))
            usage = {"prompt_tokens": 5, "completion_tokens": 3}
        else:
            text, usage = super()._response(system, payload)
        return ("```json\n" + text + "\n```" if self.fenced else text), usage


def query_records(state, stage):
    group = next(iter(state["groups"].values())) if "groups" in state else None
    research = group["research_plan_state"] if group else state["research_plan_state"]
    work = group["work"] if group else state["work"]
    return (
        research["responses"]["analyst"]
        if stage == "analyst"
        else (work or {}).get("responses", [])
    )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stage", ["analyst", "miner"])
def test_empty_queries_do_not_hide_valid_role_results(tmp_path, asynchronous, stage):
    class EmptyQueries(V1Models):
        def _response(self, system, payload):
            text, usage = super()._response(system, payload)
            role = "Research Analyst Agent" if stage == "analyst" else "Factor Miner Agent"
            if role in system:
                text = json.dumps({**json.loads(text), "queries": []})
            return text, usage

    models = EmptyQueries()
    method, current = setup_v1(
        tmp_path,
        models=models,
        config=V1AsyncConfig(parallel_plans=1),
        attempts=1,
        asynchronous=asynchronous,
    )
    try:
        assert method.ask(current.base.get_context())[0].expression == "$x"
        assert len(models.calls) == method.usage["chat_requests"] == 3
        records = query_records(method.dump_state(), stage)
        assert len(records) == 1 and "result" in records[0] and "query_results" not in records[0]
        assert current.base.attempts_used == 0 and current.remaining_budget() == 1
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stage", ["analyst", "miner"])
@pytest.mark.parametrize("fenced", [False, True])
def test_analyst_and_miner_share_all_five_read_queries_without_spending_budget(
    tmp_path, monkeypatch, asynchronous, stage, fenced
):
    requests = [
        {"name": "get_context", "arguments": {}},
        {"name": "library_search", "arguments": {"source": "reference", "query": "tsmom_63"}},
        {"name": "library_get", "arguments": {"factor_id": "absent"}},
        {"name": "library_list", "arguments": {}},
        {"name": "library_stats", "arguments": {}},
    ]
    models = QueryModels(stage, [{"queries": requests}], fenced=fenced)
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1, max_formula_repairs=0)
    method, current = setup_v1(
        tmp_path, config=config, models=models, attempts=3, asynchronous=asynchronous
    )
    calls = []
    query = current.query
    owner = threading.get_ident()

    def record(name, arguments):
        assert threading.get_ident() == owner
        calls.append({"name": name, "arguments": arguments})
        return query(name, arguments)

    monkeypatch.setattr(current, "query", record)
    try:
        candidate = method.ask(current.base.get_context())[0]
        assert candidate.expression == "$x"
        assert calls[1:] == requests
        saved = query_records(method.dump_state(), stage)[0]["query_results"]
        assert saved["requests"] == requests
        assert "remaining_attempts" not in json.dumps(saved)
        assert current.base.attempts_used == 0 and current.remaining_budget() == 3
        assert method.usage["chat_requests"] == 4
        for system, _, _ in models.calls:
            if "Atlas Explorer Agent" not in system:
                assert all(request["name"] in system for request in requests)
        feedback = current.base.evaluate(candidate)
        current.remaining -= 1
        method.tell([feedback])
        assert current.base.attempts_used == method.completed_attempts == 1
        assert current.remaining_budget() == 2
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("stage", ["analyst", "miner"])
@pytest.mark.parametrize("tool", ["evaluate", "register_operator", "research_products"])
def test_role_queries_cannot_evaluate_write_or_expand_public_permissions(tmp_path, stage, tool):
    models = QueryModels(stage, [{"queries": [{"name": tool, "arguments": {}}]}])
    method, current = setup_v1(tmp_path, models=models)
    candidate = method.ask(current.base.get_context())[0]
    result = query_records(method.dump_state(), stage)[0]["query_results"]["results"][0]
    assert "Unknown tool" in result["error"]
    assert candidate.expression == "$x"
    assert current.base.attempts_used == 0 and current.remaining_budget() == 4


@pytest.mark.parametrize("stage", ["analyst", "miner"])
@pytest.mark.parametrize("requests", [[], ["library_stats"], [{"name": "library_stats"}] * 17])
def test_invalid_query_batches_return_errors_without_dispatch_or_candidate_cost(
    tmp_path, monkeypatch, stage, requests
):
    models = QueryModels(stage, [{"queries": requests}])
    method, current = setup_v1(tmp_path, models=models)
    query = current.query
    names = []

    def record(name, arguments):
        names.append(name)
        return query(name, arguments)

    monkeypatch.setattr(current, "query", record)
    candidate = method.ask(current.base.get_context())[0]
    assert query_records(method.dump_state(), stage)[0]["error"] == "invalid metadata queries"
    assert names == ["get_context"]
    assert candidate.expression == "$x" and current.base.attempts_used == 0


@pytest.mark.parametrize("stage", ["analyst", "miner"])
@pytest.mark.parametrize("rounds", [8, 9])
@pytest.mark.parametrize("batch_size", [1, 16])
def test_read_query_rounds_are_bounded_separately_from_miner_formula_repairs(
    tmp_path, monkeypatch, stage, rounds, batch_size
):
    models = QueryModels(stage, [{"queries": [{"name": "library_stats"}] * batch_size}] * rounds)
    config = V1Config(max_formula_repairs=0 if rounds == 8 else 1)
    method, current = setup_v1(tmp_path, models=models, config=config)
    query = current.query
    calls = []

    def record(name, arguments):
        calls.append(name)
        return query(name, arguments)

    monkeypatch.setattr(current, "query", record)
    candidate = method.ask(current.base.get_context())[0]
    records = query_records(method.dump_state(), stage)
    assert calls.count("library_stats") == 8 * batch_size
    assert sum("query_results" in record for record in records) == 8
    if rounds == 9:
        assert records[8]["error"] == "metadata query round budget exhausted"
    assert candidate.expression == "$x" and current.base.attempts_used == 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stage", ["analyst", "miner"])
@pytest.mark.parametrize("processed", [False, True])
def test_saved_role_query_reuses_response_and_results_after_resume(
    tmp_path, monkeypatch, asynchronous, stage, processed
):
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1)
    models = QueryModels(stage, [{"queries": [{"name": "library_stats"}]}])
    method, current = setup_v1(
        tmp_path, config=config, models=models, asynchronous=asynchronous, attempts=1
    )
    captured = None
    calls = []
    query = current.query

    def record(name, arguments):
        calls.append(name)
        return query(name, arguments)

    def interrupt():
        nonlocal captured
        state = method.dump_state()
        if not state.get("research_plan_state") and not state.get("groups"):
            return
        records = query_records(state, stage)
        if records and '"queries"' in records[-1]["text"]:
            if ("query_results" in records[-1]) == processed:
                captured = json.loads(json.dumps(state))
                raise KeyboardInterrupt

    monkeypatch.setattr(current, "query", record)
    method.set_checkpoint(interrupt)
    restored = None
    try:
        with pytest.raises(KeyboardInterrupt):
            method.ask(current.base.get_context())
        if asynchronous:
            method.close()
        restored, _ = setup_v1(
            tmp_path / "restored", config=config, asynchronous=asynchronous, attempts=1
        )
        restored.set_session(current)
        restored.load_state(captured)
        candidate = restored.ask(current.base.get_context())[0]
        assert candidate.expression == "$x"
        assert calls.count("library_stats") == 1
        assert restored.usage["chat_requests"] == 4
        assert current.base.attempts_used == 0 and current.remaining_budget() == 1
        records = query_records(restored.dump_state(), stage)
        assert len(records) == 2 and "query_results" in records[0]
    finally:
        if asynchronous:
            method.close()
            if restored is not None:
                restored.close()


def explorer_queries(count):
    return [
        {"tool": "library_search", "arguments": {"source": "run", "query": f"topic_{index}"}}
        for index in range(count)
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("final_round", [20, 21, 40])
def test_explorer_reminds_from_request_21_and_accepts_last_allowed_reply(
    tmp_path, asynchronous, final_round, caplog
):
    caplog.set_level(logging.INFO, logger="alpha_atlas.progress")
    config = V1AsyncConfig() if asynchronous else V1Config()
    models = V1Models(explorer_queries(final_round - 1) + [proposals()])
    method, current = setup_v1(tmp_path, models=models, config=config, attempts=1)
    if asynchronous:
        original = method
        method = V1AsyncSearch(42, config, models=models)
        method.set_session(current)
        method.set_atlas_context(original._atlas_context)
    try:
        candidate = method.ask(current.base.get_context())[0]
        assert candidate.region_ids == ("std",)
        assert current.remaining_budget() == 1
        assert not method.factor_trial_records
        calls = [c for c in models.calls if "Atlas Explorer Agent" in c[0]]
        assert len(calls) == final_round
        messages = [record.getMessage() for record in caplog.records]
        requests = [message for message in messages if "· Explorer] 探索方向 ·" in message]
        assert len(requests) == final_round
        reminders = [message for message in messages if "· Explorer] 提醒\n" in message]
        assert len(reminders) == int(final_round > 20)
        assert all("提醒=" not in message for message in requests)
        for index, (system, payload, _) in enumerate(calls, start=1):
            assert (EXPLORER_REMINDER in system) == (index > 20)
            assert "remaining_attempts" not in payload
        assert config.max_dialogue_rounds == 40
        assert AnnealingLLMConfig().max_dialogue_rounds == 8
    finally:
        if asynchronous:
            method.close()


def test_explorer_stops_after_40_queries_without_request_41_or_a_trial(tmp_path):
    models = V1Models(explorer_queries(40) + [proposals()])
    method, current = setup_v1(tmp_path, models=models, config=V1Config())
    with pytest.raises(ValueError, match=r"dialogue limit exhausted \(40 replies\)"):
        method.ask(current.base.get_context())
    assert len(models.calls) == 40
    assert current.remaining_budget() == 4
    assert method.pending is None and not method.factor_trial_records


def test_explorer_resume_after_20_replies_keeps_reminder_without_repeating_queries(
    tmp_path, monkeypatch
):
    config = V1Config()
    method, current = setup_v1(tmp_path, models=V1Models(explorer_queries(20)), config=config)
    captured = None

    def interrupt():
        nonlocal captured
        records = (method.research_plan_state or {}).get("responses", {}).get("explorer", [])
        if len(records) == 20 and records[-1].get("processed"):
            captured = json.loads(json.dumps(method.dump_state()))
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    with pytest.raises(KeyboardInterrupt):
        method.ask(current.base.get_context())
    restored, _ = setup_v1(tmp_path / "restored", config=config)
    restored.set_session(current)
    restored.load_state(captured)
    monkeypatch.setattr(current, "query", lambda *_: pytest.fail("saved query must not repeat"))
    restored.ask(current.base.get_context())
    calls = [c for c in restored.models.calls if "Atlas Explorer Agent" in c[0]]
    assert len(calls) == 1 and EXPLORER_REMINDER in calls[0][0]
    assert len(restored.research_plan_state["responses"]["explorer"]) == 21
    assert restored.usage["chat_requests"] == 23


def test_v1_scores_three_directions_then_uses_original_annealing(tmp_path):
    method, current = setup_v1(tmp_path, config=AnnealingLLMConfig(candidates_per_plan=1))
    before = method.rng.getstate()
    candidate = method.ask(current.base.get_context())[0]
    assert candidate.region_ids == ("std",)
    batch = method.research_plan_state["explorer_proposals"]
    assert [p["score"] for p in batch["proposals"]] == [72, 84, 66]
    assert batch["selected"] == 1
    assert method.rng.getstate() == before
    analyst = next(c for c in method.models.calls if "Research Analyst Agent" in c[0])
    assert analyst[1]["exploration"]["proposal"]["alternative"] == "Measurement noise"
    tell(method, current, True, 1)
    assert method.factor_trial_records[-1]["explorer_proposals"] == batch
    assert method.factor_trial_records[-1]["annealing_result"]["proposal_score"] == 1
    assert method.active_region_id == "std"
    assert V1Search._miner_candidate is AnnealingLLMSearch._miner_candidate
    assert V1Search.tell is AnnealingLLMSearch.tell


@pytest.mark.parametrize("count", [2, 3, 4])
def test_v1_accepts_complete_batches_without_extra_rng(tmp_path, count):
    batch = proposals(count)
    method, current = setup_v1(tmp_path, models=V1Models([batch]))
    before = method.rng.getstate()
    candidate = method.ask(current.base.get_context())[0]
    assert candidate.region_ids == ("std",)
    assert len(method.research_plan_state["explorer_proposals"]["proposals"]) == count
    assert method.rng.getstate() == before
    assert current.remaining_budget() == 4


def test_explorer_prompt_review_example_is_accepted(tmp_path):
    review, _ = json.JSONDecoder().raw_decode(EXPLORER_PROMPT.split("review: ", 1)[1])
    batch = proposals(2)
    for proposal in batch["proposals"]:
        proposal["review"] = copy.deepcopy(review)
    method, current = setup_v1(tmp_path, models=V1Models([batch]))
    method.ask(current.base.get_context())
    submitted = method.research_plan_state["explorer_proposals"]["proposals"]
    assert all(proposal["review"] == review for proposal in submitted)


@pytest.mark.parametrize("policy", ["greedy", "random", "boltzmann"])
def test_proposal_validation_does_not_choose_or_consume_rng(tmp_path, policy):
    method, _ = setup_v1(tmp_path, config=V1Config(selection_policy=policy))
    _, catalog = method._catalog()
    before = method.rng.getstate()
    batch = method._validate_proposals(proposals(), catalog, set())
    assert "selected" not in batch
    assert method.rng.getstate() == before


@pytest.mark.parametrize("policy", ["greedy", "random", "boltzmann"])
def test_proposal_selector_orders_without_replacement(tmp_path, policy):
    method, _ = setup_v1(tmp_path, config=V1Config(selection_policy=policy))
    values = [{"score": score} for score in [72, 84, 66, 60]]
    state = method.rng.getstate()
    assert method._select_proposals(values, 0, 0.25) == []
    assert method.rng.getstate() == state
    selected = method._select_proposals(values, 8, 0.25)
    assert sorted(selected) == [0, 1, 2, 3]
    if policy == "greedy":
        assert selected == [1, 0, 2, 3]
        assert method.rng.getstate() == state
    elif policy == "random":
        expected = random.Random()
        expected.setstate(state)
        assert selected == expected.sample(range(4), 4)


def test_boltzmann_uses_normalized_scores_and_cools_toward_greedy(tmp_path):
    method, _ = setup_v1(tmp_path, config=V1Config(selection_policy="boltzmann"))

    class Draws:
        def choices(self, population, *, weights, k):
            assert population == [0, 1, 2] and k == 1
            assert weights == pytest.approx([math.exp(-0.48), 1, math.exp(-0.72)])
            return [2]

    method.rng = Draws()
    assert method._select_proposals([{"score": s} for s in [72, 84, 66]], 1, 0.25) == [2]
    values = [{"score": 20}, {"score": 100}]
    method.rng = random.Random(1)
    assert method._select_proposals(values, 1, 1_000_000) == [0]
    method.rng = random.Random(1)
    assert method._select_proposals(values, 1, 1e-300) == [1]


@pytest.mark.parametrize("policy", ["greedy", "random", "boltzmann"])
def test_new_policies_share_neutral_inputs_and_complete_without_metropolis(tmp_path, policy):
    config = V1Config(selection_policy=policy, candidates_per_plan=1)
    method, current = setup_v1(tmp_path, config=config, attempts=2)
    method.active_region_id = "mean"
    method.completed_attempts = 900
    method.attempts_total = 1000
    for index in [1, 2]:
        method.ask(current.base.get_context())
        after_selection = method.rng.getstate()
        tell(method, current, True, index)
        assert method.rng.getstate() == after_selection
    explorer = next(call for call in method.models.calls if "Atlas Explorer Agent" in call[0])
    assert not {"mode", "active_region", "active_region_score"} & explorer[1].keys()
    assert "depth investigates" not in explorer[0]
    assert method.active_region_id == "mean"
    assert method.completed_research_plan_count == 2
    assert sum(s["attempts"] for s in method.region_scores.values()) == 2
    assert all("annealing_result" not in r for r in method.factor_trial_records)
    assert all(p["completed"] for p in method._plan_results())
    assert len(method._plan_results()) == 2


@pytest.mark.parametrize("policy", ["random", "boltzmann"])
def test_pending_selection_resumes_without_drawing_again(tmp_path, policy):
    config = V1Config(selection_policy=policy, candidates_per_plan=1)
    method, current = setup_v1(tmp_path, config=config, attempts=1)
    candidate = method.ask(current.base.get_context())[0]
    saved = json.loads(json.dumps(method.dump_state()))
    restored = V1Search(42, config, models=V1Models())
    restored.set_session(current)
    restored.load_state(saved)

    def unexpected(*args):
        raise AssertionError("committed selection must not be repeated")

    restored._select_proposals = unexpected
    assert candidate_from_dict(restored.pending) == candidate
    assert not restored.models.calls
    assert restored.rng.getstate() == method.rng.getstate()
    tell(restored, current, True, 1)
    assert restored.rng.getstate() == method.rng.getstate()
    assert restored._plan_results()[0]["completed"]


@pytest.mark.parametrize("policy", ["", "annealing", "metropolis", False, 3])
def test_v1_rejects_unknown_selection_policy(policy):
    with pytest.raises(ValueError, match="selection_policy"):
        V1Config(selection_policy=policy)


def test_explorer_deduplicates_direction_history_without_changing_saved_snapshot(tmp_path):
    method, current = setup_v1(tmp_path)
    method.context = current.query("get_context", {})
    snapshot, catalog = method._catalog()
    method.research_plan_state = method._new_round(current.base.get_context(), 1)
    previous = [
        {"region_id": "mean", "direction": "Inventory pressure"},
        {"region_id": "std", "direction": "Delayed discovery"},
        {"region_id": "mean", "direction": "Inventory pressure"},
    ]
    method.research_plan_state["explorer_input"] = {
        "atlas": method._explorer_atlas(snapshot),
        "previous_directions": previous,
    }
    method._explore_round(snapshot, catalog)
    assert method.models.calls[0][1]["previous_directions"] == previous[:2]
    assert method.research_plan_state["explorer_input"]["previous_directions"] == previous


@pytest.mark.parametrize(
    "damage", ["count", "too_few", "too_many", "duplicate", "score", "reason", "ref", "region"]
)
def test_v1_rejects_invalid_batches_without_registering_partial_regions(tmp_path, damage):
    method, _ = setup_v1(tmp_path)
    raw = proposals()
    first = raw["proposals"][0]
    first.pop("region_id")
    first["new_region"] = {"hypothesis": "new pressure", "continent_ids": ["price_volume"]}
    if damage == "count":
        raw["proposals"].clear()
    elif damage == "too_few":
        raw["proposals"] = raw["proposals"][:1]
    elif damage == "too_many":
        raw["proposals"].extend([copy.deepcopy(first), copy.deepcopy(first)])
    elif damage == "duplicate":
        raw["proposals"][2] = {**first, "objective": "Only the title changes"}
    elif damage == "score":
        raw["proposals"][2]["review"]["scores"]["importance"] = True
    elif damage == "reason":
        raw["proposals"][2]["review"]["information_gain"] = ""
    elif damage == "ref":
        raw["proposals"][2]["references"] = ["trial:999"]
    else:
        first["region_id"] = "mean"
    _, catalog = method._catalog()
    with pytest.raises(ValueError):
        method._validate_proposals(raw, catalog, set())
    assert not method.proposed_regions


def test_v1_shared_query_full_evidence_and_no_free_evaluations(tmp_path):
    batch = proposals()
    for item in batch["proposals"]:
        item["references"] = ["library:1:1:0"]
    models = V1Models(
        [
            {"tool": "library_search", "arguments": {"source": "run", "limit": 3}},
            batch,
        ]
    )
    method, current = setup_v1(tmp_path, models=models)
    from alpha_atlas.contracts import Candidate

    evaluated = current.base.evaluate(Candidate("$x"))
    assert evaluated.accepted
    remaining = current.base.remaining_budget()
    method.ask(current.base.get_context())
    assert current.base.remaining_budget() == remaining
    second_payload = models.calls[1][1]
    source = second_payload["dialogue"][0]["sources"][0]
    assert source["value"]["candidate"]["expression"] == "$x"
    assert source["value"]["metrics"]
    assert method.research_plan_state["exploration"]["evidence_refs"] == ["library:1:1:0"]
    assert method.research_plan_state["exploration"]["sources"] == [source]
    assert "remaining_attempts" not in json.dumps(second_payload)
    assert not {"fold", "universe", "run_id", "snapshot_id"} & second_payload.keys()
    tell(method, current, True, 2)
    method.ask(current.base.get_context())
    tell(method, current, False, 3)
    assert method.factor_trial_records[-1]["explorer_proposals"]["sources"] == [source]


def evidence_step(context, trial, research_plan_index, value, *, completed=True, failed=False):
    step = {
        "factor_trial_index": trial,
        "research_plan_index": research_plan_index,
        "region_id": "mean",
        "direction": f"Research question {research_plan_index}",
        "plan": {
            "region_id": "mean",
            "direction": f"Research question {research_plan_index}",
            "hypothesis": "Test the main measurement against its controls",
            "allowed_fields": ["x"],
            "measurement_plan": "Compare main measurement and controls",
            "miner_instruction": "Use the recorded measurement",
        },
        "candidate": asdict(Candidate(f"$x * {trial}", region_ids=("mean",))),
        "accepted": False,
        "evaluation": {
            "factor_id": f"factor-{trial}",
            "status": "compile_error" if failed else "success",
            "metrics": []
            if failed
            else [
                {"name": context["metric"], "split": "train", "value": 0.01},
                {"name": context["metric"], "split": "val", "value": value},
            ],
            "direction": None if failed else 1,
            "coverage": 0.0 if failed else 0.9,
            "reason": "compile_error" if failed else "quality_threshold",
            "remaining_attempts": 99,
        },
    }
    if completed:
        step["annealing_result"] = {"accepted": False}
    return json.loads(json.dumps(step))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_plan_results_include_whole_map_samples_recent_plans_and_failures_without_budget(
    tmp_path, asynchronous
):
    config = V1AsyncConfig(parallel_plans=2) if asynchronous else V1Config()
    method, current = setup_v1(tmp_path, config=config, asynchronous=asynchronous)
    try:
        method.context = current.query("get_context", {})
        method.factor_trial_records = commit_records(
            current,
            [
                evidence_step(method.context, 1, 1, 0.02),
                evidence_step(method.context, 2, 2, 0.002, completed=False),
                evidence_step(method.context, 3, 3, 0, completed=False, failed=True),
                evidence_step(method.context, 4, 2, 0.001),
                evidence_step(method.context, 5, 3, -0.001),
                evidence_step(method.context, 6, 4, 0.03, completed=False),
            ],
        )
        before = copy.deepcopy(method.factor_trial_records)
        snapshot, _ = method._catalog()
        plans = method._explorer_atlas(snapshot)["plan_results"]
        # The map also expands plan 4, whose execution is still active.
        assert [plan["research_plan_index"] for plan in plans] == (
            [2, 3, 4] if asynchronous else [3, 4]
        )
        assert plans[-1]["completed"] is False
        recent = [row for plan in plans if plan["completed"] for row in plan["trials"]]
        assert [row["ref"] for row in recent] == (
            ["trial:2", "trial:4", "trial:3", "trial:5"] if asynchronous else ["trial:3", "trial:5"]
        )
        assert all(row["admitted"] is False for row in recent)
        assert (
            next(row for row in recent if row["ref"] == "trial:3")["evaluation"]["status"]
            == "compile_error"
        )
        assert recent[-1]["candidate"] == current.get_trial(5)["candidate"]
        assert recent[-1]["evaluation"]["metrics"] == current.get_trial(5)["metrics"]
        assert "remaining_attempts" not in json.dumps(recent)
        assert method.factor_trial_records == before and current.remaining_budget() == 4
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
def test_resume_oversized_explorer_keeps_indexes_and_expands_old_evidence(
    tmp_path, asynchronous, confirmed
):
    count = 2 if asynchronous else 1
    config = (
        V1AsyncConfig(parallel_plans=count, candidates_per_plan=1)
        if asynchronous
        else V1Config(candidates_per_plan=1)
    )
    method, current = setup_v1(tmp_path, config=config, asynchronous=asynchronous)
    method.context = current.query("get_context", {})
    measurements = [evidence_step(method.context, index, index, 0.001) for index in range(1, 21)]
    for step in measurements:
        step["plan"]["measurement_plan"] = "Measure the primary and its control. " * 200
    method.factor_trial_records = commit_records(current, measurements)
    method.blocked_plans = [
        {
            "research_plan_index": 1,
            "completed_attempts": 0,
            "plan": {**measurements[0]["plan"], "blocked": "Missing quote data"},
        }
    ]
    method.completed_research_plan_count = 20
    if asynchronous:
        method.next_research_plan_index = 21
    captured = None

    def interrupt():
        nonlocal captured
        saved = copy.deepcopy(method.dump_state())
        state = (
            saved["explorer"]["research_plan_state"]
            if asynchronous and saved["explorer"]
            else saved.get("research_plan_state")
        )
        if state and "explorer_input" in state:
            atlas = state["explorer_input"]["atlas"]
            assert len(atlas["plan_results"]) == count
            assert set(atlas["blocked_plans"][0]["plan"]) == {"region_id", "direction", "blocked"}
            # Reproduce the old saved request: all map samples expanded, full blocked plans.
            atlas["plan_results"] = method._plan_results()
            atlas["blocked_plans"] = copy.deepcopy(method.blocked_plans)
            captured = saved
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    restored = None
    try:
        with pytest.raises(KeyboardInterrupt):
            method.ask(current.base.get_context())
        if asynchronous:
            method.close()
        batch = proposals()
        batch["proposals"] = [batch["proposals"][0], batch["proposals"][2]]
        batch["proposals"][0]["references"] = ["trial:1"]
        if confirmed:
            state = (
                captured["explorer"]["research_plan_state"]
                if asynchronous
                else captured["research_plan_state"]
            )
            _, catalog = method._catalog()
            state["responses"]["explorer"] = [
                {
                    "text": json.dumps(batch),
                    "processed": True,
                    "result": V1Search._validate_proposals(method, batch, catalog, {"trial:1"}),
                }
            ]
        before = copy.deepcopy(captured)
        models = V1Models([batch, batch])
        restored, _ = setup_v1(
            tmp_path / "restored", config=config, models=models, asynchronous=asynchronous
        )
        restored.set_session(current)
        restored.load_state(captured)
        restored.ask(current.base.get_context())
        calls = [payload for system, payload, _ in models.calls if "Atlas Explorer Agent" in system]
        assert len(calls) == (0 if confirmed else 2)
        research = (
            next(iter(restored.groups.values()))["research_plan_state"]
            if asynchronous
            else restored.research_plan_state
        )
        payload = research["explorer_input"] if confirmed else calls[0]
        assert len(json.dumps(payload)) < len(json.dumps(captured)) / 2
        assert [item["research_plan_index"] for item in payload["previous_directions"]] == list(
            range(1, 21)
        )
        assert [plan["research_plan_index"] for plan in payload["atlas"]["plan_results"]] == (
            ([1] if confirmed else []) + list(range(21 - count, 21))
        )
        assert payload["atlas"]["blocked_plans"][0]["plan"]["blocked"] == "Missing quote data"
        source = {"ref": "trial:1", "value": method._plan_results({1})[0]}
        if not confirmed:
            assert calls[1]["dialogue"][0]["sources"][0] == source
        assert source["ref"] == "trial:1"
        assert source["value"]["plan"] == measurements[0]["plan"]
        analyst = next(
            payload
            for system, payload, _ in models.calls
            if "Research Analyst" in system and payload["exploration"]["sources"]
        )
        assert analyst["exploration"]["sources"] == [source]
        assert restored.factor_trial_records == method.factor_trial_records
        assert restored.blocked_plans == method.blocked_plans and captured == before
        assert current.base.attempts_used == 0 and current.remaining_budget() == 4
    finally:
        if asynchronous:
            method.close()
            if restored is not None:
                restored.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("trial", [1, 2])
def test_map_and_recent_unadmitted_evidence_reaches_analyst_without_library_membership(
    tmp_path, asynchronous, trial
):
    batch = proposals()
    batch["proposals"] = [batch["proposals"][0], batch["proposals"][2]]
    batch["proposals"][0]["references"] = [f"trial:{trial}"]
    models = V1Models([batch, batch])
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1)
    method, current = setup_v1(tmp_path, models=models, config=config, asynchronous=asynchronous)
    try:
        method.context = current.query("get_context", {})
        method.factor_trial_records = commit_records(
            current,
            [
                evidence_step(method.context, 1, 1, 0.02),
                evidence_step(method.context, 2, 2, 0.001),
            ],
        )
        assert current.query("library_get", {"factor_id": f"factor-{trial}"}) is None
        candidate = method.ask(current.base.get_context())[0]
        explorer = next(
            payload for system, payload, _ in models.calls if "Atlas Explorer Agent" in system
        )
        map_refs = {
            row["ref"]
            for region in explorer["atlas"]["regions"]
            for name in ("supporting_trials", "opposing_trials")
            for row in region["statistics"][name]
        }
        assert map_refs == {"trial:1"}
        assert [row["trials"][0]["ref"] for row in explorer["atlas"]["plan_results"]] == ["trial:2"]
        analyst = next(
            payload for system, payload, _ in models.calls if "Research Analyst Agent" in system
        )
        (source,) = analyst["exploration"]["sources"]
        assert source["ref"] == f"trial:{trial}"
        assert source["value"]["plan"] == method.factor_trial_records[trial - 1]["plan"]
        (measurement,) = source["value"]["trials"]
        assert measurement["candidate"] == current.get_trial(trial)["candidate"]
        assert measurement["evaluation"]["metrics"] == current.get_trial(trial)["metrics"]
        assert "remaining_attempts" not in json.dumps(source)
        assert candidate.expression == "$x" and current.remaining_budget() == 4
        assert current.base.attempts_used == 0 and len(models.calls) == (4 if trial == 1 else 3)
    finally:
        if asynchronous:
            method.close()


def memory_steps(context, *, control_visible=False):
    # Synthetic measurements reproduce the Haiku 190/194/196 main/control relationship.
    values = {190: 0.000839, 192: 0.002191, 194: -0.001436, 196: 0.011218, 197: 0.000221}
    factor_trial_records = []
    for trial, value in values.items():
        step = evidence_step(context, trial, 39, value, completed=trial == 197)
        step["direction"] = "Does flat-price positioning predict returns beyond volatility?"
        step["plan"]["direction"] = step["direction"]
        step["plan"]["measurement_plan"] = "Main interaction, positioning alone, volatility control"
        step["candidate"]["name"] = "positioning" if trial in (190, 194) else "control"
        if trial == 196:
            step["candidate"]["expression"] = "TS_STD(DELTA($x, 1), 48)"
            step["evaluation"]["reason"] = "behavior_duplicate"
        factor_trial_records.append(step)
    # Other plans interleave, repeat a title, and include a failed measurement.
    for trial, failed in [(191, True), (193, False)]:
        step = evidence_step(context, trial, 40, 0.001, completed=trial == 193, failed=failed)
        step["direction"] = factor_trial_records[0]["direction"]
        factor_trial_records.append(step)
    factor_trial_records.extend(
        [
            evidence_step(context, 999, 99, 0.001 if control_visible else 0.04),
            evidence_step(context, 1000, 100, -0.04),
            evidence_step(context, 1001, 101, 0.0001, completed=False),
        ]
    )
    return sorted(factor_trial_records, key=lambda step: step["factor_trial_index"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_legacy_step_copies_are_discarded_and_atomic_trials_supply_all_history(
    tmp_path, monkeypatch, asynchronous
):
    method, current = setup_v1(tmp_path, asynchronous=asynchronous)
    method.context = current.query("get_context", {})
    measurements = memory_steps(method.context)
    method.factor_trial_records = commit_records(current, measurements)
    saved = json.loads(json.dumps(method.dump_state()))
    saved.pop("factor_trial_records")
    saved["steps"] = copy.deepcopy(measurements)
    for step in saved["steps"]:
        step["candidate"]["expression"] = "$incorrect_legacy_copy"
        step["evaluation"]["metrics"] = []
        step["accepted"] = True
    original_saved = copy.deepcopy(saved)
    restored, _ = setup_v1(tmp_path / "restored", asynchronous=asynchronous)
    restored.set_session(current)
    try:
        restored.load_state(saved)
        assert saved == original_saved
        assert restored.factor_trial_records == method.factor_trial_records
        assert "steps" not in restored.dump_state()
        assert all(
            not {"candidate", "evaluation", "accepted"} & record.keys()
            for record in restored.factor_trial_records
        )
        reads = []
        get_trial = current.get_trial

        def read(index):
            reads.append(index)
            return get_trial(index)

        monkeypatch.setattr(current, "get_trial", read)
        (plan,) = restored._plan_results({39})
        assert reads == [190, 192, 194, 196, 197]
        assert plan["trials"][0]["candidate"] == measurements[0]["candidate"]
        assert plan["trials"][2]["evaluation"]["metrics"] == get_trial(194)["metrics"]
        assert all(not row["admitted"] for row in plan["trials"])
        assert AnnealingLLMSearch._analyst_previous_round(restored, 39)["qualified_candidates"] == 0
        reads.clear()
        snapshot, _ = restored._catalog()
        restored._explorer_atlas(snapshot)
        assert reads == [record["factor_trial_index"] for record in method.factor_trial_records]
        path = current.base._store.directory / "factor_trials/00000194.json"
        path.unlink()
        with pytest.raises(FileNotFoundError):
            restored._plan_results({39})
    finally:
        if asynchronous:
            method.close()
            restored.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("control_visible", [False, True])
def test_plan_history_preserves_main_controls_and_completed_revisits(
    tmp_path, asynchronous, control_visible
):
    batch = proposals()
    batch["proposals"] = [batch["proposals"][0], batch["proposals"][2]]
    batch["proposals"][0]["references"] = ["trial:196", "trial:194"]
    models = V1Models([batch, batch])
    method, current = setup_v1(
        tmp_path, models=models, config=V1AsyncConfig(parallel_plans=1), asynchronous=asynchronous
    )
    try:
        method.context = current.query("get_context", {})
        measurements = memory_steps(method.context, control_visible=control_visible)
        measurements[0]["evaluation"]["metrics"].append(
            {"name": method.context["metric"], "split": "test", "value": 0.9}
        )
        method.factor_trial_records = commit_records(current, measurements)
        method.completed_research_plan_count = 101
        if asynchronous:
            method.next_research_plan_index = 102
        before, rng = copy.deepcopy(method.factor_trial_records), method.rng.getstate()
        candidate = method.ask(current.base.get_context())[0]
        calls = [call for call in models.calls if "Atlas Explorer Agent" in call[0]]
        assert len(calls) == 2
        index = calls[0][1]["previous_directions"]
        assert [row["research_plan_index"] for row in index] == [39, 40, 99, 100]
        assert index[0]["direction"] == index[1]["direction"]
        assert index[0]["factor_trial_refs"] == [f"trial:{i}" for i in (190, 192, 194, 196, 197)]
        assert index[0]["successful_evaluations"] == 5 and index[0]["admitted"] == 0
        assert index[1]["successful_evaluations"] == 1
        assert index[1]["rejection_reasons"]["compile_error"] == 1
        initial = calls[0][1]["atlas"]["plan_results"]
        assert 39 not in {plan["research_plan_index"] for plan in initial}
        analyst = next(call[1] for call in models.calls if "Research Analyst Agent" in call[0])
        (source,) = analyst["exploration"]["sources"]
        assert source["ref"] == "trial:196"
        plan = source["value"]
        assert plan["research_plan_index"] == 39 and plan["completed"] is True
        assert plan["plan"] == before[0]["plan"]
        assert [row["ref"] for row in plan["trials"]] == index[0]["factor_trial_refs"]
        assert plan["trials"][3]["evaluation"]["reason"] == "behavior_duplicate"
        assert all(not row["admitted"] for row in plan["trials"])
        assert "remaining_attempts" not in json.dumps(source)
        assert not any(
            m["split"] == "test" for row in plan["trials"] for m in row["evaluation"]["metrics"]
        )
        record = calls[1][1]["dialogue"][0]
        assert "sources" in record and "result" not in record and "query" not in record
        assert "confirm or revise" in record["error"]
        assert method.factor_trial_records == before and method.rng.getstate() == rng
        assert candidate.expression == "$x" and current.remaining_budget() == 4
        assert current.base.attempts_used == 0
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stage", ["draft", "expanded", "confirmed"])
@pytest.mark.parametrize("legacy_names", [False, True])
def test_historical_plan_verification_resumes_saved_evidence_and_replies(
    tmp_path, monkeypatch, asynchronous, stage, legacy_names
):
    batch = proposals()
    batch["proposals"] = [batch["proposals"][0], batch["proposals"][2]]
    batch["proposals"][0]["references"] = ["trial:194"]
    confirmed = copy.deepcopy(batch)
    # Any sibling becomes citable only after the full plan has actually been returned.
    confirmed["proposals"][0]["references"] = ["trial:190"]
    config = V1AsyncConfig(parallel_plans=1)
    method, current = setup_v1(
        tmp_path, models=V1Models([batch, confirmed]), config=config, asynchronous=asynchronous
    )
    method.context = current.query("get_context", {})
    method.factor_trial_records = commit_records(current, memory_steps(method.context))
    method.completed_research_plan_count = 101
    if asynchronous:
        method.next_research_plan_index = 102
    captured = None

    def interrupt():
        nonlocal captured
        state = method.dump_state()
        research = (
            state["explorer"]["research_plan_state"]
            if asynchronous
            else state["research_plan_state"]
        )
        records = research["responses"]["explorer"] if research else []
        if records and (
            (stage == "draft" and len(records) == 1 and not records[-1].get("processed"))
            or (stage == "expanded" and len(records) == 1 and "sources" in records[-1])
            or (stage == "confirmed" and "result" in records[-1])
        ):
            captured = json.loads(json.dumps(state))
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    restored = None
    try:
        with pytest.raises(KeyboardInterrupt):
            method.ask(current.base.get_context())
        if asynchronous:
            method.close()
        models = V1Models([confirmed])
        restored, _ = setup_v1(
            tmp_path / "restored", models=models, config=config, asynchronous=asynchronous
        )
        restored.set_session(current)
        if legacy_names:
            captured = legacy_field_names(captured)
        saved = copy.deepcopy(captured)
        restored.load_state(captured)
        research = (
            restored.explorer["research_plan_state"]
            if asynchronous
            else restored.research_plan_state
        )
        saved_research = (
            captured["explorer"]["round" if legacy_names else "research_plan_state"]
            if asynchronous
            else captured["round_state" if legacy_names else "research_plan_state"]
        )
        assert research["explorer_input"] == saved_research["explorer_input"]
        assert research["responses"] == saved_research["responses"]
        reads = []
        original = restored._plan_results

        def read(rounds=None):
            reads.append(rounds)
            assert rounds == {39}
            return original(rounds)

        monkeypatch.setattr(restored, "_plan_results", read)
        monkeypatch.setattr(current, "query", lambda *_: pytest.fail("no new library query"))
        restored.ask(current.base.get_context())
        assert reads == ([{39}] if stage == "draft" else [])
        assert sum("Atlas Explorer Agent" in call[0] for call in models.calls) == (
            0 if stage == "confirmed" else 1
        )
        analyst = next(call[1] for call in models.calls if "Research Analyst Agent" in call[0])
        assert analyst["exploration"]["sources"][0]["ref"] == "trial:190"
        assert len(analyst["exploration"]["sources"][0]["value"]["trials"]) == 5
        assert (
            restored.factor_trial_records == method.factor_trial_records
            and restored.rng.getstate() == method.rng.getstate()
        )
        assert captured == saved
        assert restored.usage["chat_requests"] == 4
        assert current.base.attempts_used == 0 and current.remaining_budget() == 4
    finally:
        if asynchronous:
            method.close()
            if restored is not None:
                restored.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("damage", [None, "unknown_ref", "score"])
def test_history_verification_cannot_bypass_dialogue_limit_or_invalid_batches(
    tmp_path, asynchronous, damage
):
    batch = proposals()
    batch["proposals"] = [batch["proposals"][0], batch["proposals"][2]]
    batch["proposals"][0]["references"] = ["trial:194"]
    if damage == "unknown_ref":
        batch["proposals"][0]["references"].append("trial:999999")
    elif damage == "score":
        batch["proposals"][0]["review"]["scores"]["importance"] = 6
    config = V1AsyncConfig(parallel_plans=1, max_dialogue_rounds=1)
    method, current = setup_v1(
        tmp_path, config=config, models=V1Models([batch]), asynchronous=asynchronous
    )
    try:
        method.context = current.query("get_context", {})
        method.factor_trial_records = commit_records(current, memory_steps(method.context))
        method.completed_research_plan_count = 101
        if asynchronous:
            method.next_research_plan_index = 102
        before = copy.deepcopy(method.factor_trial_records)
        with pytest.raises(ValueError, match="dialogue limit exhausted"):
            method.ask(current.base.get_context())
        state = method.dump_state()
        research = (
            state["explorer"]["research_plan_state"]
            if asynchronous
            else state["research_plan_state"]
        )
        (record,) = research["responses"]["explorer"]
        assert ("sources" in record) == (damage is None)
        assert "result" not in record and method.pending is None
        assert method.usage["chat_requests"] == 1 and not method.proposed_regions
        assert method.factor_trial_records == before and current.base.attempts_used == 0
        assert current.remaining_budget() == 4
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_saved_individual_trial_evidence_is_not_rebuilt_on_resume(
    tmp_path, monkeypatch, asynchronous
):
    config = V1AsyncConfig(parallel_plans=1)
    method, current = setup_v1(tmp_path, config=config, asynchronous=asynchronous)
    method.context = current.query("get_context", {})
    method.factor_trial_records = commit_records(current, memory_steps(method.context))
    snapshot, catalog = method._catalog()
    method.completed_research_plan_count = 101
    if asynchronous:
        method.next_research_plan_index = 102
        method._start_explorer(current.base.get_context())
        state = method.explorer["research_plan_state"]
    else:
        state = method.research_plan_state = method._new_round(current.base.get_context(), 102)
    atlas = AnnealingLLMSearch._explorer_atlas(method, snapshot)
    row = method._plan_results({39})[0]["trials"][0]
    atlas["recent_trials"] = [row]
    state["explorer_input"] = {
        "research_context": method.context,
        "atlas": atlas,
        "previous_directions": [{"region_id": "mean", "direction": "Old title-only history"}],
    }
    batch = proposals()
    batch["proposals"] = [batch["proposals"][0], batch["proposals"][2]]
    batch["proposals"][0]["references"] = ["trial:190"]
    accepted = V1Search._validate_proposals(method, batch, catalog, {"trial:190"})
    # A historical one-proposal result was already validated under the old protocol.
    batch["proposals"] = batch["proposals"][:1]
    accepted["proposals"] = accepted["proposals"][:1]
    accepted["selected"] = 0
    state["responses"]["explorer"] = [
        {
            "text": json.dumps(batch),
            "processed": True,
            "result": accepted,
        }
    ]
    saved = json.loads(json.dumps(method.dump_state()))
    if asynchronous:
        method.close()
    models = V1Models()
    restored, _ = setup_v1(
        tmp_path / "restored", config=config, models=models, asynchronous=asynchronous
    )
    try:
        restored.set_session(current)
        restored.load_state(saved)
        monkeypatch.setattr(restored, "_plan_results", lambda *_: pytest.fail("keep saved input"))
        restored.ask(current.base.get_context())
        assert not any("Atlas Explorer Agent" in call[0] for call in models.calls)
        analyst = next(
            payload for system, payload, _ in models.calls if "Research Analyst Agent" in system
        )
        assert analyst["exploration"]["sources"] == [{"ref": "trial:190", "value": row}]
        research = (
            next(iter(restored.groups.values()))["research_plan_state"]
            if asynchronous
            else restored.research_plan_state
        )
        assert research["explorer_input"] == json.loads(json.dumps(state["explorer_input"]))
        assert (
            restored.factor_trial_records == saved["factor_trial_records"]
            and current.remaining_budget() == 4
        )
    finally:
        if asynchronous:
            restored.close()


def test_v1_cannot_evaluate_or_repeat_reads_and_list_is_not_evidence(tmp_path):
    models = V1Models(
        [
            {"tool": "evaluate", "arguments": {"expression": "$x"}},
            {"tool": "library_list", "arguments": {}},
            {"tool": "library_list", "arguments": {}},
            proposals(),
        ]
    )
    method, current = setup_v1(tmp_path, models=models)
    method.ask(current.base.get_context())
    records = method.research_plan_state["responses"]["explorer"]
    assert "read-only" in records[0]["error"]
    assert records[1]["sources"] == []
    assert "already answered" in records[2]["error"]
    assert current.base.remaining_budget() == 4


def test_v1_recovery_uses_saved_unprocessed_reply_without_new_explorer_call(tmp_path):
    method, current = setup_v1(tmp_path)
    captured = None

    def interrupt():
        nonlocal captured
        records = (method.research_plan_state or {}).get("responses", {}).get("explorer", [])
        if records and not records[-1].get("processed"):
            captured = json.loads(json.dumps(method.dump_state()))
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    with pytest.raises(KeyboardInterrupt):
        method.ask(current.base.get_context())
    restored, _ = setup_v1(tmp_path / "restored")
    restored.set_session(current)
    restored.load_state(captured)
    assert restored.ask(current.base.get_context())[0].region_ids == ("std",)
    assert not any("Atlas Explorer Agent" in call[0] for call in restored.models.calls)
    assert restored.usage["chat_requests"] == 3
    with pytest.raises(ValueError, match="own Explorer checkpoint"):
        restored.load_state({**captured, "version": 1})


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("origin", ["live", "saved", "redirected"])
def test_depth_unresolved_redirects_to_breadth_and_resumes_without_replaying_replies(
    tmp_path, asynchronous, origin
):
    config = V1AsyncConfig(parallel_plans=1, max_dialogue_rounds=6)
    method, current = setup_v1(
        tmp_path, config=config, models=V1Models(), attempts=1, asynchronous=asynchronous
    )
    method.context = current.query("get_context", {})
    method.active_region_id = "mean"
    if asynchronous:
        method._start_explorer(current.base.get_context())
        research = method.explorer["research_plan_state"]
    else:
        method.research_plan_state = method._new_round(current.base.get_context(), 1)
        research = method.research_plan_state
    research["mode"] = "depth"
    raw = {"summary": "Local measurement blocked", "unresolved": "Mean region needs a roll flag"}
    research["responses"]["explorer"] = [
        {"text": json.dumps(raw), "processed": True, **raw}
        for _ in range(1 if origin == "live" else 2)
    ]
    original_texts = [record["text"] for record in research["responses"]["explorer"]]
    captured = None

    def interrupt_after_redirect():
        nonlocal captured
        state = method.dump_state()
        research = (
            state["explorer"]["research_plan_state"]
            if asynchronous
            else state["research_plan_state"]
        )
        if research["responses"]["explorer"][-1].get("redirected_to_breadth"):
            captured = json.loads(json.dumps(state))
            raise KeyboardInterrupt("after breadth redirect")

    restored = None
    try:
        if origin != "live":
            if origin == "redirected":
                method.set_checkpoint(interrupt_after_redirect)
                with pytest.raises(KeyboardInterrupt, match="after breadth redirect"):
                    method.ask(current.base.get_context())
                transport = method._transport if asynchronous else method.models
                assert not transport.calls
            else:
                captured = json.loads(json.dumps(method.dump_state()))
            restored, _ = setup_v1(
                tmp_path / "restored",
                config=config,
                models=V1Models(),
                attempts=1,
                asynchronous=asynchronous,
            )
            restored.set_session(current)
            restored.load_state(captured)
        active = restored or method
        assert active.ask(current.base.get_context())[0].expression == "$x"
        transport = active._transport if asynchronous else active.models
        calls = [call for call in transport.calls if "Atlas Explorer Agent" in call[0]]
        assert len(calls) == 1 and calls[0][1]["mode"] == "breadth"
        assert calls[0][1]["active_region"]["id"] == "mean"
        dialogue = calls[0][1]["dialogue"]
        assert [record["text"] for record in dialogue[: len(original_texts)]] == original_texts
        assert all(
            record.get("redirected_to_breadth") for record in dialogue[: len(original_texts)]
        )
        assert "existing fields" in dialogue[len(original_texts) - 1]["error"]
        assert current.base.attempts_used == 0 and current.remaining_budget() == 1
    finally:
        if asynchronous:
            method.close()
            if restored:
                restored.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_breadth_unresolved_after_depth_redirect_gets_one_clarification(tmp_path, asynchronous):
    raw = {"summary": "No executable test across the map", "unresolved": "No available route"}
    models = V1Models([raw, raw])
    config = V1AsyncConfig(parallel_plans=1, max_dialogue_rounds=6)
    method, current = setup_v1(
        tmp_path, config=config, models=models, attempts=1, asynchronous=asynchronous
    )
    method.context = current.query("get_context", {})
    if asynchronous:
        method._start_explorer(current.base.get_context())
        research = method.explorer["research_plan_state"]
    else:
        method.research_plan_state = method._new_round(current.base.get_context(), 1)
        research = method.research_plan_state
    research["mode"] = "depth"
    research["responses"]["explorer"] = [
        {"text": json.dumps(raw), "processed": True, **raw} for _ in range(2)
    ]
    try:
        with pytest.raises(ExplorerUnresolved, match="No available route"):
            method.ask(current.base.get_context())
        calls = [call for call in models.calls if "Atlas Explorer Agent" in call[0]]
        assert len(calls) == 2 and all(call[1]["mode"] == "breadth" for call in calls)
        assert not method.pending and current.base.attempts_used == 0
        assert len(research["responses"]["explorer"]) == 4
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_depth_redirect_cannot_request_beyond_saved_dialogue_budget(tmp_path, asynchronous):
    method, current = setup_v1(
        tmp_path,
        config=V1AsyncConfig(parallel_plans=1, max_dialogue_rounds=2),
        attempts=1,
        asynchronous=asynchronous,
    )
    method.context = current.query("get_context", {})
    if asynchronous:
        method._start_explorer(current.base.get_context())
        research = method.explorer["research_plan_state"]
    else:
        method.research_plan_state = method._new_round(current.base.get_context(), 1)
        research = method.research_plan_state
    research["mode"] = "depth"
    raw = {"summary": "Local capability missing", "unresolved": "No roll flag"}
    research["responses"]["explorer"] = [
        {"text": json.dumps(raw), "processed": True, **raw} for _ in range(2)
    ]
    try:
        with pytest.raises(ExplorerUnresolved, match="No roll flag"):
            method.ask(current.base.get_context())
        transport = method._transport if asynchronous else method.models
        assert not transport.calls and method.pending is None
        assert len(research["responses"]["explorer"]) == 2 and research["mode"] == "depth"
        assert current.remaining_budget() == 1
    finally:
        if asynchronous:
            method.close()


def test_v1_recovery_does_not_repeat_saved_queries(tmp_path, monkeypatch):
    method, current = setup_v1(
        tmp_path,
        models=V1Models([{"tool": "library_search", "arguments": {"source": "run"}}, proposals()]),
    )
    captured = None

    def interrupt():
        nonlocal captured
        records = (method.research_plan_state or {}).get("responses", {}).get("explorer", [])
        if records and records[-1].get("query"):
            captured = json.loads(json.dumps(method.dump_state()))
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    with pytest.raises(KeyboardInterrupt):
        method.ask(current.base.get_context())
    restored, _ = setup_v1(tmp_path / "restore")
    restored.set_session(current)
    restored.load_state(captured)

    def unexpected_query(*args):
        raise AssertionError("saved query must not be repeated")

    monkeypatch.setattr(current, "query", unexpected_query)
    restored.ask(current.base.get_context())
    assert len(restored.research_plan_state["responses"]["explorer"]) == 2


@pytest.mark.parametrize("report", [True, False])
def test_v1_unresolved_and_exhaustion_stop_without_fabricated_trial(tmp_path, report):
    responses = (
        [{"summary": "No evidence", "unresolved": "Cannot justify two directions"}]
        if report
        else [{"bad": "reply"}]
    )
    method, current = setup_v1(
        tmp_path, models=V1Models(responses), config=AnnealingLLMConfig(max_dialogue_rounds=1)
    )
    with pytest.raises(ValueError, match="unresolved" if report else "limit exhausted"):
        method.ask(current.base.get_context())
    assert current.base.remaining_budget() == 4
    assert not method.factor_trial_records
    assert method.pending is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_completed_direction_can_be_revisited_without_resetting_history_or_budget(
    tmp_path, asynchronous
):
    original = proposals()
    original["proposals"] = [original["proposals"][0], original["proposals"][2]]
    revisit = copy.deepcopy(original)
    revisit["summary"] = (
        "Revisit the prior question: one candidate does not separate inventory pressure "
        "from persistent information; the next test must distinguish their predictions."
    )
    models = V1Models([original, revisit], expressions=["$x", "TS_MEAN($x, 3)"])
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1)
    method, current = setup_v1(
        tmp_path, models=models, config=config, attempts=2, asynchronous=asynchronous
    )
    try:
        first = method.ask(current.base.get_context())[0]
        tell(method, current, False, 1)
        prior_steps = copy.deepcopy(method.factor_trial_records)
        second = method.ask(current.base.get_context())[0]
        calls = [call for call in models.calls if "Atlas Explorer Agent" in call[0]]
        assert len(calls) == 2
        (history,) = calls[1][1]["previous_directions"]
        assert history["direction"] == original["proposals"][0]["objective"]
        assert history["research_plan_index"] == 1 and history["factor_trial_refs"] == ["trial:1"]
        assert history["admitted"] == 0
        assert "revisit promising directions to resolve remaining questions" in calls[1][0]
        assert first.region_ids == second.region_ids == ("mean",)
        assert first.expression == "$x" and second.expression == "TS_MEAN($x, 3)"
        assert method.factor_trial_records == prior_steps
        assert method.completed_attempts == 1 and current.remaining_budget() == 1
        tell(method, current, False, 2)
        assert method.completed_attempts == 2 and current.remaining_budget() == 0
        assert method.usage["chat_requests"] == 6
    finally:
        if asynchronous:
            method.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_unresolved_clarifies_family_novelty_before_accepting_a_subquestion(tmp_path, asynchronous):
    unresolved = {"summary": "Related families studied", "unresolved": "Other families need quotes"}
    complete = proposals()
    complete["proposals"] = [complete["proposals"][0], complete["proposals"][2]]
    models = V1Models([unresolved, complete])
    config = V1AsyncConfig(parallel_plans=1, candidates_per_plan=1)
    method, current = setup_v1(tmp_path, models=models, config=config, asynchronous=asynchronous)
    try:
        candidate = method.ask(current.base.get_context())[0]
        calls = [call for call in models.calls if "Atlas Explorer Agent" in call[0]]
        assert len(calls) == 2
        clarification = calls[1][1]["dialogue"][0]["error"]
        assert "closest completed plans" in clarification
        assert "control, condition or interaction" in clarification
        assert "discriminating prediction" in clarification
        assert "two to four" in clarification
        assert candidate.expression == "$x" and candidate.region_ids == ("mean",)
        assert method.usage["chat_requests"] == 4
        assert current.base.attempts_used == 0 and current.remaining_budget() == 4
    finally:
        if asynchronous:
            method.close()


def test_repeated_unresolved_stops_after_one_clarification_without_spending_budget(tmp_path):
    unresolved = {"summary": "No complete direction", "unresolved": "No falsifiable question"}
    models = V1Models([unresolved, unresolved])
    method, current = setup_v1(tmp_path, models=models)
    with pytest.raises(ValueError, match="V1 Explorer unresolved"):
        method.ask(current.base.get_context())
    records = method.research_plan_state["responses"]["explorer"]
    assert len(records) == 2
    assert "two to four" in records[0]["error"]
    assert method.usage["chat_requests"] == 2
    assert current.remaining_budget() == 4
    assert not method.factor_trial_records and method.pending is None
    saved = json.loads(json.dumps(method.dump_state()))
    retry_models = V1Models()
    restored, _ = setup_v1(tmp_path, models=retry_models)
    restored.load_state(saved)
    with pytest.raises(ValueError, match="V1 Explorer unresolved"):
        restored.ask(current.base.get_context())
    assert not retry_models.calls
    assert restored.usage == saved["usage"]


def test_v1_native_runner_freezes_own_config_and_preserves_old_method(project, monkeypatch):
    monkeypatch.setattr(
        "alpha_atlas.methods.annealing.HTTPModels",
        lambda config: V1Models(expressions=["$adj_close", "$volume"]),
    )
    directory = run(project, "ashare", "fold1", "v1", 42, attempts=2)
    spec = read_json(directory / "run.json")
    assert spec["implementation"] == "atlas_explorer_v1"
    assert spec["method_config"]["max_dialogue_rounds"] == 40
    assert spec["status"] == "frozen"
    state = read_json(directory / "checkpoint.json")["method_state"]
    assert state["version"] == "atlas_explorer_v1"
    assert state["completed_attempts"] == 2
    assert "steps" not in state
    assert all(
        not {"candidate", "evaluation", "accepted"} & record.keys()
        for record in state["factor_trial_records"]
    )
    assert "explorer_proposals" in state["factor_trial_records"][-1]
    config = project / "configs/v1.toml"
    config.write_text(
        config.read_text().replace("final_temperature = 0.02", "final_temperature = 0.03")
    )
    with pytest.raises(ValueError, match="v1 configuration changed"):
        evaluate_frozen(project, directory)
    assert "final_temperature = 0.02" in (project / "configs/annealing.toml").read_text()


@pytest.mark.parametrize("legacy_names", [False, True])
@pytest.mark.parametrize("legacy_directory", [False, True])
def test_v1_resume_after_committed_trial_reuses_miner_dialogue(
    project, monkeypatch, legacy_names, legacy_directory
):
    config = AnnealingLLMConfig(candidates_per_plan=3)
    models = V1Models(expressions=["$adj_close", "$volume", "$adj_close * $volume"])
    original = AnnealingLLMSearch.tell

    def interrupt(self, feedback):
        original(self, feedback)
        if feedback[0].factor_trial_index == 2:
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(AnnealingLLMSearch, "tell", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(
                project,
                "ashare",
                "fold1",
                "v1",
                42,
                3,
                method_impl=V1Search(42, config, models=models),
            )
    directory = next((project / "artifacts/runs").iterdir())
    if legacy_names:
        for path in [directory / "checkpoint.json", *(directory / "factor_trials").glob("*.json")]:
            path.write_text(json.dumps(legacy_field_names(read_json(path))))
    if legacy_directory:
        (directory / "factor_trials").rename(directory / "trials")
    trial_directory = RunStore(directory).factor_trials_directory
    committed = {path: path.read_bytes() for path in trial_directory.glob("*.json")}
    restored = V1Search(42, config, models=V1Models(expressions=["$adj_close * $volume"]))
    resume(project, directory, method_impl=restored)
    assert all(path.read_bytes() == original for path, original in committed.items())
    assert not (directory / ("factor_trials" if legacy_directory else "trials")).exists()
    assert restored.completed_attempts == 3
    assert not any("Atlas Explorer Agent" in call[0] for call in restored.models.calls)
    baseline = run(
        project,
        "ashare",
        "fold1",
        "v1",
        42,
        3,
        method_impl=V1Search(
            42,
            config,
            models=V1Models(expressions=["$adj_close", "$volume", "$adj_close * $volume"]),
        ),
    )
    expected = read_json(baseline / "checkpoint.json")["method_state"]
    assert json.loads(json.dumps(restored.factor_trial_records)) == expected["factor_trial_records"]
    assert restored.region_scores == expected["region_scores"]
    assert asdict(config) == restored.configuration
