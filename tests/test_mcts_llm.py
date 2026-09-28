import copy
import json
import math
from dataclasses import replace

import numpy as np
import polars as pl
import pytest
from test_checkpoint import logical_trials
from test_evaluation_library import evaluator, market_frame
from test_pipeline import project as project

from alpha_atlas.checkpoint import Checkpoint, read_json
from alpha_atlas.expressions import parse
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods.mcts_llm import (
    DIMENSIONS,
    MCTSLLMConfig,
    MCTSLLMSearch,
    abstract_tree,
    contains_tree,
    dimension_probabilities,
    frequent_patterns,
    relative_score,
    root_genes,
)
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


class FakeModels:
    def __init__(self, expressions=None, *, invalid_phase=None, query=False):
        self.expressions = expressions or ["$x", "$x * $close", "$x / $close", "$close"]
        self.invalid_phase = invalid_phase
        self.query = query
        self.calls = []
        self.temperatures = []

    def chat(self, system, payload, *, temperature=None):
        self.calls.append(copy.deepcopy((system, payload)))
        self.temperatures.append(temperature)
        phase = payload["phase"]
        if phase == self.invalid_phase:
            return "bad json", None
        if self.query and not payload["previous_stages"].get(phase):
            result = {"queries": [{"name": "get_context", "arguments": {}}]}
        elif phase == "suggestion":
            result = {"suggestion": "Test market mechanism"}
        elif phase == "risk":
            result = {"score": 0.6, "reason": "Test risk assessment"}
        else:
            index = (payload["evaluation_result"] or {}).get("trial_index", 0)
            result = {
                "expression": self.expressions[index % len(self.expressions)],
                "hypothesis": "Test",
            }
        return json.dumps(result), {"prompt_tokens": 5, "completion_tokens": 3}


def session(tmp_path, attempts=10):
    data = market_frame().with_columns(
        (pl.col("x") + pl.Series(np.random.default_rng(42).normal(size=120))).alias("x")
    )
    service = evaluator(data, diagnostics="icir_turnover_v1", retain_training=True)
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


def search_for(session, models=None, **options):
    search = MCTSLLMSearch(42, MCTSLLMConfig(fsa_top_k=0, **options), models=models or FakeModels())
    search.set_session(session)
    return search


def step(search, session):
    candidate = search.ask(session.get_context())[0]
    feedback = session.evaluate(candidate)
    search.tell([feedback])
    return feedback


def test_relative_rank_ties_direction_and_softmax_hand_calculation():
    assert relative_score(0.1, [0.1, 0.2]) == 0.5
    assert relative_score(0.2, [0.1, 0.2]) == 1
    assert relative_score(0.2, [0.1, 0.2], higher=False) == 0.5
    assert relative_score(0.7, []) == 0.5
    assert relative_score(None, [0.2]) is None
    scores = dict.fromkeys(DIMENSIONS, 1.0)
    scores["stability"] = 0
    probabilities = dimension_probabilities(scores, 1)
    assert probabilities[1] == pytest.approx(math.e / (math.e + 4))
    assert sum(dimension_probabilities(scores, 1e-300)) == pytest.approx(1)
    assert dimension_probabilities(scores, 1e-320) == [0, 1, 0, 0, 0]


def test_uct_virtual_action_and_nonleaf_expansion():
    search = MCTSLLMSearch(1, models=FakeModels())
    search.root = "r"
    search.nodes = {
        "r": {"q": 0.8, "visits": 10, "children": ["a", "b"]},
        "a": {"q": 0.8, "visits": 8, "children": []},
        "b": {"q": 0.1, "visits": 1, "children": []},
    }
    assert search.select_node() == "r"  # Q + sqrt(log(10)/3) beats both existing actions.
    search.nodes["b"]["q"] = 0.8
    assert search.select_node() == "b"


def test_refinement_and_risk_receive_full_ancestry_and_siblings():
    search = MCTSLLMSearch(1, models=FakeModels())
    for identity, parent, children in (
        ("root", None, ["a"]),
        ("a", "root", ["b", "sibling"]),
        ("b", "a", []),
        ("sibling", "a", []),
    ):
        search.nodes[identity] = {
            "parent": parent,
            "children": children,
            "candidate": {},
            "scores": {},
            "raw": {},
            "refinement": identity,
        }
    history = search._history("b")
    assert [n["refinement"] for n in history["ancestors"]] == ["root", "a"]
    assert [n["refinement"] for n in history["siblings"]] == ["sibling"]
    assert history["selected"]["refinement"] == "b"


def test_fsa_parameter_abstraction_closed_structure_and_per_formula_support():
    a = root_genes(parse("TS_MEAN($x,5) + TS_MEAN($x,10)"))
    b = root_genes(parse("TS_MEAN($x,20) + TS_MEAN($x,30)"))
    assert a == b and len(a) == 2
    counts = {key: 2 for key in a}
    patterns = frequent_patterns(counts, 3)
    assert len(patterns) == 1 and patterns[0][0] == "ADD"
    assert contains_tree(abstract_tree(parse("TS_MEAN($x,3) + TS_MEAN($x,4)")), patterns[0])
    assert not contains_tree(abstract_tree(parse("TS_MEAN($xx,3) + TS_MEAN($xx,4)")), patterns[0])
    assert root_genes(parse("5 + 2")) == set()
    assert frequent_patterns(counts, 0) == []


def test_real_shared_five_dimensions_max_backprop_and_fixed_context(tmp_path):
    current = session(tmp_path)
    models = FakeModels(query=True)
    search = search_for(current, models)
    initial_context = current.query("get_context", {})
    first = step(search, current)
    root = search.root
    assert root == first.report.expression_id
    assert search.nodes[root]["score"] == pytest.approx(0.52)
    step(search, current)
    assert len(search.nodes) == 2
    assert search.nodes[root]["visits"] == 2
    assert search.nodes[root]["q"] == max(n["score"] for n in search.nodes.values())
    assert search.nodes[root]["score"] == pytest.approx(0.52)
    assert current.query("get_context", {}) == initial_context
    for system, payload in models.calls:
        assert payload["context"] == initial_context
        assert "remaining_attempts" not in json.dumps(payload["context"])
        assert "remaining_attempts" not in system
        assert not ({"fold", "universe", "snapshot_id", "run_id"} & payload["context"].keys())
    assert search.usage["chat_requests"] == len(models.calls)
    assert search.usage["unknown_usage_requests"] == 0
    for (_, payload), temperature in zip(models.calls, models.temperatures, strict=True):
        assert temperature == (0.1 if payload["phase"] == "risk" else 1.0)
        if payload["phase"] == "risk":
            assert payload["candidate"]["expression"] in models.expressions


def test_root_budget_one_and_local_budget_never_extends_global(tmp_path):
    current = session(tmp_path, 1)
    search = search_for(current, tree_budget=100, budget_increment=100)
    step(search, current)
    assert len(search.nodes) == 1 and current.attempts_used == 1
    with pytest.raises(ValueError, match="budget exhausted"):
        search.ask(current.get_context())
    current = session(tmp_path / "restart", 3)
    search = search_for(current, tree_budget=1, budget_increment=0)
    for _ in range(3):
        step(search, current)
    assert search.trees_started == 2
    assert len([n for n in search.nodes.values() if n["parent"] is None]) == 2


@pytest.mark.parametrize("invalid", ["suggestion", "formula_0", "risk"])
def test_invalid_model_stage_is_charged_without_fake_score(tmp_path, invalid):
    current = session(tmp_path)
    search = search_for(current, FakeModels(invalid_phase=invalid), max_dialogue_rounds=2)
    feedback = step(search, current)
    assert feedback.report.status == "compile_error" and current.attempts_used == 1
    assert not search.nodes and search.steps[0]["reward"] is None
    assert search.usage["unknown_usage_requests"] == 2


def test_compile_and_fsa_correction_preserve_failure_and_cost(tmp_path):
    current = session(tmp_path)
    search = search_for(current, FakeModels(["$forbidden"]), max_formula_repairs=1)
    feedback = step(search, current)
    assert feedback.candidate.expression == "$forbidden" and feedback.reason == "compile_error"
    assert [p[1]["phase"] for p in search.models.calls] == ["suggestion", "formula_0", "formula_1"]
    current = session(tmp_path / "fsa")
    search = search_for(current, FakeModels(["$x + $close"]), max_formula_repairs=1)
    search.config = replace(search.config, fsa_top_k=3)
    search.pattern_counts = {p: 1 for p in root_genes(parse("$x + $close"))}
    result = step(search, current)
    assert result.candidate.name == "mcts_llm_generation_error"
    assert "FSA" in search.steps[0]["error"] and not search.nodes


def test_undefined_core_diagnostic_and_duplicate_do_not_create_nodes(tmp_path):
    current = session(tmp_path)
    search = search_for(current, FakeModels(["$x"]))
    step(search, current)
    step(search, current)
    assert len(search.nodes) == 1 and search.nodes[search.root]["visits"] == 1
    assert search.steps[-1]["reason"] == "duplicate_search_node"
    candidate = search.ask(current.get_context())[0]
    feedback = current.evaluate(candidate)
    report = replace(feedback.report, diagnostics={**feedback.report.diagnostics, "icir": None})
    search.tell([replace(feedback, report=report)])
    assert search.steps[-1]["reason"] == "incomplete_search_feedback"
    assert search.steps[-1]["reward"] is None


@pytest.mark.parametrize("phase", ["suggestion", "formula_0", "risk"])
def test_saved_raw_response_recovers_without_resending(tmp_path, phase):
    current = session(tmp_path)
    search = search_for(current)
    saved = None

    def checkpoint():
        nonlocal saved
        saved = json.loads(json.dumps(search.dump_state()))
        records = (search.work or {}).get("responses", {}).get(phase, [])
        if records and "result" not in records[-1]:
            raise KeyboardInterrupt("injected after raw response saved")

    search.set_checkpoint(checkpoint)
    with pytest.raises(KeyboardInterrupt):
        search.ask(current.get_context())
    already_sent = [p[1]["phase"] for p in search.models.calls]
    restored = search_for(current)
    restored.load_state(saved)
    step(restored, current)
    sent = already_sent + [p[1]["phase"] for p in restored.models.calls]
    assert sent == ["suggestion", "formula_0", "risk"]
    assert len(restored.nodes) == 1 and restored.usage["chat_requests"] == 3


def test_risk_stream_retry_preserves_stage_and_charges_one_evaluation(tmp_path, monkeypatch):
    import io

    current = session(tmp_path, attempts=1)
    method = MCTSLLMSearch(
        42, MCTSLLMConfig(chat_model="synthetic", chat_key_env="ATLAS_TEST_MODEL_KEY")
    )
    method.set_session(current)
    monkeypatch.setenv("ATLAS_TEST_MODEL_KEY", "synthetic-key")
    phases, checkpoints, waits = [], [], []
    method.set_checkpoint(lambda: checkpoints.append(copy.deepcopy(method.usage)))
    fake = FakeModels()

    def request(req, **kwargs):
        payload = json.loads(json.loads(req.data)["messages"][1]["content"])
        phase = payload["phase"]
        phases.append(phase)
        assert checkpoints[-1]["chat_requests"] == len(phases)
        assert current.remaining_budget() == 1 and not current._store.trials()
        text, usage = fake.chat("", payload)
        if phase == "risk" and phases.count("risk") == 1:
            text = '{"score": 0.99'
        chunk = {"choices": [{"delta": {"content": text}}]}
        data = f"data: {json.dumps(chunk)}\n\n"
        if phases.count("risk") != 1 or phase != "risk":
            data += f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n"
            data += "data: [DONE]\n\n"
        return io.BytesIO(data.encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    feedback = step(method, current)
    assert phases == ["suggestion", "formula_0", "risk", "risk"] and waits == [1]
    assert len(current._store.trials()) == 1 and current.remaining_budget() == 0
    assert feedback.candidate.expression == "$x"
    assert method.steps[0]["risk"]["score"] == 0.6
    assert len(method.steps[0]["responses"]["risk"]) == 1
    assert method.usage["chat_requests"] == 4
    assert method.usage["unknown_usage_requests"] == 1
    assert method.usage["prompt_tokens"] == 15


@pytest.mark.parametrize("boundary", ["after_commit", "after_tell", "model_response"])
def test_runner_resume_and_frozen_oos(project, monkeypatch, boundary):
    expressions = ["$adj_close", "$volume", "$adj_close * $volume"]
    config = MCTSLLMConfig(fsa_top_k=0)
    method = MCTSLLMSearch(42, config, models=FakeModels(expressions))
    with monkeypatch.context() as patch:
        if boundary == "after_commit":
            original = RunStore.record

            def record(self, index, *args, **kwargs):
                original(self, index, *args, **kwargs)
                if index == 2:
                    raise KeyboardInterrupt("injected")

            patch.setattr(RunStore, "record", record)
        elif boundary == "after_tell":
            original = MCTSLLMSearch.tell

            def tell(self, feedback):
                original(self, feedback)
                if feedback[0].trial_index == 2:
                    raise KeyboardInterrupt("injected")

            patch.setattr(MCTSLLMSearch, "tell", tell)
        else:
            original = Checkpoint.save

            def save(self, method, *args, **kwargs):
                original(self, method, *args, **kwargs)
                if (method.work or {}).get("responses", {}).get("risk"):
                    raise KeyboardInterrupt("injected")

            patch.setattr(Checkpoint, "save", save)
        with pytest.raises(KeyboardInterrupt):
            run(project, "ashare", "fold1", "mcts_llm", 42, 3, method_impl=method)
    directory = next((project / "artifacts/runs").iterdir())
    restored = MCTSLLMSearch(999, config, models=FakeModels(expressions))
    resume(project, directory, method_impl=restored)
    baseline = run(
        project,
        "ashare",
        "fold1",
        "mcts_llm",
        42,
        3,
        method_impl=MCTSLLMSearch(42, config, models=FakeModels(expressions)),
    )
    assert logical_trials(directory) == logical_trials(baseline)
    left = read_json(directory / "checkpoint.json")["method_state"]
    right = read_json(baseline / "checkpoint.json")["method_state"]
    assert left == right
    before = (directory / "checkpoint.json").read_bytes()
    evaluate_frozen(project, directory)
    assert (directory / "checkpoint.json").read_bytes() == before


def test_configuration_validation_and_shared_preflight(tmp_path):
    for options in (
        {"tree_budget": 0},
        {"dimension_temperature": 0},
        {"temperature": float("nan")},
        {"chat_base_url": "https://user:secret@example.org"},
        {"tree_selection": 1},
    ):
        with pytest.raises(ValueError):
            MCTSLLMConfig(**options)
    current = session(tmp_path)
    assert current.validate_expression("$x")["error"] is None
    assert current.validate_expression("$target")["error"] is not None
    assert current.attempts_used == 0


def test_model_request_without_saved_response_keeps_unknown_usage(tmp_path):
    current = session(tmp_path)
    search = search_for(current)
    saved = []
    search.set_checkpoint(lambda: saved.append(json.loads(json.dumps(search.dump_state()))))

    def failed_request(*args, **kwargs):
        raise RuntimeError("chat model request failed")

    search.models.chat = failed_request
    with pytest.raises(RuntimeError, match="request failed"):
        search.ask(current.get_context())
    restored = search_for(current)
    restored.load_state(saved[-1])
    step(restored, current)
    assert restored.usage["chat_requests"] == 4
    assert restored.usage["unknown_usage_requests"] == 1
    assert restored.usage["prompt_tokens"] == 15


def test_unknown_training_correlation_is_not_high_diversity(tmp_path, monkeypatch):
    current = session(tmp_path)
    search = search_for(current)
    assert step(search, current).accepted
    original = current.factor_correlations
    monkeypatch.setattr(
        current,
        "factor_correlations",
        lambda pairs: tuple(replace(row, value=None) for row in original(pairs)),
    )
    step(search, current)
    assert search.steps[-1]["raw"]["diversity"] is None
    assert search.steps[-1]["reward"] is None
    assert len(search.nodes) == 1


def test_rejected_candidate_is_search_history_but_not_fsa_member(tmp_path):
    current = session(tmp_path)
    current._library._rules["min_abs_val_ic"] = 2
    search = search_for(current, FakeModels(["TS_MEAN($x,2)"]))
    feedback = step(search, current)
    assert not feedback.accepted and len(search.nodes) == 1
    assert search.members == {} and search.pattern_counts == {}


def test_few_shot_quality_filter_diversity_and_zero_shot(tmp_path, monkeypatch):
    from alpha_atlas.contracts import FactorCorrelation

    current = session(tmp_path)
    search = search_for(current)
    search.members = {
        k: {"raw": {"effectiveness": score, "stability": 1 - score}}
        for k, score in zip("bcde", (0.1, 0.8, 0.9, 1.0), strict=True)
    }
    monkeypatch.setattr(
        current,
        "factor_correlations",
        lambda pairs: tuple(
            FactorCorrelation(a, b, corr, 20, "train")
            for (a, b), corr in zip(pairs, (0.1, -0.2, 0.9, None), strict=True)
        ),
    )
    monkeypatch.setattr(current, "library_get", lambda identity: {"factor_id": identity})
    assert search._examples("a", "effectiveness") == [{"factor_id": "c"}]
    assert search._examples("a", "stability") == [{"factor_id": "b"}]
    assert search._examples("a", "diversity") == [{"factor_id": "b"}]
    assert search._examples("a", "turnover") == []
    assert search._examples("a", "overfitting") == []


def test_normal_factory_entry_configuration_freeze_and_diagnostics_preflight(project, monkeypatch):
    monkeypatch.setattr(
        "alpha_atlas.methods.mcts_llm.HTTPModels",
        lambda config: FakeModels(["$adj_close", "$volume"]),
    )
    directory = run(project, "ashare", "fold1", "mcts_llm", 42, 1)
    spec = read_json(directory / "run.json")
    assert spec["implementation"] == "mcts_llm_atlas_v1"
    assert spec["resumable"] and spec["method_config"]["risk_temperature"] == 0.1
    assert "共享训练诊断" in (directory / "report.md").read_text(encoding="utf-8")
    config = project / "configs/mcts_llm.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("tree_budget = 3", "tree_budget = 4"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MCTS-LLM configuration changed"):
        evaluate_frozen(project, directory)
    benchmark = project / "configs/benchmark.toml"
    benchmark.write_text(
        benchmark.read_text(encoding="utf-8").replace(
            'search_diagnostics = "icir_turnover_v1"', 'search_diagnostics = ""'
        ),
        encoding="utf-8",
    )
    before = len(list((project / "artifacts/runs").iterdir()))
    with pytest.raises(ValueError, match="requires shared"):
        run(project, "ashare", "fold1", "mcts_llm", 42, 1)
    assert len(list((project / "artifacts/runs").iterdir())) == before


def test_shared_http_client_preserves_explicit_zero_temperature(monkeypatch):
    from alpha_atlas.methods.models import HTTPModels

    client = HTTPModels(MCTSLLMConfig(chat_model="synthetic"))
    bodies = []

    def post(kind, route, body, **kwargs):
        bodies.append(body)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(client, "_post", post)
    client.chat_messages([], temperature=0)
    client.chat_messages([])
    assert [b["temperature"] for b in bodies] == [0, 1.0]


@pytest.mark.parametrize("streaming", [False, True])
def test_shared_http_client_optional_output_limit(monkeypatch, streaming):
    from alpha_atlas.methods.alphaprobe import AlphaProbeConfig
    from alpha_atlas.methods.models import HTTPModels
    from alpha_atlas.methods.react import ReactConfig

    bodies = []

    def post(self, kind, route, body, **kwargs):
        bodies.append(body)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(HTTPModels, "_post", post)
    for config in (
        MCTSLLMConfig(chat_model="synthetic"),
        AlphaProbeConfig(chat_model="synthetic", embedding_model="synthetic"),
        ReactConfig(chat_model="synthetic"),
    ):
        client = HTTPModels(config)
        on_delta = (lambda *args: None) if streaming else None
        client.chat_messages([], on_delta=on_delta)
        client.chat_messages([], on_delta=on_delta, max_output_tokens=123)
    assert "max_tokens" not in bodies[0]
    assert "max_completion_tokens" not in bodies[0]
    assert [b["max_tokens"] for b in bodies[1:]] == [123, 4096, 123, 1536, 123]
    assert all(b.get("stream", False) == streaming for b in bodies)
