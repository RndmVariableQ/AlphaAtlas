"""Run with uv run python examples/complex_operator.py."""

from datetime import datetime, timedelta

import numpy as np
import polars as pl

from alpha_atlas.contracts import Candidate
from alpha_atlas.expressions import execute
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime

# Registration validates in a subprocess; accepted kernels run locally through Numba.
KERNEL = """
def kernel(x, volume, window):
    out = np.full_like(x, np.nan)
    if window < 3:
        return out
    time = np.arange(window, dtype=np.float64)
    for end in range(window - 1, len(x)):
        y = x[end - window + 1:end + 1]
        v = volume[end - window + 1:end + 1]
        if not np.all(np.isfinite(y)) or not np.all(np.isfinite(v)):
            continue
        if np.any(v < 0.0):
            continue
        center = np.median(y)
        mad = np.median(np.abs(y - center))
        if mad <= 1e-12 or np.max(v) <= 1e-12:
            continue
        y = np.clip(y, center - 3.0 * 1.4826 * mad, center + 3.0 * 1.4826 * mad)
        weights = (v / np.max(v)) * (time + 1.0)
        weights = weights / np.sum(weights)
        tx = time - np.sum(weights * time)
        dy = y - np.sum(weights * y)
        denominator = np.sum(weights * tx * tx)
        if denominator <= 1e-12:
            continue
        slope = np.sum(weights * tx * dy) / denominator
        residual = dy - slope * tx
        noise = np.sqrt(np.sum(weights * residual * residual))
        if noise > 1e-12:
            out[end] = slope / noise
    return out
"""

# Independent scalar normal equations, instead of the kernel's centered vector calculation.
GOLDEN = """
def golden(x, volume, window):
    result = np.full(len(x), np.nan)
    if window < 3:
        return result
    for end in range(window - 1, len(x)):
        start = end - window + 1
        values = x[start:end + 1]
        volumes = volume[start:end + 1]
        if not np.all(np.isfinite(values)) or not np.all(np.isfinite(volumes)):
            continue
        if np.any(volumes < 0.0):
            continue
        middle = np.quantile(values, 0.5)
        deviations = np.abs(values - middle)
        width = 3.0 * 1.4826 * np.quantile(deviations, 0.5)
        largest = np.max(volumes)
        if width <= 3.0 * 1.4826 * 1e-12 or largest <= 1e-12:
            continue
        sw = 0.0
        st = 0.0
        sy = 0.0
        stt = 0.0
        sty = 0.0
        for j in range(window):
            weight = volumes[j] / largest * (j + 1)
            value = min(max(values[j], middle - width), middle + width)
            sw += weight
            st += weight * j
            sy += weight * value
            stt += weight * j * j
            sty += weight * j * value
        determinant = sw * stt - st * st
        if determinant / (sw * sw) <= 1e-12:
            continue
        slope = (sw * sty - st * sy) / determinant
        intercept = (sy - slope * st) / sw
        error = 0.0
        for j in range(window):
            weight = volumes[j] / largest * (j + 1)
            value = min(max(values[j], middle - width), middle + width)
            error += weight * (value - intercept - slope * j) ** 2
        noise = np.sqrt(error / sw)
        if noise > 1e-12:
            result[end] = slope / noise
    return result
"""


def definitions():
    """Register in order; the second definition demonstrates TS -> CS composition."""
    return (
        OperatorDefinition(
            name="ROBUST_VOLUME_TREND",
            description="MAD clipping, volume/time weighted slope divided by residual RMS",
            parameters=(("x", "series"), ("volume", "series"), ("window", "window")),
            kind="group_batch",
            scope="ts",
            window_arg="window",
            body=KERNEL,
            golden=GOLDEN,
            examples=(
                {
                    "inputs": [[-2.0, -1.0, 1.0, 1.0, 2.0], [5.0, 2.5, 5.0 / 3, 1.25, 1.0]],
                    "params": [5],
                    "expected": [None, None, None, None, 2.5],
                },
            ),
        ),
        OperatorDefinition(
            name="ROBUST_TREND_RANK",
            parameters=(("x", "series"), ("volume", "series"), ("window", "window")),
            body="CS_RANK(ROBUST_VOLUME_TREND(x, volume, window))",
        ),
    )


def run(session):
    """Can also be used as a Runner method's run(session) implementation."""
    for definition in definitions():
        feedback = session.register_operator(definition)
        if not feedback.accepted:
            raise ValueError(feedback.error)
    return session.evaluate(Candidate("ROBUST_TREND_RANK(LOG($close), $volume, 20)"))


def synthetic_panel():
    rows = []
    for asset, direction in enumerate((1.0, -1.0, 0.3)):
        for bar in range(8):
            rows.append(
                {
                    "row_id": len(rows),
                    "exchange": "SYNTHETIC",
                    "instrument_id": str(asset),
                    "timestamp": datetime(2020, 1, 1) + timedelta(days=bar),
                    "eligible": not (asset == 2 and bar == 7),
                    "x": direction * bar + 0.4 * np.sin(bar * 1.7 + asset),
                    "volume": float(10 + bar + asset),
                }
            )
    return pl.DataFrame(rows)


if __name__ == "__main__":
    runtime = NumbaRuntime()
    registry = OperatorRegistry(runtime=runtime)
    for definition in definitions():
        feedback = registry.register(definition)
        print(definition.name, feedback)
        if not feedback.accepted:
            raise SystemExit(1)
    panel = synthetic_panel()
    values = execute("ROBUST_TREND_RANK($x, $volume, 5)", panel, {"x", "volume"}, registry)
    print(panel.join(values, on="row_id").select("timestamp", "instrument_id", "value"))
