"""Mandatory candidate checks. Only numeric fixtures and orchestration run on the host."""

import time
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from alpha_atlas.operators.code_policy import validate_source

GROUP = ["exchange", "instrument_id", "segment_id"]


def assert_equal(actual, expected, error):
    if np.shape(actual) != np.shape(expected) or not np.allclose(
        actual, expected, equal_nan=True, rtol=1e-8, atol=1e-10
    ):
        raise ValueError(error)


def performance_assessment(small, large, ratio, limits):
    growth = large / max(small, limits.benchmark_floor_seconds) / ratio
    return {
        "small_kernel_seconds": small,
        "large_kernel_seconds": large,
        "work_ratio": ratio,
        "growth_vs_linear": growth,
        "timer_floor_seconds": limits.benchmark_floor_seconds,
        "timer_limited": small < limits.benchmark_floor_seconds,
        "max_growth_vs_linear": limits.benchmark_max_growth,
        "blocked": growth > limits.benchmark_max_growth,
    }


class OperatorValidation:
    def __init__(self, runtime, definition, report=None):
        self.runtime, self.definition = runtime, definition
        self.report = {} if report is None else report
        self.deadline = time.perf_counter() + runtime.limits.validation_seconds
        self.progress = lambda: None
        self.columns = [f"x{i}" for i, (_, k) in enumerate(definition.parameters) if k == "series"]

    def process(self):
        return self.runtime.process(self.definition, deadline=self.deadline)

    def evaluate(self, data):
        return self.runtime.evaluate(
            self.definition, data, self.columns, self.params, GROUP, deadline=self.deadline
        ).to_numpy()

    def run(self):
        for name in (
            "signature",
            "nopython",
            "golden",
            "finite_output",
            "group_isolation",
            "window_causality",
            "future_perturbation",
            "performance_growth",
        ):
            result = self.report[name] = {"status": "running"}
            started = time.perf_counter()
            self.progress()
            try:
                getattr(self, name)(result)
                result["status"] = "passed"
            except Exception as exc:
                result.update(status="failed", error=str(exc))
                raise
            finally:
                result["elapsed_seconds"] = time.perf_counter() - started
                self.progress()
        return tuple(self.report)

    def nopython(self, result):
        with self.process() as worker:
            result["signatures"] = [str(s) for s in worker.kernel.nopython_signatures]
        result["engine"] = "numba"

    def signature(self, result):
        definition = self.definition
        parameters = [p for p, _ in definition.parameters]
        validate_source(definition.body, "kernel", parameters)
        validate_source(definition.golden, "golden", parameters)
        if not definition.examples:
            raise ValueError("group_batch requires at least one independent example")
        self.examples = []
        kinds = [k for _, k in definition.parameters if k != "series"]
        for example in definition.examples:
            arrays = [np.asarray(a, dtype=float) for a in example["inputs"]]
            params = example["params"]
            if len(arrays) != len(self.columns) or any(
                a.ndim != 1 or len(a) != len(arrays[0]) for a in arrays
            ):
                raise ValueError("invalid example inputs")
            if len(params) != len(kinds) or any(
                (type(p) is not int or p < 1)
                if kind == "window"
                else (type(p) not in (int, float) or not np.isfinite(p))
                for p, kind in zip(params, kinds, strict=True)
            ):
                raise ValueError("invalid example parameters")
            expected = np.asarray(example["expected"], dtype=float)
            if expected.shape != arrays[0].shape:
                raise ValueError("invalid example output shape")
            self.examples.append((arrays, params, expected))
        finite = [item for item in self.examples if np.isfinite(item[2]).any()]
        self.sample = (finite or self.examples)[0]
        self.params = self.sample[1]
        self.history = definition.history
        if definition.window_arg:
            static = [p for p, k in definition.parameters if k != "series"]
            self.history = (
                self.params[static.index(definition.window_arg)] + definition.history_offset
            )
        if self.history < 0:
            raise ValueError("invalid example history")

    def golden(self, result):
        boundaries = (
            [],
            [0.0],
            [1.0] * 6,
            [0.0] * 6,
            [np.nan] * 6,
            [1.0, np.nan, 3.0, 4.0, 5.0, 6.0],
            [1.0, 2.0, 2.0, -1.0, 0.0, 4.0],
            [np.inf, 1.0, 2.0, 3.0, 4.0, 5.0],
        )
        cases = [
            ([np.asarray(v) * (i + 1) for i in range(len(self.columns))], self.params, None)
            for v in boundaries
        ] + self.examples
        with self.process() as worker:
            for arrays, params, expected in cases:
                actual = worker.call(arrays, params)
                reference = worker.call(arrays, params, "golden")
                assert_equal(actual, reference, "golden_mismatch")
                if not np.array_equal(actual, worker.call(arrays, params), equal_nan=True):
                    raise ValueError("nondeterministic_output")
                if expected is not None:
                    assert_equal(actual, expected, "example_mismatch")
        result.update(
            boundary_cases=len(boundaries),
            submitted_cases=len(self.examples),
            checks=["shape_dtype", "input_immutable", "determinism", "platform_boundaries"],
        )

    def panel(self):
        arrays = self.sample[0]
        n = max(self.history + 4, len(arrays[0]) + 2, 8)
        rows = []
        if self.definition.scope == "ts":
            groups = [("X", "A", 0), ("Y", "A", 0), ("X", "B", 0), ("X", "A", 1)]
            for asset, (exchange, instrument, segment) in enumerate(groups):
                for bar in range(n):
                    rows.append(
                        {
                            "exchange": exchange,
                            "instrument_id": instrument,
                            "segment_id": segment,
                            "timestamp": datetime(2020, 1, 1) + timedelta(days=bar + segment * n),
                            "eligible": True,
                            **{
                                c: float(a[bar % len(a)]) * (asset + 1)
                                for c, a in zip(self.columns, arrays, strict=True)
                            },
                        }
                    )
            self.cutoff = datetime(2020, 1, 1) + timedelta(days=n - 2)
        else:
            for bar in range(3):
                for asset in range(max(len(arrays[0]), 4) + 2):
                    excluded = asset >= max(len(arrays[0]), 4)
                    rows.append(
                        {
                            "exchange": "X",
                            "instrument_id": str(asset),
                            "segment_id": 0,
                            "timestamp": datetime(2020, 1, 1) + timedelta(days=bar),
                            "eligible": asset != max(len(arrays[0]), 4),
                            **{
                                c: (999.0 if asset == max(len(arrays[0]), 4) else np.nan)
                                if excluded
                                else float(a[asset % len(a)]) * (bar + 1)
                                for c, a in zip(self.columns, arrays, strict=True)
                            },
                        }
                    )
            self.cutoff = datetime(2020, 1, 2)
        return pl.DataFrame(rows).with_row_index("row_id")

    def finite_output(self, result):
        if not any(np.isfinite(expected).any() for _, _, expected in self.examples):
            raise ValueError("no_finite_example_output")
        self.data = self.panel()
        self.actual = self.evaluate(self.data)
        if not np.isfinite(self.actual).any():
            raise ValueError("no_finite_group_output")
        result["finite_rows"] = int(np.isfinite(self.actual).sum())

    def group_isolation(self, result):
        expected = np.full(self.data.height, np.nan)
        with self.process() as worker:
            if self.definition.scope == "ts":
                for part in self.data.partition_by(GROUP):
                    for end in range(self.history, part.height):
                        window = part.slice(end - self.history, self.history + 1)
                        arrays = [window[c].to_numpy() for c in self.columns]
                        if all(np.isfinite(a).all() for a in arrays):
                            expected[part["row_id"][end]] = worker.call(
                                arrays, self.params, "golden"
                            )[-1]
            else:
                valid = self.data.filter(
                    pl.col("eligible") & pl.all_horizontal(pl.col(self.columns).is_finite())
                ).sort(GROUP + ["timestamp"])
                for part in valid.partition_by("timestamp"):
                    expected[part["row_id"].to_numpy()] = worker.call(
                        [part[c].to_numpy() for c in self.columns], self.params, "golden"
                    )
        assert_equal(self.actual, expected, "group_golden_mismatch")
        if self.definition.scope == "ts":
            protected = (
                (pl.col("exchange") == "X")
                & (pl.col("instrument_id") == "A")
                & (pl.col("segment_id") == 0)
            )
            changed = self.data.with_columns(
                pl.when(protected).then(pl.col(c)).otherwise(pl.col(c) + 1234).alias(c)
                for c in self.columns
            )
            mask = self.data.select(protected).to_series().to_numpy()
            assert_equal(self.evaluate(changed)[mask], self.actual[mask], "ts_group_leakage")
            assert_equal(self.evaluate(self.data.reverse())[::-1], self.actual, "ts_row_order")
            result["checks"] = ["exchange", "contract", "segment", "row_order", "group_golden"]
        else:
            # Alter excluded rows; keep the eligible-but-invalid row invalid in its first input.
            valid = pl.col("eligible") & pl.all_horizontal(pl.col(self.columns).is_finite())
            changed = self.data.with_columns(
                pl.when(valid | (pl.col("eligible") & (pl.lit(i) == 0)))
                .then(pl.col(c))
                .otherwise(pl.lit(9999.0))
                .alias(c)
                for i, c in enumerate(self.columns)
            )
            assert_equal(self.evaluate(changed), self.actual, "cs_membership_leakage")
            result["checks"] = ["eligible_mask", "finite_mask", "timestamp", "group_golden"]

    def window_causality(self, result):
        self.prefix = (self.data["timestamp"] <= self.cutoff).to_numpy()
        actual = self.evaluate(self.data.filter(pl.col("timestamp") <= self.cutoff))
        if not np.isfinite(actual).any():
            raise ValueError("no_finite_history_output")
        assert_equal(actual, self.actual[self.prefix], "causality_failure")
        result["finite_history_rows"] = int(np.isfinite(actual).sum())

    def future_perturbation(self, result):
        future = self.data.with_columns(
            pl.when(pl.col("timestamp") > self.cutoff)
            .then(pl.col(c) * -7 + 1000)
            .otherwise(pl.col(c))
            .alias(c)
            for c in self.columns
        )
        assert_equal(self.evaluate(future)[self.prefix], self.actual[self.prefix], "future_leakage")

    def performance_growth(self, result):
        limits = self.runtime.limits
        sizes = [limits.benchmark_small_size, limits.benchmark_large_size]
        fixed = self.definition.scope == "ts" and not self.definition.window_arg
        mode = (
            "fixed_window_calls"
            if fixed
            else ("ts_window_size" if self.definition.scope == "ts" else "cs_section_size")
        )
        result.update(mode=mode, sizes=sizes, repeats=limits.benchmark_repeats)
        workloads = []
        for size in sizes:
            params = list(self.params)
            length, calls = (self.history + 1, size) if fixed else (size, 1)
            if self.definition.window_arg and not fixed:
                static = [p for p, k in self.definition.parameters if k != "series"]
                params[static.index(self.definition.window_arg)] = size
                length = size + self.definition.history_offset + 1
            # Positive, nonconstant and non-collinear input channels.
            t = np.arange(length, dtype=float)
            arrays = [2 + i + np.sin(t * (0.31 + i * 0.13)) for i in range(len(self.columns))]
            workloads.append((arrays, params, calls))
        timings = [[], []]
        with self.process() as worker:
            for arrays, params, _ in workloads:
                worker.call(arrays, params)  # Warm both sizes before measurement.
            for repeat in range(limits.benchmark_repeats):
                for index in (0, 1) if repeat % 2 == 0 else (1, 0):
                    arrays, params, calls = workloads[index]
                    elapsed = 0.0
                    for _ in range(calls):
                        worker.call(arrays, params)
                        elapsed += worker.last_kernel_seconds
                    timings[index].append(elapsed)
        result.update(
            performance_assessment(
                float(np.median(timings[0])),
                float(np.median(timings[1])),
                sizes[1] / sizes[0],
                limits,
            )
        )
        if result["blocked"]:
            raise ValueError("performance_growth_exceeded")
