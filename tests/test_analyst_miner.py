import json

import pytest
from test_annealing import AgentModels, FixedFeedbackSession, tell
from test_mcts_llm import session as make_session

from alpha_atlas.methods.analyst_miner import AnalystMinerConfig, AnalystMinerSearch


class Models(AgentModels):
    def _response(self, system, payload):
        if "Research Analyst Agent" in system:
            return json.dumps(
                {
                    "direction": "price reversal",
                    "hypothesis": "prices reverse",
                    "allowed_fields": payload["research_context"]["fields"],
                    "measurement_plan": "compare deviations",
                    "miner_instruction": "use x",
                }
            ), {"prompt_tokens": 5, "completion_tokens": 3}
        return super()._response(system, payload)


def test_history_reset_and_resume(tmp_path):
    models = Models(expressions=["$x", "$x"])
    session = FixedFeedbackSession(make_session(tmp_path, attempts=3), 3)
    config = AnalystMinerConfig(candidates_per_plan=1)
    method = AnalystMinerSearch(42, config, models=models)
    method.set_session(session)
    first = method.ask(session.base.get_context())[0]
    assert first.region_ids == ()
    tell(method, session, False, 1)
    saved = json.loads(json.dumps(method.dump_state()))
    resumed = AnalystMinerSearch(42, config, models=models)
    resumed.set_session(session)
    resumed.load_state(saved)
    second = resumed.ask(session.base.get_context())[0]
    assert second.region_ids == ()
    analyst_calls = [payload for system, payload, _ in models.calls if "Research Analyst" in system]
    assert analyst_calls[1]["history"]["trials"][0]["factor_trial_index"] == 1
    miners = [
        payload["messages"]
        for system, payload, _ in models.calls
        if system.startswith("You are the Factor Miner Agent")
    ]
    assert len(miners) == 2
    assert len(miners[1]) == 2
    assert resumed.atlas_regions() == ()


def test_blocked_plan_is_in_next_history_without_trial(tmp_path):
    class BlockOnce(Models):
        def __init__(self):
            super().__init__()
            self.blocked = False

        def _response(self, system, payload):
            if "Research Analyst Agent" in system and not self.blocked:
                self.blocked = True
                return json.dumps({"blocked": "missing field"}), {}
            return super()._response(system, payload)

    models = BlockOnce()
    session = FixedFeedbackSession(make_session(tmp_path, attempts=3), 3)
    method = AnalystMinerSearch(42, AnalystMinerConfig(), models=models)
    method.set_session(session)
    method.ask(session.base.get_context())
    analyst_calls = [payload for system, payload, _ in models.calls if "Research Analyst" in system]
    assert analyst_calls[1]["history"]["plans"][0]["end_reason"] == "missing field"
    assert analyst_calls[1]["history"]["trials"] == []
    assert method.completed_attempts == 0


def test_miner_continues_within_plan_and_no_test_history(tmp_path):
    models = Models(expressions=["$x", "$x", "$x"])
    session = FixedFeedbackSession(make_session(tmp_path, attempts=4), 4)
    method = AnalystMinerSearch(42, AnalystMinerConfig(candidates_per_plan=2), models=models)
    method.set_session(session)
    method.ask(session.base.get_context())
    tell(method, session, False, 1)
    method.ask(session.base.get_context())
    miner_calls = [
        payload["messages"]
        for system, payload, _ in models.calls
        if system.startswith("You are the Factor Miner Agent")
    ]
    assert len(miner_calls[1]) > len(miner_calls[0])
    assert "platform_evaluation" in miner_calls[1][-1]["content"]
    tell(method, session, False, 2)
    method.ask(session.base.get_context())
    calls = [payload for system, payload, _ in models.calls if "Research Analyst" in system]
    assert len(calls[-1]["history"]["trials"]) == 2
    assert '"test"' not in json.dumps(calls[-1]["history"]).lower()


def test_compressed_history_is_bounded_and_keeps_recent_evidence():
    method = AnalystMinerSearch(42, AnalystMinerConfig(), models=Models())
    method.context = {"metric": "ic"}
    history = {
        "plans": [
            {
                "research_plan_index": i,
                "plan": {"direction": "direction " + "x" * 2000, "hypothesis": "h"},
                "end_reason": "finished",
            }
            for i in range(1000)
        ],
        "trials": [
            {
                "research_plan_index": i,
                "factor_trial_index": i,
                "candidate": {"name": "n", "expression": "x" * 2000},
                "metrics": [
                    {"name": "ic", "split": "train", "value": 0.1},
                    {"name": "ic", "split": "val", "value": 0.2},
                ],
                "accepted": i == 999,
                "reason": "accepted" if i == 999 else "quality_threshold",
                "residual_ic": 0.03,
            }
            for i in range(1000)
        ],
    }
    saved = json.dumps(history)
    compressed = method._compressed_history(history)
    assert len(json.dumps(compressed, ensure_ascii=False)) <= 120000
    assert compressed["trial_count"] == compressed["plan_count"] == 1000
    assert compressed["omitted_trial_count"] == 1000 - len(compressed["trials"])
    assert compressed["omitted_plan_count"] == 1000 - len(compressed["plans"])
    assert compressed["earlier_directions"]
    assert compressed["earlier_directions"][-1][0] == 959
    assert compressed["omitted_direction_count"] == (
        1000 - len(compressed["plans"]) - len(compressed["earlier_directions"])
    )
    assert compressed["outcome_counts"] == {"quality_threshold": 999, "accepted": 1}
    assert compressed["trials"][-1]["factor_trial_index"] == 999
    assert compressed["trials"][-1]["train_ic"] == 0.1
    assert compressed["trials"][-1]["val_ic"] == 0.2
    assert compressed["trials"][-1]["residual_ic"] == 0.03
    assert compressed["trials"][-1]["expression"].endswith(" [truncated]")
    assert json.dumps(history) == saved


def test_resume_pending_analyst_keeps_saved_query_and_compresses_snapshot(tmp_path):
    class Interrupted(Models):
        def _response(self, system, payload):
            if "Research Analyst Agent" in system:
                if len([call for call in self.calls if "Research Analyst Agent" in call[0]]) == 1:
                    return json.dumps({"queries": [{"name": "get_context", "arguments": {}}]}), {}
                raise RuntimeError("model interrupted")
            return super()._response(system, payload)

    session = FixedFeedbackSession(make_session(tmp_path, attempts=3), 3)
    method = AnalystMinerSearch(42, AnalystMinerConfig(), models=Interrupted())
    method.set_session(session)
    with pytest.raises(RuntimeError, match="model interrupted"):
        method.ask(session.base.get_context())
    saved = json.loads(json.dumps(method.dump_state()))
    records = saved["research_plan_state"]["responses"]["analyst"]
    assert len(records) == 1 and "query_results" in records[0]

    models = Models()
    resumed = AnalystMinerSearch(42, AnalystMinerConfig(), models=models)
    resumed.set_session(session)
    resumed.load_state(saved)
    resumed.ask(session.base.get_context())
    analyst = next(payload for system, payload, _ in models.calls if "Research Analyst" in system)
    assert analyst["query_results"] == [records[0]["query_results"]]
    assert analyst["history"]["compression"].startswith("recent structured")
