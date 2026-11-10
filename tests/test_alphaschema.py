import copy
import io
import json
import math
from dataclasses import replace
from datetime import date
from urllib.error import HTTPError

import numpy as np
import polars as pl
import pytest
from test_checkpoint import logical_trials
from test_evaluation_library import evaluator, market_frame
from test_pipeline import project as project

from alpha_atlas.checkpoint import Checkpoint, read_json
from alpha_atlas.config import load_toml
from alpha_atlas.contracts import EvaluationReport
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods.alphaschema import (
    DIAGNOSTICS,
    AlphaSchemaConfig,
    AlphaSchemaSearch,
    Plan,
    RewardEnsemble,
    SchemaSpace,
    plan_features,
    quota_for,
    realization_reward,
    select_batch,
)
from alpha_atlas.methods.models import HTTPModels
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


class FakeModels:
    def __init__(self, expressions=("$x", "$x * $close"), *, query=False, fail=False):
        self.expressions, self.query, self.fail = expressions, query, fail
        self.calls = []

    def chat(self, system, payload):
        self.calls.append(copy.deepcopy((system, payload)))
        if self.fail:
            return "bad json", None
        records = payload["previous_responses"].get(str(payload["repair"]), [])
        if self.query and not records:
            result = {
                "queries": [
                    {"name": "library_stats", "arguments": {}},
                    {"name": "get_context", "arguments": {}},
                ]
            }
        else:
            result = {
                "name": "synthetic",
                "hypothesis": "Synthetic plan realization",
                "realizations": [
                    {"period": p, "expression": self.expressions[i % len(self.expressions)]}
                    for i, p in enumerate(payload["periods"])
                ],
                "queries": [],
            }
        return json.dumps(result), {"prompt_tokens": 5, "completion_tokens": 3}


def config(**options):
    return AlphaSchemaConfig(
        **{
            "candidate_pool_size": 40,
            "batch_size": 4,
            "periods": (2, 3),
            "ensemble_size": 2,
            "schedule": (
                {
                    "min_observations": 0,
                    "max_observations": 4,
                    "explore": 4,
                    "exploit": 0,
                    "mutate": 0,
                },
                {
                    "min_observations": 4,
                    "max_observations": None,
                    "explore": 1,
                    "exploit": 2,
                    "mutate": 1,
                },
            ),
            **options,
        }
    )


def session(tmp_path, attempts=16):
    data = market_frame().with_columns(pl.Series("x", np.random.default_rng(4).normal(size=120)))
    service = evaluator(data, diagnostics=DIAGNOSTICS)
    rules = {
        "min_abs_val_ic": 0.01,
        "min_coverage": 0.8,
        "max_abs_corr": 0.99,
        "min_corr_overlap": 5,
        "reference_library": "",
    }
    return SearchSession(
        service,
        FactorLibrary(rules),
        RunStore(tmp_path),
        attempts,
        run_spec={"asset": "ashare", "rules": rules},
    )


def search_for(current, models=None, options=None):
    method = AlphaSchemaSearch(42, options or config(), models=models or FakeModels())
    method.set_session(current)
    return method


def step(method, current):
    candidate = method.ask(current.get_context())[0]
    feedback = current.evaluate(candidate)
    method.tell([feedback])
    return feedback


def test_author_vocabulary_sampling_feature_map_and_quota_boundaries():
    space = SchemaSpace()
    assert {key: len(ids) for key, ids in space.ids.items()} == {
        "event": 54,
        "context": 43,
        "quality": 59,
        "direction": 3,
        "output": 8,
    }
    options = AlphaSchemaConfig()
    first = space.sample(100, 42, options.quality_count_weights, set())
    assert first == space.sample(100, 42, options.quality_count_weights, set())
    assert len({p.key for p in first}) == 100
    second = space.sample(100, 42, options.quality_count_weights, {p.key for p in first})
    assert not {p.key for p in first} & {p.key for p in second}
    for plan in first:
        assert len(set(plan.qualities)) == len(plan.qualities) <= 3
        assert Plan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan
    plan = Plan("e", "c", ("q1", "q2"), "d", "o")
    features = plan_features(plan)
    assert features == {
        "event=e": 1,
        "context=c": 1,
        "direction=d": 1,
        "output=o": 1,
        "event_context=e|c": 1,
        "event_output=e|o": 1,
        "event_direction=e|d": 1,
        "quality_count": 2,
        "quality=q1": 1,
        "event_quality=e|q1": 1,
        "context_quality=c|q1": 1,
        "quality=q2": 1,
        "event_quality=e|q2": 1,
        "context_quality=c|q2": 1,
    }
    reverse = replace(plan, qualities=("q2", "q1"))
    assert plan.key != reverse.key and features == plan_features(reverse)
    assert [
        quota_for(n, options.schedule)["explore"] for n in (0, 149, 150, 299, 300, 699, 700)
    ] == [16, 16, 8, 8, 5, 5, 3]


def test_selector_hand_computed_novelty_prediction_mean_ties_and_local_mutation():
    space = SchemaSpace()
    sampled = space.sample(8, 42, {"1": 1}, set())
    parent = sampled[0]
    same = replace(parent, output=next(o for o in space.ids["output"] if o != parent.output))
    fresh = sampled[1:4]
    history = [parent]
    observations = [(parent, -2.0)]

    class Model:
        def predict(self, plans):
            return [(10.0 if p == fresh[1] else 0.0, 999.0 if p == same else 0.0) for p in plans]

    chosen = select_batch(
        [same, *fresh],
        history,
        observations,
        {"explore": 1, "exploit": 1, "mutate": 1},
        Model(),
        space,
        42,
        {"replace_output": 1},
    )
    assert chosen[0]["plan"] == fresh[0].to_dict()  # Stable sampled order breaks novelty ties.
    assert chosen[0]["novelty"] == 1
    assert chosen[1]["plan"] == fresh[1].to_dict()  # Std is recorded, never used as UCB.
    assert chosen[1]["prediction_rank"] == 1
    mutation = chosen[2]
    assert mutation["parent_reward"] == -2.0  # Negative observations remain valid parents.
    assert mutation["novelty"] == pytest.approx(1 / math.sqrt(2))
    assert mutation["mutation_operation"] == "replace_output"
    assert list(mutation["changed_components"]) == ["output"]
    assert len({Plan.from_dict(row["plan"]).key for row in chosen}) == 3
    with pytest.raises(ValueError, match="fitted reward"):
        select_batch(
            fresh,
            history,
            observations,
            {"explore": 0, "exploit": 1, "mutate": 0},
            None,
            space,
            42,
            {"replace_output": 1},
        )


@pytest.mark.parametrize("operation", list(AlphaSchemaConfig().mutation_weights))
def test_each_mutation_is_local_and_preserves_valid_components(operation):
    space = SchemaSpace()
    parent = space.sample(1, 42, {"1": 1}, set())[0]
    child, actual = space.mutate(parent, 19, {operation: 1})
    assert child != parent and actual == operation
    changed = [key for key in parent.to_dict() if parent.to_dict()[key] != child.to_dict()[key]]
    assert len(changed) == 1 and len(set(child.qualities)) == len(child.qualities) <= 3
    space.expand(child)


def test_real_lightgbm_ensemble_learns_semantic_rewards_and_is_reconstructible():
    observations = [(Plan(f"e{i % 2}", f"c{i}", (), "d", "o"), float(i % 2)) for i in range(60)]
    options = config()
    model = RewardEnsemble(options, 42).fit(observations)
    probes = [Plan("e0", "unseen", (), "d", "o"), Plan("e1", "unseen", (), "d", "o")]
    predictions = model.predict(probes)
    assert predictions[0][0] < 0.1 and predictions[1][0] > 0.9
    assert all(math.isfinite(mean) and std >= 0 for mean, std in predictions)
    assert np.asarray(predictions) == pytest.approx(
        np.asarray(RewardEnsemble(options, 42).fit(observations).predict(probes))
    )
    assert model.predict([]) == []


def report(**overrides):
    diagnostics = {
        "version": DIAGNOSTICS,
        "split": "train",
        "rank_ic": 0.03,
        "rank_icir": 0.4,
        "lag1_rank_ic": 0.01,
        **overrides,
    }
    return EvaluationReport("factor", (), -1, 1, 0, diagnostics=diagnostics)


def test_signed_reward_hand_calculation_and_missing_core_metrics():
    score = realization_reward(report(), config())
    assert score == pytest.approx(
        {"reward": 0.66, "rank_ic_term": 0.3, "rank_icir_term": 0.4, "lag1_penalty": -0.04}
    )
    assert realization_reward(report(rank_ic=-0.03, rank_icir=-0.4), config())[
        "reward"
    ] == pytest.approx(-0.7)
    assert realization_reward(report(lag1_rank_ic=0.05), config())["reward"] == pytest.approx(0.7)
    for override in (
        {"rank_icir": None},
        {"lag1_rank_ic": float("nan")},
        {"rank_ic": True},
        {"split": "val"},
        {"version": "icir_turnover_v1"},
    ):
        assert realization_reward(report(**override), config()) is None
    assert realization_reward(None, config()) is None


def test_full_cold_and_learned_rounds_count_both_realizations_and_keep_duplicates(tmp_path):
    current = session(tmp_path)
    models = FakeModels(query=True)
    method = search_for(current, models)
    for i in range(16):
        feedback = step(method, current)
        assert feedback.factor_trial_index == i + 1
        assert current.attempts_used == i + 1
    assert len(method.history) == 8 and method.work is None
    assert [r["selection_reason"] for r in method.history[:4]] == ["explore"] * 4
    assert [r["selection_reason"] for r in method.history[4:]] == [
        "explore",
        "exploit",
        "exploit",
        "mutation",
    ]
    assert len(models.calls) == method.usage["chat_requests"] == 16
    assert method.usage["prompt_tokens"] == 80 and method.usage["unknown_usage_requests"] == 0
    assert len({r["results"][0]["expression_id"] for r in method.history}) == 1
    assert all(len(r["results"]) == 2 for r in method.history)
    assert method.history[0]["reward"] == max(
        r["score"]["reward"] for r in method.history[0]["results"]
    )
    for _, payload in models.calls:
        assert payload["context"] == current.query("get_context", {})
        rendered = json.dumps(payload)
        for key in ("remaining_attempts", "snapshot_id", "run_id", '"fold"', '"universe"'):
            assert key not in rendered


def test_budget_tail_records_incomplete_plan_without_training_on_one_period(tmp_path):
    current = session(tmp_path, attempts=3)
    method = search_for(current)
    for _ in range(3):
        step(method, current)
    assert [r["status"] for r in method.history] == ["measured", "budget_partial"]
    assert method.history[1]["reward"] is None and len(method.history[1]["results"]) == 1
    assert len(method.models.calls) == 2 and current.attempts_used == 3


@pytest.mark.parametrize("kind", ["json", "dsl", "missing"])
def test_failed_generation_is_bounded_charged_and_never_fabricates_zero_reward(tmp_path, kind):
    current = session(tmp_path, attempts=4)
    models = FakeModels(expressions=("$not_allowed",), fail=kind == "json")
    if kind == "missing":
        models.chat = lambda *args: ('{"error":"required industry field is absent"}', None)
    method = search_for(current, models)
    feedback = step(method, current)
    assert feedback.report.status == "compile_error" and current.attempts_used == 1
    assert method.usage["chat_requests"] == 2
    if kind == "dsl":
        step(method, current)  # Each supplied invalid expression still costs a candidate.
        assert method.usage["chat_requests"] == 2
    assert method.history[0]["reward"] is None and method.history[0]["status"] == "failed"
    assert method.history[0]["validation"]


def test_read_only_queries_and_syntax_repair_do_not_submit_hidden_evaluations(tmp_path):
    class Repair(FakeModels):
        def chat(self, system, payload):
            self.expressions = ("$target",) if payload["repair"] == 0 else ("$x", "$close")
            return super().chat(system, payload)

    current = session(tmp_path)
    method = search_for(current, Repair(query=True))
    candidate = method.ask(current.get_context())[0]
    assert candidate.expression == "$x"
    assert current.attempts_used == 0 and current._store.trials() == []
    assert len(method.models.calls) == 4
    assert method.work["validation"]["0"]["2"]["error"]
    assert method.work["validation"]["1"]["2"]["error"] is None


def test_validation_rejection_does_not_change_training_reward(tmp_path):
    current = session(tmp_path, attempts=2)
    current._library._rules["min_abs_val_ic"] = 2
    method = search_for(current)
    assert not step(method, current).accepted
    assert not step(method, current).accepted
    assert method.history[0]["status"] == "measured"
    assert method.history[0]["reward"] is not None and not current.get_library().members


@pytest.mark.parametrize(
    "boundary",
    ["model_response", "query_result", "realized", "after_commit", "after_tell", "between_periods"],
)
def test_runner_resume_reuses_saved_stages_trials_and_preserves_frozen_oos(
    project, monkeypatch, boundary
):
    options = config()
    models = FakeModels(("$adj_close", "$volume"), query=True)
    method = AlphaSchemaSearch(42, options, models=models)
    with monkeypatch.context() as patch:
        if boundary == "after_commit":
            original = RunStore.record

            def record(self, index, *args, **kwargs):
                original(self, index, *args, **kwargs)
                if index == 2:
                    raise KeyboardInterrupt("injected")

            patch.setattr(RunStore, "record", record)
        elif boundary == "after_tell":
            original = AlphaSchemaSearch.tell

            def tell(self, feedback):
                original(self, feedback)
                if feedback[0].factor_trial_index == 2:
                    raise KeyboardInterrupt("injected")

            patch.setattr(AlphaSchemaSearch, "tell", tell)
        else:
            original = Checkpoint.save

            def save(self, method, *args, **kwargs):
                original(self, method, *args, **kwargs)
                work = method.work or {}
                records = work.get("responses", {}).get("0", [])
                if (
                    (boundary == "model_response" and records)
                    or (boundary == "query_result" and records and "query_results" in records[-1])
                    or (boundary == "realized" and "candidates" in work)
                    or (boundary == "between_periods" and len(work.get("results", [])) == 1)
                ):
                    raise KeyboardInterrupt("injected")

            patch.setattr(Checkpoint, "save", save)
        with pytest.raises(KeyboardInterrupt):
            run(project, "ashare", "fold1", "alphaschema", 42, 5, method_impl=method)
    directory = next((project / "artifacts/runs").iterdir())
    restored_models = FakeModels(("$adj_close", "$volume"), query=True)
    restored = AlphaSchemaSearch(999, options, models=restored_models)
    resume(project, directory, method_impl=restored)
    baseline = run(
        project,
        "ashare",
        "fold1",
        "alphaschema",
        42,
        5,
        method_impl=AlphaSchemaSearch(
            42, options, models=FakeModels(("$adj_close", "$volume"), query=True)
        ),
    )
    assert logical_trials(directory) == logical_trials(baseline)
    left = read_json(directory / "checkpoint.json")["method_state"]
    right = read_json(baseline / "checkpoint.json")["method_state"]
    assert left == right
    assert len(models.calls) + len(restored_models.calls) == right["usage"]["chat_requests"]
    before = (directory / "checkpoint.json").read_bytes()
    evaluate_frozen(project, directory)
    assert (directory / "checkpoint.json").read_bytes() == before


def test_runner_resumes_exhausted_503_at_same_plan_without_replaying_trials(project, monkeypatch):
    options = config(
        chat_model="synthetic",
        chat_base_url="http://synthetic.invalid/v1",
        chat_key_env="ATLAS_TEST_MODEL_KEY",
    )
    requests, waits = [], []
    replies = FakeModels(("$adj_close", "$volume"))

    def request(req, **kwargs):
        body = json.loads(req.data)
        requests.append(body)
        if 2 <= len(requests) <= 5:
            raise HTTPError("private-url", 503, "private-body", {}, io.BytesIO(b"private"))
        text, usage = replies.chat("synthetic", json.loads(body["messages"][-1]["content"]))
        response = {"choices": [{"delta": {"content": text}}], "usage": usage}
        return io.BytesIO(f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n".encode())

    monkeypatch.setenv("ATLAS_TEST_MODEL_KEY", "synthetic-key")
    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    with pytest.raises(RuntimeError, match=r"HTTP 503.*failed after 4 requests"):
        run(
            project,
            "ashare",
            "fold1",
            "alphaschema",
            42,
            4,
            method_impl=AlphaSchemaSearch(42, options, models=HTTPModels(options)),
        )
    directory = next((project / "artifacts/runs").iterdir())
    checkpoint = read_json(directory / "checkpoint.json")
    committed = logical_trials(directory)
    assert read_json(directory / "run.json")["status"] == "failed"
    assert checkpoint["completed"] == checkpoint["attempts"] == len(committed) == 2
    assert checkpoint["pending"] is None
    assert checkpoint["method_state"]["work"]["responses"] == {"0": []}
    assert checkpoint["method_state"]["usage"]["chat_requests"] == 5
    assert checkpoint["method_state"]["usage"]["unknown_usage_requests"] == 4
    assert waits == [30, 60, 120]

    restored = AlphaSchemaSearch(42, options, models=HTTPModels(options))
    resume(project, directory, method_impl=restored)
    assert read_json(directory / "run.json")["status"] == "frozen"
    assert logical_trials(directory)[:2] == committed
    checkpoint = read_json(directory / "checkpoint.json")
    assert checkpoint["completed"] == checkpoint["attempts"] == 4
    assert restored.usage == {
        "chat_requests": 6,
        "unknown_usage_requests": 4,
        "prompt_tokens": 10,
        "completion_tokens": 6,
    }
    assert len(requests) == 6 and all(body == requests[1] for body in requests[2:])


def test_unknown_usage_network_failure_and_json_pending_state(tmp_path):
    current = session(tmp_path, attempts=2)
    method = search_for(current)
    saved = []
    method.set_checkpoint(lambda: saved.append(json.loads(json.dumps(method.dump_state()))))

    def fail(*args):
        raise RuntimeError("chat model connection failed")

    method.models.chat = fail
    with pytest.raises(RuntimeError, match="connection failed"):
        method.ask(current.get_context())
    assert current.attempts_used == 0
    restored = search_for(current)
    restored.load_state(saved[-1])
    step(restored, current)
    assert restored.usage["chat_requests"] == 2 and restored.usage["unknown_usage_requests"] == 1
    candidate = restored.ask(current.get_context())[0]
    pending = search_for(current)
    pending.load_state(json.loads(json.dumps(restored.dump_state())))
    feedback = current.evaluate(candidate)
    restored.tell([feedback])
    pending.tell([feedback])
    assert json.loads(json.dumps(restored.dump_state())) == pending.dump_state()


def test_normal_factory_configuration_freeze_and_shared_preflight(project, monkeypatch):
    monkeypatch.setattr(
        "alpha_atlas.methods.alphaschema.HTTPModels",
        lambda options: FakeModels(("$adj_close", "$volume")),
    )
    directory = run(project, "ashare", "fold1", "alphaschema", 42, 2)
    spec = read_json(directory / "run.json")
    assert spec["implementation"] == "alphaschema_atlas_v1" and spec["resumable"]
    assert spec["method_config"]["periods"] == [20, 80]
    report_text = (directory / "report.md").read_text()
    assert "AlphaSchema 计划搜索" in report_text and "滞后一 Bar RankIC" in report_text
    evaluate_frozen(project, directory)
    config_path = project / "configs/alphaschema.toml"
    config_path.write_text(
        config_path.read_text().replace("lag1_penalty = 2.0", "lag1_penalty = 3.0")
    )
    with pytest.raises(ValueError, match="AlphaSchema configuration changed"):
        evaluate_frozen(project, directory)
    benchmark = project / "configs/benchmark.toml"
    benchmark.write_text(benchmark.read_text().replace(DIAGNOSTICS, "icir_turnover_v1"))
    before = list((project / "artifacts/runs").iterdir())
    with pytest.raises(ValueError, match="requires shared"):
        run(project, "ashare", "fold1", "alphaschema", 42, 2)
    assert list((project / "artifacts/runs").iterdir()) == before
    # Evaluation-only prefixes have no historical method checkpoint; do not invent zeros.
    from alpha_atlas.reporting import write_report

    (directory / "checkpoint.json").unlink()
    assert "AlphaSchema 计划搜索" not in write_report(directory).read_text()


def test_config_rejects_uncovered_schedules_credentials_and_invalid_budgets(project):
    loaded = AlphaSchemaConfig.from_mapping(load_toml(project / "configs/alphaschema.toml"))
    assert loaded.schedule[-1]["max_observations"] is None
    assert AlphaSchemaConfig.from_mapping(json.loads(json.dumps(loaded.__dict__))) == loaded
    for options in (
        {"periods": (1, 1)},
        {"periods": (True, 2)},
        {"batch_size": 0},
        {"candidate_pool_size": 1},
        {"bootstrap_fraction": 0},
        {"temperature": float("nan")},
        {"mutation_weights": {"invalid": 1}},
        {"quality_count_weights": {"4": 1}},
        {"max_repairs": -1},
        {"schedule": ()},
        {"schedule": AlphaSchemaConfig().schedule[:-1]},
        {"chat_base_url": "https://user:secret@example.org"},
    ):
        with pytest.raises(ValueError):
            AlphaSchemaConfig(**options)


def test_validation_change_cannot_change_alpha_schema_reward(tmp_path):
    current = session(tmp_path)
    original = current._evaluator
    altered = original.features.with_columns(
        pl.when(pl.col("trading_day") > date(2020, 1, 10))
        .then(1e9)
        .otherwise(pl.col("x"))
        .alias("x")
    )
    from alpha_atlas.contracts import Candidate

    left = original.evaluate(Candidate("$x"))
    right = evaluator(altered, diagnostics=DIAGNOSTICS).evaluate(Candidate("$x"))
    assert left.diagnostics == right.diagnostics
    assert realization_reward(left, config()) == realization_reward(right, config())
