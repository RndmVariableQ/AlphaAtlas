import runpy
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from test_expressions import frame
from test_pipeline import project as project

from alpha_atlas.expressions import execute
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.code_policy import validate_source
from alpha_atlas.operators.runtime import KernelError, NumbaRuntime, RuntimeLimits

KERNEL = """import numpy as np
def kernel(x, window):
    out = np.full_like(x, np.nan)
    for i in range(window-1, len(x)):
        out[i] = np.mean(x[i-window+1:i+1])
    return out
"""
GOLDEN = """import numpy as np
def golden(x, window):
    result = np.full(len(x), np.nan)
    for i in range(window-1, len(x)):
        total = 0.0
        for j in range(i-window+1, i+1):
            total += x[j]
        result[i] = total/window
    return result
"""


def definition():
    return OperatorDefinition(
        "CUSTOM_MEAN",
        (("x", "series"), ("window", "window")),
        KERNEL,
        kind="group_batch",
        window_arg="window",
        golden=GOLDEN,
        examples=({"inputs": [[1.0, 2.0, 3.0]], "params": [2], "expected": [None, 1.5, 2.5]},),
    )


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef kernel(x):\n return x",
        "def kernel(x):\n return open('/secret')",
        "def kernel(x):\n return x.__class__",
        "def kernel(x):\n return np.load(x)",
        "def kernel(x):\n return np.random.rand(3)",
        "def kernel(x):\n return getattr(x, 'shape')",
        "def kernel(x):\n global state\n return x",
        "state=[]\ndef kernel(x):\n return x",
        "def kernel(x):\n np.mean = x\n return x",
        "def kernel(x):\n return np.ctypeslib.as_array(x)",
        "def kernel(x):\n return np.lib.stride_tricks.as_strided(x)",
    ],
)
def test_restricted_numeric_language(source):
    with pytest.raises(ValueError):
        validate_source(source, "kernel", ["x"])


def test_registration_without_runtime_fails_closed_and_records():
    records = []
    registry = OperatorRegistry(record=lambda d, f: records.append((d, f)))
    feedback = registry.register(definition())
    assert not feedback.accepted
    assert "operator_runtime_unavailable" in feedback.error
    assert len(records) == 1
    with pytest.raises(ValueError, match="unknown operator"):
        registry.spec("CUSTOM_MEAN")


def test_runtime_only_sends_current_window_and_current_section():
    calls = []

    class FakeWorker:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def call(self, arrays, params):
            calls.append([a.copy() for a in arrays])
            return arrays[0].copy()

    class FakeRuntime(NumbaRuntime):
        def process(self, definition, **kwargs):
            return FakeWorker()

    runtime = FakeRuntime()
    data = frame((1.0, 2.0, 3.0, 100.0)).with_columns(pl.Series("segment_id", [0, 0, 0, 1]))
    result = runtime.evaluate(
        definition(), data, ["x"], [2], ["exchange", "instrument_id", "segment_id"]
    )
    np.testing.assert_allclose(result.to_numpy(), [np.nan, 2, 3, np.nan], equal_nan=True)
    assert [a[0].tolist() for a in calls] == [[1.0, 2.0], [2.0, 3.0]]
    calls.clear()
    cs = replace(definition(), scope="cs", window_arg=None, parameters=(("x", "series"),))
    data = frame().with_columns(pl.Series("eligible", [True, False, True, True, True]))
    result = runtime.evaluate(cs, data, ["x"], [], ["exchange", "instrument_id"])
    assert len(calls) == 4 and all(len(a[0]) == 1 for a in calls)
    assert result[1] is None


@pytest.fixture
def numba_runtime():
    return NumbaRuntime()


def test_numba_ts_registration_and_execution(numba_runtime):
    registry = OperatorRegistry(runtime=numba_runtime)
    feedback = registry.register(definition())
    assert feedback.accepted, feedback.error
    assert feedback.validation["performance_growth"]["mode"] == "ts_window_size"
    actual = execute("CUSTOM_MEAN($x,3)", frame(), {"x"}, registry)["value"].to_numpy()
    np.testing.assert_allclose(actual, [np.nan, np.nan, 2.0, 3.0, 4.0], equal_nan=True)
    assert numba_runtime.cost["kernel_calls"] > 0
    assert feedback.validation["nopython"]["signatures"]


def test_numba_cs_registration_and_execution(numba_runtime):
    cs = OperatorDefinition(
        "CUSTOM_CENTER",
        (("x", "series"),),
        "def kernel(x):\n    return x - np.mean(x)",
        kind="group_batch",
        scope="cs",
        golden="def golden(x):\n    return x - np.sum(x)/max(len(x),1)",
        examples=({"inputs": [[1.0, 2.0, 3.0]], "params": [], "expected": [-1.0, 0.0, 1.0]},),
    )
    registry = OperatorRegistry(runtime=numba_runtime)
    feedback = registry.register(cs)
    assert feedback.accepted, feedback.error
    data = frame().with_columns(
        pl.lit(frame()["timestamp"][0]).alias("timestamp"),
        pl.Series("instrument_id", ["A", "B", "C", "D", "E"]),
    )
    actual = execute("CUSTOM_CENTER($x)", data, {"x"}, registry)["value"].to_numpy()
    np.testing.assert_allclose(actual, np.array([2, 1, 3, 5, 4]) - 3)


def test_numba_registration_rejects_all_null(numba_runtime):
    candidate = replace(
        definition(),
        body="def kernel(x, window):\n    return np.full_like(x, np.nan)",
        golden="def golden(x, window):\n    return np.full(len(x), np.nan)",
        examples=({"inputs": [[1.0, 2.0]], "params": [2], "expected": [None, None]},),
    )
    registry = OperatorRegistry(runtime=numba_runtime)
    feedback = registry.register(candidate)
    assert not feedback.accepted
    assert feedback.error == "no_finite_example_output"
    assert feedback.validation["finite_output"]["status"] == "failed"
    with pytest.raises(ValueError, match="unknown operator"):
        registry.spec(candidate.name)


def test_numba_registration_rejects_explosive_growth(numba_runtime):
    candidate = OperatorDefinition(
        name="CUBIC_CENTER",
        parameters=(("x", "series"),),
        kind="group_batch",
        scope="cs",
        body="""
def kernel(x):
    count = 0.0
    for i in range(len(x)):
        for j in range(len(x)):
            for k in range(len(x)):
                count += np.sin((x[i] - x[j]) * x[k])
    return x - np.mean(x) + count * 0.0
""",
        golden="def golden(x):\n    return x - np.mean(x)",
        examples=({"inputs": [[1.0, 2.0, 3.0]], "params": [], "expected": [-1.0, 0.0, 1.0]},),
    )
    registry = OperatorRegistry(runtime=numba_runtime)
    feedback = registry.register(candidate)
    assert not feedback.accepted, feedback.validation
    assert feedback.error == "performance_growth_exceeded"
    result = feedback.validation["performance_growth"]
    assert result["status"] == "failed" and result["blocked"]
    assert result["growth_vs_linear"] > result["max_growth_vs_linear"]
    with pytest.raises(ValueError, match="unknown operator"):
        registry.spec(candidate.name)


def test_numba_complex_operator_example(numba_runtime):
    example = runpy.run_path(str(Path(__file__).parents[1] / "examples/complex_operator.py"))
    registry = OperatorRegistry(runtime=numba_runtime)
    definitions = example["definitions"]()
    for item in definitions:
        feedback = registry.register(item)
        assert feedback.accepted, feedback.error

    # Independent hand calculation: equal effective weights, slope 1, residual variance .16.
    x = np.array([-2.0, -1.0, 1.0, 1.0, 2.0])
    volume = 5.0 / np.arange(1.0, 6.0)
    with numba_runtime.process(definitions[0]) as worker:
        actual = worker.call([x, volume], [5])
        assert np.isnan(actual[:4]).all()
        assert actual[-1] == pytest.approx(2.5)
        for invalid in (np.zeros(5), -volume, np.array([1.0, 1.0, np.nan, 1.0, 1.0])):
            assert np.isnan(worker.call([x, invalid], [5])).all()
        assert np.isnan(worker.call([np.arange(5, dtype=float), volume], [5])).all()
        outlier = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 1000.0])
        arrays = [outlier, np.ones(6)]
        actual = worker.call(arrays, [6])
        golden = worker.call(arrays, [6], "golden")
        assert np.isfinite(actual[-1])
        np.testing.assert_allclose(actual, golden, equal_nan=True)
        capped = np.clip(outlier, -3.1717, 10.1717)
        np.testing.assert_allclose(actual, worker.call([capped, np.ones(6)], [6]), equal_nan=True)

    panel = example["synthetic_panel"]()
    fields = {"x", "volume"}
    raw = execute("ROBUST_VOLUME_TREND($x, $volume, 5)", panel, fields, registry)
    formula = "ROBUST_TREND_RANK($x, $volume, 5)"
    ranked = execute(formula, panel.reverse(), fields, registry).sort("row_id")
    reference = panel.join(raw, on="row_id")
    expected = np.full(panel.height, np.nan)
    for part in reference.partition_by("timestamp"):
        valid = part.filter(pl.col("eligible") & pl.col("value").is_finite())
        scores = valid["value"].to_numpy()
        for row in valid.iter_rows(named=True):
            rank = np.sum(scores < row["value"]) + (np.sum(scores == row["value"]) + 1) / 2
            expected[row["row_id"]] = rank / len(scores)
    np.testing.assert_allclose(ranked["value"].to_numpy(), expected, equal_nan=True)
    prefix = panel.filter(pl.col("timestamp") < panel["timestamp"].max())
    earlier = execute(formula, prefix, fields, registry).sort("row_id")
    np.testing.assert_allclose(
        earlier["value"].to_numpy(),
        ranked.filter(pl.col("row_id").is_in(prefix["row_id"].to_list()))["value"].to_numpy(),
        equal_nan=True,
    )


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("def kernel(x, window):\n    return np.zeros(1)", "invalid_output_shape"),
        (
            "def kernel(x, window):\n    return np.zeros(len(x)).reshape(1,len(x))",
            "invalid_output_shape",
        ),
        ("def kernel(x, window):\n    x[0] = 3\n    return x", "nopython_compile_failed"),
        ("def kernel(x, window):\n    return np.isfinite(x)", "invalid_output_dtype"),
        ("def kernel(x, window):\n    return x + x[1000000]", "index is out of bounds"),
        ("def kernel(x, window):\n    return x[1000000:]", "invalid_output_shape"),
        (
            "def kernel(x, window):\n    return np.ones(len(x)).astype(np.int64)",
            "forbidden attribute",
        ),
    ],
)
def test_numba_bad_kernels_rejected_before_local_execution(numba_runtime, source, error):
    registry = OperatorRegistry(runtime=numba_runtime)
    candidate = replace(definition(), body=source)
    feedback = registry.register(candidate)
    assert not feedback.accepted and error in feedback.error
    assert not numba_runtime._kernels


def test_validation_timeout_kills_child_before_local_execution():
    runtime = NumbaRuntime(RuntimeLimits(validation_seconds=5))
    candidate = replace(
        definition(),
        body="""
def kernel(x, window):
    value = 1.0
    while value > 0.0:
        value += 1.0
    return x * value
""",
    )
    feedback = OperatorRegistry(runtime=runtime).register(candidate)
    assert not feedback.accepted and feedback.error == "validation_timeout"
    assert not runtime._kernels


def test_evaluation_reuses_compilation_without_subprocess(numba_runtime, monkeypatch):
    registry = OperatorRegistry(runtime=numba_runtime)
    candidate = definition()
    feedback = registry.register(candidate)
    assert feedback.accepted, feedback.error
    compiled = numba_runtime.prepare(candidate).kernel

    def forbid(*args, **kwargs):
        raise AssertionError("evaluation must not start a process")

    monkeypatch.setattr(subprocess, "run", forbid)
    for _ in range(2):
        execute("CUSTOM_MEAN($x,3)", frame(), {"x"}, registry)
    assert numba_runtime.prepare(candidate).kernel is compiled
    assert len(compiled.nopython_signatures) == 1
    assert numba_runtime.cost["validation_processes"] == 1


def test_new_input_failure_becomes_compute_error(numba_runtime):
    candidate = OperatorDefinition(
        "INPUT_GUARD",
        (("x", "series"),),
        """
def kernel(x):
    if len(x) > 0 and np.isfinite(x[0]) and x[0] > 10000.0:
        return x + x[1000000]
    return x.copy()
""",
        kind="group_batch",
        golden="def golden(x):\n    return x.copy()",
        examples=({"inputs": [[1.0, 2.0]], "params": [], "expected": [1.0, 2.0]},),
    )
    registry = OperatorRegistry(runtime=numba_runtime)
    feedback = registry.register(candidate)
    assert feedback.accepted, feedback.error
    with pytest.raises(KernelError, match="kernel_execution_failed: IndexError"):
        execute("INPUT_GUARD($x)", frame((10001.0,)), {"x"}, registry)


def test_numba_runtime_creation_freeze_and_oos(numba_runtime, project, monkeypatch):
    from alpha_atlas.contracts import Candidate
    from alpha_atlas.runner import run, test_frozen
    from alpha_atlas.storage import RunStore

    class Method:
        def run(self, session):
            feedback = session.register_operator(definition())
            assert feedback.accepted, feedback.error
            result = session.evaluate(Candidate("CUSTOM_MEAN($volume,5)"))
            assert result.report.status == "success", result.report.failure_reason
            assert result.accepted
            assert session.get_library().version == 1

    path = run(
        project,
        "ashare",
        "fold1",
        "synthetic_code",
        1,
        attempts=1,
        method_impl=Method(),
        runtime=numba_runtime,
    )
    assert RunStore(path).library_view().version == 1
    report = test_frozen(project, path)
    assert len(report["results"]) == 1
    assert report["results"][0]["status"] == "success"
    assert test_frozen(project, path) == report
    original = NumbaRuntime.environment.fget
    monkeypatch.setattr(
        NumbaRuntime,
        "environment",
        property(lambda runtime: {**original(runtime), "numba": "changed"}),
    )
    with pytest.raises(ValueError, match="runtime environment changed"):
        test_frozen(project, path)
