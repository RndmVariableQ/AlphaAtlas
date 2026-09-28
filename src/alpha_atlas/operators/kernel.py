"""One read-only Float64 Numba ABI and an independent NumPy reference function."""

import builtins
import time

import numba
import numpy as np
from numba import types

from alpha_atlas.operators.code_policy import BUILTINS, validate_source


class KernelError(RuntimeError):
    pass


class Kernel:
    def __init__(self, definition, cost):
        self.definition, self.cost = definition, cost
        self.last_kernel_seconds = 0.0
        self._golden = None
        if numba.config.DISABLE_JIT:
            raise KernelError("numba_jit_disabled")
        self.kernel = numba.njit(nogil=True, boundscheck=True, fastmath=False, error_model="numpy")(
            self._function("kernel", definition.body)
        )
        signature = tuple(
            types.Array(types.float64, 1, "C", readonly=True)
            if kind == "series"
            else types.int64
            if kind == "window"
            else types.float64
            for _, kind in definition.parameters
        )
        try:
            self.kernel.compile(signature)
        except Exception as exc:
            raise KernelError(f"nopython_compile_failed: {exc}") from None
        if not self.kernel.nopython_signatures:
            raise KernelError("nopython_signature_missing")
        self.kernel.disable_compile()

    def _function(self, name, source):
        tree = validate_source(source, name, [p for p, _ in self.definition.parameters])
        namespace = {"np": np, "__builtins__": {n: getattr(builtins, n) for n in BUILTINS}}
        exec(compile(tree, "<numeric-kernel>", "exec"), namespace)
        return namespace[name]

    def call(self, arrays, params, function="kernel"):
        inputs = [np.array(a, dtype=np.float64, order="C", copy=True) for a in arrays]
        if not inputs or any(a.ndim != 1 or len(a) != len(inputs[0]) for a in inputs):
            raise KernelError("invalid_input_shape")
        originals = [a.copy() for a in inputs]
        for array in inputs:
            array.flags.writeable = False
        series, scalars = iter(inputs), iter(params)
        args = [
            next(series)
            if k == "series"
            else int(next(scalars))
            if k == "window"
            else float(next(scalars))
            for _, k in self.definition.parameters
        ]
        if function == "golden":
            if self._golden is None:
                self._golden = self._function("golden", self.definition.golden)
            target = self._golden
        elif function == "kernel":
            target = self.kernel
        else:
            raise KernelError("invalid_function")
        started = time.perf_counter()
        try:
            with np.errstate(all="ignore"):
                result = target(*args)
        except Exception as exc:
            raise KernelError(f"kernel_execution_failed: {type(exc).__name__}: {exc}") from None
        finally:
            self.last_kernel_seconds = time.perf_counter() - started
            self.cost["kernel_seconds"] += self.last_kernel_seconds
            self.cost["kernel_calls"] += 1
        if any(
            not np.array_equal(a, b, equal_nan=True) for a, b in zip(inputs, originals, strict=True)
        ):
            raise KernelError("input_mutation")
        if not isinstance(result, np.ndarray) or result.ndim != 1 or len(result) != len(inputs[0]):
            raise KernelError("invalid_output_shape")
        if result.dtype != np.float64:
            raise KernelError("invalid_output_dtype: expected float64")
        return np.where(np.isfinite(result), result, np.nan)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass
