"""Synthetic CLI fixture; no credentials, model SDK, provider or market access."""

import json
import os
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


def emit(value):
    print(json.dumps(value), flush=True)


def event(kind, **kwargs):
    emit({"type": "stream_event", "event": {"type": kind, **kwargs}})


def main():
    args = sys.argv[1:]

    def option(name):
        return args[args.index(name) + 1]

    servers = json.loads(option("--mcp-config"))["mcpServers"]
    tools = []
    if servers:
        url = servers["atlas"]["url"]
        opener = build_opener(ProxyHandler({}))

        def rpc(method):
            request = Request(
                url,
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": method}).encode(),
                {"Content-Type": "application/json"},
            )
            with opener.open(request) as response:
                return json.load(response)["result"]

        rpc("initialize")
        tools = rpc("tools/list")["tools"]
    prompt = json.loads(sys.stdin.readline())["message"]["content"]
    if prompt.startswith("wait:"):
        Path(prompt[5:]).write_text(str(os.getpid()))
        time.sleep(30)
        return
    if prompt == "error":
        print("synthetic-private-key in CLI stderr", file=sys.stderr)
        emit({"type": "result", "is_error": True, "result": "synthetic-private-key"})
        return
    if prompt in {"auth", "context"}:
        emit(
            {
                "type": "assistant",
                "isApiErrorMessage": True,
                "error": "authentication_failed" if prompt == "auth" else "invalid_request",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Prompt is too long: synthetic-private-key",
                        }
                    ]
                },
            }
        )
        return
    if prompt == "invalid":
        print("synthetic-private-key in malformed stdout", flush=True)
        return

    usage = {
        "input_tokens": 3,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 7,
        "output_tokens": 1,
    }
    message = {"id": "synthetic-message", "model": "synthetic-claude-pinned", "usage": usage}
    event("message_start", message=message)
    if prompt == "propose":
        assert tools and next(t for t in tools if t["name"] == "evaluate")["annotations"] == {
            "readOnlyHint": False,
        }
        blocks = [{"type": "text", "text": "Two synthetic proposals"}] + [
            {
                "type": "tool_use",
                "id": f"t{i}",
                "name": "mcp__atlas__evaluate",
                "input": {"expression": f"$x + {i}", "hypothesis": "Synthetic evidence"},
            }
            for i in (1, 2)
        ]
    elif prompt == "unavailable-tool":
        blocks = [{"type": "tool_use", "id": "bad", "name": "Bash", "input": {}}]
    elif "JSON transcript" in prompt:
        history = json.loads(prompt.split("\n\n", 1)[1])
        results = [m["content"] for m in history if m["role"] == "tool"]
        blocks = [{"type": "text", "text": "Read tool evidence: " + " | ".join(results)}]
    else:
        blocks = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "prompt": prompt,
                        "pid": os.getpid(),
                        "cwd": os.getcwd(),
                        "system": Path(option("--system-prompt-file")).read_text(),
                        "args": args,
                        "api_key_inherited": "ANTHROPIC_API_KEY" in os.environ,
                        "provider_inherited": "CLAUDE_CODE_USE_BEDROCK" in os.environ,
                    }
                ),
            }
        ]
    for i, block in enumerate(blocks):
        event("content_block_start", index=i, content_block={"type": block["type"]})
        emit({"type": "assistant", "message": {**message, "content": [block]}})
        event("content_block_stop", index=i)
    if prompt == "partial":
        return
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    event("message_delta", delta={"stop_reason": stop}, usage={"output_tokens": 11})
    event("message_stop")
    # The bridge must stop here instead of allowing a hidden CLI reasoning/tool loop.
    time.sleep(30)


if __name__ == "__main__":
    main()
