"""Small JSON checkpoints and a single-writer lock for synchronous runs."""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.contracts import fingerprint

MUTABLE_RUN_FIELDS = {"status", "library_size", "total_seconds", "library_sha256"}


def config_fingerprint(spec: dict) -> str:
    # Existing v2 run files originally fingerprinted status="running".
    original = {
        k: v for k, v in spec.items() if k not in MUTABLE_RUN_FIELDS | {"config_fingerprint"}
    }
    original["status"] = "running"
    return fingerprint(original)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def run_lock(directory: Path):
    """OS releases the lock on process death; never unlink a potentially locked file."""
    with (directory / ".run.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("run is already open by another writer") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Checkpoint:
    def __init__(self, directory: Path, spec: dict):
        self.directory, self.spec = directory, spec
        self.stage = "setup"
        self.trial_index = None
        self.started = time.perf_counter()
        self.previous_seconds = spec.get("total_seconds", 0.0)

    def elapsed(self) -> float:
        return self.previous_seconds + time.perf_counter() - self.started

    def save(self, method, completed: int, attempts: int, library_version: int, pending=None):
        self.trial_index = pending["trial_index"] if pending else completed
        # Strict JSON: no pickle, implicit str conversion, or non-finite state values.
        state = json.loads(json.dumps(method.dump_state(), allow_nan=False))
        payload = {
            "version": 1,
            "config_fingerprint": self.spec["config_fingerprint"],
            "completed": completed,
            "attempts": attempts,
            "library_version": library_version,
            "method_state": state,
            "pending": pending,
            "elapsed_seconds": self.elapsed(),
        }
        atomic_json(self.directory / "checkpoint.json", payload)

    def load(self) -> dict:
        data = read_json(self.directory / "checkpoint.json")
        if data.get("version") != 1:
            raise ValueError("unsupported checkpoint version")
        if data["config_fingerprint"] != self.spec["config_fingerprint"]:
            raise ValueError("checkpoint configuration mismatch")
        self.previous_seconds = max(self.previous_seconds, data["elapsed_seconds"])
        self.trial_index = data["pending"]["trial_index"] if data["pending"] else data["completed"]
        return data

    def status(self, status: str, **extra):
        self.spec.update(status=status, total_seconds=self.elapsed(), **extra)
        atomic_json(self.directory / "run.json", self.spec)
        if status in {"frozen", "failed", "interrupted"}:
            from alpha_atlas.reporting import terminal_progress

            terminal_progress(
                {"frozen": "搜索完成", "failed": "运行失败", "interrupted": "运行中断"}[status],
                状态=status,
                成员=self.spec.get("library_size"),
                累计耗时=f"{self.elapsed():.2f} s",
                报告=self.directory / "report.md",
            )

    def failure(self, exc: BaseException):
        path = self.directory / "failures.json"
        records = read_json(path) if path.exists() else []
        # No locals, environment, or source lines are collected.
        message = re.sub(
            r"(?i)(password|passwd|token|secret|api[_-]?key)(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[redacted]",
            str(exc),
        )[:2000]
        records.append(
            {
                "time": datetime.now(UTC).isoformat(),
                "stage": self.stage,
                "trial_index": self.trial_index,
                "elapsed_seconds": self.elapsed(),
                "type": type(exc).__name__,
                "message": message,
                "frames": [
                    {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                    for f in traceback.extract_tb(exc.__traceback__)
                ],
            }
        )
        atomic_json(path, records)


@contextmanager
def tracked_run(directory: Path, spec: dict):
    from alpha_atlas.reporting import refresh_report

    progress = Checkpoint(directory, spec)
    try:
        yield progress
    except BaseException as exc:
        # Preserve the original exception even when the disk also rejects failure evidence.
        for write in (
            lambda error=exc: progress.failure(error),
            lambda error=exc: progress.status(
                "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
            ),
        ):
            try:
                write()
            except Exception as storage_error:
                exc.add_note(f"Could not persist run failure: {type(storage_error).__name__}")
        raise
    finally:
        refresh_report(directory)
