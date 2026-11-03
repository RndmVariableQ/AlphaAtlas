import copy
import json
import logging
import math
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_mcts_llm import session as make_session
from test_pipeline import project as project

from alpha_atlas.atlas import FactorAtlas
from alpha_atlas.checkpoint import read_json
from alpha_atlas.config import load_toml
from alpha_atlas.contracts import EvaluationReport, Metric, TrialFeedback
from alpha_atlas.methods.annealing import (
    AnnealingLLMConfig,
    AnnealingLLMSearch,
    acceptance_probability,
    temperature,
)
from alpha_atlas.methods.baselines import default_regions
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.storage import candidate_from_dict


class AgentModels:
    def __init__(self, *, regions=None, expressions=None):
        self.regions = list(regions or [{"region_id": "mean", "direction": "study mean reversion"}])
        self.expressions = list(expressions or ["$x"])
        self.calls = []

    def chat(self, system, payload, *, temperature=None):
        self.calls.append((system, payload, temperature))
        return self._response(system, payload)

    def chat_messages(self, messages, *, max_output_tokens=None, on_delta=None, temperature=None):
        system = messages[0]["content"]
        payload = {"messages": json.loads(json.dumps(messages))}
        self.calls.append((system, payload, temperature))
        result = self._response(system, payload)
        if on_delta is not None:
            on_delta("content", result[0])
        return result

    def _response(self, system, payload):
        if "Atlas Explorer Agent" in system:
            result = (
                self.regions.pop(0)
                if self.regions
                else {
                    "region_id": "mean",
                    "direction": "study mean reversion",
                }
            )
        elif "Research Analyst Agent" in system:
            result = {
                "hypothesis": "Short-term price reversal predicts the next period",
                "allowed_fields": payload["research_context"]["fields"],
                "measurement_plan": "Use rolling deviations from recent prices",
                "miner_instruction": "Build one reversal expression using x",
            }
        elif "Factor Miner Agent" in system:
            result = {"expression": self.expressions.pop(0) if self.expressions else "$x"}
        else:
            raise AssertionError("unknown agent role")
        return json.dumps(result), {"prompt_tokens": 5, "completion_tokens": 3}


class FixedFeedbackSession:
    def __init__(self, base, remaining=8):
        self.base = base
        self.remaining = remaining

    def query(self, name, arguments):
        return self.base.query(name, arguments)

    def validate_expression(self, expression):
        return self.base.validate_expression(expression)

    def get_trial(self, factor_trial_index):
        return self.base.get_trial(factor_trial_index)

    def evaluation_result(self, feedback):
        return {
            "candidate": asdict(feedback.candidate),
            "accepted": feedback.accepted,
            "reason": feedback.reason,
            "factor_trial_index": feedback.factor_trial_index,
        }

    def remaining_budget(self):
        return self.remaining


def setup_method(tmp_path, *, attempts=8, config=None, models=None, session=None):
    base = session or make_session(tmp_path, attempts=attempts)
    fixed = FixedFeedbackSession(base, attempts)
    atlas = FactorAtlas()
    for region in default_regions():
        atlas.register_hypothesis(region)
    method = AnnealingLLMSearch(
        42,
        config or AnnealingLLMConfig(candidates_per_plan=2),
        models=models or AgentModels(),
    )
    method.set_session(fixed)
    method.set_atlas_context(atlas.to_dict)
    return method, fixed


def tell(method, session, accepted, index):
    candidate = candidate_from_dict(method.pending)
    feedback = TrialFeedback(
        candidate,
        EvaluationReport(None, (), None, None, 0.0),
        accepted,
        "accepted" if accepted else "quality_threshold",
        factor_trial_index=index,
    )
    session.base._store.record(index, feedback, 0, 0, 0, feedback.library_version)
    method.tell([feedback])
    session.remaining -= 1
    return feedback


class BlockedModels(AgentModels):
    def __init__(self, stage="analyst", block_at=1):
        super().__init__(
            regions=[
                {"region_id": "mean", "direction": "Compare target horizons"},
                {"region_id": "std", "direction": "Test volatility as a feature"},
            ]
        )
        self.stage, self.block_at, self.stage_calls = stage, block_at, 0

    def _response(self, system, payload):
        role = "analyst" if "Research Analyst Agent" in system else "miner"
        if "Atlas Explorer Agent" not in system and role == self.stage:
            self.stage_calls += 1
            if self.stage_calls == self.block_at:
                return json.dumps(
                    {"blocked": "Separate target-horizon evaluations unavailable"}
                ), {}
        return super()._response(system, payload)


@pytest.mark.parametrize("stage", ["analyst", "miner"])
def test_blocked_plan_changes_question_without_a_trial(tmp_path, stage):
    models = BlockedModels(stage)
    method, current = setup_method(tmp_path, attempts=3, models=models)
    candidate = method.ask(current.base.get_context())[0]
    assert candidate.region_ids == ("std",)
    assert method.completed_attempts == 0 and current.remaining == 3
    assert method.completed_research_plan_count == 1
    assert method.factor_trial_records == [] and current.base._store.trials() == []
    blocked = method.blocked_plans[0]
    assert blocked["plan"]["direction"] == "Compare target horizons"
    assert "horizon" in blocked["plan"]["blocked"]
    explorers = [payload for system, payload, _ in models.calls if "Explorer Agent" in system]
    assert explorers[-1]["atlas"]["blocked_plans"][0]["plan"] == blocked["plan"]
    assert "completed_attempts" not in explorers[-1]["atlas"]["blocked_plans"][0]
    miners = [system for system, _, _ in models.calls if "Factor Miner Agent" in system]
    assert len(miners) == (1 if stage == "analyst" else 2)


def test_miner_block_after_trial_keeps_evidence_and_ends_plan(tmp_path):
    method, current = setup_method(tmp_path, models=BlockedModels("miner", block_at=2))
    method.ask(current.base.get_context())
    tell(method, current, False, 1)
    record = copy.deepcopy(method.factor_trial_records[0])
    next_candidate = method.ask(current.base.get_context())[0]
    assert next_candidate.region_ids == ("std",)
    assert method.factor_trial_records == [record]
    assert method.completed_attempts == 1 and len(current.base._store.trials()) == 1
    assert method.region_scores["mean"] == {"attempts": 1, "accepted": 0}
    assert method.active_region_id is None


@pytest.mark.parametrize("cut", ["response", "plan", "archived"])
def test_blocked_plan_recovery_reuses_reply_and_does_not_archive_twice(tmp_path, cut):
    models = BlockedModels()
    method, current = setup_method(tmp_path, models=models)
    saved = None

    def interrupt():
        nonlocal saved
        state = method.dump_state()
        active = state["research_plan_state"]
        records = active["responses"]["analyst"] if active else []
        ready = (
            bool(records and "blocked" in records[-1]["text"] and "result" not in records[-1])
            if cut == "response"
            else bool(active and active["plan"] and "blocked" in active["plan"])
            if cut == "plan"
            else bool(state["blocked_plans"])
        )
        if ready:
            saved = json.loads(json.dumps(state))
            raise KeyboardInterrupt

    method.set_checkpoint(interrupt)
    with pytest.raises(KeyboardInterrupt):
        method.ask(current.base.get_context())
    restored, _ = setup_method(tmp_path / "restored", models=models)
    restored.set_session(current)
    restored.load_state(saved)
    assert restored.ask(current.base.get_context())[0].region_ids == ("std",)
    assert len(restored.blocked_plans) == 1 and restored.completed_attempts == 0
    assert models.stage_calls == 2  # One blocked Analyst, one executable Analyst.


def test_repeated_blocking_is_bounded_across_recovery(tmp_path):
    class AlwaysBlocked(AgentModels):
        def _response(self, system, payload):
            if "Research Analyst Agent" in system:
                return '{"blocked":"Missing far-leg field"}', {}
            return super()._response(system, payload)

    config = AnnealingLLMConfig(max_dialogue_rounds=2)
    method, current = setup_method(tmp_path, models=AlwaysBlocked(), config=config)
    with pytest.raises(ValueError, match="blocked plan limit"):
        method.ask(current.base.get_context())
    assert len(method.blocked_plans) == 2 and method.completed_attempts == 0
    restored, _ = setup_method(tmp_path / "restored", models=AlwaysBlocked(), config=config)
    restored.set_session(current)
    restored.load_state(json.loads(json.dumps(method.dump_state())))
    with pytest.raises(ValueError, match="blocked plan limit"):
        restored.ask(current.base.get_context())
    assert restored.models.calls == []


@pytest.mark.parametrize(
    "raw", [{"blocked": ""}, {"blocked": True}, {"blocked": "x", "expression": "$x"}]
)
def test_invalid_blocked_response_is_not_a_blocked_plan(raw):
    with pytest.raises(ValueError, match="specific missing capability"):
        AnnealingLLMSearch._blocked_reason(raw)


def commit_records(current, records):
    """Commit synthetic measurements separately from their lean method-side records."""
    result = []
    for record in records:
        evaluation = record["evaluation"]
        report = EvaluationReport(
            evaluation.get("factor_id"),
            tuple(
                Metric(**{"n_obs": 100, "aggregation": "synthetic", **metric})
                for metric in evaluation.get("metrics", [])
            ),
            evaluation.get("direction"),
            evaluation.get("coverage"),
            0.0,
            status=evaluation["status"],
            failure_reason=evaluation.get("failure_reason"),
        )
        feedback = TrialFeedback(
            candidate_from_dict(record["candidate"]),
            report,
            record["accepted"],
            evaluation.get("reason", "accepted" if record["accepted"] else "quality_threshold"),
            factor_trial_index=record["factor_trial_index"],
        )
        current.base._store.record(feedback.factor_trial_index, feedback, 0, 0, 0, 0)
        result.append(
            {
                key: value
                for key, value in record.items()
                if key not in {"candidate", "evaluation", "accepted"}
            }
        )
    return result


def test_schedule_and_region_metropolis_probability():
    assert temperature(0, 0.25, 0.02) == pytest.approx(0.25)
    assert temperature(0.5, 0.25, 0.02) == pytest.approx(math.sqrt(0.25 * 0.02))
    assert temperature(1, 0.25, 0.02) == pytest.approx(0.02)
    assert acceptance_probability(0.75, 0.875, 0.25) == 1
    assert acceptance_probability(0.75, 0.625, 0.25) == pytest.approx(math.exp(-0.5))
    assert acceptance_probability(0.75, 0.625, 0.02) == pytest.approx(math.exp(-6.25))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_temperature": float("inf")},
        {"final_temperature": 0},
        {"final_temperature": 1},
        {"initial_temperature": True},
        {"candidates_per_plan": 0},
        {"analyst_feedback_top_k": 0},
    ],
)
def test_invalid_region_annealing_config_fails(kwargs):
    with pytest.raises(ValueError):
        AnnealingLLMConfig(**kwargs)


def test_config_uses_shared_generation_settings_without_mcts_tree_controls():
    root = Path(__file__).resolve().parents[1]
    annealing = load_toml(root / "configs/annealing.toml")
    mcts = load_toml(root / "configs/mcts_llm.toml")
    # Service timeouts depend on the backend, independently of generation settings.
    shared = (
        "temperature",
        "repair_temperature",
        "max_formula_repairs",
        "max_dialogue_rounds",
    )
    assert {key: annealing[key] for key in shared} == {key: mcts[key] for key in shared}
    assert "tree_budget" not in annealing and "exploration" not in annealing
    assert AnnealingLLMConfig.from_mapping(annealing).candidates_per_plan == 5
    assert AnnealingLLMConfig.from_mapping(annealing).analyst_feedback_top_k == 5


def test_structured_miner_instructions_preserve_content_and_upstream_constraints():
    instructions = {
        "instructions": "用价差验证反转",
        "candidates": [{"expression": "$x", "window": 10}],
        "rules": ["Use only allowed fields", "Do not change the direction"],
    }
    raw = {
        "hypothesis": "Short-term reversal",
        "allowed_fields": ["x"],
        "measurement_plan": "Compare rolling deviations",
        "miner_instruction": instructions,
        "region_id": "invented",
        "direction": "invented",
    }
    exploration = {"region_id": "mean", "direction": "study mean reversion"}
    plan = AnnealingLLMSearch._validate_plan(raw, exploration, {"fields": ["x"]})
    assert json.loads(plan["miner_instruction"]) == instructions
    assert raw["miner_instruction"] is instructions
    assert plan["region_id"] == "mean" and plan["direction"] == exploration["direction"]
    with pytest.raises(ValueError, match="fields must be a nonempty subset"):
        AnnealingLLMSearch._validate_plan({**raw, "allowed_fields": ["missing"]}, exploration, {})


@pytest.mark.parametrize("instruction", [None, "", "  ", {}, [], ["Use x"], 0, False])
def test_miner_instruction_compatibility_rejects_empty_or_unsupported_types(instruction):
    with pytest.raises(ValueError, match="miner_instruction must be nonempty"):
        AnnealingLLMSearch._validate_plan(
            {"hypothesis": "reversal", "allowed_fields": ["x"], "miner_instruction": instruction},
            {"region_id": "mean", "direction": "study mean reversion"},
            {"fields": ["x"]},
        )


def test_three_agents_run_in_order_and_plan_spans_candidate_budget(tmp_path, caplog):
    models = AgentModels(expressions=["$x", "TS_MEAN($x, 3)"])
    method, current = setup_method(tmp_path, attempts=4, models=models)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        first = method.ask(current.base.get_context())[0]
        assert first.region_ids == ("mean",)
        tell(method, current, True, 1)
        method.ask(current.base.get_context())
        tell(method, current, True, 2)
    assert method.research_plan_state is None
    assert "[计划 #1 · Explorer] 方向已提交\n    方向: study mean reversion" in caplog.text
    assert (
        "[计划 #1 · Analyst] 计划已提交\n    假设: Short-term price reversal predicts the next period"
        in caplog.text
    )
    roles = [
        "explorer"
        if "Atlas Explorer Agent" in call[0]
        else "analyst"
        if "Research Analyst Agent" in call[0]
        else "miner"
        for call in models.calls
    ]
    assert roles == ["explorer", "analyst", "miner", "miner"]
    assert method.active_region_id == "mean"
    assert method.region_scores["mean"] == {"accepted": 2, "attempts": 2}
    assert (
        method.factor_trial_records[-1]["annealing_result"]["reason"]
        == "initialize_or_continue_region"
    )


def test_miner_preserves_allowed_adjusted_timeframes_and_rejects_other_base_fields(tmp_path):
    base = make_session(tmp_path)
    base._context = replace(base._context, frequency="5m", panel="product")
    base._fields = ("adj_close", "adj_vwap", "volume", "amount")
    expression = "TS_MEAN($adj_close@30m / $adj_vwap@30m - 1,20)"
    models = AgentModels(expressions=[expression])
    method, current = setup_method(tmp_path, models=models, session=base)

    candidate = method.ask(current.base.get_context())[0]
    assert candidate.expression == expression
    miner = next(call[0] for call in models.calls if "Factor Miner Agent" in call[0])
    assert "$adj_close@30m / $adj_vwap@30m - 1" in miner
    assert "adj_close_zscore_15m_64" in miner and "$close@" not in miner
    assert not current.base.attempts_used
    method.research_plan_state["plan"]["allowed_fields"] = ["adj_close", "adj_vwap"]
    assert method._validate_miner({"expression": expression})["expression"] == expression
    with pytest.raises(ValueError, match="outside the Analyst plan: volume"):
        method._validate_miner({"expression": "$adj_close@30m + $volume@30m"})
    with pytest.raises(ValueError, match="invalid feature field: adj_close_p1"):
        method._validate_miner({"expression": "$adj_close_p1@30m"})


def test_miner_conversation_includes_all_prior_candidates_and_evaluations(tmp_path):
    models = AgentModels(expressions=["$x", "TS_MEAN($x, 3)"])
    method, current = setup_method(
        tmp_path,
        attempts=4,
        models=models,
        config=AnnealingLLMConfig(candidates_per_plan=3),
    )
    method.ask(current.base.get_context())
    tell(method, current, False, 1)
    method.ask(current.base.get_context())

    miner_calls = [call for call in models.calls if "Factor Miner Agent" in call[0]]
    first_messages = miner_calls[0][1]["messages"]
    second_messages = miner_calls[1][1]["messages"]
    assert len(second_messages) > len(first_messages)
    assert any(
        message["role"] == "assistant" and '"expression": "$x"' in message["content"]
        for message in second_messages
    )
    feedback = [
        json.loads(message["content"])
        for message in second_messages
        if message["role"] == "user" and "platform_evaluation" in message["content"]
    ]
    assert len(feedback) == 1
    assert feedback[0]["platform_evaluation"]["accepted"] is False
    assert feedback[0]["candidate_attempt_limit"] == 3


def test_analyst_receives_top_qualified_factors_from_previous_round(tmp_path):
    models = AgentModels()
    method, current = setup_method(
        tmp_path,
        attempts=8,
        models=models,
        config=AnnealingLLMConfig(analyst_feedback_top_k=2),
    )
    method.context = current.query("get_context", {})
    method.completed_research_plan_count = 1
    metric_name = method.context["metric"]

    def prior_step(index, accepted, coverage, val_ic, research_plan_index=1):
        return {
            "research_plan_index": research_plan_index,
            "factor_trial_index": index,
            "accepted": accepted,
            "candidate": {"expression": f"$x + {index}", "hypothesis": f"factor {index}"},
            "evaluation": {
                "status": "success",
                "coverage": coverage,
                "metrics": [
                    {"name": metric_name, "split": "train", "value": 0.02},
                    {"name": metric_name, "split": "val", "value": val_ic},
                ],
            },
        }

    method.factor_trial_records = commit_records(
        current,
        [
            prior_step(10, True, 1.0, 0.9, research_plan_index=0),
            prior_step(1, True, 0.9, 0.04),
            prior_step(2, True, 0.95, -0.08),
            prior_step(3, False, 0.99, 0.20),
            prior_step(4, True, 0.79, 0.07),
            prior_step(5, True, 0.85, 0.03),
        ],
    )

    method.ask(current.base.get_context())
    analyst_call = next(call for call in models.calls if "Research Analyst Agent" in call[0])
    summary = analyst_call[1]["previous_round_results"]
    assert summary["attempted_candidates"] == 5
    assert summary["qualified_candidates"] == 3
    assert [item["factor_trial_index"] for item in summary["top_factors"]] == [2, 1]
    assert [item["absolute_primary_val_ic"] for item in summary["top_factors"]] == [0.08, 0.04]


def test_logged_agent_direction_is_complete_and_has_agent_and_plan(tmp_path, caplog):
    direction = "研究反转信号与成交量变化的关系。" * 12
    models = AgentModels(regions=[{"region_id": "mean", "direction": direction}])
    method, current = setup_method(tmp_path, models=models)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        method.ask(current.base.get_context())
    assert "[计划 #1 · Explorer] 方向已提交" in caplog.text
    message = next(
        record.getMessage() for record in caplog.records if "方向已提交" in record.getMessage()
    )
    assert direction in "".join(line.strip() for line in message.splitlines())


def test_explorer_region_is_registered_and_metropolis_can_reject_it(tmp_path, monkeypatch):
    regions = [
        {"region_id": "mean", "direction": "study the existing mean-reversion region"},
        {
            "new_region": {
                "hypothesis": "volume shock predicts delayed reversal",
                "continent_ids": ["price_volume"],
            },
            "direction": "test delayed reversal after volume shocks",
        },
    ]
    method, current = setup_method(
        tmp_path,
        attempts=4,
        models=AgentModels(regions=regions),
        config=AnnealingLLMConfig(candidates_per_plan=1, initial_temperature=0.25),
    )
    method.ask(current.base.get_context())
    tell(method, current, True, 1)
    assert method.active_region_id == "mean"
    second = method.ask(current.base.get_context())[0]
    new_region_id = second.region_ids[0]
    assert new_region_id.startswith("explorer-")
    assert new_region_id in {value["id"] for value in method.atlas_regions()}
    monkeypatch.setattr("alpha_atlas.methods.annealing.acceptance_probability", lambda *_: 0.0)
    tell(method, current, False, 2)
    assert method.active_region_id == "mean"
    assert method.factor_trial_records[-1]["annealing_result"]["reason"] == "metropolis_reject"

    restored = AnnealingLLMSearch(999, method.config, models=AgentModels())
    restored.load_state(json.loads(json.dumps(method.dump_state())))
    assert restored.active_region_id == "mean"
    assert restored.atlas_regions() == method.atlas_regions()


def test_native_runner_uses_shared_budget_and_freezes_region_annealing_config(project, monkeypatch):
    monkeypatch.setattr(
        "alpha_atlas.methods.annealing.HTTPModels",
        lambda config: AgentModels(
            regions=[
                {
                    "new_region": {
                        "hypothesis": "volume shocks predict delayed reversals",
                        "continent_ids": ["price_volume"],
                    },
                    "direction": "test delayed reversals after volume shocks",
                }
            ],
            expressions=["$adj_close", "$volume"],
        ),
    )
    directory = run(project, "ashare", "fold1", "annealing", 42, attempts=2)
    spec = read_json(directory / "run.json")
    assert spec["implementation"] == "annealing_llm_atlas_v1"
    assert spec["attempts"] == 2
    assert spec["resumable"]
    assert len(spec["method_config"]) > 0

    config = project / "configs/annealing.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "final_temperature = 0.02", "final_temperature = 0.03"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="annealing configuration changed"):
        evaluate_frozen(project, directory)


def test_runner_resume_restores_three_agent_region_state(project, monkeypatch):
    models = AgentModels(expressions=["$adj_close", "$volume", "$adj_close * $volume"])
    config_path = project / "configs/annealing.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "candidates_per_plan = 5", "candidates_per_plan = 3"
        ),
        encoding="utf-8",
    )
    config = AnnealingLLMConfig.from_mapping(load_toml(config_path))
    original = AnnealingLLMSearch.tell

    def interrupt_after_second(self, feedback):
        original(self, feedback)
        if feedback[0].factor_trial_index == 2:
            raise KeyboardInterrupt("injected")

    with monkeypatch.context() as patch:
        patch.setattr(AnnealingLLMSearch, "tell", interrupt_after_second)
        with pytest.raises(KeyboardInterrupt):
            run(
                project,
                "ashare",
                "fold1",
                "annealing",
                42,
                3,
                method_impl=AnnealingLLMSearch(
                    42,
                    config,
                    models=models,
                ),
            )

    directory = next((project / "artifacts/runs").iterdir())
    resumed_models = AgentModels(expressions=["$adj_close * $volume"])
    restored = AnnealingLLMSearch(999, config, models=resumed_models)
    resume(project, directory, method_impl=restored)
    baseline = run(
        project,
        "ashare",
        "fold1",
        "annealing",
        42,
        3,
        method_impl=AnnealingLLMSearch(
            42,
            config,
            models=AgentModels(expressions=["$adj_close", "$volume", "$adj_close * $volume"]),
        ),
    )
    left = read_json(directory / "checkpoint.json")["method_state"]
    right = read_json(baseline / "checkpoint.json")["method_state"]
    assert left["active_region_id"] == right["active_region_id"]
    assert left["region_scores"] == right["region_scores"]
    assert left["completed_research_plan_count"] == right["completed_research_plan_count"]
    assert left["factor_trial_records"] == right["factor_trial_records"]
    resumed_miner_call = next(
        call for call in resumed_models.calls if "Factor Miner Agent" in call[0]
    )
    conversation = resumed_miner_call[1]["messages"]
    observations = [
        json.loads(message["content"])
        for message in conversation
        if message["role"] == "user" and "platform_evaluation" in message["content"]
    ]
    assert len(observations) == 2
