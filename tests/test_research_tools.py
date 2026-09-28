import json

import pytest
from test_evaluation_library import RULES, evaluator

from alpha_atlas.contracts import Candidate
from alpha_atlas.expressions import compile_factor
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods import make_method
from alpha_atlas.methods.alphaprobe import AlphaProbeSearch
from alpha_atlas.methods.mcts_llm import MCTSLLMSearch
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


@pytest.mark.parametrize("reference", ["", "futures_cta"])
def test_all_methods_share_context_library_access_and_evaluation(tmp_path, reference):
    results = []
    for name in ("random", "gp", "mcts", "atlas", "react", "alphaprobe", "mcts_llm"):
        service = evaluator(diagnostics="icir_turnover_v1")
        service.profile["frequency"] = "1d"
        current = SearchSession(
            service,
            FactorLibrary({**RULES, "reference_library": reference}),
            RunStore(tmp_path / name),
            3,
        )
        method = (
            AlphaProbeSearch(42, models=object())
            if name == "alphaprobe"
            else MCTSLLMSearch(42, models=object())
            if name == "mcts_llm"
            else make_method(name, 42)
        )
        method.set_session(current)

        def query(tool, arguments, *, name=name, method=method):
            if name == "react":
                return method._query(tool, arguments)
            if name == "alphaprobe":
                return method._query({"name": tool, "arguments": arguments})
            return method._session.query(tool, arguments)

        context = query("get_context", {})
        syntax = context["expression_rules"]
        example = json.loads(syntax["syntax_example"])["expression"].replace("$field", "$x")
        assert "\n" in example
        multiline = compile_factor(example, {"x"})
        nested = compile_factor("$x / TS_MEAN($x, 20) - 1", {"x"})
        assert multiline == nested
        before = json.dumps(context, sort_keys=True)
        assert "remaining_attempts" not in before
        if name == "react":
            method._initialize()
            prompt = method.system_prompt
            assert syntax["syntax_rules"] in prompt.replace("\n- ", " ")
            assert syntax["syntax_example"] in prompt
        found = query("library_search", {"source": "reference", "query": "tsmom_63"})
        assert found["total"] == int(bool(reference))
        assert not current._store.trials() and current.remaining_budget() == 3
        feedback = current.evaluate(Candidate("$x", name="统一工具测试"))
        result = current.evaluation_result(feedback)
        assert result["accepted"] and result["remaining_attempts"] == 2
        assert before == json.dumps(query("get_context", {}), sort_keys=True)
        if name == "react":
            method._initialize()
            assert method.system_prompt == prompt
        matches = query("library_search", {"source": "run", "query": "统一工具"})
        assert matches["total"] == 1
        outputs = [
            query("get_context", {}),
            found,
            matches,
            query("library_list", {"limit": 1}),
            query("library_get", {"factor_id": result["factor_id"]}),
            query("library_stats", {}),
            result,
        ]
        results.append(json.dumps(outputs, sort_keys=True))
        assert current.remaining_budget() == 2
    assert len(set(results)) == 1


def test_shared_queries_cannot_evaluate_or_expose_internal_budget(tmp_path):
    current = SearchSession(evaluator(), FactorLibrary(RULES), RunStore(tmp_path), 1)
    with pytest.raises(ValueError, match="Unknown tool"):
        current.query("evaluate", {"expression": "$x"})
    assert not current._store.trials() and current.remaining_budget() == 1
    assert "remaining_attempts" not in current.query("get_context", {})
    feedback = current.evaluate(Candidate("$x"))
    result = current.evaluation_result(feedback)
    assert result["remaining_attempts"] == 0
    assert current.get_context().remaining_attempts == 0
