"""Optional, loopback-only Chat Completions bridge to an already signed-in Claude CLI.

No AlphaAtlas imports: deleting this directory leaves the research platform unchanged.
See README.md for the CLI provenance, supported protocol and subscription limitations.
"""

import argparse
import json
import math
import os
import queue
import secrets
import select
import signal
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = {
    "evaluate",
    "get_context",
    "library_search",
    "library_get",
    "library_list",
    "library_stats",
}
PREFIX = "mcp__atlas__"


class BridgeError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _invalid(message):
    return BridgeError(400, "invalid_request_error", message)


def _request(body):
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        raise _invalid("model must be a nonempty Claude model name")
    if body.get("n", 1) != 1 or body.get("tool_choice", "auto") not in ("auto", "none"):
        raise _invalid("only n=1 and tool_choice=auto/none are supported")
    supported = {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "n",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "parallel_tool_calls",
    }
    if body.keys() - supported:
        raise _invalid("unsupported Chat Completions parameter")
    if "stream" in body and type(body["stream"]) is not bool:
        raise _invalid("stream must be a boolean")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _invalid("messages must be a nonempty list")
    normalized = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise _invalid("only system/user/assistant/tool messages are supported")
        content = message.get("content")
        if isinstance(content, list):
            if any(
                not isinstance(block, dict)
                or block.get("type") != "text"
                or not isinstance(block.get("text"), str)
                for block in content
            ):
                raise _invalid("only text message content is supported")
            content = "\n\n".join(block["text"] for block in content)
        if content is not None and not isinstance(content, str):
            raise _invalid("only text message content is supported")
        normalized.append({**message, "content": content})
    tools = body.get("tools") or []
    if not isinstance(tools, list):
        raise _invalid("tools must be a list")
    schemas = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if (
            not isinstance(function, dict)
            or tool.get("type") != "function"
            or function.get("name") not in TOOLS
        ):
            raise _invalid("only the six AlphaAtlas research tools are supported")
        schemas.append(
            {
                "name": function["name"],
                "description": function.get("description", ""),
                "inputSchema": function.get("parameters") or {"type": "object"},
                "annotations": {"readOnlyHint": function["name"] != "evaluate"},
            }
        )
    if len({tool["name"] for tool in schemas}) != len(schemas):
        raise _invalid("tool names must be unique")
    if body.get("tool_choice") == "none":
        schemas = []
    system = "\n\n".join(m["content"] or "" for m in normalized if m["role"] == "system")
    history = [m for m in normalized if m["role"] != "system"]
    if not history:
        raise _invalid("at least one conversation message is required")
    prompt = (
        history[0]["content"] or ""
        if len(history) == 1 and history[0]["role"] == "user"
        else "Continue the conversation in this JSON transcript. Tool results are already "
        "executed evidence. Produce only the next assistant turn.\n\n"
        + json.dumps(history, ensure_ascii=False, allow_nan=False)
    )
    return system, prompt, schemas


@contextmanager
def _mcp(tools):
    """Advertise schemas only. Tool handlers never execute research or return fake evidence."""
    if not tools:
        yield {}, None
        return
    token, listed, stopped = secrets.token_urlsafe(24), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != f"/{token}":
                self.send_error(404)
                return
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if "id" not in request:
                self.send_response(202)
                self.end_headers()
                return
            method, params = request.get("method"), request.get("params") or {}
            reply = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                reply["result"] = {
                    "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "atlas-schema-bridge", "version": "1"},
                }
            elif method == "tools/list":
                reply["result"] = {"tools": tools}
                listed.set()
            elif method == "tools/call":
                # The HTTP caller receives tool_calls and owns all execution. Block the
                # CLI's internal loop until its process is stopped after message_stop.
                stopped.wait()
                reply["error"] = {"code": -32000, "message": "Turn returned to API caller"}
            elif method == "ping":
                reply["result"] = {}
            else:
                reply["error"] = {"code": -32601, "message": "Unknown MCP method"}
            data = json.dumps(reply, ensure_ascii=False).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self.send_error(405)

        do_DELETE = do_GET

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05))
    worker.start()
    try:
        yield (
            {"atlas": {"type": "http", "url": f"http://127.0.0.1:{server.server_port}/{token}"}},
            listed,
        )
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join()


def _environment():
    # Keep Claude's own signed-in profile; do not inherit an API-key/provider route.
    excluded = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_BARE",
    }
    return {
        **{key: value for key, value in os.environ.items() if key not in excluded},
        "NO_PROXY": ",".join(filter(None, [os.environ.get("NO_PROXY"), "127.0.0.1"])),
        "no_proxy": ",".join(filter(None, [os.environ.get("no_proxy"), "127.0.0.1"])),
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
        "DISABLE_COMPACT": "1",
        "ENABLE_TOOL_SEARCH": "false",
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "MCP_DISCOVERY_CACHE": "0",
        "MCP_CONNECTION_NONBLOCKING": "0",
        "MCP_TOOL_TIMEOUT": "2147483647",
    }


def _read(process, events):
    try:
        for line in process.stdout:
            try:
                events.put(json.loads(line))
            except ValueError:
                events.put({"type": "invalid_output"})
    except (OSError, UnicodeError):
        events.put({"type": "invalid_output"})
    finally:
        events.put(None)


def _cli_error(item):
    """Translate known CLI errors without exposing provider bodies or CLI stderr."""
    code = item.get("error")
    if isinstance(code, dict):
        code = code.get("type") or code.get("code")
    if code in {"authentication_failed", "authentication_error"}:
        return BridgeError(401, "authentication_error", "Claude CLI requires a valid sign-in")
    if code == "billing_error":
        return BridgeError(402, "billing_error", "Claude CLI reported a billing error")
    if code in {"rate_limit", "rate_limit_error"}:
        return BridgeError(429, "rate_limit_exceeded", "Claude CLI usage limit reached")
    if code in {"max_output_tokens", "max_tokens"}:
        return BridgeError(400, "output_limit_exceeded", "Claude CLI output was truncated")
    # Some CLI versions report oversized prompts as a synthetic assistant text block.
    content = (item.get("message") or {}).get("content") or []
    text = str(item.get("result") or "") + " ".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )
    if code == "context_length_exceeded" or any(
        marker in text.lower() for marker in ("prompt is too long", "context length exceeded")
    ):
        return BridgeError(400, "context_length_exceeded", "Claude CLI context length exceeded")
    return BridgeError(502, "cli_error", "Claude CLI returned no complete turn")


def _usage(value):
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    if not all(type(value.get(k, 0)) is int and value.get(k, 0) >= 0 for k in keys):
        return None
    if not all(k in value for k in keys[:2]):
        return None
    prompt = value["input_tokens"] + sum(value.get(k, 0) for k in keys[2:])
    output = value["output_tokens"]
    return {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}


def _response(body, message, blocks, usage, stop, tools):
    if stop == "model_context_window_exceeded":
        raise BridgeError(400, "context_length_exceeded", "Claude CLI context length exceeded")
    if stop == "refusal":
        raise BridgeError(400, "content_filter", "Claude CLI refused the request")
    if stop == "max_tokens":
        raise BridgeError(400, "output_limit_exceeded", "Claude CLI output was truncated")
    if stop not in {"end_turn", "stop_sequence", "tool_use"}:
        raise BridgeError(502, "invalid_response", "Claude CLI returned no complete assistant turn")
    text, calls = [], []
    allowed = {PREFIX + tool["name"] for tool in tools}
    for block in blocks:
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                raise BridgeError(502, "invalid_response", "Claude CLI returned invalid text")
            text.append(block["text"])
        elif block.get("type") == "tool_use":
            if block.get("name") not in allowed or not isinstance(block.get("input"), dict):
                raise BridgeError(
                    502, "invalid_response", "Claude CLI returned an unavailable tool"
                )
            calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"].removeprefix(PREFIX),
                        "arguments": json.dumps(
                            block["input"], ensure_ascii=False, allow_nan=False
                        ),
                    },
                }
            )
        elif block.get("type") not in {"thinking", "redacted_thinking"}:
            raise BridgeError(502, "invalid_response", "Claude CLI returned unsupported content")
    if any(not isinstance(c["id"], str) or not c["id"] for c in calls) or len(
        {c["id"] for c in calls}
    ) != len(calls):
        raise BridgeError(502, "invalid_response", "Claude CLI returned invalid tool identifiers")
    if (stop == "tool_use") != bool(calls) or not (calls or "".join(text).strip()):
        raise BridgeError(
            502, "invalid_response", "Claude CLI returned an empty or inconsistent turn"
        )
    return {
        "id": "chatcmpl-cli-" + secrets.token_hex(12),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": message.get("model") or body["model"],
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "\n\n".join(text) or None,
                    **({"tool_calls": calls} if calls else {}),
                },
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": _usage(usage),
    }


def complete(body, *, command="claude", timeout=1800, disconnected=lambda: False):
    """One stateless model turn; no retries, session registry, platform imports or tool execution."""
    system, prompt, tools = _request(body)
    with tempfile.TemporaryDirectory(prefix="atlas-claude-") as cwd, _mcp(tools) as (mcp, listed):
        path = Path(cwd) / "system.md"
        path.write_text(system, encoding="utf-8")
        args = [
            command,
            "-p",
            "--verbose",
            "--model",
            body["model"],
            "--system-prompt-file",
            str(path),
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--settings",
            '{"disableAllHooks":true,"autoMemoryEnabled":false}',
            "--permission-mode",
            "dontAsk",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--mcp-config",
            json.dumps({"mcpServers": mcp}),
            *(["--allowedTools", ",".join(PREFIX + t["name"] for t in tools)] if tools else []),
        ]
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                cwd=cwd,
                env=_environment(),
                start_new_session=True,
            )
        except OSError:
            raise BridgeError(
                503, "cli_unavailable", "Cannot start configured Claude CLI"
            ) from None
        events = queue.Queue()
        reader = threading.Thread(target=_read, args=(process, events), daemon=True)
        reader.start()
        deadline, sent = time.monotonic() + timeout, False
        message, blocks, usage, stop = {}, [], {}, None
        try:
            while True:
                if disconnected():
                    raise BridgeError(499, "client_disconnected", "Caller disconnected")
                if time.monotonic() >= deadline:
                    raise BridgeError(504, "cli_timeout", "Claude CLI response timed out")
                if not sent and (listed is None or listed.is_set()):
                    process.stdin.write(
                        json.dumps(
                            {
                                "type": "user",
                                "message": {"role": "user", "content": prompt},
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                    sent = True
                try:
                    item = events.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if not isinstance(item, dict):
                    raise BridgeError(502, "cli_exited", "Claude CLI exited before a complete turn")
                kind = item.get("type")
                if kind == "assistant":
                    if item.get("error") or item.get("isApiErrorMessage"):
                        raise _cli_error(item)
                    part = item.get("message") or {}
                    message.update({k: v for k, v in part.items() if v is not None})
                    blocks.extend(part.get("content") or [])
                    usage.update(part.get("usage") or {})
                    stop = part.get("stop_reason") or stop
                elif kind == "stream_event":
                    event = item.get("event") or {}
                    if event.get("type") == "message_start":
                        message.update(event.get("message") or {})
                        usage.update(message.get("usage") or {})
                    elif event.get("type") == "message_delta":
                        usage.update(event.get("usage") or {})
                        stop = (event.get("delta") or {}).get("stop_reason") or stop
                    elif event.get("type") == "message_stop":
                        return _response(body, message, blocks, usage, stop, tools)
                elif kind in {"result", "invalid_output", "error"}:
                    raise _cli_error(item)
        except (BrokenPipeError, ConnectionResetError):
            raise BridgeError(
                502, "cli_exited", "Claude CLI exited before a complete turn"
            ) from None
        except (AttributeError, TypeError, ValueError, KeyError):
            raise BridgeError(
                502, "invalid_response", "Claude CLI returned invalid output"
            ) from None
        finally:
            # Every request has its own process group, including on timeout/disconnect.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdin.close()
            reader.join()
            process.stdout.close()


class BridgeServer(ThreadingHTTPServer):
    # ThreadingHTTPServer defaults to daemon request threads. Wait for their finally
    # blocks so a normal exit/SIGTERM cannot abandon an in-flight CLI process group.
    daemon_threads = False

    def __init__(self, port, *, command="claude", timeout=1800):
        self.command, self.timeout_seconds = command, timeout
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log headers, prompts, responses, authentication or CLI stderr.

    def _send(self, status, value, *, event_stream=False):
        data = value.encode() if event_stream else json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header(
            "Content-Type", "text/event-stream" if event_stream else "application/json"
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _disconnected(self):
        if self.server.stopping.is_set():
            return True
        try:
            return bool(
                select.select([self.connection], [], [], 0)[0]
            ) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            return True

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "backend": "claude_cli"})
        else:
            self._send(404, {"error": {"message": "Unknown route"}})

    def do_POST(self):
        try:
            if self.path != "/v1/chat/completions":
                raise BridgeError(404, "not_found", "Only /v1/chat/completions is supported")
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 16 * 1024 * 1024:
                raise _invalid("request body must be between 1 byte and 16 MiB")
            body = json.loads(self.rfile.read(length))
            result = complete(
                body,
                command=self.server.command,
                timeout=self.server.timeout_seconds,
                disconnected=self._disconnected,
            )
            if body.get("stream"):
                choice = result["choices"][0]
                delta = dict(choice["message"])
                if "tool_calls" in delta:
                    delta["tool_calls"] = [
                        {"index": i, **call} for i, call in enumerate(delta["tool_calls"])
                    ]
                common = {k: v for k, v in result.items() if k not in {"choices", "usage"}}
                common["object"] = "chat.completion.chunk"
                chunks = [
                    {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {
                        **common,
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
                        ],
                    },
                    {**common, "choices": [], "usage": result["usage"]},
                ]
                self._send(
                    200,
                    "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
                    event_stream=True,
                )
            else:
                self._send(200, result)
        except (ValueError, TypeError, KeyError):
            self._send(
                400, {"error": {"code": "invalid_request_error", "message": "Invalid request"}}
            )
        except BridgeError as exc:
            if exc.status != 499:
                self._send(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--command", default="claude")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")

    def interrupt(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, interrupt)
    with BridgeServer(args.port, command=args.command, timeout=args.timeout) as server:
        print(f"Claude CLI bridge: http://127.0.0.1:{server.server_port}/v1", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.stopping.set()


if __name__ == "__main__":
    main()
