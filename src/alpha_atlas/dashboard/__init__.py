"""Local read-only dashboard: run monitoring, research history and the residual map."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from alpha_atlas.dashboard import views

STATIC = Path(__file__).parent / "static"


def make_handler(index: views.RunIndex):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # Keep the terminal quiet; pages poll frequently.
            pass

        def _send(self, status, body: bytes, kind: str):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False, default=str).encode()
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802 - http.server API
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/runs":
                    return self._json(views.run_list(index))
                if url.path == "/api/summary":
                    return self._json(views.summary(index, query["id"]))
                if url.path == "/api/plans":
                    return self._json(views.plans(index, query["id"]))
                if url.path == "/api/map":
                    return self._json(views.map_view(index, query["id"]))
                if url.path == "/api/trial":
                    return self._json(views.trial(index, query["id"], int(query["n"])))
            except (KeyError, ValueError) as error:
                return self._json({"error": f"not found: {error}"}, 404)
            name = "index.html" if url.path in {"", "/"} else url.path.lstrip("/")
            path = (STATIC / name).resolve()
            if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if kind.startswith("text/") or kind.endswith("javascript"):
                kind += "; charset=utf-8"
            return self._send(200, path.read_bytes(), kind)

    return Handler


def serve(root: Path, host: str = "127.0.0.1", port: int = 8780, extra=()):
    index = views.RunIndex(root, [Path(p).resolve() for p in extra])
    server = ThreadingHTTPServer((host, port), make_handler(index))
    print(f"AlphaAtlas dashboard: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
