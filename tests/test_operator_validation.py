"""Fault injection checks the common registration gate without executing candidate source."""

import json
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime, RuntimeLimits
from alpha_atlas.operators.validation import OperatorValidation, performance_assessment
from alpha_atlas.storage import RunStore


def identity(scope="ts", *, fixed=False):
    return OperatorDefinition(
        name="IDENTITY_TEST",
        parameters=(("x", "series"),),
        body="def kernel(x):\n    return x.copy()",
        golden="def golden(x):\n    return x.copy()",
        kind="group_batch",
        scope=scope,
        history=2 if fixed else 0,
        examples=({"inputs": [[1.0, 2.0, 3.0]], "params": [], "expected": [1.0, 2.0, 3.0]},),
    )


class SimulatedRuntime(NumbaRuntime):
    """Trusted stand-in for identity kernels; never imports/executes submitted source."""

    def __init__(self, fault=None):
        super().__init__()
        self.fault = fault
        self.processes = 0

    def validate(self, definition, *, report=None):
        return OperatorValidation(self, definition, report).run()

    def process(self, definition, **kwargs):
        self.processes += 1
        fault = self.fault

        class Worker:
            class kernel:
                nopython_signatures = ("simulated",)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def call(self, arrays, params, function="kernel"):
                self.last_kernel_seconds = (
                    len(arrays[0]) ** (2 if fault == "quadratic" else 1) * 1e-5
                )
                if fault == "all_null":
                    return np.full(len(arrays[0]), np.nan)
                return arrays[0].copy()

        return Worker()

    def evaluate(self, definition, frame, columns, params, group, **kwargs):
        if self.fault == "group":
            group = ["instrument_id"]
        if self.fault == "membership":
            frame = frame.with_columns(pl.lit(True).alias("eligible"))
        result = super().evaluate(definition, frame, columns, params, group, **kwargs)
        if self.fault == "no_group_output":
            return pl.Series("value", [None] * frame.height, dtype=pl.Float64)
        if self.fault == "future" and frame[columns[0]].is_between(900, 1100).any():
            return result + 1
        return result


@pytest.mark.parametrize("scope", ["ts", "cs"])
def test_common_gate_passes_and_records(scope):
    records = []
    runtime = SimulatedRuntime()
    registry = OperatorRegistry(runtime=runtime, record=lambda d, f: records.append(f))
    result = registry.register(identity(scope))
    assert result.accepted, result.error
    assert records == [result]
    assert all(item["status"] == "passed" for item in result.validation.values())
    assert result.validation["finite_output"]["finite_rows"] > 0
    assert result.validation["window_causality"]["finite_history_rows"] > 0
    perf = result.validation["performance_growth"]
    assert perf["repeats"] == 5 and not perf["blocked"]
    assert perf["mode"] == ("fixed_window_calls" if scope == "ts" else "cs_section_size")


@pytest.mark.parametrize(
    ("fault", "scope", "error", "stage"),
    [
        ("all_null", "ts", "no_finite_example_output", "finite_output"),
        ("no_group_output", "ts", "no_finite_group_output", "finite_output"),
        ("group", "ts", "group_golden_mismatch", "group_isolation"),
        ("membership", "cs", "group_golden_mismatch", "group_isolation"),
        ("future", "ts", "future_leakage", "future_perturbation"),
        ("quadratic", "cs", "performance_growth_exceeded", "performance_growth"),
    ],
)
def test_common_gate_rejects_faults_before_publication(fault, scope, error, stage, tmp_path):
    candidate = identity(scope, fixed=scope == "ts")
    if fault == "all_null":
        candidate = replace(
            candidate,
            examples=({"inputs": [[1.0, 2.0, 3.0]], "params": [], "expected": [None, None, None]},),
        )
    registry = OperatorRegistry(
        runtime=SimulatedRuntime(fault), record=RunStore(tmp_path).record_operator
    )
    result = registry.register(candidate)
    assert not result.accepted and error in result.error
    assert result.validation[stage]["status"] == "failed"
    saved = json.loads((tmp_path / "operators/00000001.json").read_text(encoding="utf-8"))
    assert saved["feedback"]["validation"] == result.validation
    assert saved["feedback"]["accepted"] is False
    with pytest.raises(ValueError, match="unknown operator"):
        registry.spec(candidate.name)


def test_benchmark_window_axis_and_validation_records_are_per_candidate():
    runtime = SimulatedRuntime()
    registry = OperatorRegistry(runtime=runtime)
    windowed = replace(
        identity(),
        parameters=(("x", "series"), ("window", "window")),
        window_arg="window",
        body="def kernel(x, window):\n    return x.copy()",
        golden="def golden(x, window):\n    return x.copy()",
        examples=({"inputs": [[1.0, 2.0, 3.0]], "params": [2], "expected": [1.0, 2.0, 3.0]},),
    )
    result = registry.register(windowed)
    assert result.accepted, result.error
    assert result.validation["performance_growth"]["mode"] == "ts_window_size"
    rejected = registry.register(replace(windowed, name="BAD", golden=""))
    assert list(rejected.validation) == ["signature"]
    assert result.validation["signature"]["status"] == "passed"


def test_performance_growth_uses_floor_and_relative_scale():
    limits = RuntimeLimits()
    assert not performance_assessment(0.01, 0.04, 4, limits)["blocked"]
    assert performance_assessment(0.01, 0.16, 4, limits)["blocked"]
    tiny = performance_assessment(1e-7, 1e-5, 4, limits)
    assert tiny["timer_limited"] and not tiny["blocked"]
