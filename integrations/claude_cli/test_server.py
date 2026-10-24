"""Run explicitly: uv run pytest -q integrations/claude_cli (all model I/O is fake)."""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from server import BridgeError, BridgeServer, _mcp, _response, _usage, complete

from alpha_atlas.methods.models import ContextLimitError, HTTPModels, _RetryableError


def request(prompt="inspect", **kwargs):
    return {
        "model": "synthetic-claude",
        "messages": [
            {"role": "system", "content": "Synthetic research instructions"},
            {"role": "user", "content": prompt},
        ],
        **kwargs,
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate",
            "description": "Queue a synthetic factor",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}},
        },
    }
]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    # The real `claude` executable is never invoked, even if installed and signed in.
    command = tmp_path / "fake-claude"
    command.write_text(
        f"#!{sys.executable}\n" + Path(__file__).with_name("fake_cli.py").read_text()
    )
    command.chmod(0o700)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-do-not-forward")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    return str(command)


@pytest.fixture
def bridge(cli, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    server = BridgeServer(0, command=cli, timeout=5)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    worker.start()
    try:
        yield server
    finally:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        worker.join()


def client(bridge):
    return HTTPModels(
        SimpleNamespace(
            chat_model="synthetic-claude",
            chat_base_url=f"http://127.0.0.1:{bridge.server_port}/v1",
            chat_key_env="SYNTHETIC_UNUSED_BRIDGE_KEY",
            temperature=0.5,
            timeout_seconds=5,
        )
    )


def test_text_is_raw_and_usage_includes_cache_without_double_counting(cli):
    result = complete(request(), command=cli, timeout=5)
    payload = json.loads(result["choices"][0]["message"]["content"])
    assert payload["prompt"] == "inspect" and payload["system"] == "Synthetic research instructions"
    assert not payload["api_key_inherited"] and not payload["provider_inherited"]
    assert result["model"] == "synthetic-claude-pinned"
    assert result["usage"] == {"prompt_tokens": 15, "completion_tokens": 11, "total_tokens": 26}
    args = payload["args"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert {"--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"} <= set(
        args
    )
    assert "--bare" not in args and not Path(payload["cwd"]).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(payload["pid"], 0)


@pytest.mark.parametrize("stream", [False, True])
def test_existing_http_models_accept_json_and_sse(bridge, stream):
    deltas = []
    text, usage = client(bridge).chat_messages(
        request()["messages"],
        on_delta=(lambda *args: deltas.append(args)) if stream else None,
    )
    assert json.loads(text)["prompt"] == "inspect"
    assert usage == {"prompt_tokens": 15, "completion_tokens": 11, "total_tokens": 26}
    assert bool(deltas) == stream


def test_simultaneous_calls_have_no_shared_cli_state(bridge):
    models = client(bridge)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(
            pool.map(lambda i: models.chat_once(request(str(i))["messages"])[0], range(4))
        )
    payloads = [json.loads(text) for text in replies]
    assert [p["prompt"] for p in payloads] == ["0", "1", "2", "3"]
    assert len({p["pid"] for p in payloads}) == len({p["cwd"] for p in payloads}) == 4


def test_tool_batch_and_transcript_replay_remain_caller_owned(cli):
    body = request("propose", tools=TOOLS)
    result = complete(body, command=cli, timeout=5)
    choice = result["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert [c["id"] for c in choice["message"]["tool_calls"]] == ["t1", "t2"]
    assert [
        json.loads(c["function"]["arguments"])["expression"]
        for c in choice["message"]["tool_calls"]
    ] == ["$x + 1", "$x + 2"]
    body["messages"] += [
        choice["message"],
        {"role": "tool", "tool_call_id": "t1", "content": "one"},
        {"role": "tool", "tool_call_id": "t2", "content": "two"},
    ]
    resumed = complete(body, command=cli, timeout=5)
    assert resumed["choices"][0]["message"]["content"] == "Read tool evidence: one | two"
    assert resumed["choices"][0]["finish_reason"] == "stop"


def test_native_react_uses_unchanged_agentscope_loop_and_queue(bridge):
    from alpha_atlas.methods.react import ReactConfig, ReactSearch

    config = ReactConfig(
        framework="agentscope",
        chat_model="synthetic-claude",
        chat_base_url=f"http://127.0.0.1:{bridge.server_port}/v1",
        timeout_seconds=5,
        chat_key_env="SYNTHETIC_UNUSED_BRIDGE_KEY",
    )
    method = ReactSearch(42, config)
    method.system_prompt = "Use evaluate for the two synthetic proposals, then wait."
    try:
        method._agent_reply("propose")
        assert method.models is None
        assert [c["expression"] for c in method.candidate_queue] == ["$x + 1", "$x + 2"]
        assert method.usage["chat_requests"] == 2
        assert method.usage["unknown_usage_requests"] == 0
        assert method.usage["prompt_tokens"] == 30
        assert method.usage["completion_tokens"] == 22
        state = json.loads(json.dumps(method.dump_state(), allow_nan=False))
        assert state["config"] == asdict(config)
        restored = ReactSearch(42, config)
        restored.load_state(state)
        assert restored.candidate_queue == method.candidate_queue
        assert restored.configuration == method.configuration
        restored.close()
    finally:
        method.close()


@pytest.mark.parametrize(
    "body",
    [
        request(tools=[{"type": "function", "function": {"name": "Bash"}}]),
        request(tool_choice="required"),
        request(n=2),
        request(response_format={"type": "json_object"}),
        request(
            messages=[{"role": "user", "content": [{"type": "image_url", "image_url": "secret"}]}]
        ),
    ],
)
def test_unsupported_input_fails_before_any_process(body, monkeypatch):
    monkeypatch.setattr(
        "server.subprocess.Popen", lambda *a, **k: pytest.fail("must not spawn CLI")
    )
    with pytest.raises(BridgeError) as raised:
        complete(body)
    assert raised.value.status == 400


@pytest.mark.parametrize("prompt", ["error", "invalid", "partial", "unavailable-tool"])
def test_cli_failure_never_returns_partial_or_private_output(cli, prompt):
    with pytest.raises(BridgeError) as raised:
        complete(request(prompt), command=cli, timeout=5)
    assert raised.value.status == 502
    assert "synthetic-private-key" not in str(raised.value)


@pytest.mark.parametrize("disconnect", [False, True])
def test_timeout_and_disconnect_kill_the_request_process(cli, tmp_path, disconnect):
    pidfile = tmp_path / "child.pid"
    with pytest.raises(BridgeError) as raised:
        complete(
            request(f"wait:{pidfile}"),
            command=cli,
            timeout=1,
            disconnected=lambda: disconnect and pidfile.exists(),
        )
    assert raised.value.status == (499 if disconnect else 504)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_bridge_does_not_retry_chat_once_and_never_proxies_embeddings(bridge):
    with pytest.raises(_RetryableError):
        client(bridge).chat_once(request("partial")["messages"])
    opener = build_opener(ProxyHandler({}))
    with pytest.raises(HTTPError) as raised:
        opener.open(Request(f"http://127.0.0.1:{bridge.server_port}/v1/embeddings", b"{}"))
    assert raised.value.code == 404
    raised.value.close()


@pytest.mark.parametrize(
    "prompt,exception", [("auth", RuntimeError), ("context", ContextLimitError)]
)
def test_permanent_cli_errors_keep_the_existing_client_retry_boundary(
    bridge, monkeypatch, prompt, exception
):
    monkeypatch.setattr(
        "alpha_atlas.methods.models.time.sleep", lambda _: pytest.fail("must not retry")
    )
    with pytest.raises(exception) as raised:
        client(bridge).chat_messages(request(prompt)["messages"])
    assert "synthetic-private-key" not in str(raised.value)


def test_sigterm_cleans_up_inflight_request_before_bridge_exits(cli, tmp_path):
    pidfile = tmp_path / "inflight.pid"
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("server.py")),
            "--port",
            "0",
            "--command",
            cli,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    failures = []
    try:
        url = process.stdout.readline().strip().removeprefix("Claude CLI bridge: ")

        def invoke():
            try:
                with build_opener(ProxyHandler({})).open(
                    Request(
                        url + "/chat/completions",
                        json.dumps(request(f"wait:{pidfile}")).encode(),
                        {"Content-Type": "application/json"},
                    ),
                    timeout=5,
                ):
                    pytest.fail("interrupted request cannot return a model answer")
            except Exception as exc:
                failures.append(type(exc).__name__)

        worker = threading.Thread(target=invoke)
        worker.start()
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert pidfile.exists()
        process.terminate()
        assert process.wait(timeout=5) == 0
        worker.join(5)
        assert not worker.is_alive() and failures
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_mcp_endpoint_is_schema_only_and_tool_handlers_wait_for_shutdown():
    with _mcp([{"name": "get_context", "inputSchema": {"type": "object"}}]) as (mcp, _):
        url = mcp["atlas"]["url"]
        result = []

        def invoke():
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_context", "arguments": {}},
            }
            with build_opener(ProxyHandler({})).open(Request(url, json.dumps(body).encode())) as r:
                result.append(json.load(r))

        thread = threading.Thread(target=invoke)
        thread.start()
        thread.join(0.1)
        assert thread.is_alive() and not result
    thread.join(2)
    assert not thread.is_alive() and "error" in result[0] and "result" not in result[0]


@pytest.mark.parametrize(
    "value", [{}, {"input_tokens": 1}, {"input_tokens": True, "output_tokens": 2}]
)
def test_missing_or_invalid_usage_stays_unknown(value):
    assert _usage(value) is None


@pytest.mark.parametrize("stop", ["max_tokens", "model_context_window_exceeded", "refusal"])
def test_truncated_or_refused_tool_arguments_cannot_reach_platform_execution(stop):
    with pytest.raises(BridgeError) as raised:
        _response(request(), {}, [{"type": "tool_use"}], {}, stop, [])
    assert raised.value.status == 400
