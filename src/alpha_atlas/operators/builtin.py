"""Polars primitives plus bounded NumPy window batches for order statistics.

No pandas, vendor access, per-row rolling_map, or full-history window matrix.
"""

from __future__ import annotations

import numpy as np
import polars as pl

EPS = 1e-12


def build_expression(op: str, args: list[pl.Expr], params: list, group: list[str]) -> pl.Expr:
    a = args[0]
    b = args[1] if len(args) > 1 else None
    unary = {
        "NEG": lambda: -a,
        "ABS": a.abs,
        "SIGN": a.sign,
        "LOG": lambda: pl.when(a > 0).then(a.log()),
        "LOG1P": lambda: pl.when(a > -1).then(a.log1p()),
        "SQRT": lambda: pl.when(a >= 0).then(a.sqrt()),
        "EXP": a.exp,
        "IS_FINITE": lambda: a.is_finite().fill_null(False),
        "NOT": lambda: ~a,
    }
    if op in unary:
        return unary[op]()
    binary = {
        "ADD": lambda: a + b,
        "SUBTRACT": lambda: a - b,
        "MULTIPLY": lambda: a * b,
        "DIVIDE": lambda: pl.when(b.abs() > EPS).then(a / b),
        "MIN": lambda: pl.when(a.is_not_null() & b.is_not_null()).then(pl.min_horizontal(a, b)),
        "MAX": lambda: pl.when(a.is_not_null() & b.is_not_null()).then(pl.max_horizontal(a, b)),
        "LT": lambda: a < b,
        "LE": lambda: a <= b,
        "GT": lambda: a > b,
        "GE": lambda: a >= b,
        "EQ": lambda: a == b,
        "NE": lambda: a != b,
        "AND": lambda: pl.when(a.is_not_null() & b.is_not_null()).then(a & b),
        "OR": lambda: pl.when(a.is_not_null() & b.is_not_null()).then(a | b),
        "FILLNA": lambda: a.fill_null(b),
    }
    if op in binary:
        return binary[op]()
    if op == "IF_THEN_ELSE":
        return pl.when(a.is_not_null()).then(pl.when(a).then(b).otherwise(args[2]))
    if op == "CLIP":
        return a.clip(*params)
    if op in {"POWER", "SIGNED_POWER"}:
        p = params[0]
        result = a.pow(p) if op == "POWER" else a.sign() * a.abs().pow(p)
        return pl.when((a != 0) | (p > 0)).then(result)
    if op.startswith("CS_"):
        x = pl.when(pl.col("eligible").fill_null(False)).then(a)
        count = x.count().over("timestamp")
        mean = x.mean().over("timestamp")
        if op == "CS_RANK":
            return x.rank(method="average").over("timestamp") / count
        if op == "CS_DEMEAN":
            return x - mean
        if op == "CS_ZSCORE":
            std = x.std(ddof=1).over("timestamp")
            return pl.when(std > EPS).then((x - mean) / std)
        if op == "CS_SCALE":
            total = x.abs().sum().over("timestamp")
            return pl.when(total > EPS).then(x / total)
        if op == "CS_WINSORIZE":
            lo = x.quantile(params[0], interpolation="linear").over("timestamp")
            hi = x.quantile(params[1], interpolation="linear").over("timestamp")
            return x.clip(lo, hi)
    n = int(params[0])
    if op in {"DELAY", "DELTA", "RETURN"}:
        lag = a.shift(n).over(group)
        # Interior missing values also break a lag/return's declared history.
        complete = a.is_not_null().cast(pl.Int32).rolling_sum(n + 1).over(group) == n + 1
        result = (
            lag
            if op == "DELAY"
            else a - lag
            if op == "DELTA"
            else pl.when(lag.abs() > EPS).then(a / lag - 1)
        )
        return pl.when(complete).then(result)
    options = {"window_size": n, "min_samples": n}
    rolling = {
        "TS_SUM": lambda: a.rolling_sum(**options),
        "TS_MEAN": lambda: a.rolling_mean(**options),
        "TS_MEDIAN": lambda: a.rolling_median(**options),
        "TS_MIN": lambda: a.rolling_min(**options),
        "TS_MAX": lambda: a.rolling_max(**options),
        "TS_STD": lambda: a.rolling_std(**options, ddof=1),
        "TS_VAR": lambda: a.rolling_var(**options, ddof=1),
        "TS_QUANTILE": lambda: a.rolling_quantile(params[1], interpolation="linear", **options),
        "TS_RANK": lambda: a.rolling_rank(method="average", **options) / n,
        "TS_SKEW": lambda: a.rolling_skew(n, bias=False, min_samples=n),
        "TS_KURT": lambda: a.rolling_kurtosis(n, fisher=True, bias=False, min_samples=n),
        # Polars weighted rolling panics on null arrays. NaN propagates within the
        # affected windows; materialize() converts those results back to null.
        "TS_LINEAR_DECAY": lambda: a.fill_null(float("nan")).rolling_mean(
            **options, weights=list(range(1, n + 1))
        ),
    }
    if op in rolling:
        result = rolling[op]().over(group)
        if op in {"TS_SKEW", "TS_KURT"}:
            return pl.when(a.rolling_std(**options).over(group) > EPS).then(result)
        return result
    if op == "TS_ZSCORE":
        mean = a.rolling_mean(**options).over(group)
        std = a.rolling_std(**options, ddof=1).over(group)
        return pl.when(std > EPS).then((a - mean) / std)
    if op in {"TS_CORR", "TS_COV"}:
        finite = a.is_not_null() & b.is_not_null()
        x, y = pl.when(finite).then(a), pl.when(finite).then(b)
        function = pl.rolling_corr if op == "TS_CORR" else pl.rolling_cov
        result = function(x, y, **options, ddof=1).over(group)
        return result.clip(-1, 1) if op == "TS_CORR" else result
    if op in {"TS_COUNT", "TS_RATE", "TS_ANY", "TS_ALL"}:
        count = a.cast(pl.Float64).rolling_sum(**options).over(group)
        return {"TS_COUNT": count, "TS_RATE": count / n, "TS_ANY": count > 0, "TS_ALL": count == n}[
            op
        ]
    raise ValueError(f"operator implementation missing: {op}")


def _ranks(matrix: np.ndarray) -> np.ndarray:
    """Vectorized average ranks, row-wise; ties remain exact."""
    order = np.argsort(matrix, axis=1, kind="stable")
    sorted_values = np.take_along_axis(matrix, order, axis=1)
    width = matrix.shape[1]
    positions = np.broadcast_to(np.arange(width), matrix.shape)
    first = np.concatenate(
        (np.ones((len(matrix), 1), dtype=bool), sorted_values[:, 1:] != sorted_values[:, :-1]),
        axis=1,
    )
    last = np.concatenate((first[:, 1:], np.ones((len(matrix), 1), dtype=bool)), axis=1)
    starts = np.maximum.accumulate(np.where(first, positions, 0), axis=1)
    ends = np.minimum.accumulate(np.where(last, positions, width)[:, ::-1], axis=1)[:, ::-1]
    ranks = np.empty(matrix.shape, dtype=np.float64)
    np.put_along_axis(ranks, order, (starts + ends) / 2 + 1, axis=1)
    return ranks


def window_array(op: str, arrays: list[np.ndarray], window: int) -> np.ndarray:
    size = len(arrays[0])
    out = np.full(size, np.nan)
    if size < window:
        return out
    # Approx. 2 MiB per temporary matrix; rank operations allocate several temporaries.
    batch = max(1, 262144 // window)
    for start in range(window - 1, size, batch):
        end = min(size, start + batch)
        matrices = [
            np.lib.stride_tricks.sliding_window_view(
                np.asarray(a[start - window + 1 : end], dtype=np.float64), window
            )
            for a in arrays
        ]
        valid = np.logical_and.reduce([np.isfinite(m).all(axis=1) for m in matrices])
        x = matrices[0]
        with np.errstate(all="ignore"):
            if op == "TS_PROD":
                values = np.prod(x, axis=1)
            elif op in {"TS_ARGMIN", "TS_ARGMAX"}:
                function = np.argmin if op == "TS_ARGMIN" else np.argmax
                values = function(x[:, ::-1], axis=1).astype(float)
            else:
                rx, ry = (_ranks(m) for m in matrices)
                rx -= rx.mean(axis=1, keepdims=True)
                ry -= ry.mean(axis=1, keepdims=True)
                values = np.sum(rx * ry, axis=1) / np.sqrt(
                    np.sum(rx * rx, axis=1) * np.sum(ry * ry, axis=1)
                )
            out[start:end] = np.where(valid & np.isfinite(values), values, np.nan)
    return out
