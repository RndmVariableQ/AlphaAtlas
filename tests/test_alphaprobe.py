import json
import math
from dataclasses import asdict, replace
from urllib.error import HTTPError

import numpy as np
import polars as pl
import pytest
from test_checkpoint import logical_trials
from test_evaluation_library import evaluator, market_frame
from test_pipeline import project as project

from alpha_atlas.checkpoint import Checkpoint, read_json
from alpha_atlas.contracts import Candidate, FactorCorrelation
from alpha_atlas.expressions import parse
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods.alphaprobe import (
    AlphaProbeConfig,
    AlphaProbeSearch,
    HTTPModels,
    ProbeNode,
    sigmoid,
    syntax_distance,
)
from alpha_atlas.reporting import compare
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


class FakeModels:
    """Deterministic protocol responses, no credentials, network, or vendor SDK."""

    def __init__(self, *, interrupt_phase=None, malformed=False):
        self.calls = []
        self.interrupt_phase = interrupt_phase
        self.malformed = malformed

    def chat(self, system, payload):
        phase = ("analyst", "execution", "validator")[len(payload["previous_stages"])]
        self.calls.append((phase, payload))
        if self.interrupt_phase == phase:
            self.interrupt_phase = None
            raise KeyboardInterrupt("injected model interruption")
        if self.malformed:
            return "invalid json", {"prompt_tokens": 5, "completion_tokens": 2}
        count = payload["count"]
        if phase == "analyst":
            response = {"strategies": [f"Price interaction {i}" for i in range(count)]}
        else:
            response = {
                "candidates": [
                    {
                        "expression": f"$adj_close + $volume * {i + 1}",
                        "hypothesis": f"Price and activity interaction {i}",
                    }
                    for i in range(count)
                ]
            }
        return json.dumps(response), {"prompt_tokens": 5, "completion_tokens": 2}

    def embed(self, texts):
        self.calls.append(("embedding", list(texts)))
        return [[1.0, float(len(t) % 11 + 1), float(sum(map(ord, t)) % 17 + 1)] for t in texts], {
            "total_tokens": len(texts) * 3
        }


def method(models=None, **options):
    config = AlphaProbeConfig(
        **{
            "initial_expressions": ("$adj_close", "TS_MEAN($volume,2)"),
            "min_train_quality": 0,
            "top_k": 2,
            "offspring": 3,
            **options,
        }
    )
    return AlphaProbeSearch(7, config, models=models or FakeModels())


def node(identity, quality, expression, **kwargs):
    return ProbeNode(identity, parse(expression), identity, quality, embedding=[1.0, 0.0], **kwargs)


def set_corr(search, a, b, value):
    search.correlations[search._pair(a, b)] = asdict(FactorCorrelation(a, b, value, 200, "test"))


def test_paper_leaf_prior_and_three_diversities():
    search = method()
    search.nodes = {"a": node("a", 0.1, "$x", depth=2, retrievals=3), "b": node("b", 0.3, "$y")}
    search.nodes["b"].embedding = [0.0, 1.0]
    set_corr(search, "a", "b", -0.25)
    result = search.score_pool()["a"]
    assert result["prior"] == pytest.approx(sigmoid(-1) * 0.95**2 * 0.9**3)
    assert result["likelihood"] == pytest.approx(0.75 * sigmoid(1) * 0.5)
    assert result["score"] == pytest.approx(result["prior"] * result["likelihood"])
    set_corr(search, "a", "b", None)
    assert search.score_pool()["a"]["likelihood"] == 0


def test_nonleaf_gain_times_vertical_times_horizontal_diversity():
    search = method(pool_capacity=1)
    search.nodes = {
        "p": node("p", 0.2, "$x", children=["a", "b"]),
        "a": node("a", 0.3, "$y", parent="p"),
        "b": node("b", 0.5, "$z", parent="p"),
    }
    # Keep all three eligible for scoring; capacity eviction must retain graph ancestry.
    search.config = replace(search.config, pool_capacity=3)
    set_corr(search, "p", "a", 0.2)
    set_corr(search, "p", "b", 0.4)
    set_corr(search, "a", "b", 0.5)
    assert search.score_pool()["p"]["likelihood"] == pytest.approx(1.0 * 0.7 * 0.5)
    search.nodes["p"].children = ["a"]
    assert search.score_pool()["p"]["likelihood"] == pytest.approx(0.5 * 0.8)
    search.nodes["a"].quality = 0.1
    assert search.score_pool()["p"]["likelihood"] == 0
    search.config = replace(search.config, pool_capacity=1)
    assert search.active_pool() == ["b"]
    assert search._trace("a")[0]["factor_id"] == "p"


def test_syntax_distance_and_degenerate_quality_are_finite():
    assert syntax_distance(parse("ADD($x,$y)"), parse("ADD($y,$x)")) == 0
    assert syntax_distance(parse("$x"), parse("NEG($x)")) == pytest.approx(1 / 3)
    assert syntax_distance(parse("TS_MEAN($x,2)"), parse("TS_MEAN($x,3)")) > 0
    assert syntax_distance(parse("SUBTRACT($x,$y)"), parse("SUBTRACT($y,$x)")) > 0
    search = method()
    search.nodes = {"a": node("a", 0.2, "$x")}
    assert search.score_pool()["a"]["score"] == 0.5
    search.nodes["b"] = node("b", 0.2, "$y")
    set_corr(search, "a", "b", 0)
    assert all(math.isfinite(r["score"]) for r in search.score_pool().values())


def session(tmp_path, data=None, **kwargs):
    service = evaluator(data, retain_training=True, **kwargs)
    rules = {
        "min_abs_val_ic": 0.01,
        "min_coverage": 0.8,
        "max_abs_corr": 0.9,
        "min_corr_overlap": 10,
    }
    library = FactorLibrary(rules)
    return SearchSession(service, library, RunStore(tmp_path), 20, run_spec={"rules": rules})


def test_statistics_are_training_only_and_reuse_cached_values(tmp_path, monkeypatch):
    search = session(tmp_path)
    a = search.evaluate(Candidate("$x")).report.expression_id
    b = search.evaluate(Candidate("-$x")).report.expression_id
    before = search.attempts_used
    monkeypatch.setattr("alpha_atlas.evaluation.execute", lambda *a, **k: pytest.fail("recomputed"))
    (result,) = search.factor_correlations([(a, b), (b, a)])
    assert result.value == pytest.approx(-1)
    assert result.split == "train" and result.n_obs == 48
    assert result.aggregation == "cross_sectional_pearson"
    assert search.attempts_used == before
    with pytest.raises(ValueError, match="evaluated factors"):
        search.factor_correlations([(a, "another-run-id")])
    assert not any(k in asdict(result) for k in ("target", "frame", "values"))


def test_training_statistics_ignore_validation_and_rebuild_only_requested(tmp_path, monkeypatch):
    data = market_frame()
    first = session(tmp_path / "one", data)
    altered = data.with_columns(
        pl.when(pl.col("trading_day").dt.day() > 10)
        .then(-pl.col("x"))
        .otherwise(pl.col("x"))
        .alias("x")
    )
    second = session(tmp_path / "two", altered)
    results = []
    for current in (first, second):
        a = current.evaluate(Candidate("$x")).report.expression_id
        b = current.evaluate(Candidate("$close")).report.expression_id
        results.append(current.factor_correlations([(a, b)]))
    assert results[0] == results[1]
    second._evaluator._training.clear()
    from alpha_atlas.evaluation import execute

    calls = []

    def compute(compiled, frame, *args):
        calls.append(compiled.factor_id)
        assert frame["trading_day"].max() == second._evaluator.fold.train.end
        return execute(compiled, frame, *args)

    monkeypatch.setattr("alpha_atlas.evaluation.execute", compute)
    assert second.factor_correlations([(a, b)]) == results[1]
    assert set(calls) == {a, b} and len(calls) == 2
    assert second.factor_correlations([(a, b)]) == results[1]
    assert len(calls) == 2


def test_statistics_missing_overlap_is_unknown(tmp_path):
    current = session(tmp_path)
    a = current.evaluate(Candidate("$x")).report.expression_id
    b = current.evaluate(Candidate("$close")).report.expression_id
    current._min_corr_overlap = 49
    assert current.factor_correlations([(a, b)])[0].value is None


def test_futures_correlations_pool_contracts_within_product():
    from alpha_atlas.evaluation import metric

    rows = []
    for product, exchange, contract, count, sign in [
        ("P", "X", "same", 5, 1),
        ("P", "Y", "same", 10, -1),
        ("Q", "X", "B", 30, 1),
    ]:
        rows += [
            {
                "product": product,
                "exchange": exchange,
                "instrument_id": contract,
                "value": float(i),
                "target": float(sign * i),
            }
            for i in range(count)
        ]
    result = metric(
        pl.DataFrame(rows), "time_series_equal_spearman_ic", "train", correlation="pearson"
    )
    pooled = np.corrcoef(np.r_[np.arange(5), np.arange(10)], np.r_[np.arange(5), -np.arange(10)])[
        0, 1
    ]
    assert result.value == pytest.approx((pooled + 1) / 2)
    assert result.n_obs == 45 and result.n_groups == 2
    assert result.aggregation == "time_series_equal_pearson_ic"


def test_val_rejection_does_not_rewrite_graph_quality_and_duplicates_do_not_create_cycles(tmp_path):
    current = session(tmp_path)
    feedback = current.evaluate(Candidate("$x", hypothesis="Cross-sectional order"))
    search = method()
    search.pending = {"candidate": asdict(feedback.candidate), "parent": None, "batch": None}
    rejected = replace(feedback, accepted=False, reason="quality_threshold")
    search.tell([rejected])
    identity = feedback.report.expression_id
    assert search.nodes[identity].quality == pytest.approx(1)
    search.pending = {"candidate": asdict(feedback.candidate), "parent": identity, "batch": None}
    search.tell([feedback])
    assert len(search.nodes) == 1 and search.nodes[identity].children == []


def test_graph_quality_uses_primary_training_metric(tmp_path):
    current = session(tmp_path)
    feedback = current.evaluate(Candidate("$x"))
    primary = replace(
        feedback.report.metrics[0], name="time_series_weighted_pearson_ic", value=-0.2
    )
    secondary = replace(primary, name="time_series_equal_pearson_ic", value=0.9)
    feedback = replace(feedback, report=replace(feedback.report, metrics=(primary, secondary)))
    search = method()
    search.pending = {"candidate": asdict(feedback.candidate), "parent": None, "batch": None}
    search.tell([feedback])
    assert search.nodes[feedback.report.expression_id].quality == pytest.approx(0.2)


@pytest.mark.parametrize("origin", ["configured_root", "generated_root", "child"])
@pytest.mark.parametrize("quality", [0.003, -0.003, 0.006, 0.0, None, float("nan"), float("inf")])
def test_root_quality_exemption_preserves_child_threshold_and_finite_nonzero_rule(
    tmp_path, origin, quality
):
    feedback = session(tmp_path).evaluate(Candidate("$x"))
    feedback = replace(
        feedback,
        accepted=False,
        reason="quality_threshold",
        report=replace(
            feedback.report, metrics=(replace(feedback.report.metrics[0], value=quality),)
        ),
    )
    search = method(min_train_quality=0.006)
    parent = "parent" if origin == "child" else None
    if parent:
        search.nodes[parent] = node(parent, 0.01, "$close")
    search.batches = [{"outcomes": []}]
    search.pending = {
        "candidate": asdict(feedback.candidate),
        "parent": parent,
        "batch": None if origin == "configured_root" else 0,
    }
    search.tell([feedback])
    expected = (
        quality is not None
        and math.isfinite(quality)
        and abs(quality) > 0
        and (parent is None or abs(quality) >= 0.006)
    )
    identity = feedback.report.expression_id
    assert (identity in search.nodes) == expected
    if expected:
        assert search.nodes[identity].parent == parent
        assert search.nodes[identity].quality == abs(quality)
    if parent:
        assert search.nodes[parent].children == ([identity] if expected else [])
    assert not feedback.accepted


def test_failed_root_evaluation_cannot_enter_graph(tmp_path):
    feedback = session(tmp_path).evaluate(Candidate("$x"))
    search = method()
    search.pending = {"candidate": asdict(feedback.candidate), "parent": None, "batch": None}
    search.tell([replace(feedback, report=replace(feedback.report, status="evaluation_error"))])
    assert not search.nodes


@pytest.mark.parametrize("attempts", [1, 5, 6])
def test_default_cold_start_generates_roots_within_budget_then_evolves(project, attempts):
    models = FakeModels()
    search = AlphaProbeSearch(7, models=models)
    path = run(project, "ashare", "fold1", "alphaprobe", 7, attempts=attempts, method_impl=search)
    trials = RunStore(path).trials()
    count = min(5, attempts)
    assert len(trials) == attempts
    assert search.roots == [] and search.root_index == 0
    first = search.batches[0]
    assert first["parent"] is None and first["trace"] == [] and first["count"] == count
    assert len(first["outcomes"]) == count
    assert [t["feedback"]["candidate"]["expression"] for t in trials[:count]] == [
        f"$adj_close + $volume * {i + 1}" for i in range(count)
    ]
    assert [p for p, _ in models.calls[:3]] == ["analyst", "execution", "validator"]
    assert models.calls[0][1]["ancestor_trace"] == []
    assert search.nodes and all(n.parent is None for n in search.nodes.values())
    if attempts > 5:
        assert search.batches[1]["parent"] in search.nodes
        assert search.batches[1]["trace"]
        assert search.batches[1]["count"] == 1
    else:
        assert search.usage["chat_requests"] == 3
        assert search.usage["embedding_requests"] == 0


def test_cold_start_invalid_and_duplicate_roots_are_charged_without_replacement(project):
    class MixedRoots(FakeModels):
        def chat(self, system, payload):
            text, usage = super().chat(system, payload)
            response = json.loads(text)
            if "candidates" in response:
                for row, expression in zip(
                    response["candidates"],
                    ["$adj_close", "$missing", "$adj_close", "SUBTRACT($volume,$volume)"],
                    strict=True,
                ):
                    row["expression"] = expression
            return json.dumps(response), usage

    search = AlphaProbeSearch(7, models=MixedRoots())
    path = run(project, "ashare", "fold1", "alphaprobe", 7, attempts=4, method_impl=search)
    trials = RunStore(path).trials()
    assert len(trials) == 4 and len(search.batches) == 1
    assert len(search.batches[0]["outcomes"]) == 4 and search.usage["chat_requests"] == 3
    assert trials[1]["feedback"]["reason"] == "compile_error"
    assert trials[2]["feedback"]["reason"] == "expression_duplicate"
    assert not trials[3]["feedback"]["accepted"]
    assert len(search.nodes) == 1


def test_empty_graph_bootstraps_with_llm_and_rejects_nontraining_feedback(tmp_path):
    current = session(tmp_path)
    search = AlphaProbeSearch(
        7, AlphaProbeConfig(initial_expressions=("$missing",)), models=FakeModels()
    )
    search.set_session(current)
    context = current.get_context()
    candidate = search.ask(context)[0]
    search.tell([current.evaluate(candidate)])
    assert not search.nodes
    candidate = search.ask(current.get_context())[0]
    assert candidate.expression
    assert search.batches[0]["parent"] is None and search.batches[0]["trace"] == []
    with pytest.raises(ValueError, match="one ask"):
        search.ask(context)
    other = method()
    from types import SimpleNamespace

    other.set_session(
        SimpleNamespace(
            factor_correlations=lambda _: (FactorCorrelation("a", "b", 0.1, 100, "x", "test"),)
        )
    )
    with pytest.raises(ValueError, match="training-only"):
        other.ask(context)


def test_end_to_end_llm_stages_graph_and_frozen_oos(project):
    models = FakeModels()
    search = method(models)
    path = run(project, "ashare", "fold1", "alphaprobe", 7, attempts=9, method_impl=search)
    state = read_json(path / "checkpoint.json")["method_state"]
    assert read_json(path / "run.json")["status"] == "frozen"
    assert len(RunStore(path).trials()) == 9
    assert {c[0] for c in models.calls} == {"analyst", "execution", "validator", "embedding"}
    assert len(state["batches"]) == 3
    assert [b["count"] for b in state["batches"]] == [3, 3, 1]
    assert state["usage"]["chat_requests"] == 9
    assert state["usage"]["prompt_tokens"] == 45
    assert state["usage"]["unknown_usage_requests"] == 0
    assert any(n["parent"] for n in state["nodes"])
    assert state["retrievals"] and state["correlations"]
    for phase, payload in models.calls:
        if phase != "embedding":
            assert set(payload["context"]) == {
                "asset",
                "frequency",
                "target",
                "metric",
                "fields",
                "operators",
                "expression_rules",
                "evaluation_rules",
                "reference_library",
            }
            assert not any(k in json.dumps(payload) for k in ('"test"', '"target_values"'))
    report = (path / "report.md").read_text(encoding="utf-8")
    assert "chat_requests" in report and "演化图节点" in report
    assert compare(project)["runs"][0]["method_config"] == search.configuration
    before = (path / "checkpoint.json").read_bytes()
    calls = len(models.calls)
    evaluate_frozen(project, path)
    assert (path / "checkpoint.json").read_bytes() == before and len(models.calls) == calls


class QueryModels(FakeModels):
    def __init__(self, *, interrupt=False, repeat=False):
        super().__init__()
        self.interrupt = interrupt
        self.repeat = repeat
        self.lookups = 0

    def chat(self, system, payload):
        if not payload["previous_stages"]:
            if not payload["query_results"] or self.repeat:
                self.lookups += 1
                return json.dumps(
                    {
                        "queries": [
                            {"name": "get_context"},
                            {"name": "library_stats"},
                            {"name": "library_search", "arguments": {"source": "run"}},
                            {"name": "read_targets"},
                        ]
                    }
                ), {"prompt_tokens": 5, "completion_tokens": 2}
            if self.interrupt:
                self.interrupt = False
                raise KeyboardInterrupt("after lookup")
            results = payload["query_results"][0]["results"]
            assert "adj_close" in results[0]["fields"]
            assert len(results[0]["operators"]) == 60
            assert results[0]["expression_rules"]["max_nodes"] == 100
            assert "members" in results[1] and "results" in results[2] and "error" in results[3]
            assert set(payload["context"]) == {
                "asset",
                "frequency",
                "target",
                "metric",
                "fields",
                "operators",
                "expression_rules",
                "evaluation_rules",
                "reference_library",
            }
        return super().chat(system, payload)


def test_model_metadata_queries_resume_without_repeating_lookup(project):
    models = QueryModels(interrupt=True)
    with pytest.raises(KeyboardInterrupt, match="after lookup"):
        run(project, "ashare", "fold1", "alphaprobe", 7, attempts=5, method_impl=method(models))
    path = next((project / "artifacts/runs").iterdir())
    state = read_json(path / "checkpoint.json")["method_state"]
    assert len(state["batches"][0]["queries"]["analyst"]) == 1
    models = QueryModels()
    search = method(models)
    resume(project, path, method_impl=search)
    assert models.lookups == 0 and search.usage["unknown_usage_requests"] == 1
    assert search.usage["chat_requests"] == 5
    assert search.usage["prompt_tokens"] == 20
    for key in ("run_id", "snapshot_id", "universe", "train", "val", "warmup", "fold_id"):
        assert key not in search.generation_context
    reference = run(
        project, "ashare", "fold1", "alphaprobe", 7, attempts=5, method_impl=method(QueryModels())
    )
    assert logical_trials(path) == logical_trials(reference)


def test_repeated_metadata_queries_are_bounded_and_recorded(project):
    search = method(QueryModels(repeat=True))
    path = run(project, "ashare", "fold1", "alphaprobe", 7, attempts=3, method_impl=search)
    assert len(search.batches[0]["queries"]["analyst"]) == 8
    assert search.usage["chat_requests"] == 9
    assert RunStore(path).trials()[-1]["feedback"]["reason"] == "compile_error"


@pytest.mark.parametrize("cold_start", [False, True])
@pytest.mark.parametrize("phase", ["execution", "validator"])
def test_model_stage_resume_keeps_completed_responses(project, phase, cold_start):
    options = {"initial_expressions": (), "offspring": 5} if cold_start else {}
    models = FakeModels(interrupt_phase=phase)
    with pytest.raises(KeyboardInterrupt, match="injected"):
        run(
            project,
            "ashare",
            "fold1",
            "alphaprobe",
            7,
            attempts=5,
            method_impl=method(models, **options),
        )
    path = next((project / "artifacts/runs").iterdir())
    saved = read_json(path / "checkpoint.json")
    assert saved["completed"] == (0 if cold_start else 2) and saved["pending"] is None
    assert "analyst" in saved["method_state"]["batches"][0]["responses"]
    resumed_models = FakeModels()
    resumed = method(resumed_models, **options)
    resume(project, path, method_impl=resumed)
    assert "analyst" not in [p for p, _ in resumed_models.calls]
    if phase == "validator":
        assert [p for p, _ in resumed_models.calls] == ["validator"]
    assert resumed.usage["unknown_usage_requests"] == 1
    reference = run(
        project, "ashare", "fold1", "alphaprobe", 7, attempts=5, method_impl=method(**options)
    )
    assert logical_trials(path) == logical_trials(reference)


@pytest.mark.parametrize("cold_start", [False, True])
@pytest.mark.parametrize("boundary", ["after_commit", "after_tell", "after_response"])
def test_trial_and_batch_resume_does_not_repeat_evaluation_or_generation(
    project, monkeypatch, boundary, cold_start
):
    options = {"initial_expressions": (), "offspring": 5} if cold_start else {}
    original_record, original_tell, original_save = (
        RunStore.record,
        AlphaProbeSearch.tell,
        Checkpoint.save,
    )

    def record(self, attempt, *args, **kwargs):
        original_record(self, attempt, *args, **kwargs)
        if boundary == "after_commit" and attempt == 3:
            raise KeyboardInterrupt("injected commit")

    def tell(self, feedback):
        original_tell(self, feedback)
        if boundary == "after_tell" and feedback[0].trial_index == 3:
            raise KeyboardInterrupt("injected tell")

    def save(self, search, *args, **kwargs):
        original_save(self, search, *args, **kwargs)
        if (
            boundary == "after_response"
            and search.batches
            and "validator" in search.batches[0]["responses"]
        ):
            raise KeyboardInterrupt("injected response")

    with monkeypatch.context() as patch:
        patch.setattr(RunStore, "record", record)
        patch.setattr(AlphaProbeSearch, "tell", tell)
        patch.setattr(Checkpoint, "save", save)
        with pytest.raises(KeyboardInterrupt, match="injected"):
            run(
                project,
                "ashare",
                "fold1",
                "alphaprobe",
                7,
                attempts=5,
                method_impl=method(**options),
            )
    path = next((project / "artifacts/runs").iterdir())
    committed = {p: p.read_bytes() for p in (path / "trials").glob("*.json")}
    models = FakeModels()
    resume(project, path, method_impl=method(models, **options))
    assert not models.calls
    assert all(p.read_bytes() == b for p, b in committed.items())
    reference = run(
        project, "ashare", "fold1", "alphaprobe", 7, attempts=5, method_impl=method(**options)
    )
    assert logical_trials(path) == logical_trials(reference)
    assert (
        read_json(path / "checkpoint.json")["method_state"]
        == read_json(reference / "checkpoint.json")["method_state"]
    )


@pytest.mark.parametrize("cold_start", [False, True])
def test_malformed_generation_consumes_attempt_without_fake_factor(project, cold_start):
    options = {"initial_expressions": ()} if cold_start else {}
    search = method(FakeModels(malformed=True), **options)
    path = run(project, "ashare", "fold1", "alphaprobe", 7, attempts=4, method_impl=search)
    trials = RunStore(path).trials()
    assert len(trials) == 4
    failures = trials if cold_start else trials[2:]
    assert all(t["feedback"]["reason"] == "compile_error" for t in failures)
    assert all(t["feedback"]["candidate"]["expression"] == "" for t in failures)
    assert search.usage["chat_requests"] == len(failures)


def test_no_model_calls_when_only_seed_budget_and_configuration_validation(project):
    models = FakeModels()
    run(project, "ashare", "fold1", "alphaprobe", 7, attempts=1, method_impl=method(models))
    assert not models.calls
    config_path = project / "configs/alphaprobe.toml"
    original_config = config_path.read_text()
    try:
        config_path.write_text(
            original_config.replace(
                'embedding_model = "Qwen3-Embedding-0.6B"', 'embedding_model = ""'
            )
        )
        with pytest.raises(ValueError, match="configure chat_model"):
            run(project, "ashare", "fold1", "alphaprobe", 7, attempts=1)
    finally:
        config_path.write_text(original_config)
    for options in ({"pool_capacity": 0}, {"depth_penalty": 1}, {"temperature": float("nan")}):
        with pytest.raises(ValueError):
            AlphaProbeConfig(**options)


def test_builtin_factory_records_configuration_and_oos_rejects_changes(project, monkeypatch):
    monkeypatch.setattr("alpha_atlas.methods.alphaprobe.HTTPModels", lambda config: FakeModels())
    path = run(project, "ashare", "fold1", "alphaprobe", 7, attempts=1)
    assert read_json(path / "run.json")["implementation"] == "alphaprobe_atlas_v1"
    config_path = project / "configs/alphaprobe.toml"
    config_path.write_text(config_path.read_text().replace("top_k = 15", "top_k = 14"))
    with pytest.raises(ValueError, match="AlphaPROBE configuration changed"):
        evaluate_frozen(project, path)


def test_http_protocol_uses_only_configured_fake_credentials(monkeypatch):
    monkeypatch.setenv("TEST_PROBE_KEY", "synthetic-test-key")
    config = AlphaProbeConfig(
        chat_model="test-chat",
        embedding_model="test-embedding",
        chat_key_env="TEST_PROBE_KEY",
        embedding_key_env="TEST_PROBE_KEY",
    )
    seen = []

    def open_request(request, **kwargs):
        import io

        seen.append((request, json.loads(request.data)))
        if request.full_url.endswith("/embeddings"):
            response = {
                "data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}],
                "usage": {"total_tokens": 2},
            }
        else:
            response = {"choices": [{"delta": {"content": "{}"}}]}
            return io.BytesIO(f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n".encode())
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", open_request)
    client = HTTPModels(config)
    assert client.chat("test", {}) == ("{}", None)
    assert client.embed(["one", "two"])[0] == [[1, 0], [0, 1]]
    assert seen[0][1]["model"] == "test-chat"
    assert seen[0][1]["stream"] is True
    assert seen[1][1]["encoding_format"] == "float"
    assert seen[0][0].get_header("Authorization") == "Bearer synthetic-test-key"

    def fail(*args, **kwargs):
        raise HTTPError("https://secret.invalid", 401, "secret body", {}, None)

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", fail)
    with pytest.raises(RuntimeError) as error:
        client.chat("test", {})
    assert str(error.value) == "chat model request failed (HTTP 401)"


def test_bad_embeddings_fail_without_disabling_semantics():
    models = FakeModels()
    models.embed = lambda texts: ([[float("nan"), 0]], None)
    search = method(models)
    search.nodes["a"] = node("a", 0.1, "$x")
    search.nodes["a"].embedding = None
    with pytest.raises(ValueError, match="embedding vectors"):
        search._embed_pool()
    assert search.nodes["a"].embedding is None
