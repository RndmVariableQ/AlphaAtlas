import io
import json
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from urllib.error import HTTPError

import polars as pl
import pytest
from test_checkpoint import interrupted_run, logical_trials
from test_evaluation_library import RULES, evaluator
from test_pipeline import project as project

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.checkpoint import read_json
from alpha_atlas.config import asset_config, load_toml
from alpha_atlas.contracts import DateRange, Fold
from alpha_atlas.evaluation import EvaluationService
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods.models import ContextLimitError, HTTPModels
from alpha_atlas.methods.react import SUMMARY, ReactConfig, ReactSearch
from alpha_atlas.operators import OperatorDefinition
from alpha_atlas.runner import resume, run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import RunStore


def command(tool="evaluate", **arguments):
    return json.dumps({"tool": tool, "arguments": arguments})


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def chat_messages(self, messages, **kwargs):
        self.calls.append(json.loads(json.dumps(messages)))
        result = next(self.replies)
        if isinstance(result, BaseException):
            raise result
        return result, {"prompt_tokens": 12, "completion_tokens": 4}


class FeedbackModel:
    """Deterministic proposals from observations, including after a new-process restore."""

    def chat_messages(self, messages, **kwargs):
        previous = 0
        for msg in messages:
            if msg["role"] == "user" and msg["content"].startswith('{"tool_result"'):
                previous = json.loads(msg["content"])["tool_result"].get("trial_index", previous)
        price = "adj_close" if "| `$adj_close` |" in messages[0]["content"] else "close"
        return command(expression=f"${price} + {previous}"), {
            "prompt_tokens": 12,
            "completion_tokens": 4,
        }


def session(tmp_path, budget=10, reference_library=""):
    return SearchSession(
        evaluator(),
        FactorLibrary({**RULES, "reference_library": reference_library}),
        RunStore(tmp_path),
        budget,
    )


def search(current, replies):
    method = ReactSearch(42, models=ScriptedModel(replies))
    method.set_session(current)
    return method


def step(method, current):
    candidate = method.ask(current.get_context())[0]
    feedback = current.evaluate(candidate)
    method.tell([feedback])
    return feedback


def cycles(n):
    return [
        [
            {"role": "assistant", "content": command(expression=f"$x + {i}")},
            {"role": "user", "content": f"tool_result {i}: " + "data " * 100},
        ]
        for i in range(n)
    ]


def tool_examples(tmp_path):
    """Exercise the JSON dispatch path on isolated synthetic futures bars, without an LLM."""
    from pathlib import Path

    from test_timeframes import panel

    root = Path(__file__).resolve().parents[1]
    profile = asset_config(root, "futures_curve")
    rules = load_toml(root / "configs/benchmark.toml")
    fields = [
        f"{field}{suffix}"
        for suffix in ("", "_p1", "_p2")
        for field in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "open_interest",
            "days_to_maturity",
        )
    ]
    train = panel(128).with_columns(
        pl.lit("P").alias("product"),
        *[
            pl.lit(90.0 + 30 * i).alias(f"days_to_maturity{s}")
            for i, s in enumerate(("", "_p1", "_p2"))
        ],
    )
    valid = train.with_columns(
        pl.col("row_id") + train.height,
        pl.col("timestamp") + timedelta(days=1),
        pl.col("trading_day") + timedelta(days=1),
    )
    fold = Fold(
        "fixture",
        DateRange(date(2025, 1, 2), date(2025, 1, 2)),
        DateRange(date(2025, 1, 3), date(2025, 1, 3)),
        DateRange(date(2025, 1, 4), date(2025, 1, 4)),
    )
    service = EvaluationService(
        pl.concat([train, valid]),
        fold,
        profile,
        set(fields),
        compile_options={
            "max_nodes": rules["max_expression_nodes"],
            "max_depth": rules["max_expression_depth"],
        },
    )
    current = SearchSession(
        service,
        FactorLibrary(rules, min_train_val_ic=profile["min_train_val_ic"]),
        RunStore(tmp_path),
        10,
        run_spec={"asset": "futures_curve", "fields": fields, "rules": rules},
    )
    method = ReactSearch(
        42,
        ReactConfig(),
        models=ScriptedModel(
            [
                command("get_context"),
                command("library_search", source="reference", query="tsmom_63", limit=1),
                command(
                    expression="$close", name="Synthetic close", hypothesis="Protocol fixture only."
                ),
            ]
        ),
    )
    method.set_session(current)
    admitted = step(method, current)
    assert admitted.accepted
    factor_id = admitted.report.expression_id
    method.models = ScriptedModel(
        [
            command("get_context"),
            command("library_list", offset=0, limit=1),
            command("library_get", factor_id=factor_id),
            command("library_stats"),
            command("library_search", source="run", query="Synthetic close", limit=1),
            command("library_get", factor_id="missing"),
            command("library_list", offset=100, limit=1),
            command("get_fields"),
            command("library_search", source="reference", limit=21),
            command(expression="$close"),
        ]
    )
    assert step(method, current).reason == "expression_duplicate"
    return method, current


def test_all_react_tools_round_trip_through_json_dispatch(tmp_path):
    method, current = tool_examples(tmp_path)
    examples = [
        (json.loads(request["content"]), json.loads(response["content"]))
        for request, response in method.rounds
    ]
    by_tool = {}
    for request, response in examples:
        result = response["tool_result"]
        if not isinstance(result, dict) or "error" not in result:
            by_tool.setdefault(request["tool"], result)
    assert set(by_tool) == {
        "evaluate",
        "get_context",
        "library_search",
        "library_list",
        "library_get",
        "library_stats",
    }
    context = by_tool["get_context"]
    assert len(context["fields"]) == 24 and len(context["operators"]) == 60
    assert context["frequency"] == "5m"
    assert all(op["description"] for op in context["operators"])
    assert context["expression_rules"]["timeframes"]["suffixes"] == [
        "@15m",
        "@30m",
        "@60m",
        "@1d",
    ]
    assert context["evaluation_rules"]["quality"] == {"train_and_val_ic_gt": 0.005}
    assert context["reference_library"]["enabled"]
    reference = by_tool["library_search"]["results"][0]
    assert reference["expression"] == "RETURN($close, 63)" and not reference["admitted"]
    assert reference["missing_fields"] == [] and "metrics" not in reference
    assert by_tool["evaluate"]["accepted"] and by_tool["evaluate"]["trial_index"] == 1
    assert by_tool["library_list"]["total"] == 1
    assert by_tool["library_get"]["factor_id"] == by_tool["evaluate"]["factor_id"]
    assert by_tool["library_stats"] == {"members": 1, "version": 1}
    run_matches = examples[7][1]["tool_result"]
    assert run_matches["total"] == 1
    assert run_matches["results"][0]["source"] == "run"
    assert run_matches["results"][0]["admitted"]
    assert examples[8][1]["tool_result"] is None
    assert examples[9][1]["tool_result"]["members"] == []
    errors = [
        i
        for i, (_, response) in enumerate(examples)
        if isinstance(response["tool_result"], dict) and "error" in response["tool_result"]
    ]
    assert errors == [10, 11]
    assert all("remaining_attempts" not in response for _, response in examples)
    budgets = [
        response["tool_result"]["remaining_attempts"]
        for request, response in examples
        if request["tool"] == "evaluate"
    ]
    assert budgets == [9, 8]
    assert examples[0][1] == examples[3][1]
    assert "remaining_attempts" not in context
    assert method.system_prompt == method._messages()[0]["content"]
    assert current.attempts_used == 2 and len(current._store.trials()) == 2
    assert len(current.get_library().members) == 1
    for _, response in examples:
        text = json.dumps(response)
        for key in (
            "snapshot_id",
            "run_id",
            "fold",
            "universe",
            "target_values",
            "observation_ref",
        ):
            assert f'"{key}"' not in text
    assert {metric["split"] for metric in by_tool["evaluate"]["metrics"]} == {
        "train",
        "val",
        "val_raw",
    }


def test_prompt_tools_and_budget_follow_actual_session(tmp_path):
    current = session(tmp_path, 3)
    recipe = OperatorDefinition(
        "DOUBLE_MEAN",
        (("x", "series"), ("n", "window")),
        "TS_MEAN(TS_MEAN(x,n),n)",
        description="Two rolling means | smooth `x`\nagain.",
    )
    assert current.register_operator(recipe).accepted
    method = search(
        current,
        [
            "not json",
            command("get_context"),
            command("library_stats"),
            command(expression="$x"),
            command(expression="$x"),
            command(expression="$missing"),
        ],
    )
    first = step(method, current)
    assert first.accepted
    assert step(method, current).reason == "expression_duplicate"
    assert step(method, current).reason == "compile_error"
    assert current.remaining_budget() == 0 and len(current._store.trials()) == 3
    assert method.usage["compressions"] == 0
    assert all(call[0]["content"] == method.system_prompt for call in method.models.calls)
    assert (
        "| Signature | Meaning | Scope / output | History / constraints |\n"
        "| --- | --- | --- | --- |\n"
    ) in method.system_prompt
    assert "Two rolling means \\| smooth &#96;x&#96; again." in method.system_prompt
    for op in current.list_operators():
        assert f"{op['name']}(" in method.system_prompt
        details = current.get_operator(op["name"])
        if details["kind"] == "builtin":
            assert details["description"]
            assert details["description"] in method.system_prompt
    assert (
        "| Expression | Meaning | Coarser suffixes |\n| --- | --- | --- |" in method.system_prompt
    )
    assert "| `$close` | Last traded price in the native bar. | none |" in method.system_prompt
    assert "| `$x` | Adapter-provided numeric feature;" in method.system_prompt
    for row in ("max_nodes | 100", "max_depth | 20"):
        assert f"| {row} |" in method.system_prompt
    assert "## 3. Research setting\n\n| Property | Value | Meaning |" in method.system_prompt
    assert "| target.price_field | close |" in method.system_prompt
    expression_rules = current.get_expression_rules()
    prose = method.system_prompt.replace("\n- ", " ")
    assert expression_rules["numeric_rules"] in prose
    assert expression_rules["timeframes"]["semantics"] in prose
    for key in ("aggregation", "direction", "coverage"):
        assert current.get_evaluation_rules()[key] in prose
    assert current.get_evaluation_rules()["deduplication"]["policy"] in prose
    for row in ("enabled | false", "name | null", "bars_per_day | 1"):
        assert f"| {row} |" in method.system_prompt
    assert "Forward return horizon in native bars, not wall-clock time." in method.system_prompt
    assert "strictly below this value" in method.system_prompt
    assert "must be at least this value" in method.system_prompt
    assert method.system_prompt.startswith("# Factor Research Agent\n\n## 1.")
    assert [
        line.split(".")[0] for line in method.system_prompt.splitlines() if line.startswith("## ")
    ] == [f"## {n}" for n in range(1, 9)]
    example = method.system_prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(example)["tool"] == "evaluate"
    assert str(tmp_path) not in method.system_prompt and "2020-01" not in method.system_prompt
    for field in ("snapshot_id", "run_id", "fold", "universe", "target_values"):
        assert f'"{field}"' not in method.system_prompt
        assert f"| {field} |" not in method.system_prompt
    assert set(asdict(current.get_context())) == {
        "asset",
        "frequency",
        "target",
        "metric",
        "remaining_attempts",
    }
    rules = current.get_evaluation_rules()
    assert rules["deduplication"]["absolute_correlation_lt"] == RULES["max_abs_corr"]
    rules["quality"]["val_ic_gte"] = 999
    assert current.get_evaluation_rules()["quality"]["val_ic_gte"] == RULES["min_abs_val_ic"]
    listed = method._query("library_list", {"offset": 0, "limit": 1})
    assert listed["total"] == 1
    detail = method._query("library_get", {"factor_id": first.report.expression_id})
    assert detail["direction"] == first.report.direction and len(detail["metrics"]) == 6
    assert "observation_ref" not in detail and "evaluation_id" not in detail
    assert method._query("library_get", {"factor_id": "missing"}) is None
    with pytest.raises(ValueError, match="budget"):
        method.ask(current.get_context())


def test_context_query_is_complete_isolated_and_does_not_load_reference(tmp_path, monkeypatch):
    def unexpected_load(*args, **kwargs):
        raise AssertionError("get_context must not load reference formulas")

    monkeypatch.setattr("alpha_atlas.factor_libraries.load_library", unexpected_load)
    current = session(tmp_path, reference_library="futures_cta")
    method = ReactSearch(42, models=object())
    method.set_session(current)
    method._initialize()
    context = method._query("get_context", {})
    assert set(context) == (set(asdict(current.get_context())) - {"remaining_attempts"}) | {
        "fields",
        "operators",
        "expression_rules",
        "evaluation_rules",
        "reference_library",
    }
    assert context["fields"] == current.get_fields()
    assert context["operators"] == [
        current.get_operator(op["name"]) for op in current.list_operators()
    ]
    assert context["expression_rules"] == current.get_expression_rules()
    assert context["evaluation_rules"] == current.get_evaluation_rules()
    assert context["reference_library"]["name"] == "futures_cta"
    context["operators"][0]["description"] = "changed"
    context["expression_rules"]["max_nodes"] = 1
    context["evaluation_rules"]["deduplication"]["absolute_correlation_lt"] = 0.1
    again = method._query("get_context", {})
    assert again["operators"][0]["description"] != "changed"
    assert again["expression_rules"] == current.get_expression_rules()
    assert again["evaluation_rules"] == current.get_evaluation_rules()
    assert current.remaining_budget() == 10 and not current._store.trials()


@pytest.mark.parametrize(
    "tool",
    [
        "get_fields",
        "list_operators",
        "get_operator",
        "get_expression_rules",
        "get_evaluation_rules",
    ],
)
def test_react_only_exposes_context_for_metadata_queries(tmp_path, tool):
    current = session(tmp_path)
    method = search(current, [command(tool), command(expression="$x")])
    assert step(method, current).accepted
    result = json.loads(method.rounds[0][1]["content"])
    assert "Unknown tool" in result["tool_result"]["error"]
    assert "remaining_attempts" not in result
    assert f"| `{tool}(" not in method.system_prompt


@pytest.mark.parametrize("frequency", ["5m", "1d"])
def test_prompt_explains_only_permitted_fields_and_their_timeframes(tmp_path, frequency):
    current = session(tmp_path)
    current._context = replace(current._context, frequency=frequency)
    current._fields = ("close", "open_interest_p1", "days_to_maturity_p2", "x")
    method = search(current, [])
    method._initialize()
    prompt = method.system_prompt
    suffixes = "`@15m`, `@30m`, `@60m`, `@1d`" if frequency == "5m" else "none"
    assert f"| `$close` | Last traded price in the native bar. | {suffixes} |" in prompt
    assert "Outstanding contract position count at bar end; a level, not a bar flow." in prompt
    assert (
        "Calendar days from trading_day to the contract's vendor-supplied maturity_date" in prompt
    )
    assert "### 4.2 First later-maturity contract (_p1)" in prompt
    assert "### 4.3 Second later-maturity contract (_p2)" in prompt
    field_section = prompt.split("## 4. Permitted input fields\n", 1)[1].split("\n## 5.", 1)[0]
    rows = [line for line in field_section.splitlines() if line.startswith("| `$")]
    assert len(rows) == 4
    assert all(f"| `${field}` |" in prompt for field in current.get_fields())
    assert rows[-1].endswith(f"| {suffixes} |")
    assert next(row for row in rows if row.startswith("| `$x` |")).endswith("| none |")
    assert "timeframes.fields |" not in prompt


@pytest.mark.parametrize(
    "reply",
    [
        "Done",
        "[]",
        '{"tool":"evaluate","arguments":{}}',
        command("register_operator"),
        command("library_list", limit=0),
        command("library_list", offset=-1),
        command("get_operator", name="MISSING"),
    ],
)
def test_no_progress_is_bounded_and_resumable_without_fake_trials(tmp_path, reply):
    current = session(tmp_path)
    method = search(current, [reply] * 10)
    with pytest.raises(RuntimeError, match="10 consecutive"):
        method.ask(current.get_context())
    assert len(method.models.calls) == 10 and current.attempts_used == 0
    restored = search(current, [command(expression="$x")])
    restored.load_state(method.dump_state())
    assert step(restored, current).accepted


def test_only_provider_overflow_triggers_compression_and_preserves_fixed_prompt(tmp_path):
    current = session(tmp_path)
    method = search(
        current,
        [
            command(expression="$x"),
            ContextLimitError(),
            "Past formulas were redundant.",
            command(expression="$close"),
        ],
    )
    method.rounds = cycles(30)  # No proactive compression even for a long transcript.
    step(method, current)
    assert len(method.rounds) == 31 and method.usage["compressions"] == 0
    recent = method.rounds[-2:]
    prompt = method.system_prompt
    step(method, current)
    assert method.usage["context_overflows"] == method.usage["compressions"] == 1
    assert method.rounds[:2] == recent and method.system_prompt == prompt
    summary_request = method.models.calls[2]
    assert summary_request[0]["content"] == SUMMARY
    assert "Operators (all currently registered)" not in json.dumps(summary_request)
    assert len(json.loads(summary_request[1]["content"])["cycles"]) == 29
    assert method.models.calls[-1][0]["content"] == prompt


def test_summary_overflow_splits_whole_cycles_and_merges_incrementally(tmp_path):
    current = session(tmp_path)
    method = search(
        current,
        [
            ContextLimitError(),
            ContextLimitError(),
            "First half.",
            "Merged halves.",
            command(expression="$x"),
        ],
    )
    method.summary = "Older memory."
    method.rounds = cycles(6)
    step(method, current)
    calls = method.models.calls
    assert [len(json.loads(c[1]["content"])["cycles"]) for c in calls[1:4]] == [4, 2, 2]
    assert json.loads(calls[3][1]["content"])["previous_summary"] == "First half."
    assert method.summary == "Merged halves." and len(method.rounds) == 3
    assert method.usage["context_overflows"] == 2 and current.attempts_used == 1


def test_repeated_overflow_reduces_recent_history_to_one_cycle(tmp_path):
    current = session(tmp_path)
    method = search(
        current,
        [
            ContextLimitError(),
            "One summary.",
            ContextLimitError(),
            "Smaller summary.",
            command(expression="$x"),
        ],
    )
    method.rounds = cycles(5)
    latest = method.rounds[-1]
    step(method, current)
    assert method.usage["compressions"] == 2 and method.rounds[0] == latest
    assert len(method.rounds) == 2


@pytest.mark.parametrize("failure", ["fixed", "single", "empty", "long", "service"])
def test_compression_failures_do_not_drop_history_or_consume_budget(tmp_path, failure):
    current = session(tmp_path)
    responses = {
        "fixed": [ContextLimitError()],
        "single": [ContextLimitError(), ContextLimitError()],
        "empty": [ContextLimitError(), ""],
        "long": [ContextLimitError(), "x" * 10000],
        "service": [RuntimeError("HTTP 429")],
    }
    method = search(current, responses[failure])
    method.rounds = cycles(0 if failure == "fixed" else 2)
    before = json.loads(json.dumps(method.rounds))
    with pytest.raises(RuntimeError):
        method.ask(current.get_context())
    assert current.attempts_used == 0 and method.rounds == before
    if failure == "service":
        assert method.usage["context_overflows"] == 0 and len(method.models.calls) == 1


@pytest.mark.parametrize(
    "boundary", ["response", "pending", "summary_response", "chunk", "compressed"]
)
def test_checkpoint_at_internal_boundaries_reuses_recorded_responses(tmp_path, boundary):
    current = session(tmp_path)
    responses = [command(expression="$x")]
    if boundary in {"summary_response", "chunk", "compressed"}:
        responses = [ContextLimitError(), "Completed summary.", *responses]
    method = search(current, responses)
    if len(responses) > 1:
        method.rounds = cycles(5)
    saved = None

    def checkpoint():
        nonlocal saved
        state = method.dump_state()
        hit = {
            "response": method.reply is not None and method.pending is None,
            "pending": method.pending is not None,
            "summary_response": method.reply is not None and method.reply["kind"] == "summary",
            "chunk": method.compression is not None and not method.compression["queue"],
            "compressed": method.usage["compressions"] == 1,
        }[boundary]
        if hit:
            saved = state
            raise KeyboardInterrupt("checkpoint saved")

    method.set_checkpoint(checkpoint)
    with pytest.raises(KeyboardInterrupt):
        method.ask(current.get_context())
    assert saved is not None
    restored = ReactSearch(42, models=method.models)
    restored.set_session(current)
    restored.load_state(saved)
    assert step(restored, current).accepted
    assert len(method.models.calls) == len(responses)
    assert restored.system_prompt == saved["system_prompt"]


@pytest.mark.parametrize(
    "boundary",
    [
        "before_evaluate",
        "before_commit",
        "after_commit",
        "tell",
        "before_checkpoint",
        "after_checkpoint",
    ],
)
def test_runner_resume_never_repeats_committed_evaluation(project, monkeypatch, boundary):
    monkeypatch.setattr("alpha_atlas.methods.react.HTTPModels", lambda _: FeedbackModel())
    with monkeypatch.context() as patch:
        path = interrupted_run(project, patch, "react", boundary, attempts=6)
    committed = {p: p.read_bytes() for p in (path / "trials").glob("*.json")}
    calls = []
    original = SearchSession.evaluate

    def evaluate(self, candidate, **kwargs):
        calls.append(candidate)
        return original(self, candidate, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SearchSession, "evaluate", evaluate)
        resume(project, path)
    reference = run(project, "ashare", "fold1", "react", 42, attempts=6)
    assert logical_trials(path) == logical_trials(reference)
    assert len(calls) == 6 - len(committed)
    assert all(p.read_bytes() == content for p, content in committed.items())
    state = read_json(path / "checkpoint.json")["method_state"]
    assert state["pending"] is None and len(state["rounds"]) == 6
    assert (
        state["system_prompt"]
        == read_json(reference / "checkpoint.json")["method_state"]["system_prompt"]
    )


@pytest.mark.parametrize("asset", ["ashare", "futures"])
def test_builtin_react_freeze_oos_config_and_report(project, monkeypatch, asset):
    monkeypatch.setattr("alpha_atlas.methods.react.HTTPModels", lambda _: FeedbackModel())
    if asset == "futures":
        rows = [
            {
                "timestamp": datetime(year, 1, 1, 9) + timedelta(minutes=5 * i),
                "trading_day": date(year, 1, 1),
                "exchange": "X",
                "product": "P",
                "instrument_id": f"P{year}",
                "close": 100 + 0.001 * i * i,
                "amount": 10.0,
            }
            for year in range(2015, 2023)
            for i in range(80)
        ]
        atomic_parq(with_row_id(pl.DataFrame(rows)), project / "data/futures/bars/fixture.parq")
        atomic_json(
            project / "data/futures/manifest.json",
            {"status": "complete", "snapshot_id": "synthetic", "rows": len(rows)},
        )
    price = "close" if asset == "futures" else "adj_close"
    path = run(project, asset, "fold1", "react", 42, attempts=3, field_names=[price])
    spec = read_json(path / "run.json")
    assert spec["implementation"] == "single_agent_react_v1" and spec["status"] == "frozen"
    state = read_json(path / "checkpoint.json")["method_state"]
    assert len(state["rounds"]) == 3 and state["usage"]["chat_requests"] == 3
    result = evaluate_frozen(project, path)
    assert result["results"] and all(r["status"] == "success" for r in result["results"])
    if asset == "futures":
        assert result["results"][0]["metrics"][0]["name"] == "time_series_weighted_pearson_ic"
        minimum = spec["profile"]["min_train_val_ic"]
        assert f"| quality.train_and_val_ic_gt | {minimum} |" in state["system_prompt"]
    report = (path / "report.md").read_text(encoding="utf-8")
    assert "compressions" in report and "summary_requests" in report
    config = project / "configs/react.toml"
    config.write_text(config.read_text().replace("temperature = 0.5", "temperature = 0.6"))
    with pytest.raises(ValueError, match="ReAct configuration changed"):
        evaluate_frozen(project, path)


@pytest.mark.parametrize(
    ("status", "error", "context"),
    [
        (400, {"code": "context_length_exceeded"}, True),
        (
            400,
            {"message": "This model's maximum context length is 8192 tokens. You requested 9000"},
            True,
        ),
        (400, {"message": "Input exceeds max_model_len"}, True),
        (400, {"message": "Invalid temperature"}, False),
        (400, {"message": "maximum context length configured"}, False),
        (401, {"code": "context_length_exceeded"}, False),
        (429, {"message": "rate limit"}, False),
        (500, {}, False),
    ],
)
def test_http_context_errors_are_explicit_and_redacted(monkeypatch, status, error, context):
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", lambda _: None)

    def fail(*args, **kwargs):
        raise HTTPError(
            "https://secret.invalid",
            status,
            "private text",
            {},
            io.BytesIO(json.dumps({"error": error}).encode()),
        )

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", fail)
    with pytest.raises(ContextLimitError if context else RuntimeError) as exc:
        HTTPModels(ReactConfig()).chat_messages([{"role": "user", "content": "test"}])
    assert isinstance(exc.value, ContextLimitError) == context
    assert "secret" not in str(exc.value) and "private" not in str(exc.value)


def test_react_config_and_state_are_strict_json(tmp_path):
    current = session(tmp_path)
    method = search(current, [])
    for options in (
        {"summary_output_tokens": 0},
        {"temperature": float("nan")},
        {"chat_base_url": "http://user:password@example.com"},
        {"timeout_seconds": 0},
    ):
        with pytest.raises(ValueError):
            ReactConfig(**options)
    state = method.dump_state()
    assert state == json.loads(json.dumps(state, allow_nan=False))
    other = ReactSearch(42, replace(method.config, temperature=0.1), models=method.models)
    with pytest.raises(ValueError, match="configuration"):
        other.load_state(state)


def sse(delta):
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode()


def test_stream_is_incremental_preserves_unicode_usage_and_think_tags(monkeypatch, capsys, caplog):
    import logging

    from alpha_atlas.reporting import TerminalStream

    class Response(io.BytesIO):
        def __iter__(self):
            yield b": heartbeat\n"
            yield from sse({"content": "<thi"}).splitlines(keepends=True)
            yield from sse({"content": "nk>检查量价关系"}).splitlines(keepends=True)
            # This assertion runs before the remaining response has been delivered.
            shown = capsys.readouterr()
            assert "检查量价关系" in shown.err and "流式" in shown.err and shown.out == ""
            yield from sse({"content": "</thi"}).splitlines(keepends=True)
            yield from sse({"content": 'nk>{"tool":"evaluate"}'}).splitlines(keepends=True)
            yield b'data: {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 8}}\n'
            yield b"\n"
            yield b"data: [DONE]\n"
            yield b"\n"

    def open_request(request, **kwargs):
        body = json.loads(request.data)
        assert body["stream"] and body["stream_options"] == {"include_usage": True}
        return Response()

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", open_request)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        text, usage = HTTPModels(ReactConfig()).chat_messages([], on_delta=stream.delta)
    assert text == '<think>检查量价关系</think>{"tool":"evaluate"}'
    assert usage == {"prompt_tokens": 20, "completion_tokens": 8}
    output = capsys.readouterr()
    assert "[模型输出 · 流式]" in output.err and "<think>" not in output.err
    assert "</thi" not in output.err and output.out == ""


@pytest.mark.parametrize("key", ["reasoning_content", "reasoning"])
@pytest.mark.parametrize("quiet", [False, True])
def test_separate_reasoning_stream_and_quiet(monkeypatch, capsys, caplog, key, quiet):
    import logging

    from alpha_atlas.reporting import TerminalStream

    data = sse({key: "独立思考\n检查\x1b"}) + sse({"content": "{}"}) + b"data: [DONE]\n\n"
    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", lambda *a, **kw: io.BytesIO(data))
    level = logging.CRITICAL + 1 if quiet else logging.INFO
    with caplog.at_level(level, logger="alpha_atlas.progress"), TerminalStream() as stream:
        text, usage = HTTPModels(ReactConfig()).chat_messages([], on_delta=stream.delta)
    assert text == "{}" and usage is None
    output = capsys.readouterr()
    assert output.out == "" and "\x1b" not in output.err
    assert ("独立思考" in output.err) == (not quiet)
    if quiet:
        assert output.err == ""


@pytest.mark.parametrize("tail", [b"", b"data: invalid\n\n", b'data: {"error":"secret"}\n\n'])
def test_incomplete_or_invalid_stream_never_creates_pending_candidate(tmp_path, monkeypatch, tail):
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", lambda _: None)
    current = session(tmp_path, 1)
    method = ReactSearch(42)
    method.set_session(current)
    data = sse({"content": command(expression="$x")}) + tail
    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", lambda *a, **kw: io.BytesIO(data))
    with pytest.raises(RuntimeError, match="stream") as error:
        method.ask(current.get_context())
    assert "secret" not in str(error.value)
    assert method.reply is None and method.pending is None and current.remaining_budget() == 1
    requests = 4 if tail == b"" else 1
    assert method.usage["chat_requests"] == requests
    assert method.usage["unknown_usage_requests"] == requests
    restored = search(current, [command(expression="$x", name="价格信号")])
    restored.load_state(method.dump_state())
    feedback = step(restored, current)
    assert feedback.candidate.name == "价格信号" and current.remaining_budget() == 0
    assert RunStore(tmp_path).trials()[0]["feedback"]["candidate"]["name"] == "价格信号"


def test_stream_http_context_error_still_triggers_only_explicit_overflow(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError(
            "private-url",
            400,
            "private",
            {},
            io.BytesIO(b'{"error":{"code":"context_length_exceeded"}}'),
        )

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", fail)
    with pytest.raises(ContextLimitError):
        HTTPModels(ReactConfig()).chat_messages([], on_delta=lambda *args: None)


def test_failed_trial_commit_never_prints_admission(tmp_path, monkeypatch, caplog):
    import logging

    current = session(tmp_path, 1)
    method = search(current, [command(expression="$x", name="价格信号")])

    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(RunStore, "record", fail)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        with pytest.raises(OSError, match="disk failure"):
            step(method, current)
    assert "价格信号" in caplog.text and "$x" in caplog.text
    assert "[因子入库]" not in caplog.text and "候选完成" not in caplog.text


def test_react_rejects_invalid_name_without_using_budget(tmp_path):
    current = session(tmp_path, 1)
    method = search(
        current,
        [command(expression="$x", name=["invalid"]), command(expression="$x", name="价格信号")],
    )
    candidate = method.ask(current.get_context())[0]
    assert candidate.name == "价格信号" and current.remaining_budget() == 1
    assert "error" in method.rounds[0][1]["content"]


def test_reference_search_is_lazy_read_only_paginated_and_explicitly_enabled(tmp_path, monkeypatch):
    from alpha_atlas import factor_libraries

    current = session(tmp_path, 2)
    current._context = replace(current._context, frequency="5m")
    calls = []
    original = factor_libraries.load_library

    def load(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(factor_libraries, "load_library", load)
    disabled = search(current, [])
    disabled._initialize()
    assert disabled._query("library_search", {"source": "reference"})["total"] == 0
    assert not calls
    current = session(tmp_path, 2, reference_library="futures_cta")
    current._context = replace(current._context, frequency="5m")
    method = ReactSearch(42, models=object())
    method.set_session(current)
    method._initialize()
    assert not calls
    first = method._query("library_search", {"source": "reference", "limit": 20})
    last = method._query("library_search", {"source": "reference", "offset": 20})
    assert first["total"] == 22 and len(first["results"]) == 20 and len(last["results"]) == 2
    found = method._query("library_search", {"query": "CARRY close_p1", "source": "reference"})
    assert found["total"] >= 1
    for row in found["results"]:
        assert not row["admitted"] and row["status"] == "missing_fields"
        assert "close_p1" in row["missing_fields"]
        assert "metrics" not in row and row["source_commit"]
    momentum = method._query("library_search", {"query": "tsmom_63"})["results"][0]
    assert momentum["expression"] == "RETURN($close, 63)"
    assert momentum["status"] == "available"
    assert current.remaining_budget() == 2 and current.get_library().version == 0
    assert not current._store.trials()
    restored = ReactSearch(42, method.config, models=object())
    restored.set_session(current)
    restored.load_state(method.dump_state())
    assert restored._query("library_search", {"query": "tsmom_63"})["results"] == [momentum]
    assert "library_search" in restored.system_prompt


def test_evaluate_auto_admission_and_search_are_returned_to_agent(tmp_path):
    current = session(tmp_path, 3)
    current._library = FactorLibrary({**RULES, "min_corr_overlap": 5})
    method = search(
        current,
        [
            command(expression="$x", name="Quality momentum"),
            command(expression="$x+1", name="Correlated copy"),
            command(expression="$not_allowed"),
        ],
    )
    admitted = step(method, current)
    assert admitted.accepted
    result = json.loads(method.rounds[-1][1]["content"])["tool_result"]
    assert result["accepted"] and result["reason"] == "accepted"
    assert result["library_version"] == 1 and result["trial_index"] == 1
    rows = method._query("library_search", {"query": "QUALITY momentum", "source": "run"})
    assert rows["total"] == 1 and rows["results"][0]["admitted"]
    assert rows["results"][0]["factor_id"] == admitted.report.expression_id
    assert {m["split"] for m in rows["results"][0]["metrics"]} <= {"train", "val", "val_raw"}
    rejected = step(method, current)
    assert not rejected.accepted and rejected.reason == "behavior_duplicate"
    result = json.loads(method.rounds[-1][1]["content"])["tool_result"]
    assert not result["accepted"] and result["max_abs_corr"] == pytest.approx(1)
    assert result["nearest_factor"] == admitted.report.expression_id
    assert result["library_version"] == 1
    step(method, current)
    assert not json.loads(method.rounds[-1][1]["content"])["tool_result"]["accepted"]
    assert method._query("library_search", {"source": "run"})["total"] == 1
    # Queries cannot see another run's independently admitted members.
    other = search(session(tmp_path / "other"), [])
    assert other._query("library_search", {"source": "run"})["total"] == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": 1},
        {"query": "x" * 1001},
        {"source": "test"},
        {"source": []},
        {"offset": -1},
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
    ],
)
def test_library_search_rejects_invalid_queries_without_spending_budget(tmp_path, arguments):
    current = session(tmp_path)
    method = search(current, [])
    with pytest.raises(ValueError):
        method._query("library_search", arguments)
    assert current.remaining_budget() == 10
