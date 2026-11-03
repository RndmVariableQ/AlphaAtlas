"""Causal calendar-month calculations on MarketData-derived daily observations.

No supplier access, targets, fitted prediction models, or data-dependent parameter selection.
The definitions and deliberate paper deviations are described in factors.jsonl and README.md.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from alpha_atlas.factor_libraries.zookeeper import definitions

KEY = ["exchange", "product"]
EPS = 1e-12
MIN_RETURN_COVERAGE = 0.8


def divide(a, b):
    return a / b if np.isfinite(a) and np.isfinite(b) and abs(b) > EPS else np.nan


def complete(values, size=None):
    return len(values) == (len(values) if size is None else size) and np.isfinite(values).all()


def months_between(start: date, end: date):
    current = start.replace(day=1)
    while current <= end:
        yield current
        current = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )


def compound(values, size):
    return float(np.prod(1 + values) - 1) if complete(values, size) else np.nan


def _monthly_base(frame, calendar):
    frame = frame.sort("trading_day")
    days = frame["trading_day"].to_list()
    p = frame["price"].to_numpy()
    r = np.full(len(p), np.nan)
    index = np.array([calendar[day] for day in days])
    valid = (np.diff(index) == 1) & np.isfinite(p[1:]) & np.isfinite(p[:-1]) & (p[:-1] > 0)
    r[1:][valid] = p[1:][valid] / p[:-1][valid] - 1
    # A missing observed trading day also interrupts 250-day windows, even if prices exist.
    segment = np.r_[0, np.cumsum(np.diff(index) != 1)]
    daily = frame.with_columns(
        pl.Series("r", r).fill_nan(None), pl.Series("segment", segment)
    ).with_columns(
        pl.col("r").rolling_std(250, ddof=1).over("segment").alias("var"),
        pl.col("r").rolling_skew(250, bias=True).over("segment").alias("skew"),
        pl.col("r").rolling_kurtosis(250, fisher=True, bias=True).over("segment").alias("kurt"),
    )
    daily = daily.with_columns(pl.col("var").rolling_std(250, ddof=1).over("segment").alias("vov"))
    month_index = {}
    for i, day in enumerate(days):
        month_index.setdefault(day.replace(day=1), []).append(i)
    amount = frame["amount"].to_numpy()
    volume = frame["volume"].to_numpy()
    oi = frame["oi"].to_numpy()
    rows = []
    for month in months_between(days[0], days[-1]):
        ix = month_index.get(month, [])
        row = {"month": month, "P": np.nan, "V": np.nan, "OI": np.nan, "R": np.nan}
        row.update({name: np.nan for name in ("skew", "kurt", "var", "vov", "max", "iliq")})
        row["return_count"] = int(np.isfinite(r[ix]).sum())
        row["day_count"] = len(ix)
        if ix:
            row.update(P=p[ix[-1]], OI=oi[ix[-1]])
            row["V"] = float(volume[ix].sum()) if complete(volume[ix]) else np.nan
            row["R"] = compound(r[ix], len(ix))
            row["max"] = float(r[ix].max()) if complete(r[ix]) else np.nan
            for name in ("skew", "kurt", "var", "vov"):
                value = daily[name][ix[-1]]
                row[name] = np.nan if value is None else value
            selected = np.isfinite(r[ix]) & (np.abs(r[ix]) > EPS)
            selected &= np.isfinite(amount[ix]) & (amount[ix] > 0)
            if selected.sum() >= max(2, np.ceil(MIN_RETURN_COVERAGE * len(ix))):
                row["iliq"] = divide(1.0, np.mean(amount[ix][selected] / np.abs(r[ix][selected])))
        rows.append(row)
    for i, row in enumerate(rows):
        row["mom1"] = row["R"]
        for window, name in ((12, "mom12"), (36, "val36"), (60, "val60")):
            values = np.array([v["R"] for v in rows[max(0, i - window + 1) : i + 1]])
            row[name] = compound(values, window)
        row["seasonality"] = rows[i - 11]["R"] if i >= 11 else np.nan
        row["dOI"] = row["OI"] - rows[i - 1]["OI"] if i else np.nan
        for source, name in (("V", "atv"), ("OI", "dmaOI")):
            history = np.array([v[source] for v in rows[max(0, i - 12) : i]])
            row[name] = divide(row[source], history.mean()) - 1 if complete(history, 12) else np.nan
        history = np.array([v["P"] for v in rows[max(0, i - 54) : max(0, i - 41)]])
        row["val"] = (
            divide(row["P"], history.mean()) if i >= 54 and complete(history, 13) else np.nan
        )
        row["cv"] = np.nan
        if i >= 11:
            recent = rows[i - 11 : i + 1]
            left = recent[0]["month"]
            right = row["month"]
            ix = [k for k, day in enumerate(days) if left <= day.replace(day=1) <= right]
            if all(v["day_count"] > 0 for v in recent) and len(ix) >= 2 and complete(r[ix]):
                row["cv"] = divide(np.var(r[ix], ddof=1), np.mean(r[ix]))
    return rows


def _monthly_curve(frame, calendar):
    frame = frame.sort("trading_day")
    days = frame["trading_day"].to_list()
    index = np.array([calendar[day] for day in days])
    returns = []
    for j in (0, 1):
        p = frame[f"F{j}"].to_numpy()
        identity = frame[f"leg{j}"].to_list()
        r = np.full(len(p), np.nan)
        same = np.array(
            [a is not None and a == b for a, b in zip(identity[1:], identity[:-1], strict=True)]
        )
        valid = same & (np.diff(index) == 1) & np.isfinite(p[1:]) & np.isfinite(p[:-1])
        valid &= p[:-1] > EPS
        r[1:][valid] = p[1:][valid] / p[:-1][valid] - 1
        returns.append(r)
    positions = {}
    for i, day in enumerate(days):
        positions.setdefault(day.replace(day=1), []).append(i)
    rows = []
    for month in months_between(days[0], days[-1]):
        ix = positions.get(month, [])
        row = {"month": month, **dict.fromkeys(("basis", "rbasis", "C0", "C1"), np.nan)}
        for j in (0, 1):
            row[f"curve_return_coverage{j}"] = np.nan
        if ix:
            f0, f1, f2 = (frame[f"F{j}"][ix[-1]] for j in range(3))
            f0, f1, f2 = (np.nan if x is None else x for x in (f0, f1, f2))
            row["basis"] = divide(f0, f1)
            row["rbasis"] = divide(divide(f0, f1), divide(f1, f2))
            for j in (0, 1):
                valid = returns[j][ix]
                valid = valid[np.isfinite(valid)]
                coverage = len(valid) / len(ix)
                row[f"curve_return_coverage{j}"] = coverage
                if len(valid) >= 2 and coverage >= MIN_RETURN_COVERAGE:
                    row[f"C{j}"] = compound(valid, len(valid))
        rows.append(row)
    for i, row in enumerate(rows):
        recent = rows[max(0, i - 11) : i + 1]
        c0 = np.array([v["C0"] for v in recent])
        c1 = np.array([v["C1"] for v in recent])
        row["bmom"] = compound(c0, 12) - compound(c1, 12)
        for name in ("basis", "rbasis", "bmom"):
            values = np.array([v[name] for v in recent])
            row[name + "12"] = float(values.mean()) if complete(values, 12) else np.nan
        for name in ("basis12", "bmom12", "rbasis"):
            row["l2" + name] = rows[i - 2][name] if i >= 2 else np.nan
    return rows


def _portfolio_mean(values):
    """Observed-member proxy, never replace a missing holding with a differently ranked one."""
    valid = values[np.isfinite(values)]
    if len(values) and len(valid) >= np.ceil(MIN_RETURN_COVERAGE * len(values)):
        return float(valid.mean())
    return np.nan


def _bgr(monthly):
    """Prior-month holdings, 12-month full-rank OLS with intercept, no target access."""
    monthly = monthly.with_columns(pl.col(pl.Float64).fill_null(float("nan")))
    all_rows = monthly.to_dicts()
    by_month = {}
    for row in all_rows:
        by_month.setdefault(row["month"], {})[(row["exchange"], row["product"])] = row
    factor_returns = {}
    previous = {}
    for month, current in sorted(by_month.items()):
        result = np.full(3, np.nan)
        universe = [
            key for key, row in previous.items() if np.isfinite(row["P"]) and np.isfinite(row["R"])
        ]
        realized = np.array([current.get(key, {}).get("R", np.nan) for key in universe])
        if len(universe) >= 4:
            result[0] = _portfolio_mean(realized)
        for col, name in enumerate(("basis", "mom12"), 1):
            ranked = sorted(
                (key for key in universe if np.isfinite(previous[key][name])),
                key=lambda key: (previous[key][name], key),
            )
            n = len(ranked) // 4
            if n:
                low = np.array([current.get(key, {}).get("R", np.nan) for key in ranked[:n]])
                high = np.array([current.get(key, {}).get("R", np.nan) for key in ranked[-n:]])
                result[col] = _portfolio_mean(high) - _portfolio_mean(low)
        factor_returns[month] = result
        previous = current
    residual_vol = {}
    for key, frame in monthly.partition_by(KEY, as_dict=True, maintain_order=True).items():
        records = frame.sort("month").to_dicts()
        for i, row in enumerate(records):
            volatility = np.nan
            if i >= 11:
                window = records[i - 11 : i + 1]
                y = np.array([v["R"] for v in window])
                x = np.column_stack((np.ones(12), [factor_returns[v["month"]] for v in window]))
                if complete(y) and np.isfinite(x).all() and np.linalg.matrix_rank(x) == 4:
                    residual = y - x @ np.linalg.lstsq(x, y, rcond=None)[0]
                    volatility = float(np.std(residual, ddof=1))
            residual_vol[(*key, row["month"])] = volatility
    return pl.Series(
        "ivolaBGR", [residual_vol[(v["exchange"], v["product"], v["month"])] for v in all_rows]
    )


def compute_monthly(base_daily: pl.DataFrame, curve_daily: pl.DataFrame) -> pl.DataFrame:
    """Return one row per product/calendar month; values become usable NEXT month.

    base_daily: keys, trading_day, price (adjusted VWAP), volume, amount, oi.
    curve_daily: keys, trading_day, F0/F1/F2 (raw close), leg0/leg1/leg2.
    Both must already contain only observations available through the requested cutoff.
    """
    for data in (base_daily, curve_daily):
        if data.select(*KEY, "trading_day").is_duplicated().any():
            raise ValueError("duplicate daily product observation")
    calendar = {
        day: i
        for i, day in enumerate(
            sorted(set(base_daily["trading_day"]) | set(curve_daily["trading_day"]))
        )
    }
    base = []
    for key, frame in base_daily.partition_by(KEY, as_dict=True).items():
        base.extend(
            {**dict(zip(KEY, key, strict=True)), **r} for r in _monthly_base(frame, calendar)
        )
    curve = []
    for key, frame in curve_daily.partition_by(KEY, as_dict=True).items():
        curve.extend(
            {**dict(zip(KEY, key, strict=True)), **r} for r in _monthly_curve(frame, calendar)
        )
    if not base or not curve:
        raise ValueError("both base and curve daily observations are required")
    monthly = (
        pl.DataFrame(base, infer_schema_length=None)
        .join(pl.DataFrame(curve, infer_schema_length=None), on=KEY + ["month"], how="left")
        .sort(KEY + ["month"])
    )
    # Keep NaN internally for NumPy's explicit missing checks, null at the public boundary.
    monthly = monthly.with_columns(pl.col(pl.Float64).fill_null(float("nan")))
    monthly = monthly.with_columns(_bgr(monthly))
    return monthly.with_columns(
        pl.when(pl.col(pl.Float64).is_finite()).then(pl.col(pl.Float64)).otherwise(None)
    ).rename({row["name"]: row["prepared_field"] for row in definitions()})


def align_monthly(features: pl.DataFrame, monthly: pl.DataFrame, *, fields=None) -> pl.DataFrame:
    """Exact prior calendar-month join; no same-month values or stale forward filling."""
    fields = [row["prepared_field"] for row in definitions()] if fields is None else list(fields)
    return (
        features.with_columns(
            pl.col("trading_day").dt.truncate("1mo").dt.offset_by("-1mo").alias("__month")
        )
        .join(
            monthly.select(*KEY, pl.col("month").alias("__month"), *fields),
            on=KEY + ["__month"],
            how="left",
            validate="m:1",
            maintain_order="left",
        )
        .drop("__month")
    )
