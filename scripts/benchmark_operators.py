"""Small reproducible benchmark; independent of ordinary unit-test timing."""

import json
import os
import statistics
import time
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from alpha_atlas.contracts import Candidate, EvaluationReport, Expression, FactorValues, Metric
from alpha_atlas.expressions import execute
from alpha_atlas.library import FactorLibrary


def peak_memory_bytes():
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize


def main():
    size = 20000
    data = pl.DataFrame(
        {
            "row_id": range(size),
            "exchange": ["X"] * size,
            "instrument_id": ["A"] * size,
            "timestamp": [datetime(2020, 1, 1) + timedelta(minutes=i) for i in range(size)],
            "eligible": [True] * size,
            "close": [10.0 + i % 101 for i in range(size)],
        }
    )
    formulas = {
        "mean": "TS_MEAN($close,20)",
        "zscore": "TS_ZSCORE($close,20)",
        "nested": "TS_MEAN(DELTA($close,5),20)",
        "shared": "ADD(TS_MEAN($close,20),TS_MEAN($close,20))",
        "rankcorr": "TS_RANKCORR($close,DELAY($close,1),20)",
    }
    result = {"rows": size, "polars": pl.__version__, "numpy": np.__version__, "median_seconds": {}}
    for name, formula in formulas.items():
        execute(formula, data, {"close"})
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            execute(formula, data, {"close"})
            timings.append(time.perf_counter() - started)
        result["median_seconds"][name] = statistics.median(timings)
    rng = np.random.default_rng(20260909)
    library = FactorLibrary(
        {"min_abs_val_ic": 0.01, "min_coverage": 0.8, "max_abs_corr": 0.9, "min_corr_overlap": 100}
    )
    for index in range(12):
        candidate = Candidate(Expression("field", value=f"x{index}"))
        identity = candidate.expression.expression_id
        report = EvaluationReport(
            identity,
            (
                Metric("ic", "train", 0.1, size, "benchmark"),
                Metric("ic", "val", 0.1, size, "benchmark"),
            ),
            1,
            1.0,
            0.0,
        )
        observation = FactorValues(
            identity,
            "synthetic",
            pl.DataFrame({"row_id": range(size), "value": rng.normal(size=size)}),
        )
        started = time.perf_counter()
        feedback = library.consider(candidate, report, observation)
        if not feedback.accepted:
            raise RuntimeError(feedback.reason)
        if index == 11:
            result["compare_11_members_seconds"] = time.perf_counter() - started
    result["process_peak_memory_bytes"] = peak_memory_bytes()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
