import json
from dataclasses import replace

import pytest
from test_checkpoint import legacy_field_names
from test_evaluation_library import RULES, evaluator

from alpha_atlas.contracts import Candidate
from alpha_atlas.expressions import compile_factor
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods import make_method
from alpha_atlas.methods.alphaprobe import AlphaProbeSearch
from alpha_atlas.methods.alphaschema import AlphaSchemaSearch
from alpha_atlas.methods.annealing import AnnealingLLMSearch
from alpha_atlas.methods.mcts_llm import MCTSLLMSearch
from alpha_atlas.methods.v1 import V1Search
from alpha_atlas.methods.v1_async import V1AsyncSearch
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


@pytest.mark.parametrize("legacy_names", [False, True])
def test_internal_trial_read_uses_only_committed_facts_without_budget_or_recomputation(
    tmp_path, monkeypatch, legacy_names
):
    service = evaluator()
    store = RunStore(tmp_path)
    current = SearchSession(service, FactorLibrary(RULES), store, 3)
    feedback = current.evaluate(Candidate("$x"))
    expected = current.evaluation_result(feedback)
    expected.pop("remaining_attempts")
    expected = json.loads(json.dumps(expected))
    current.evaluate(Candidate("$missing"))
    for path in (tmp_path / "factor_trials").glob("*.json"):
        if legacy_names:
            path.write_text(json.dumps(legacy_field_names(json.loads(path.read_text()))))
    originals = {path: path.read_bytes() for path in (tmp_path / "factor_trials").glob("*.json")}
    attempts, version = current.attempts_used, current.get_library().version
    monkeypatch.setattr(store, "trials", lambda: pytest.fail("history read must not scan trials"))
    monkeypatch.setattr(service, "evaluate", lambda *_: pytest.fail("no reevaluation"))
    monkeypatch.setattr(current, "remaining_budget", lambda: pytest.fail("no live budget read"))
    assert current.get_trial(1) == expected
    assert current.get_trial(2)["status"] == "compile_error"
    changed = current.get_trial(1)
    changed["metrics"].clear()
    assert current.get_trial(1) == expected
    assert current.attempts_used == attempts and current.get_library().version == version
    assert all(path.read_bytes() == value for path, value in originals.items())
    with pytest.raises(ValueError, match="Unknown tool"):
        current.query("get_trial", {"factor_trial_index": 1})


@pytest.mark.parametrize("index", [0, -1, True, "1", 1.5])
def test_internal_trial_read_requires_a_positive_integer(tmp_path, index):
    with pytest.raises(ValueError, match="positive integer"):
        RunStore(tmp_path).trial(index)


@pytest.mark.parametrize("damage", ["missing", "json", "version", "index", "feedback", "report"])
def test_internal_trial_read_fails_explicitly_on_missing_or_damaged_records(tmp_path, damage):
    current = SearchSession(evaluator(), FactorLibrary(RULES), RunStore(tmp_path), 1)
    current.evaluate(Candidate("$x"))
    path = tmp_path / "factor_trials/00000001.json"
    row = json.loads(path.read_text())
    error = ValueError
    if damage == "missing":
        path.unlink()
        error = FileNotFoundError
    elif damage == "json":
        path.write_text("{")
    else:
        if damage == "version":
            row["format_version"] = -1
        elif damage == "index":
            row["factor_trial_index"] = 2
        elif damage == "feedback":
            row["feedback"]["factor_trial_index"] = 2
        else:
            row["feedback"]["report"] = None
        path.write_text(json.dumps(row))
    with pytest.raises(error):
        current.get_trial(1)
    assert current.attempts_used == 1


@pytest.mark.parametrize("reference", ["", "futures_cta"])
def test_all_methods_share_context_library_access_and_evaluation(tmp_path, reference):
    results = []
    llm_methods = {
        "alphaprobe": AlphaProbeSearch,
        "alphaschema": AlphaSchemaSearch,
        "mcts_llm": MCTSLLMSearch,
        "annealing": AnnealingLLMSearch,
        "v1": V1Search,
        "v1_async": V1AsyncSearch,
    }
    for name in ("random", "gp", "mcts", "atlas", "react", *llm_methods):
        service = evaluator(diagnostics="icir_turnover_v1")
        service.profile["frequency"] = "1d"
        current = SearchSession(
            service,
            FactorLibrary({**RULES, "reference_library": reference}),
            RunStore(tmp_path / name),
            3,
        )
        method = (
            llm_methods[name](42, models=object()) if name in llm_methods else make_method(name, 42)
        )
        method.set_session(current)

        def query(tool, arguments, *, name=name, method=method):
            if name == "react":
                return method._query(tool, arguments)
            if name == "alphaprobe":
                return method._query({"name": tool, "arguments": arguments})
            return method._session.query(tool, arguments)

        context = query("get_context", {})
        assert context["evaluation_rules"]["clipping"]["quantiles"] == (0.01, 0.99)
        assert context["evaluation_rules"]["clipping"]["fit_split"] == "train"
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


@pytest.mark.parametrize("reference", ["", "futures_cta"])
def test_reference_source_alias_uses_only_enabled_library_and_no_evaluation(tmp_path, reference):
    service = evaluator()
    service.profile["frequency"] = "1d"
    current = SearchSession(
        service, FactorLibrary({**RULES, "reference_library": reference}), RunStore(tmp_path), 3
    )
    arguments = {"source": "futures_cta", "query": "tsmom_63"}
    before = dict(arguments)
    if reference:
        aliased = current.query("library_search", arguments)
        assert aliased == current.query("library_search", {**arguments, "source": "reference"})
        assert aliased["total"] == 1 and aliased["results"][0]["source"] == "reference"
    else:
        with pytest.raises(ValueError, match="source must be all, run or reference"):
            current.query("library_search", arguments)
        assert current.query("library_search", {"source": "reference"})["total"] == 0
    assert arguments == before
    assert current.remaining_budget() == 3 and current.get_library().version == 0
    assert not current._store.trials()


@pytest.mark.parametrize(
    "tool,arguments,expected",
    [
        ("library_get", {}, ("factor_id", "library_list/search")),
        ("library_get", {"factor_id": None}, ("nonempty text", "library_get")),
        ("library_get", {"factor_id": []}, ("nonempty text", "library_get")),
        ("library_get", {"factor_id": "trial:1"}, ("history reference", "admitted factor_id")),
        ("library_search", {"source": "library"}, ("source must", "run/reference/all")),
        ("library_search", {"source": []}, ("source must", "run/reference/all")),
        ("library_search", {"limit": 30}, ("[1,20]", "paging")),
        ("library_search", {"limit": True}, ("[1,20]", "integer")),
        ("library_search", {"offset": -1}, ("offset", "paging")),
        ("library_search", {"limit": "10"}, ("[1,20]", "integer")),
        ("library_list", {"limit": 101}, ("[1,100]", "library_list")),
        ("library_search", {"field": "x"}, ("unexpected keyword", "library_search")),
        ("library_get", {1: "id"}, ("keywords must be strings", "factor_id")),
        ("get_context", {"target": "test"}, ("unexpected keyword", "get_context()")),
        ("library_stats", {"limit": 1}, ("unexpected keyword", "no arguments")),
        ("library_get", None, ("arguments must be an object", "factor_id")),
        ("library_search", [], ("arguments must be an object", "library_search")),
        ("library_search", '{"source":"run"}', ("arguments must be an object", "source")),
    ],
)
def test_shared_tool_errors_identify_parameters_and_preserve_budget(
    tmp_path, tool, arguments, expected
):
    current = SearchSession(evaluator(), FactorLibrary(RULES), RunStore(tmp_path), 3)
    with pytest.raises(ValueError) as captured:
        current.query(tool, arguments)
    assert str(captured.value).startswith(tool + ":")
    assert all(fragment in str(captured.value) for fragment in expected)
    assert not current._store.trials() and current.remaining_budget() == 3
    assert current.get_library().version == 0
    assert current.query("library_get", {"factor_id": "unknown"}) is None


@pytest.mark.parametrize("tool", [[], {}, None, "get_trial", "evaluate", "register_operator"])
def test_invalid_tool_names_return_public_help_without_dispatch(tmp_path, tool):
    current = SearchSession(evaluator(), FactorLibrary(RULES), RunStore(tmp_path), 3)
    with pytest.raises(ValueError, match="Unknown tool") as captured:
        current.query(tool, {})
    assert "library_search" in str(captured.value) and "library_get" in str(captured.value)
    assert not current._store.trials() and current.remaining_budget() == 3


@pytest.mark.parametrize("frequency", ["5m", "1d"])
def test_adjusted_context_explains_only_supported_scopes_without_expanding_permissions(
    tmp_path, frequency
):
    current = SearchSession(evaluator(), FactorLibrary(RULES), RunStore(tmp_path), 1)
    current._context = replace(current._context, frequency=frequency, panel="product")
    current._fields = ("adj_close", "adj_vwap", "volume", "amount")
    context = current.query("get_context", {})
    timeframes = context["expression_rules"]["timeframes"]
    assert timeframes["fields"] == current._fields
    assert f"$adj_close uses {frequency} bars." in timeframes["semantics"]
    assert "finite positive volume" in timeframes["semantics"]
    assert "Prefer available adj_* prices" in context["expression_rules"]["numeric_rules"]
    assert (
        "adj_* retain the adapter-supplied adjustment"
        in context["evaluation_rules"]["panel_semantics"]
    )
    if frequency == "5m":
        assert all(scope in timeframes["semantics"] for scope in timeframes["suffixes"])
        assert "$adj_close@30m" in timeframes["semantics"]
        assert "20 completed 30m bars" in timeframes["semantics"]
        assert current.validate_expression("$adj_close@30m / $adj_vwap@30m - 1")["error"] is None
        assert current.validate_expression("$close@30m")["error"]
    else:
        assert not timeframes["suffixes"]
        assert "No higher timeframes." in timeframes["semantics"]
        assert "$adj_close@30m" not in timeframes["semantics"]
    assert current.attempts_used == 0
