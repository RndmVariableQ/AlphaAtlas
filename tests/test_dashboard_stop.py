"""The dashboard's one action: interrupt the live process that holds a run's writer lock."""

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from alpha_atlas.dashboard import make_handler, views

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="uses /proc")

HOLDER = """
import signal, sys, time
from pathlib import Path
from alpha_atlas.checkpoint import run_lock
directory = Path(sys.argv[1])
if sys.argv[2] == "ignore":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
with run_lock(directory):
    (directory / "ready").write_text("1")
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        (directory / "interrupted").write_text("1")
"""


@pytest.fixture
def run_dir(tmp_path):
    directory = tmp_path / "artifacts/runs/r1"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({"status": "running"}))
    return directory


def holder(directory, mode="handle"):
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(directory), mode])
    for _ in range(200):
        if (directory / "ready").exists():
            return process
        time.sleep(0.05)
    process.kill()
    raise AssertionError("lock holder did not start")


def status(directory):
    return json.loads((directory / "run.json").read_text())["status"]


def test_stop_interrupts_the_live_lock_holder_and_records_the_run(tmp_path, run_dir):
    index = views.RunIndex(tmp_path)
    with pytest.raises(ValueError, match="no live process"):
        views.stop_run(index, "r1")
    with pytest.raises(KeyError):
        views.stop_run(index, "../r1")
    process = holder(run_dir)
    try:
        result = views.stop_run(index, "r1", grace=10)
        assert process.wait(timeout=10) == 0
    finally:
        process.kill()
    assert (run_dir / "interrupted").exists()  # Like Ctrl+C: the process handled it.
    assert result == {"id": "r1", "signals": ["SIGINT"], "status": "interrupted"}
    assert status(run_dir) == "interrupted"
    with pytest.raises(ValueError, match="no live process"):
        views.stop_run(index, "r1")


def test_a_process_that_ignores_sigint_is_terminated_and_recorded(tmp_path, run_dir):
    process = holder(run_dir, "ignore")
    try:
        result = views.stop_run(views.RunIndex(tmp_path), "r1", grace=1)
        assert process.wait(timeout=10) == -15
    finally:
        process.kill()
    assert result["signals"] == ["SIGINT", "SIGTERM"] and status(run_dir) == "interrupted"


def test_stop_waits_for_the_writer_lock_to_be_released(tmp_path, run_dir, monkeypatch):
    real_lock = views.run_lock
    attempts = 0

    @contextmanager
    def delayed_lock(directory):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("run is already open by another writer")
        with real_lock(directory):
            yield

    process = holder(run_dir)
    monkeypatch.setattr(views, "run_lock", delayed_lock)
    try:
        result = views.stop_run(views.RunIndex(tmp_path), "r1", grace=3)
        assert process.wait(timeout=10) == 0
    finally:
        process.kill()
    assert attempts >= 2
    assert result["status"] == "interrupted"


def test_stop_endpoint_requires_the_action_header(tmp_path, run_dir):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(views.RunIndex(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/api/stop?id=r1"

    def post(headers):
        request = urllib.request.Request(url, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    try:
        assert post({})[0] == 400
        status, body = post({"X-Atlas-Action": "stop"})
        assert status == 409 and "no live process" in body["error"]
    finally:
        server.shutdown()
        server.server_close()
