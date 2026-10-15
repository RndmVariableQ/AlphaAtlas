import json
import logging
import math
from dataclasses import asdict
from pathlib import Path

import pytest
from test_mcts_llm import session as make_session
from test_pipeline import project as project

from alpha_atlas.annealing import acceptance_probability, temperature
from alpha_atlas.atlas import FactorAtlas
from alpha_atlas.checkpoint import read_json
from alpha_atlas.config import load_toml
from alpha_atlas.contracts import Candidate, EvaluationReport, TrialFeedback
from alpha_atlas.methods.annealing import AnnealingLLMConfig, AnnealingLLMSearch
from alpha_atlas.methods.baselines import default_regions
from alpha_atlas.methods.mcts_llm import MCTSLLMConfig
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen


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

    def evaluation_result(self, feedback):
        return {
            "candidate": asdict(feedback.candidate),
            "accepted": feedback.accepted,
            "reason": feedback.reason,
            "trial_index": feedback.trial_index,
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
    candidate = Candidate(**method.pending)
    feedback = TrialFeedback(
        candidate,
        EvaluationReport(None, (), None, None, 0.0),
        accepted,
        "accepted" if accepted else "quality_threshold",
        trial_index=index,
    )
    method.tell([feedback])
    session.remaining -= 1
    return feedback


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


def test_config_uses_shared_deepseek_settings_without_mcts_tree_controls():
    root = Path(__file__).resolve().parents[1]
    annealing = load_toml(root / "configs/annealing.toml")
    mcts = load_toml(root / "configs/mcts_llm.toml")
    shared = (
        "chat_model",
        "chat_base_url",
        "chat_key_env",
        "temperature",
        "repair_temperature",
        "timeout_seconds",
        "max_formula_repairs",
        "max_dialogue_rounds",
    )
    assert {key: annealing[key] for key in shared} == {key: mcts[key] for key in shared}
    assert "tree_budget" not in annealing and "exploration" not in annealing
    assert AnnealingLLMConfig.from_mapping(annealing).candidates_per_plan == 5
    assert AnnealingLLMConfig.from_mapping(annealing).analyst_feedback_top_k == 5
    assert MCTSLLMConfig.from_mapping(mcts).chat_model == annealing["chat_model"]


def test_three_agents_run_in_order_and_plan_spans_candidate_budget(tmp_path, caplog):
    models = AgentModels(expressions=["$x", "TS_MEAN($x, 3)"])
    method, current = setup_method(tmp_path, attempts=4, models=models)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        first = method.ask(current.base.get_context())[0]
        assert first.region_ids == ("mean",)
        tell(method, current, True, 1)
        method.ask(current.base.get_context())
        tell(method, current, True, 2)
    assert method.round_state is None
    assert "Explorer │ 方向=study mean reversion" in caplog.text
    assert "Analyst │ 假设=Short-term price reversal predicts the next period" in caplog.text
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
    assert method.steps[-1]["annealing_result"]["reason"] == "initialize_or_continue_region"


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
    method.round_index = 1
    metric_name = method.context["metric"]

    def prior_step(index, accepted, coverage, val_ic, round_index=1):
        return {
            "round_index": round_index,
            "trial_index": index,
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

    method.steps = [
        prior_step(0, True, 1.0, 0.9, round_index=0),
        prior_step(1, True, 0.9, 0.04),
        prior_step(2, True, 0.95, -0.08),
        prior_step(3, False, 0.99, 0.20),
        prior_step(4, True, 0.79, 0.07),
        prior_step(5, True, 0.85, 0.03),
    ]

    method.ask(current.base.get_context())
    analyst_call = next(call for call in models.calls if "Research Analyst Agent" in call[0])
    summary = analyst_call[1]["previous_round_results"]
    assert summary["attempted_candidates"] == 5
    assert summary["qualified_candidates"] == 3
    assert [item["trial_index"] for item in summary["top_factors"]] == [2, 1]
    assert [item["absolute_primary_val_ic"] for item in summary["top_factors"]] == [0.08, 0.04]


def test_logged_agent_direction_is_one_sentence_and_bounded():
    from alpha_atlas.methods.annealing import _one_sentence

    assert _one_sentence("先研究反转。然后研究波动。") == "先研究反转。"
    assert len(_one_sentence("x" * 130)) == 120


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
    assert method.steps[-1]["annealing_result"]["reason"] == "metropolis_reject"

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
        if feedback[0].trial_index == 2:
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
    assert left["round_index"] == right["round_index"]
    assert left["steps"] == right["steps"]
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
