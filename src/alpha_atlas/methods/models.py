"""Shared small HTTP client for chat and embeddings; no SDK or provider state."""

import json
import os
import time
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from alpha_atlas.reporting import TerminalStream, terminal_progress


class ContextLimitError(RuntimeError):
    """The provider explicitly rejected an oversized model context."""


class _RetryableError(RuntimeError):
    """A transient transport failure; message contains no provider data."""


class HTTPModels:
    """Small Chat Completions / embeddings client; credentials are read only on a real request."""

    def __init__(self, config):
        self.config = config
        self.on_retry = lambda kind: None
        if not config.chat_model:
            raise ValueError("configure chat_model in the method configuration")
        if getattr(config, "embedding_model", None) == "":
            raise ValueError("configure chat_model and embedding_model in configs/alphaprobe.toml")

    def _post(self, kind, route, body, *, on_delta=None):
        for attempt in range(4):
            try:
                return self._post_once(kind, route, body, on_delta=on_delta)
            except _RetryableError as exc:
                if attempt == 3:
                    raise RuntimeError(f"{exc}; failed after 4 requests") from None
                if on_delta is not None:
                    on_delta("restart", "")
                delay = 2**attempt
                terminal_progress(
                    "模型请求重试", 类型=kind, 重试=f"{attempt + 1}/3", 等待秒=delay, 原因=str(exc)
                )
                time.sleep(delay)
                self.on_retry(kind)  # Persist request count before sending another request.

    def _post_once(self, kind, route, body, *, on_delta=None):
        config = self.config
        key = os.environ.get(getattr(config, f"{kind}_key_env"), "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = Request(
            getattr(config, f"{kind}_base_url").rstrip("/") + route,
            data=json.dumps(body, allow_nan=False).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                if on_delta is not None:
                    return self._stream(response, on_delta)
                return json.load(response)
        except HTTPError as exc:
            if exc.code in {408, 429, 500, 502, 503, 504}:
                exc.close()
                raise _RetryableError(f"{kind} model request failed (HTTP {exc.code})") from None
            if kind == "chat" and exc.code in {400, 413, 422}:
                try:
                    error = json.loads(exc.read(65536)).get("error", {})
                    code = error.get("code") if isinstance(error, dict) else None
                    message = error.get("message", "") if isinstance(error, dict) else ""
                    message = message.lower() if isinstance(message, str) else ""
                    if code == "context_length_exceeded" or (
                        ("maximum context length" in message or "max_model_len" in message)
                        and any(word in message for word in ("exceed", "too long", "requested"))
                    ):
                        raise ContextLimitError("model context length exceeded") from None
                except (ValueError, AttributeError, OSError):
                    pass
            raise RuntimeError(f"{kind} model request failed (HTTP {exc.code})") from None
        except (URLError, OSError, IncompleteRead):
            raise _RetryableError(f"{kind} model connection failed or timed out") from None
        except ValueError:
            # Never log provider bodies, authorization headers, or exception URLs.
            raise RuntimeError(f"{kind} model request failed or returned invalid JSON") from None

    @staticmethod
    def _stream(response, on_delta):
        """Consume SSE events immediately; incomplete streams never become saved replies."""
        content, usage, event = [], None, []
        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                event.append(line[5:].lstrip())
            elif not line and event:
                data = "\n".join(event)
                event.clear()
                if data == "[DONE]":
                    return {"choices": [{"message": {"content": "".join(content)}}], "usage": usage}
                try:
                    chunk = json.loads(data)
                    if "error" in chunk:
                        raise ValueError("stream error")
                    if chunk.get("usage") is not None:
                        usage = chunk["usage"]
                    for choice in chunk["choices"]:
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice["delta"]
                        for key in ("reasoning_content", "reasoning", "content"):
                            text = delta.get(key)
                            if text is None:
                                continue
                            if not isinstance(text, str):
                                raise ValueError("invalid delta")
                            if key == "content":
                                content.append(text)
                            if text:
                                on_delta("content" if key == "content" else "reasoning", text)
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise RuntimeError("chat model returned an invalid stream") from None
        raise _RetryableError("chat model stream ended before completion")

    def chat(self, system, payload, *, temperature=None):
        with TerminalStream() as stream:
            return self.chat_messages(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                on_delta=stream.delta,
                temperature=temperature,
            )

    def chat_messages(self, messages, *, max_output_tokens=None, on_delta=None, temperature=None):
        output_limit = max_output_tokens or getattr(self.config, "max_output_tokens", None)
        response = self._post(
            "chat",
            "/chat/completions",
            {
                "model": self.config.chat_model,
                "messages": messages,
                "temperature": self.config.temperature if temperature is None else temperature,
                **({"max_tokens": output_limit} if output_limit is not None else {}),
                **({"stream": True, "stream_options": {"include_usage": True}} if on_delta else {}),
            },
            on_delta=on_delta,
        )
        try:
            text = response["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("message content must be text")
            return text, response.get("usage")
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("chat model response has no message content") from None

    def embed(self, texts):
        response = self._post(
            "embedding",
            "/embeddings",
            {"model": self.config.embedding_model, "input": texts, "encoding_format": "float"},
        )
        try:
            rows = sorted(response["data"], key=lambda r: r["index"])
            if [r["index"] for r in rows] != list(range(len(texts))):
                raise ValueError("indices")
            return [r["embedding"] for r in rows], response.get("usage")
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("embedding response does not match requested texts") from None
