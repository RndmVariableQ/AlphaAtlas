import io
import json
import logging
from http.client import IncompleteRead
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from alpha_atlas.methods.models import ContextLimitError, HTTPModels
from alpha_atlas.reporting import TerminalStream


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

    def request(req, **kwargs):
        bodies.append(req.data)
        if len(bodies) <= 3:
            error = HTTPError("private-url", status, "private-body", {}, io.BytesIO(b"private"))
            errors.append(error)
            raise error
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr("alpha_atlas.methods.models.urlopen", request)
    monkeypatch.setattr("alpha_atlas.methods.models.time.sleep", waits.append)
    client.on_retry = retries.append
    assert client._post(kind, "/synthetic", {"model": "synthetic"}) == {"ok": True}
    assert len(bodies) == 4 and len(set(bodies)) == 1
    assert waits == [1, 2, 4] and retries == [kind] * 3
    assert all(e.fp.closed for e in errors)


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
        assert not stream.thinking and stream.buffer == ""
    assert text == "{}" and measured == usage


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
