"""Trusted local Numba execution; candidate validation happens in a disposable subprocess."""

from __future__ import annotations

import heapq
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl

from alpha_atlas.contracts import fingerprint
from alpha_atlas.operators.kernel import Kernel, KernelError


@dataclass(frozen=True)
class RuntimeLimits:
    validation_seconds: float = 120.0
    evaluation_seconds: float = 120.0
    benchmark_small_size: int = 64
    benchmark_large_size: int = 256
    benchmark_repeats: int = 5
    benchmark_floor_seconds: float = 0.001
    benchmark_max_growth: float = 3.0

    def __post_init__(self):
        if any(not np.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("runtime limits must be finite and positive")
        if (
            any(
                type(v) is not int
                for v in (
                    self.benchmark_small_size,
                    self.benchmark_large_size,
                    self.benchmark_repeats,
                )
            )
            or not self.benchmark_small_size < self.benchmark_large_size <= 10001
        ):
            raise ValueError("invalid benchmark sizes or repeats")


class NumbaRuntime:
    def __init__(self, limits: RuntimeLimits | None = None):
        self.limits = limits or RuntimeLimits()
        self._kernels = {}
        self.cost = dict(
            compile_seconds=0.0,
            validation_seconds=0.0,
            validation_processes=0,
            evaluation_seconds=0.0,
            kernel_seconds=0.0,
            kernel_calls=0,
        )

    @property
    def environment(self):
        return {
            "engine": "numba-window-v1",
            "python": platform.python_version(),
            "platform": sys.platform,
            "machine": platform.machine(),
            **{name: version(name) for name in ("numpy", "numba", "llvmlite")},
            "boundscheck": True,
            "fastmath": False,
            "parallel": False,
        }

    @property
    def fingerprint(self):
        return fingerprint((self.environment, asdict(self.limits)))

    def prepare(self, definition):
        key = definition.operator_id
        if key not in self._kernels:
            started = time.perf_counter()
            self._kernels[key] = Kernel(definition, self.cost)
            self.cost["compile_seconds"] += time.perf_counter() - started
        return self._kernels[key]

    def process(self, definition, *, deadline=None):
        if deadline is not None and time.perf_counter() > deadline:
            raise KernelError("validation_timeout")
        return self.prepare(definition)

    def validate(self, definition, *, report=None):
        report = {} if report is None else report
        started = time.perf_counter()
        # Pass only the definition/config as JSON. No data files, credentials or pickle artifacts.
        payload = json.dumps(
            {"definition": asdict(definition), "limits": asdict(self.limits)}, allow_nan=False
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "TEMP",
                "TMP",
                "LANG",
                "LC_ALL",
                "PROCESSOR_ARCHITECTURE",
                "PROCESSOR_ARCHITEW6432",
            }
        }
        env.update(
            PYTHONPATH=str(Path(__file__).resolve().parents[2]),
            PYTHONUTF8="1",
            NUMBA_NUM_THREADS="1",
            OPENBLAS_NUM_THREADS="1",
            OMP_NUM_THREADS="1",
        )
        self.cost["validation_processes"] += 1
        try:
            with tempfile.TemporaryDirectory(prefix="atlas-operator-") as directory:
                try:
                    completed = subprocess.run(
                        [sys.executable, "-m", "alpha_atlas.operators.worker"],
                        input=payload,
                        text=True,
                        encoding="utf-8",
                        capture_output=True,
                        cwd=directory,
                        env=env,
                        timeout=self.limits.validation_seconds,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except subprocess.TimeoutExpired as exc:
                    output = (
                        exc.stdout.decode("utf-8") if isinstance(exc.stdout, bytes) else exc.stdout
                    )
                    self._read_report(output, report)
                    running = next((v for v in report.values() if v["status"] == "running"), None)
                    if running is not None:
                        running.update(status="failed", error="validation_timeout")
                    raise KernelError("validation_timeout") from None
            result = self._read_report(completed.stdout, report)
            if not result or not result.get("ok") or completed.returncode:
                raise KernelError(
                    result.get("error", "validation_process_failure")
                    if result
                    else "validation_process_failure"
                )
            # Compile only after the subprocess gate passed; never load candidate machine-code/pickle.
            self.prepare(definition)
            return tuple(report)
        finally:
            self.cost["validation_seconds"] += time.perf_counter() - started

    @staticmethod
    def _read_report(output, report):
        if not output or not output.strip():
            return None
        try:
            result = json.loads(output.strip().splitlines()[-1])
            report.update(result["validation"])
            return result
        except (ValueError, KeyError, TypeError):
            return None

    def evaluate(self, definition, frame, columns, params, group, *, deadline=None):
        deadline = deadline or time.perf_counter() + self.limits.evaluation_seconds
        result = np.full(frame.height, np.nan)
        history = definition.history
        if definition.window_arg:
            static_names = [p for p, k in definition.parameters if k != "series"]
            history = (
                int(params[static_names.index(definition.window_arg)]) + definition.history_offset
            )
        started = time.perf_counter()
        ordered = frame.with_row_index("__position").sort(group + ["timestamp"])

        # Prepare host-only prefix indices; only a current window is sent to the worker.
        def events(part):
            arrays = [part[c].to_numpy() for c in columns]
            timestamps, positions = part["timestamp"].to_list(), part["__position"].to_numpy()
            for i in range(history, part.height):
                yield timestamps[i], int(positions[i]), arrays, i

        with self.process(definition, deadline=deadline) as worker:
            if definition.scope == "ts":
                tasks = heapq.merge(
                    *(events(part) for part in ordered.partition_by(group, maintain_order=True)),
                    key=lambda t: (t[0], t[1]),
                )
                for _, position, arrays, i in tasks:
                    if time.perf_counter() > deadline:
                        raise KernelError("evaluation_timeout_between_calls")
                    window = [a[i - history : i + 1] for a in arrays]
                    if not all(np.isfinite(a).all() for a in window):
                        continue
                    result[position] = worker.call(window, params)[-1]
            else:
                valid = pl.col("eligible").fill_null(False) & pl.all_horizontal(
                    pl.col(columns).is_finite().fill_null(False)
                )
                selected = ordered.filter(valid).sort("timestamp", maintain_order=True)
                for part in selected.partition_by("timestamp", maintain_order=True):
                    if time.perf_counter() > deadline:
                        raise KernelError("evaluation_timeout_between_calls")
                    output = worker.call([part[c].to_numpy() for c in columns], params)
                    result[part["__position"].to_numpy()] = output
        self.cost["evaluation_seconds"] += time.perf_counter() - started
        return pl.Series("value", result, nan_to_null=True)
