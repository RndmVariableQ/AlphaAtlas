import copy
import json
import threading
from dataclasses import asdict

import pytest
from test_annealing import AgentModels, setup_method, tell
from test_pipeline import project as project

from alpha_atlas.checkpoint import read_json
from alpha_atlas.methods.annealing import AnnealingLLMConfig, AnnealingLLMSearch
from alpha_atlas.methods.v1 import EXPLORER_REMINDER, V1Config, V1Search
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen


def proposals():
    return {
        "summary": "Three falsifiable mechanisms with different observable predictions",
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
            )
        ],
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
    def __init__(self, stage, replies):
        super().__init__()
        self.stage = stage
        self.query_replies = copy.deepcopy(replies)

    def _response(self, system, payload):
        role = "analyst" if "Research Analyst Agent" in system else "miner"
        if "Atlas Explorer Agent" not in system and role == self.stage and self.query_replies:
            return json.dumps(self.query_replies.pop(0)), {
                "prompt_tokens": 5,
                "completion_tokens": 3,
            }
        return super()._response(system, payload)


def query_records(state, stage):
    group = next(iter(state["groups"].values())) if "groups" in state else None
    research = group["round"] if group else state["round_state"]
    work = group["work"] if group else state["work"]
    return (
        research["responses"]["analyst"]
        if stage == "analyst"
        else (work or {}).get("responses", [])
    )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stage", ["analyst", "miner"])
def test_analyst_and_miner_share_all_five_read_queries_without_spending_budget(
    tmp_path, monkeypatch, asynchronous, stage
):
    requests = [
        {"name": "get_context", "arguments": {}},
        {"name": "library_search", "arguments": {"source": "reference", "query": "tsmom_63"}},
        {"name": "library_get", "arguments": {"factor_id": "absent"}},
        {"name": "library_list", "arguments": {}},
        {"name": "library_stats", "arguments": {}},
    ]
    models = QueryModels(stage, [{"queries": requests}])
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
        if not state.get("round_state") and not state.get("groups"):
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
    tmp_path, asynchronous, final_round
):
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
        assert not method.steps
        calls = [c for c in models.calls if "Atlas Explorer Agent" in c[0]]
        assert len(calls) == final_round
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
    assert method.pending is None and not method.steps


def test_explorer_resume_after_20_replies_keeps_reminder_without_repeating_queries(
    tmp_path, monkeypatch
):
    config = V1Config()
    method, current = setup_v1(tmp_path, models=V1Models(explorer_queries(20)), config=config)
    captured = None

    def interrupt():
        nonlocal captured
        records = (method.round_state or {}).get("responses", {}).get("explorer", [])
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
    assert len(restored.round_state["responses"]["explorer"]) == 21
    assert restored.usage["chat_requests"] == 23


def test_v1_scores_three_directions_then_uses_original_annealing(tmp_path):
    method, current = setup_v1(tmp_path, config=AnnealingLLMConfig(candidates_per_plan=1))
    before = method.rng.getstate()
    candidate = method.ask(current.base.get_context())[0]
    assert candidate.region_ids == ("std",)
    batch = method.round_state["explorer_proposals"]
    assert [p["score"] for p in batch["proposals"]] == [72, 84, 66]
    assert batch["selected"] == 1
    assert method.rng.getstate() == before
    analyst = next(c for c in method.models.calls if "Research Analyst Agent" in c[0])
    assert analyst[1]["exploration"]["proposal"]["alternative"] == "Measurement noise"
    tell(method, current, True, 1)
    assert method.steps[-1]["explorer_proposals"] == batch
    assert method.steps[-1]["annealing_result"]["proposal_score"] == 1
    assert method.active_region_id == "std"
    assert V1Search._miner_candidate is AnnealingLLMSearch._miner_candidate
    assert V1Search.tell is AnnealingLLMSearch.tell


@pytest.mark.parametrize("count", [1, 2])
def test_v1_accepts_complete_partial_batches_without_extra_rng(tmp_path, count):
    batch = proposals()
    batch["proposals"] = batch["proposals"][:count]
    method, current = setup_v1(tmp_path, models=V1Models([batch]))
    before = method.rng.getstate()
    candidate = method.ask(current.base.get_context())[0]
    assert candidate.region_ids == (("mean",) if count == 1 else ("std",))
    assert len(method.round_state["explorer_proposals"]["proposals"]) == count
    assert method.rng.getstate() == before
    assert current.remaining_budget() == 4


def test_explorer_deduplicates_direction_history_without_changing_saved_snapshot(tmp_path):
    method, current = setup_v1(tmp_path)
    method.context = current.query("get_context", {})
    snapshot, catalog = method._catalog()
    method.round_state = method._new_round(current.base.get_context(), 1)
    previous = [
        {"region_id": "mean", "direction": "Inventory pressure"},
        {"region_id": "std", "direction": "Delayed discovery"},
        {"region_id": "mean", "direction": "Inventory pressure"},
    ]
    method.round_state["explorer_input"] = {
        "atlas": method._explorer_atlas(snapshot),
        "previous_directions": previous,
    }
    method._explore_round(snapshot, catalog)
    assert method.models.calls[0][1]["previous_directions"] == previous[:2]
    assert method.round_state["explorer_input"]["previous_directions"] == previous


@pytest.mark.parametrize(
    "damage", ["count", "too_many", "duplicate", "score", "reason", "ref", "region"]
)
def test_v1_rejects_invalid_batches_without_registering_partial_regions(tmp_path, damage):
    method, _ = setup_v1(tmp_path)
    raw = proposals()
    first = raw["proposals"][0]
    first.pop("region_id")
    first["new_region"] = {"hypothesis": "new pressure", "continent_ids": ["price_volume"]}
    if damage == "count":
        raw["proposals"].clear()
    elif damage == "too_many":
        raw["proposals"].append(copy.deepcopy(first))
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
    assert method.round_state["exploration"]["evidence_refs"] == ["library:1:1:0"]
    assert method.round_state["exploration"]["sources"] == [source]
    assert "remaining_attempts" not in json.dumps(second_payload)
    assert not {"fold", "universe", "run_id", "snapshot_id"} & second_payload.keys()
    tell(method, current, True, 2)
    method.ask(current.base.get_context())
    tell(method, current, False, 3)
    assert method.steps[-1]["explorer_proposals"]["sources"] == [source]


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
    records = method.round_state["responses"]["explorer"]
    assert "read-only" in records[0]["error"]
    assert records[1]["sources"] == []
    assert "already answered" in records[2]["error"]
    assert current.base.remaining_budget() == 4


def test_v1_recovery_uses_saved_unprocessed_reply_without_new_explorer_call(tmp_path):
    method, current = setup_v1(tmp_path)
    captured = None

    def interrupt():
        nonlocal captured
        records = (method.round_state or {}).get("responses", {}).get("explorer", [])
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


def test_v1_recovery_does_not_repeat_saved_queries(tmp_path, monkeypatch):
    method, current = setup_v1(
        tmp_path,
        models=V1Models([{"tool": "library_search", "arguments": {"source": "run"}}, proposals()]),
    )
    captured = None

    def interrupt():
        nonlocal captured
        records = (method.round_state or {}).get("responses", {}).get("explorer", [])
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
    assert len(restored.round_state["responses"]["explorer"]) == 2


@pytest.mark.parametrize("report", [True, False])
def test_v1_unresolved_and_exhaustion_stop_without_fabricated_trial(tmp_path, report):
    responses = (
        [{"summary": "No evidence", "unresolved": "Cannot justify three directions"}]
        if report
        else [{"bad": "reply"}]
    )
    method, current = setup_v1(
        tmp_path, models=V1Models(responses), config=AnnealingLLMConfig(max_dialogue_rounds=1)
    )
    with pytest.raises(ValueError, match="unresolved" if report else "limit exhausted"):
        method.ask(current.base.get_context())
    assert current.base.remaining_budget() == 4
    assert not method.steps
    assert method.pending is None


def test_repeated_unresolved_stops_after_one_clarification_without_spending_budget(tmp_path):
    unresolved = {"summary": "No complete direction", "unresolved": "No falsifiable question"}
    models = V1Models([unresolved, unresolved])
    method, current = setup_v1(tmp_path, models=models)
    with pytest.raises(ValueError, match="V1 Explorer unresolved"):
        method.ask(current.base.get_context())
    records = method.round_state["responses"]["explorer"]
    assert len(records) == 2
    assert "一至三个" in records[0]["error"]
    assert method.usage["chat_requests"] == 2
    assert current.remaining_budget() == 4
    assert not method.steps and method.pending is None


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
    assert "explorer_proposals" in state["steps"][-1]
    config = project / "configs/v1.toml"
    config.write_text(
        config.read_text().replace("final_temperature = 0.02", "final_temperature = 0.03")
    )
    with pytest.raises(ValueError, match="v1 configuration changed"):
        evaluate_frozen(project, directory)
    assert "final_temperature = 0.02" in (project / "configs/annealing.toml").read_text()


def test_v1_resume_after_committed_trial_reuses_miner_dialogue(project, monkeypatch):
    config = AnnealingLLMConfig(candidates_per_plan=3)
    models = V1Models(expressions=["$adj_close", "$volume", "$adj_close * $volume"])
    original = AnnealingLLMSearch.tell

    def interrupt(self, feedback):
        original(self, feedback)
        if feedback[0].trial_index == 2:
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
    restored = V1Search(42, config, models=V1Models(expressions=["$adj_close * $volume"]))
    resume(project, directory, method_impl=restored)
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
    assert json.loads(json.dumps(restored.steps)) == expected["steps"]
    assert restored.region_scores == expected["region_scores"]
    assert asdict(config) == restored.configuration
