import io
import json
import logging
from http.client import IncompleteRead
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from alpha_atlas.methods.models import (
    ContextLimitError,
    HTTPModels,
    _RetryableError,
    parse_json_object,
)
from alpha_atlas.terminal import TerminalStream


@pytest.mark.parametrize("fence", [None, "```json", "```JSON", "```"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_model_json_allows_a_complete_fence_without_changing_contents(fence, newline):
    expected = {"expression": "$adj_close", "note": "文字 ```json {not another response}"}
    text = json.dumps(expected, ensure_ascii=False, indent=2).replace("\n", newline)
    if fence:
        text = newline.join([fence, text, "```"])
    assert parse_json_object(" \t" + text + "\n ") == expected


@pytest.mark.parametrize("fenced", [False, True])
@pytest.mark.parametrize("padding", ["", " \n "])
def test_model_json_removes_only_repeated_object_closings_inside_arrays(fenced, padding):
    text = (
        '{"proposals":[{"objective":"keep } , ] and \\"quotes\\"",'
        '"review":{"score":3}}}' + padding + ',{"objective":"second","review":{"score":4}}}]}'
    )
    if fenced:
        text = "```json\n" + text + "\n```"
    assert parse_json_object(text) == {
        "proposals": [
            {"objective": 'keep } , ] and "quotes"', "review": {"score": 3}},
            {"objective": "second", "review": {"score": 4}},
        ]
    }


@pytest.mark.parametrize("whitespace", ["\n", "\r", "\t", "\r\n"])
@pytest.mark.parametrize("fenced", [False, True])
@pytest.mark.parametrize("extra_closing", [False, True])
def test_model_json_preserves_literal_string_whitespace_and_escaped_content(
    whitespace, fenced, extra_closing
):
    note = 'Keep literal \\n, \\t and "quotes" unchanged'
    text = '{"expression":"a' + whitespace + 'b","note":' + json.dumps(note)
    text += ',"items":[{"score":3}' + ("}" if extra_closing else "") + "]}"
    if fenced:
        text = "```json\n" + text + "\n```"
    assert parse_json_object(text) == {
        "expression": "a" + whitespace + "b",
        "note": note,
        "items": [{"score": 3}],
    }


@pytest.mark.parametrize("control", ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\x1f"])
@pytest.mark.parametrize("prefix", ["", "line\n"])
def test_model_json_rejects_other_raw_controls_even_after_a_literal_newline(control, prefix):
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_json_object('{"expression":"' + prefix + control + '"}')


@pytest.mark.parametrize(
    "text",
    [
        'Here is the result: {"expression":"$x"}',
        '{"expression":"$x"} extra prose',
        '```json\n{"expression":"$x"}',
        '```python\n{"expression":"$x"}\n```',
        '```json\n{"expression":"$x"}\n```\nextra prose',
        "```json\n{}\n```\n```json\n{}\n```",
        '{"expression":"$x"} {"expression":"$y"}',
        '{"expression":"$x",}',
        '{"expression":"$x"}}',
        '{"items":[{"value":1} {"value":2}]}',
        '{"items":[{"value":1},]}',
        '{"items":[{"value":1}}],"expression":"unfinished',
        '{"expression":"first\nunfinished',
        '{"expression":"first\nsecond","note":"bad\\q"}',
        '{"expression":"first\nsecond" "missing_comma":1}',
        "{'expression':'$x'}",
        "[]",
        "```json\n[{}]\n```",
        "null",
        None,
    ],
)
def test_model_json_rejects_ambiguous_or_incomplete_replies(text):
    with pytest.raises(ValueError):
        parse_json_object(text)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ATLAS_TEST_MODEL_KEY", "synthetic-key")
    return HTTPModels(
        SimpleNamespace(
            chat_model="synthetic",
            chat_base_url="http://synthetic.invalid/v1",
            chat_key_env="ATLAS_TEST_MODEL_KEY",
            embedding_model="synthetic",
            embedding_base_url="http://synthetic.invalid/v1",
            embedding_key_env="ATLAS_TEST_MODEL_KEY",
            temperature=1.0,
            timeout_seconds=1,
        )
    )


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("kind", ["chat", "embedding"])
def test_transient_http_retries_same_request_then_succeeds(client, monkeypatch, status, kind):
    bodies, waits, retries, errors = [], [], [], []
    success = {"choices": [{"message": {"content": "{}"}}]} if kind == "chat" else {"ok": True}

    def request(req, **kwargs):
        bodies.append(req.data)
        if len(bodies) <= 3:
            error = HTTPError("private-url", status, "private-body", {}, io.BytesIO(b"private"))
            errors.append(error)
            raise error
        return io.BytesIO(json.dumps(success).encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    client.on_retry = retries.append
    assert client._post(kind, "/synthetic", {"model": "synthetic"}) == success
    assert len(bodies) == 4 and len(set(bodies)) == 1
    assert waits == ([30, 60, 120] if status == 429 else [1, 2, 4])
    assert retries == [kind] * 3
    assert all(e.fp.closed for e in errors)


@pytest.mark.parametrize(
    "status,header,expected",
    [
        (429, "75", 75),
        (429, "Thu, 01 Jan 1970 00:01:30 GMT", 90),
        (429, "Thu, 01 Jan 1970 00:00:00 GMT", 30),
        (429, "invalid private value", 30),
        (429, "-1", 30),
        (429, "nan", 30),
        (429, "inf", 30),
        (503, "120", 120),
    ],
)
def test_http_retry_after_is_respected_without_logging_provider_data(
    client, monkeypatch, status, header, expected
):
    error = HTTPError(
        "private-url", status, "private-body", {"Retry-After": header}, io.BytesIO(b"private")
    )

    def request(*args, **kwargs):
        raise error

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.time", lambda: 0)
    with pytest.raises(_RetryableError) as caught:
        client.chat_once([{"role": "user", "content": "synthetic"}])
    assert caught.value.retry_delay(0) == expected
    assert "private" not in str(caught.value) and error.fp.closed


@pytest.mark.parametrize("content", [None, "", "  "])
@pytest.mark.parametrize("streaming", [False, True])
def test_empty_http_200_reply_retries_the_same_request_then_recovers(
    client, monkeypatch, content, streaming
):
    calls, waits, retries, deltas = [], [], [], []
    usage = {"prompt_tokens": 7, "completion_tokens": 3}

    def request(req, **kwargs):
        calls.append(req.data)
        text = content if len(calls) == 1 else "{}"
        choice = {"delta" if streaming else "message": {"content": text}, "finish_reason": "stop"}
        body = json.dumps({"choices": [choice], "usage": usage})
        return io.BytesIO((f"data: {body}\n\ndata: [DONE]\n\n" if streaming else body).encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    client.on_retry = retries.append
    result = client.chat_messages(
        [], on_delta=(lambda *args: deltas.append(args)) if streaming else None
    )
    assert result == ("{}", usage)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert waits == [1] and retries == ["chat"]
    if streaming:
        assert ("restart", "") in deltas


def test_chat_once_empty_response_is_one_retryable_attempt(client, monkeypatch):
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(b'{"choices":[{"message":{"content":null}}]}')

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr(
        "alpha_atlas.methods.models.time.sleep", lambda _: pytest.fail("worker sleep")
    )
    client.on_retry = lambda _: pytest.fail("worker retry")
    with pytest.raises(_RetryableError, match="empty message content"):
        client.chat_once([])
    assert calls == [1]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": None},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {}}]},
    ],
)
def test_empty_response_shapes_exhaust_the_bounded_retry_budget(client, monkeypatch, response):
    calls, waits = [], []

    def request(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    with pytest.raises(RuntimeError, match="empty message content; failed after 4 requests"):
        client.chat_messages([])
    assert calls == [1] * 4 and waits == [1, 2, 4]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
def test_explicit_empty_completion_failures_are_not_retried(
    client, monkeypatch, streaming, finish_reason
):
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        choice = {
            "delta" if streaming else "message": {"content": None},
            "finish_reason": finish_reason,
        }
        body = json.dumps({"choices": [choice]})
        return io.BytesIO((f"data: {body}\n\ndata: [DONE]\n\n" if streaming else body).encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    client.on_retry = lambda _: pytest.fail("explicit failure must not retry")
    with pytest.raises(RuntimeError) as error:
        client.chat_messages([], on_delta=(lambda *args: None) if streaming else None)
    assert not isinstance(error.value, _RetryableError)
    assert calls == [1]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "error_tag",
    [
        {"code": "server_is_overloaded"},
        {"code": "service_unavailable_error"},
        {"type": "service_unavailable_error"},
    ],
)
def test_in_band_overload_retries_but_permanent_error_is_redacted(
    client, monkeypatch, streaming, error_tag, caplog
):
    calls, waits = [], []
    responses = [
        {"error": {**error_tag, "message": "private provider details"}},
        {"choices": [{"delta" if streaming else "message": {"content": "{}"}}]},
        {"error": {"code": "invalid_api_key", "message": "private synthetic-key"}},
    ]

    def request(*args, **kwargs):
        calls.append(1)
        body = json.dumps(responses.pop(0))
        return io.BytesIO((f"data: {body}\n\ndata: [DONE]\n\n" if streaming else body).encode())

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        assert (
            client.chat_messages([], on_delta=(lambda *args: None) if streaming else None)[0]
            == "{}"
        )
        with pytest.raises(RuntimeError, match="error response") as error:
            client.chat_messages([], on_delta=(lambda *args: None) if streaming else None)
    assert len(calls) == 3 and waits == [1]
    assert "private" not in str(error.value) + caplog.text
    assert "synthetic-key" not in caplog.text


@pytest.mark.parametrize("failure", [TimeoutError, ConnectionResetError, URLError, IncompleteRead])
def test_transport_exhaustion_is_bounded_and_redacted(client, monkeypatch, caplog, failure):
    calls, waits = [], []

    def request(*args, **kwargs):
        calls.append(1)
        raise failure(b"private-body")

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        with pytest.raises(RuntimeError, match="failed after 4 requests") as error:
            client.chat_messages([])
    assert len(calls) == 4 and waits == [1, 2, 4]
    assert "private" not in str(error.value) + caplog.text
    assert "synthetic-key" not in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 501])
def test_permanent_http_errors_do_not_retry(client, monkeypatch, status):
    calls, waits = [], []

    def request(*args, **kwargs):
        calls.append(1)
        raise HTTPError(
            "private-url",
            status,
            "private-body",
            {},
            io.BytesIO(b'{"error":{"code":"context_length_exceeded"}}'),
        )

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    with pytest.raises(RuntimeError) as error:
        client.chat_messages([])
    assert isinstance(error.value, ContextLimitError) == (status in {400, 413, 422})
    assert calls == [1] and waits == []


def test_incomplete_stream_restarts_content_usage_and_terminal_state(client, monkeypatch, capsys):
    def event(value):
        return ("data: " + json.dumps(value) + "\n\n").encode()

    first = event({"choices": [{"delta": {"content": "discarded<think>unfinished<thi"}}]})
    first += event({"choices": [], "usage": {"prompt_tokens": 999, "completion_tokens": 999}})
    second = event({"choices": [{"delta": {"content": "{}"}}]})
    usage = {"prompt_tokens": 5, "completion_tokens": 2}
    second += event({"choices": [], "usage": usage}) + b"data: [DONE]\n\n"
    streams = iter([first, second])
    monkeypatch.setattr(
        "alpha_atlas.methods.models.urlopen", lambda *a, **k: io.BytesIO(next(streams))
    )
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", lambda _: None)
    with TerminalStream() as stream:
        text, measured = client.chat_messages([], on_delta=stream.delta)
    assert text == "{}" and measured == usage


def test_terminal_stream_refreshes_one_redacted_tty_line(monkeypatch, caplog):
    import time

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr("alpha_atlas.terminal._PROGRESS_TEXT", "[Miner · round 2]")
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        with TerminalStream() as stream:
            stream.delta("reasoning", "private reasoning")
            time.sleep(0.35)
    output = terminal.getvalue()
    assert output.count("\r") >= 2
    assert "Miner · round 2" in output and "思考中" in output
    assert "private reasoning" not in output


def test_terminal_stream_reports_observed_activity_and_silence_without_reasoning(
    monkeypatch, caplog
):
    from alpha_atlas.terminal import _stream_display_width

    now = [0.0]
    monkeypatch.setattr("alpha_atlas.terminal.time.monotonic", lambda: now[0])
    monkeypatch.setattr("alpha_atlas.terminal._PROGRESS_TEXT", "MCTS-LLM │ 阶段=formula_0")
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        assert "等待首字" in stream._render_line()
        now[0] = 1.0
        stream.delta("reasoning", "private reasoning")
        assert "思考中" in stream._render_line()
        now[0] = 36.0
        line = stream._render_line()
        assert "无新内容 35s" in line and "private reasoning" not in line
        with monkeypatch.context() as narrow:
            narrow.setattr("alpha_atlas.terminal._terminal_width", lambda: 24)
            assert _stream_display_width(stream._render_line()) <= 24
        now[0] = 37.0
        stream.delta("content", '{"expression":"$close"}')
        line = stream._render_line()
        assert "接收回复" in line and "无新内容" not in line
        stream.delta("restart", "")
        line = stream._render_line()
        assert "等待首字" in line and "无新内容" not in line and stream.response == ""
        stream.delta("content", "<think>hidden reasoning")
        assert "思考中" in stream._render_line()
        stream.delta("content", '</think>{"expression":"$volume"}')
        line = stream._render_line()
        assert "接收回复" in line and "hidden reasoning" not in line


def test_terminal_stream_renders_only_reply_as_one_rewritten_line(client, monkeypatch, caplog):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    def event(delta):
        return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode()

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr("alpha_atlas.terminal._PROGRESS_TEXT", "[Explorer · round 1]")
    monkeypatch.setattr(
        "alpha_atlas.terminal.shutil.get_terminal_size",
        lambda **_: SimpleNamespace(columns=80, lines=24),
    )

    data = event({"reasoning_content": "separate private thought"})
    data += event({"analysis": "separate analysis channel"})
    data += event({"content": "<thinking>marked private thought</thinking>"})
    data += event({"content": "<analysis>another private thought</analysis>"})
    data += event({"content": "<thi"})
    data += event({"content": "nk>private thought</think>"})
    data += event({"content": '{"direction":"study '})
    data += event({"content": 'volatility"}'})
    data += b"data: [DONE]\n\n"
    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", lambda *a, **k: io.BytesIO(data))

    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        text, _ = client.chat_messages([], on_delta=stream.delta)

    output = terminal.getvalue()
    assert text == (
        "<thinking>marked private thought</thinking>"
        "<analysis>another private thought</analysis>"
        '<think>private thought</think>{"direction":"study volatility"}'
    )
    assert output.count("\n") == 0 and output.count("\r") > 1
    assert "study volatility" in output
    assert "private thought" not in output and "separate" not in output


def test_noninteractive_stream_keeps_one_request_without_response_noise(monkeypatch, caplog):
    from alpha_atlas.terminal import terminal_progress

    class Terminal(io.StringIO):
        def isatty(self):
            return False

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        terminal_progress("Explorer", transient=True, 计划=12, 对话="1/40")
        with TerminalStream() as stream:
            stream.delta("content", "<think>hidden thought</think>")
            stream.delta("content", "x" * 120 + "VISIBLE_TAIL")

    messages = [record.getMessage() for record in caplog.records]
    assert "hidden thought" not in "\n".join(messages)
    assert len(messages) == 1
    assert "VISIBLE_TAIL" not in messages[0]
    assert "Explorer" in messages[0] and "对话=1/40" in messages[0]
    assert "\r" not in messages[-1]


def test_terminal_stream_keeps_progress_and_latest_reply_visible(monkeypatch, caplog):
    from alpha_atlas.terminal import _display_width, _stream_display_width

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr(
        "alpha_atlas.terminal._PROGRESS_TEXT",
        "Miner │ 进度=12/500 · 计划=2",
    )
    monkeypatch.setattr(
        "alpha_atlas.terminal.shutil.get_terminal_size",
        lambda **_: SimpleNamespace(columns=73, lines=24),
    )

    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        stream.delta("content", "x" * 120)
        stream.delta("content", "tail")

    line = terminal.getvalue().split("\r")[-1].split("\x1b", maxsplit=1)[0]
    assert _display_width(line) <= 72
    assert _stream_display_width(line) <= 72
    assert "Miner" in line and "进度=12/500" in line and "计划=2" in line and "tail" in line


def test_narrow_stream_prioritizes_plan_and_dialogue_over_reply_preview(monkeypatch, caplog):
    monkeypatch.setattr("alpha_atlas.terminal._terminal_width", lambda: 78)
    monkeypatch.setattr(
        "alpha_atlas.terminal._PROGRESS_TEXT",
        "Explorer │ 计划=397 · 对话=22/40 · 模式=breadth",
    )
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        stream.delta("content", "x" * 512)
        line = stream._render_line()
    assert "计划=397" in line and "对话=22/40" in line
    assert " → " not in line


def test_terminal_stream_reply_ticker_stays_bounded_on_wide_terminals(monkeypatch, caplog):
    from alpha_atlas.terminal import _stream_display_width

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr(
        "alpha_atlas.terminal._PROGRESS_TEXT",
        "Miner │ 计划=24 · 进度=117/500 · 模式=breadth · 计划候选=2/5 · 修复=1/4",
    )
    monkeypatch.setattr(
        "alpha_atlas.terminal.shutil.get_terminal_size",
        lambda **_: SimpleNamespace(columns=240, lines=24),
    )

    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        stream.delta("content", "x" * 240)

    line = terminal.getvalue().split("\r")[-1].split("\x1b", maxsplit=1)[0]
    ticker = line.rsplit(" → ", maxsplit=1)[1]
    assert _stream_display_width(ticker) <= 32
    assert ticker.endswith("x" * 30)


def test_progress_notices_refresh_the_active_response_row(monkeypatch, caplog):
    from alpha_atlas.terminal import terminal_progress

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr("alpha_atlas.terminal._PROGRESS_TEXT", "Miner │ 计划=2 · 进度=8/500")
    monkeypatch.setattr(
        "alpha_atlas.terminal.shutil.get_terminal_size",
        lambda **_: SimpleNamespace(columns=80, lines=24),
    )

    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"), TerminalStream() as stream:
        stream.delta("content", "partial")
        terminal_progress("模型请求重试", 重试="1/3", 等待="2s")
        stream.delta("restart", "")
        stream.delta("content", "回复完成")

    output = terminal.getvalue()
    final_line = output.split("\r")[-1].split("\x1b", maxsplit=1)[0]
    assert output.count("\n") == 0 and "模型请求重试" in output
    assert sum("模型请求重试" in record.getMessage() for record in caplog.records) == 1
    assert "模型请求重试" not in final_line and "回复完成" in final_line


def test_tty_progress_is_replaced_and_cleared_before_a_permanent_event(monkeypatch):
    from alpha_atlas.terminal import terminal_progress

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr("alpha_atlas.terminal.sys.stderr", terminal)
    monkeypatch.setattr("alpha_atlas.terminal._STATUS_VISIBLE", False)
    logger = logging.getLogger("alpha_atlas.progress")
    monkeypatch.setattr(logger, "handlers", [logging.StreamHandler(terminal)])
    monkeypatch.setattr(logger, "level", logging.INFO)
    terminal_progress("Explorer", transient=True, 计划=397, 对话="7/40")
    terminal_progress("Explorer", transient=True, 计划=397, 对话="8/40")
    assert "\n" not in terminal.getvalue()
    terminal_progress("研究方向", 计划=397, 方向="检验量价背离")
    output = terminal.getvalue()
    assert "\r\x1b[K  研究方向\n" in output
    assert output.endswith("    方向: 检验量价背离\n\n")


def test_interrupt_during_retry_wait_stops_immediately(client, monkeypatch):
    calls, retries = [], []

    def request(*args, **kwargs):
        calls.append(1)
        raise TimeoutError()

    def sleep(_):
        raise KeyboardInterrupt()

    client.on_retry = retries.append
    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        client.chat_messages([])
    assert calls == [1] and retries == []
