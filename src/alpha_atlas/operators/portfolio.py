"""Lagged panel holdings. Inputs are feature returns, never supervised target labels."""

from __future__ import annotations

import numpy as np
import polars as pl


def lagged_portfolio(frame, columns, params, identity, op):
    if frame.select(*identity, "timestamp").is_duplicated().any():
        raise ValueError("portfolio requires unique identity/timestamp observations")
    if frame.select(pl.any_horizontal(pl.col(*identity, "timestamp").is_null()).any()).item():
        raise ValueError("portfolio requires nonnull identity/timestamp observations")
    out = np.full(frame.height, np.nan)
    previous = {}
    coverage = params[-1]

    def mean(keys, current):
        returns = np.array([current.get(key, np.nan) for key in keys])
        valid = returns[np.isfinite(returns)]
        return float(valid.mean()) if len(valid) >= np.ceil(coverage * len(keys)) else np.nan

    for part in (
        frame.with_row_index("__portfolio_pos")
        .sort("timestamp")
        .partition_by("timestamp", maintain_order=True)
    ):
        records = part.select(*identity, *dict.fromkeys(columns), "eligible").iter_rows(named=True)
        current = {tuple(row[k] for k in identity): row for row in records}
        realized = {
            key: row[columns[0]] if row[columns[0]] is not None else np.nan
            for key, row in current.items()
        }
        membership = columns[-1]
        universe = [key for key, row in previous.items() if row[membership] and row["eligible"]]
        result = np.nan
        if op == "CS_LAGGED_MEAN":
            if len(universe) >= int(params[0]):
                result = mean(universe, realized)
        else:
            signal = columns[1]
            ranked = sorted(
                (k for k in universe if previous[k][signal] is not None),
                key=lambda k: (previous[k][signal], k),
            )
            count = int(len(ranked) * params[0])
            if count:
                result = mean(ranked[-count:], realized) - mean(ranked[:count], realized)
        out[part["__portfolio_pos"].to_numpy()] = result
        previous = current
    return pl.Series(out, dtype=pl.Float64)
