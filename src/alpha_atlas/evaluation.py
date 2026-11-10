"""Split-safe factor evaluation with explicit run-local observation references."""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import polars as pl
from numba import njit, prange
from numpy.typing import ArrayLike

from alpha_atlas.contracts import (
    Candidate,
    DateRange,
    EvaluationReport,
    FactorCorrelation,
    FactorValues,
    Fold,
    Metric,
    fingerprint,
)
from alpha_atlas.expressions import GROUP, compile_factor, execute
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.storage import ArrowCache


def build_targets(frame: pl.DataFrame, price: str, horizon: int) -> pl.DataFrame:
    if type(horizon) is not int or horizon < 1:
        raise ValueError("target horizon must be positive integer")
    group = GROUP + (["segment_id"] if "segment_id" in frame.columns else [])
    frame = frame.sort(GROUP + ["timestamp"])
    future = pl.col(price).shift(-horizon).over(group)
    valid = pl.col(price).is_finite() & (pl.col(price) > 0)
    if "target_eligible" in frame.columns:
        valid = valid & pl.col("target_eligible").fill_null(False)
    # Interior known invalid bars cannot be compressed into an apparently valid horizon.
    complete = (
        valid.cast(pl.Int32).rolling_sum(horizon + 1).shift(-horizon).over(group) == horizon + 1
    )
    return frame.select(
        "row_id",
        pl.when(complete & (future > 0)).then((future / pl.col(price)).log()).alias("target"),
        pl.col("trading_day").shift(-horizon).over(group).alias("target_end"),
    )


def split_panel(frame: pl.DataFrame, period: DateRange) -> pl.DataFrame:
    return frame.filter(
        pl.col("eligible").fill_null(False)
        & pl.col("trading_day").is_between(period.start, period.end)
        & pl.col("target_end").is_between(period.start, period.end)
        & pl.col("target").is_finite().fill_null(False)
    )


def clip_bounds(frame: pl.DataFrame, column: str, quantiles) -> list[float] | None:
    """Linear quantiles of one finite training column, independent of other missingness."""
    values = pl.col(column).filter(pl.col(column).is_finite().fill_null(False))
    bounds = frame.select(
        values.quantile(q, interpolation="linear").alias(str(i)) for i, q in enumerate(quantiles)
    ).row(0)
    return list(bounds) if all(v is not None and math.isfinite(v) for v in bounds) else None


def apply_clipping(frame: pl.DataFrame, clipping: dict) -> pl.DataFrame:
    """Apply fitted bounds only; never turn nonfinite observations into finite scores."""
    if not clipping:
        return frame
    expressions = []
    for column in ("value", "target"):
        bounds = clipping[column]
        finite = pl.when(pl.col(column).is_finite()).then(pl.col(column))
        expressions.append(
            (finite.clip(*bounds) if bounds is not None else pl.lit(None, dtype=pl.Float64)).alias(
                column
            )
        )
    return frame.with_columns(expressions)


FUTURES_METRICS = (
    "time_series_equal_pearson_ic",
    "time_series_weighted_pearson_ic",
    "time_series_equal_spearman_ic",
    "time_series_weighted_spearman_ic",
)


def product_weights(features: pl.DataFrame, period: DateRange) -> pl.DataFrame:
    """Fixed split weights: sqrt of each product's total eligible, valid turnover."""
    return (
        features.filter(
            pl.col("trading_day").is_between(period.start, period.end)
            & pl.col("eligible").fill_null(False)
        )
        .group_by("product")
        .agg(
            pl.when(pl.col("amount").is_finite() & (pl.col("amount") >= 0))
            .then(pl.col("amount"))
            .sum()
            .sqrt()
            .alias("weight")
        )
        .filter(pl.col("weight").is_finite() & (pl.col("weight") > 0))
    )


def metrics(
    frame: pl.DataFrame, name: str, split: str, *, weights=None, correlation=None
) -> tuple[Metric, ...]:
    frame = frame.filter(
        pl.col("value").is_finite().fill_null(False) & pl.col("target").is_finite().fill_null(False)
    )
    if name == "cross_sectional_spearman":
        groups = ["trading_day"]
    elif name in FUTURES_METRICS:
        # Pool all contracts first; ranking and correlation happen within the whole product.
        groups = ["product"]
    else:
        raise ValueError(f"unsupported metric {name}")
    primary = "pearson" if "pearson" in name else "spearman"
    correlations = (
        (correlation,)
        if correlation
        else (
            primary,
            "spearman" if primary == "pearson" else "pearson",
        )
    )
    result = (
        frame.group_by(groups)
        .agg(
            *(pl.corr("value", "target", method=c).alias(c) for c in correlations),
            pl.len().alias("n"),
            pl.col("value").n_unique().alias("nx"),
            pl.col("target").n_unique().alias("ny"),
        )
        .filter((pl.col("n") >= 5) & (pl.col("nx") > 1) & (pl.col("ny") > 1))
        .sort(groups)
    )
    names = (name,)
    if name in FUTURES_METRICS:
        names += (
            name.replace("_weighted_", "_equal_")
            if "_weighted_" in name
            else name.replace("_equal_", "_weighted_"),
        )
    output = []
    for correlation in correlations:
        valid = result.filter(pl.col(correlation).is_finite()).rename({correlation: "ic"})
        for metric_name in names:
            observed = valid
            if "_weighted_" in metric_name:
                if weights is None:
                    observed = valid.head(0).with_columns(pl.lit(0.0).alias("weight"))
                else:
                    observed = valid.join(weights, on="product", how="inner", validate="1:1").sort(
                        groups
                    )
                value = (
                    observed.select(
                        (pl.col("ic") * pl.col("weight")).sum() / pl.col("weight").sum()
                    ).item()
                    if observed.height
                    else None
                )
            else:
                value = observed["ic"].mean() if observed.height else None
            if value is not None:
                value = max(-1.0, min(1.0, value)) if math.isfinite(value) else None
            label = metric_name.replace(primary, correlation)
            output.append(
                Metric(label, split, value, int(observed["n"].sum()), label, observed.height)
            )
    return tuple(output)


def metric(frame: pl.DataFrame, name: str, split: str, *, correlation=None, weights=None) -> Metric:
    correlation = correlation or ("pearson" if "pearson" in name else "spearman")
    return metrics(frame, name, split, correlation=correlation, weights=weights)[0]


def prediction_error_summary(
    target: ArrayLike,
    prediction: ArrayLike,
    baseline: ArrayLike,
    *,
    weights: ArrayLike | None = None,
) -> dict[str, int | float | None]:
    """Squared error on an explicitly aligned, nonempty sample, with optional row weights.

    Target and prediction must be finite one-dimensional arrays of equal length.
    Baseline may also be a finite scalar. No observations are silently removed;
    the caller owns alignment, baseline fitting and the evaluation split.
    Weights must be finite, nonnegative and aligned, with positive total weight.
    Product-level aggregation can assign each row its product weight / sample count.
    Relative reduction is a fraction, not a percentage, and is undefined when
    baseline error is zero. Weighted SSE uses the supplied weights, not row counts.
    """
    arrays = {}
    inputs = {"target": target, "prediction": prediction, "baseline": baseline}
    if weights is not None:
        inputs["weights"] = weights
    for name, values in inputs.items():
        try:
            array = np.asarray(values)
            if np.iscomplexobj(array):
                raise ValueError("complex values are not supported")
            array = array.astype(np.float64, copy=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must contain real numeric values") from exc
        if array.ndim != 1 and not (name == "baseline" and array.ndim == 0):
            raise ValueError(f"{name} must be one-dimensional; only baseline may be scalar")
        if not np.isfinite(array).all():
            raise ValueError(f"{name} must contain only finite values")
        arrays[name] = array
    target = arrays["target"]
    if not target.size:
        raise ValueError("target must be nonempty")
    for name in ("prediction", "baseline", *(["weights"] if weights is not None else [])):
        if arrays[name].ndim and arrays[name].shape != target.shape:
            raise ValueError(f"{name} must have the same length as target")
    if weights is not None and (arrays["weights"] < 0).any():
        raise ValueError("weights must be nonnegative")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            error = np.square(target - arrays["prediction"])
            baseline_error = np.square(target - arrays["baseline"])
            total = target.size
            if weights is not None:
                total = arrays["weights"].sum()
                if total <= 0:
                    raise ValueError("weights must have positive sum")
                error *= arrays["weights"]
                baseline_error *= arrays["weights"]
            sse, baseline_sse = error.sum(), baseline_error.sum()
            reduction = (baseline_sse - sse) / baseline_sse if baseline_sse > 0 else None
    except FloatingPointError as exc:
        raise ValueError("prediction error computation overflowed") from exc
    n = int(target.size)
    result = {
        "n": n,
        "sse": float(sse),
        "baseline_sse": float(baseline_sse),
        "mse": float(sse / total),
        "baseline_mse": float(baseline_sse / total),
        "relative_mse_reduction": float(reduction) if reduction is not None else None,
    }
    if weights is not None:
        result["weight_sum"] = float(total)
    return result


def paired_prediction_metrics(frame, weights):
    """Compare finite predictions on explicit common rows and common valid IC products."""
    required = ["target", "baseline", "prediction"]
    if (
        not frame.height
        or not frame.select(pl.all_horizontal(pl.col(required).is_finite().fill_null(False)))
        .to_series()
        .all()
    ):
        raise ValueError("paired predictions require nonempty finite common rows")
    if frame["row_id"].n_unique() != frame.height:
        raise ValueError("paired predictions require unique row_id")
    if frame["product"].null_count():
        raise ValueError("paired predictions require product identities")
    if weights["product"].n_unique() != weights.height:
        raise ValueError("duplicate product weights")
    per_product = (
        frame.group_by("product")
        .agg(
            pl.len().alias("n"),
            ((pl.col("target") - pl.col("baseline")) ** 2).mean().alias("baseline_mse"),
            ((pl.col("target") - pl.col("prediction")) ** 2).mean().alias("mse"),
            pl.col("target").n_unique().alias("ny"),
            pl.col("baseline").n_unique().alias("nb"),
            pl.col("prediction").n_unique().alias("np"),
            *(
                pl.corr(name, "target", method=kind).clip(-1.0, 1.0).alias(f"{name}_{kind}")
                for name in ("baseline", "prediction")
                for kind in ("pearson", "spearman")
            ),
        )
        .join(weights, on="product", how="inner", validate="1:1")
        .sort("product")
    )
    if (
        not per_product.height
        or not per_product["weight"].is_finite().fill_null(False).all()
        or (per_product["weight"] <= 0).any()
    ):
        raise ValueError("paired evaluation requires positive finite product weights")
    w = per_product["weight"].to_numpy()
    bmse, mse = (
        float(np.average(per_product[c].to_numpy(), weights=w)) for c in ("baseline_mse", "mse")
    )
    if not math.isfinite(bmse) or not math.isfinite(mse):
        raise ValueError("paired squared errors overflowed")
    common = per_product.filter(
        (pl.col("n") >= 5)
        & (pl.col("ny") > 1)
        & (pl.col("nb") > 1)
        & (pl.col("np") > 1)
        & pl.all_horizontal(
            pl.col(
                [f"{n}_{k}" for n in ("baseline", "prediction") for k in ("pearson", "spearman")]
            ).is_finite()
        )
    )
    result = {
        "rows": frame.height,
        "weighted_rows": int(per_product["n"].sum()),
        "products": per_product.height,
        "ic_rows": int(common["n"].sum()),
        "ic_products": common["product"].to_list(),
        "baseline_mse": bmse,
        "mse": mse,
        "relative_mse_reduction": 1 - mse / bmse if bmse else None,
        "metrics": {},
    }
    for kind in ("pearson", "spearman"):
        for weighting in ("weighted", "equal"):
            values = []
            for name in ("baseline", "prediction"):
                values.append(
                    float(
                        np.average(
                            common[f"{name}_{kind}"].to_numpy(),
                            weights=common["weight"].to_numpy()
                            if weighting == "weighted"
                            else None,
                        )
                    )
                    if common.height
                    else None
                )
            b, p = values
            result["metrics"][f"{weighting}_{kind}"] = {
                "baseline": b,
                "augmented": p,
                "delta": p - b if b is not None else None,
            }
    # JSON cannot represent NaN from constant-product correlations.
    result["per_product"] = per_product.with_columns(
        pl.when(pl.col(c).is_finite()).then(pl.col(c)).otherwise(None).alias(c)
        for c in per_product.columns
        if per_product.schema[c].is_float()
    ).to_dicts()
    return result


@njit(nogil=True, parallel=True)
def _resampled_rank_correlations(day_indices, orders, tie_ends, multiplicity):
    """Exact average ranks of duplicated rows, without materializing each resample."""
    repeats = multiplicity.shape[0]
    size = len(day_indices)
    result = np.full((repeats, 2), np.nan)
    for repeat in prange(repeats):
        counts = multiplicity[repeat, day_indices]
        total = counts.sum()
        if total < 5:
            continue
        ranks = np.empty((3, size))
        for column in range(3):
            before = 0.0
            start = 0
            while start < size:
                end = tie_ends[column, start]
                count = 0.0
                for position in range(start, end):
                    count += counts[orders[column, position]]
                # Subtract the common mean rank; the omitted +0.5 cancels exactly.
                rank = before + count / 2 - total / 2
                for position in range(start, end):
                    ranks[column, orders[column, position]] = rank
                before += count
                start = end
        yy = bb = pp = by = py = 0.0
        for i in range(size):
            y, b, p = ranks[:, i]
            weight = counts[i]
            yy += weight * y * y
            bb += weight * b * b
            pp += weight * p * p
            by += weight * b * y
            py += weight * p * y
        if yy > 0 and bb > 0 and pp > 0:
            result[repeat, 0] = min(1.0, max(-1.0, by / math.sqrt(bb * yy)))
            result[repeat, 1] = min(1.0, max(-1.0, py / math.sqrt(pp * yy)))
    return result


def _spearman_bootstrap_deltas(frame, weights, date_index, multiplicity):
    numerator = np.zeros(len(multiplicity))
    denominator = np.zeros(len(multiplicity))
    product_weights = dict(weights.select("product", "weight").iter_rows())
    for (product,), part in frame.partition_by("product", as_dict=True).items():
        if product not in product_weights:
            continue
        values = part.select("target", "baseline", "prediction").to_numpy().T
        orders = np.argsort(values, axis=1)
        tie_ends = np.zeros_like(orders)
        for column in range(3):
            sorted_values = values[column, orders[column]]
            ends = np.r_[np.flatnonzero(np.diff(sorted_values) != 0) + 1, len(part)]
            tie_ends[column, np.r_[0, ends[:-1]]] = ends
        day_indices = np.array([date_index[day] for day in part["trading_day"].to_list()])
        correlations = _resampled_rank_correlations(day_indices, orders, tie_ends, multiplicity)
        valid = np.isfinite(correlations).all(axis=1)
        numerator[valid] += (correlations[valid, 1] - correlations[valid, 0]) * product_weights[
            product
        ]
        denominator[valid] += product_weights[product]
    return np.divide(
        numerator, denominator, out=np.full(len(multiplicity), np.nan), where=denominator > 0
    )


def paired_block_bootstrap(
    frame, weights, *, days=20, repeats=2000, seed=20261102, correlation="pearson"
):
    """Year-stratified market-wide moving day blocks; pooled IC, never mean daily IC."""
    if type(days) is not int or days < 1 or type(repeats) is not int or repeats < 1:
        raise ValueError("bootstrap days and repeats must be positive integers")
    if correlation not in {"pearson", "spearman"}:
        raise ValueError("bootstrap correlation must be pearson or spearman")
    paired_prediction_metrics(frame, weights)  # Fail instead of silently dropping bad rows.
    if frame["trading_day"].null_count():
        raise ValueError("bootstrap requires trading days")
    dates = sorted(frame["trading_day"].unique().to_list())
    products = sorted(set(frame["product"].to_list()) & set(weights["product"].to_list()))
    date_index = {d: i for i, d in enumerate(dates)}
    product_index = {p: i for i, p in enumerate(products)}
    y, b, p = pl.col("target"), pl.col("baseline"), pl.col("prediction")
    daily = frame.group_by("trading_day", "product").agg(
        pl.len().alias("n"),
        y.sum().alias("y"),
        (y * y).sum().alias("yy"),
        b.sum().alias("b"),
        (b * b).sum().alias("bb"),
        (b * y).sum().alias("by"),
        p.sum().alias("p"),
        (p * p).sum().alias("pp"),
        (p * y).sum().alias("py"),
        ((y - b) ** 2).sum().alias("be"),
        ((y - p) ** 2).sum().alias("pe"),
    )
    stats = np.zeros((len(dates), len(products), 11))
    for row in daily.iter_rows(named=True):
        if row["product"] in product_index:
            stats[date_index[row["trading_day"]], product_index[row["product"]]] = [
                row[k] for k in ("n", "y", "yy", "b", "bb", "by", "p", "pp", "py", "be", "pe")
            ]
    if not np.isfinite(stats).all():
        raise ValueError("bootstrap statistics overflowed")
    rng = np.random.default_rng(seed)
    multiplicity = np.zeros((repeats, len(dates)))
    for year in sorted({d.year for d in dates}):
        indices = np.array([i for i, d in enumerate(dates) if d.year == year])
        block = min(days, len(indices))
        for r in range(repeats):
            starts = rng.integers(0, len(indices) - block + 1, size=math.ceil(len(indices) / block))
            sampled = indices[(starts[:, None] + np.arange(block)).ravel()[: len(indices)]]
            np.add.at(multiplicity[r], sampled, 1)
    aggregate = (multiplicity @ stats.reshape(len(dates), -1)).reshape(repeats, len(products), 11)
    n, sy, yy, sb, bb, by, sp, pp, py, be, pe = np.moveaxis(aggregate, -1, 0)
    w = np.array([weights.filter(pl.col("product") == name)["weight"][0] for name in products])
    with np.errstate(divide="ignore", invalid="ignore"):
        vy, vb, vp = yy - sy * sy / n, bb - sb * sb / n, pp - sp * sp / n
        rb = (by - sb * sy / n) / np.sqrt(vb * vy)
        rp = (py - sp * sy / n) / np.sqrt(vp * vy)
        # Cancellation in a constant series must not manufacture a tiny positive variance.
        eps = 8 * np.finfo(float).eps
        valid = (
            (n >= 5)
            & (vy > eps * np.abs(yy))
            & (vb > eps * np.abs(bb))
            & (vp > eps * np.abs(pp))
            & np.isfinite(rb)
            & np.isfinite(rp)
        )
        ic_weights = np.where(valid, w, 0)
        delta = np.sum(np.where(valid, np.clip(rp, -1, 1) - np.clip(rb, -1, 1), 0) * w, axis=1)
        delta /= ic_weights.sum(axis=1)
        # Errors use all observed products, even when a prediction has no defined IC.
        bmse = np.sum(np.where(n > 0, be / n, 0) * w, axis=1)
        pmse = np.sum(np.where(n > 0, pe / n, 0) * w, axis=1)
        reduction = 1 - pmse / bmse
    if correlation == "spearman":
        delta = _spearman_bootstrap_deltas(frame, weights, date_index, multiplicity)
    output = {"block_days": days, "repeats": repeats, "seed": seed}
    for name, values in (
        (f"delta_weighted_{correlation}", delta),
        ("relative_mse_reduction", reduction),
    ):
        values = values[np.isfinite(values)]
        output[name] = {
            "valid_replicates": len(values),
            "interval_95": np.quantile(values, [0.025, 0.975]).tolist() if len(values) else None,
        }
    return output


def _daily_ic(scored, name, weights):
    futures = name in FUTURES_METRICS
    groups = ["trading_day", "product"] if futures else ["trading_day"]
    daily = (
        scored.group_by(groups)
        .agg(
            pl.corr("value", "target", method="pearson" if "pearson" in name else "spearman").alias(
                "ic"
            ),
            pl.len().alias("n"),
        )
        .filter((pl.col("n") >= 5) & pl.col("ic").is_finite())
    )
    if futures:
        if "_weighted_" in name:
            daily = (
                daily.join(weights, on="product", how="inner")
                .sort("trading_day", "product")
                .group_by("trading_day")
                .agg(((pl.col("ic") * pl.col("weight")).sum() / pl.col("weight").sum()).alias("ic"))
            )
        else:
            daily = (
                daily.sort("trading_day", "product")
                .group_by("trading_day")
                .agg(pl.col("ic").mean())
            )
    daily = daily.sort("trading_day")
    return daily


def search_diagnostics(
    panel,
    period,
    name,
    direction,
    weights=None,
    *,
    factor_panel="contract",
    clipping=None,
    version="icir_turnover_v1",
):
    """Train-only stability and explicit holdings turnover; never substitute missing statistics.

    ICIR uses daily primary IC (futures: first pool within product/day), sample std,
    at least two valid days. Futures turnover uses unit signed holdings per native
    bar and continuity segment. Stocks use daily demeaned ranks / gross exposure.
    Both turnovers are half the absolute change, excluding initial positions.
    """
    futures = name in FUTURES_METRICS
    if factor_panel not in {"contract", "product"}:
        raise ValueError("panel must be 'contract' or 'product'")
    result = {
        "version": version,
        "split": "train",
        "icir": None,
        "icir_days": 0,
        "turnover": None,
        "turnover_observations": 0,
        "turnover_unit": "native_bar_unit_position" if futures else "daily_rank_portfolio",
    }
    if version == "icir_turnover_lag1_v2":
        result.update(rank_lag_diagnostics(panel, period, name, weights, factor_panel, clipping))
    if direction is None:
        return result
    scored = split_panel(panel, period)
    if clipping is not None:
        scored = apply_clipping(scored, clipping)
    scored = scored.filter(pl.col("value").is_finite())
    daily = _daily_ic(scored, name, weights).with_columns(pl.col("ic") * direction)
    result["icir_days"] = daily.height
    std = daily["ic"].std(ddof=1)
    if daily.height >= 2 and std is not None and std > 1e-12:
        result["icir"] = daily["ic"].mean() / std

    holdings = panel.filter(pl.col("trading_day").is_between(period.start, period.end))
    finite = pl.col("eligible").fill_null(False) & pl.col("value").is_finite().fill_null(False)
    if futures:
        identity = GROUP if factor_panel == "contract" else ["exchange", "product"]
        segment = "segment_id" if factor_panel == "contract" else "product_segment_id"
        group = identity + ([segment] if segment in panel.columns else [])
        holdings = (
            holdings.sort(identity + ["timestamp"])
            .with_columns(pl.when(finite).then(pl.col("value").sign()).alias("position"))
            .with_columns(
                ((pl.col("position") - pl.col("position").shift().over(group)).abs() / 2).alias(
                    "change"
                )
            )
            .filter(pl.col("change").is_finite())
            .group_by("product")
            .agg(pl.col("change").mean(), pl.len().alias("n"))
        )
        if "_weighted_" in name:
            holdings = holdings.join(weights, on="product", how="inner").sort("product")
            value = holdings.select(
                (pl.col("change") * pl.col("weight")).sum() / pl.col("weight").sum()
            ).item()
        else:
            value = holdings.sort("product")["change"].mean()
        result["turnover_observations"] = int(holdings["n"].sum())
    else:
        # Keep the full observed clock: a missing/invalid snapshot must not be bridged.
        clock = holdings.select("trading_day").unique().sort("trading_day").with_row_index("step")
        holdings = holdings.filter(pl.col("eligible").fill_null(False)).join(
            clock, on="trading_day"
        )
        complete = (
            holdings.group_by("step")
            .agg(
                pl.col("value").is_finite().fill_null(False).all().alias("complete"),
                pl.len().alias("n"),
            )
            .filter(pl.col("complete") & (pl.col("n") >= 5))
            .select("step")
        )
        holdings = (
            holdings.join(complete, on="step")
            .with_columns(pl.col("value").rank().over("step").alias("rank"))
            .with_columns((pl.col("rank") - pl.col("rank").mean().over("step")).alias("rank"))
            .with_columns(
                (pl.col("rank") / pl.col("rank").abs().sum().over("step"))
                .fill_nan(0.0)
                .alias("position")
            )
            .select("step", *GROUP, "position")
        )
        previous = holdings.with_columns(pl.col("step") + 1).rename({"position": "previous"})
        adjacent = complete.join(complete.with_columns(pl.col("step") + 1), on="step")
        changes = (
            holdings.join(previous, on=["step", *GROUP], how="full", coalesce=True)
            .join(adjacent, on="step")
            .with_columns(
                (
                    (pl.col("position").fill_null(0) - pl.col("previous").fill_null(0)).abs() / 2
                ).alias("change")
            )
            .sort("step", *GROUP)
            .group_by("step")
            .agg(pl.col("change").sum())
            .sort("step")
        )
        value = changes["change"].mean()
        result["turnover_observations"] = changes.height
    result["turnover"] = value if value is not None and math.isfinite(value) else None
    return result


def rank_lag_diagnostics(panel, period, name, weights=None, factor_panel="contract", clipping=None):
    """Raw signed train RankIC/IR and one-bar delay; no fitted direction or future rows.

    Delay on the full train clock before dropping invalid scoring rows. Reset at the
    configured factor-panel boundaries; never bridge missing/noneligible observations.
    Stocks additionally require adjacent observed market days, as in a wide panel shift.
    Apply the original factor's train bounds to both realizations without refitting.
    """
    train = panel.filter(pl.col("trading_day").is_between(period.start, period.end))
    identity = GROUP if factor_panel == "contract" else ["exchange", "product"]
    segment = "segment_id" if factor_panel == "contract" else "product_segment_id"
    group = identity + ([segment] if segment in train.columns else [])
    clock = "timestamp" if "timestamp" in train.columns else "trading_day"
    train = train.sort(identity + [clock])
    previous = pl.when(pl.col("eligible").fill_null(False) & pl.col("value").is_finite())
    delayed = train.with_columns(previous.then(pl.col("value")).shift().over(group).alias("value"))
    if name == "cross_sectional_spearman":
        days = train.select("trading_day").unique().sort("trading_day").with_row_index("day_index")
        delayed = delayed.join(days, on="trading_day").sort(identity + [clock])
        delayed = delayed.with_columns(
            pl.when(pl.col("day_index") - pl.col("day_index").shift().over(group) == 1)
            .then(pl.col("value"))
            .alias("value")
        )
    scored = apply_clipping(split_panel(train, period), clipping or {})
    lagged = apply_clipping(split_panel(delayed, period), clipping or {})
    rank_ic = metric(scored, name, "train", correlation="spearman", weights=weights)
    lag_ic = metric(lagged, name, "train", correlation="spearman", weights=weights)
    # Reuse exactly the shared daily ICIR aggregation, with raw sign and Spearman.
    rank_name = name.replace("pearson", "spearman")
    daily = _daily_ic(scored.filter(pl.col("value").is_finite()), rank_name, weights)
    std = daily["ic"].std(ddof=1)
    rank_icir = (
        daily["ic"].mean() / std if daily.height >= 2 and std is not None and std > 1e-12 else None
    )
    return {
        "rank_ic": rank_ic.value,
        "rank_icir": rank_icir,
        "rank_icir_days": daily.height,
        "lag1_rank_ic": lag_ic.value,
        "lag1_n_obs": lag_ic.n_obs,
        "lag1_gap": max(0.0, rank_ic.value - lag_ic.value)
        if rank_ic.value is not None and lag_ic.value is not None
        else None,
        "lag1_unit": "native_bar",
    }


RESIDUAL_RIDGE = 1e-3


def _standardize(values: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Standardize finite values and impute zero; None when the column carries no variation."""
    finite = np.isfinite(values)
    if finite.sum() < 2:
        return None
    observed = values[finite]
    scale = observed.std()
    if not math.isfinite(scale) or scale <= 1e-12:
        return None
    standardized = np.zeros(values.shape, dtype=np.float64)
    standardized[finite] = (observed - observed.mean()) / scale
    return standardized, finite


def library_projection(candidate: np.ndarray, members: np.ndarray, gram=None):
    """Ridge-project a candidate on library columns; return its residual and explained share.

    Uses factor values only, never targets. Columns must already be standardized with zero
    imputation; the ridge is relative to the row count so it does not depend on sample size.
    """
    if members.shape[1] == 0:
        return candidate.copy(), 0.0
    gram = members.T @ members if gram is None else gram
    ridge = RESIDUAL_RIDGE * members.shape[0] * np.eye(members.shape[1])
    coefficients = np.linalg.solve(gram + ridge, members.T @ candidate)
    residual = candidate - members @ coefficients
    total = float(candidate @ candidate)
    explained = 0.0 if total <= 0 else max(0.0, 1.0 - float(residual @ residual) / total)
    return residual, explained


class EvaluationService:
    def __init__(
        self,
        features: pl.DataFrame,
        fold: Fold,
        profile: dict,
        fields: set[str],
        *,
        snapshot_id: str | None = None,
        context: dict | None = None,
        registry: OperatorRegistry | None = None,
        cache_dir: Path | None = None,
        cache_bytes: int = 512 * 1024 * 1024,
        disk_cache: ArrowCache | None = None,
        compile_options: dict | None = None,
        retain_training: bool = False,
        diagnostics: str = "",
        training_cache_factors: int = 208,
        clip_quantiles=(0.01, 0.99),
    ):
        if type(training_cache_factors) is not int or training_cache_factors < 1:
            raise ValueError("training_cache_factors must be a positive integer")
        if clip_quantiles is not None and (
            not isinstance(clip_quantiles, (tuple, list))
            or len(clip_quantiles) != 2
            or any(type(q) not in {int, float} for q in clip_quantiles)
            or not 0 <= clip_quantiles[0] < clip_quantiles[1] <= 1
        ):
            raise ValueError("clip_quantiles must satisfy 0 <= lower < upper <= 1")
        self.clip_quantiles = tuple(clip_quantiles) if clip_quantiles is not None else None
        # Keep 200 library members plus a small allowance for parents and pending candidates.
        self._training_cache_factors = training_cache_factors
        latest = features["trading_day"].max()
        if latest is not None and latest > fold.val.end:
            raise ValueError("search evaluator cannot receive OOS features")
        if features["row_id"].n_unique() != features.height:
            raise ValueError("duplicate row_id")
        if features.select(*GROUP, "timestamp").is_duplicated().any():
            raise ValueError("duplicate instrument timestamp")
        if any(
            c.startswith(("label_", "target")) and c != "target_eligible" for c in features.columns
        ):
            raise ValueError("targets must not enter the feature panel")
        self.features = features.clone()
        if diagnostics not in {"", "icir_turnover_v1", "icir_turnover_lag1_v2"}:
            raise ValueError("unsupported search diagnostics")
        self.diagnostics = diagnostics
        self.fold = fold
        self.profile = json.loads(json.dumps(profile, default=str))
        self.panel = self.profile.get("panel", "contract")
        if self.panel not in {"contract", "product"}:
            raise ValueError("panel must be 'contract' or 'product'")
        if self.panel == "product" and "product" not in features.columns:
            raise ValueError("product panel requires a product feature column")
        self.fields = set(fields)
        self.snapshot_id = snapshot_id or fingerprint(features.hash_rows().to_list())
        self.context_id = fingerprint(
            (
                context or {},
                self.profile,
                sorted(fields),
                "quality-v3",
                self.clip_quantiles,
                asdict(fold.train),
            )
        )
        self.registry = registry or OperatorRegistry()
        self.compile_options = dict(compile_options or {})
        self.targets = build_targets(
            features, profile["price_field"], profile["target_horizon_bars"]
        )
        target_panel = features.select("row_id", "trading_day", "eligible").join(
            self.targets, on="row_id", validate="1:1"
        )
        self._target_clip_bounds = (
            clip_bounds(split_panel(target_panel, fold.train), "target", self.clip_quantiles)
            if self.clip_quantiles is not None
            else None
        )
        self.weights = (
            {s: product_weights(features, getattr(fold, s)) for s in ("train", "val")}
            if profile["metric"] in FUTURES_METRICS
            else {}
        )
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_bytes = max(0, cache_bytes)
        self.disk_cache = disk_cache or (
            ArrowCache((cache_dir,), self.cache_bytes) if cache_dir else None
        )
        self._cache: OrderedDict[str, tuple[EvaluationReport, FactorValues]] = OrderedDict()
        self._disk: dict[str, str] = {}
        self.retain_training = retain_training
        self._training: OrderedDict[str, pl.DataFrame] = OrderedDict()
        self._residual_targets = None
        self._residual_library = None
        # Concurrent evaluations share only these caches; computation runs outside the lock.
        self._lock = threading.RLock()

    def _fit_clipping(self, train):
        if self.clip_quantiles is None:
            return {}
        return {
            "quantiles": list(self.clip_quantiles),
            "value": clip_bounds(train, "value", self.clip_quantiles),
            "target": list(self._target_clip_bounds)
            if self._target_clip_bounds is not None
            else None,
        }

    def _remember_training(self, identity, frame):
        keys = ["row_id", "trading_day", "exchange", "instrument_id", "value"]
        if "product" in frame.columns:
            keys.append("product")
        frame = frame.select(keys).sort("row_id")
        with self._lock:
            self._training[identity] = frame
            self._training.move_to_end(identity)
            while len(self._training) > self._training_cache_factors:
                self._training.popitem(last=False)
        return frame

    def _training_values(self, identity, candidate):
        with self._lock:
            cached = self._training.get(identity)
            if cached is not None:
                self._training.move_to_end(identity)
                return cached
        compiled = compile_factor(
            candidate.expression, self.fields, self.registry, **self.compile_options
        )
        if compiled.factor_id != identity:
            raise ValueError("training statistic factor identity mismatch")
        # Rebuild only requested missing values, using the existing MarketData panel.
        features = self.features.filter(pl.col("trading_day") <= self.fold.train.end)
        values = execute(compiled, features, self.fields, self.registry, panel=self.panel)
        panel = features.join(self.targets, on="row_id", validate="1:1").join(
            values, on="row_id", validate="1:1"
        )
        train = split_panel(panel, self.fold.train)
        return self._remember_training(identity, apply_clipping(train, self._fit_clipping(train)))

    def factor_correlations(self, pairs, candidates, min_overlap):
        """Aggregate training-only Pearson statistics; no values or labels leave this service."""
        results = []
        aggregation = self.profile["metric"].replace("spearman", "pearson")
        for left, right in pairs:
            lhs = self._training_values(left, candidates[left])
            rhs = self._training_values(right, candidates[right]).select(
                "row_id", pl.col("value").alias("target")
            )
            # Cached panels are sorted once; equal row IDs need no repeated hash join.
            if lhs["row_id"].equals(rhs["row_id"], check_dtypes=True, null_equal=False):
                overlap = lhs.with_columns(rhs["target"])
            else:
                overlap = lhs.join(rhs, on="row_id", validate="1:1")
            result = metric(
                overlap,
                self.profile["metric"],
                "train",
                correlation="pearson",
                weights=self.weights.get("train"),
            )
            value = result.value if result.n_obs >= min_overlap else None
            results.append(FactorCorrelation(left, right, value, result.n_obs, aggregation))
        return tuple(results)

    def observation(self, reference: str) -> FactorValues:
        with self._lock:
            cached = self._cache.get(reference)
            stored = self._disk.get(reference)
        if cached is not None:
            values = cached[1]
            return replace(values, frame=values.frame.clone())
        if self.cache_dir and stored is not None:
            path = self.cache_dir / f"{reference}.arrow"
            if path in self.disk_cache and path.exists():
                data = self.disk_cache.read(path)
                return FactorValues(stored, self.snapshot_id, data, self.context_id)
        raise ValueError("observation reference is not available in this run")

    def _remember(self, reference, report, observation):
        with self._lock:
            if self.cache_dir:
                path = self.cache_dir / f"{reference}.arrow"
                self.disk_cache.write(path, observation.frame)
                self._disk[reference] = observation.expression_id
                self._disk = {
                    ref: identity
                    for ref, identity in self._disk.items()
                    if self.cache_dir / f"{ref}.arrow" in self.disk_cache
                }
            self._cache[reference] = (report, observation)
            while (
                sum(v.frame.estimated_size() for _, v in self._cache.values()) > self.cache_bytes
                and len(self._cache) > 1
            ):
                self._cache.popitem(last=False)

    def _validation_targets(self) -> pl.DataFrame:
        if self._residual_targets is None:
            keys = ["row_id", "trading_day", "eligible"]
            if "product" in self.features.columns:
                keys.append("product")
            panel = self.features.select(keys).join(self.targets, on="row_id", validate="1:1")
            valid = split_panel(panel, self.fold.val).sort("row_id")
            if self._target_clip_bounds is not None:
                valid = valid.with_columns(pl.col("target").clip(*self._target_clip_bounds))
            self._residual_targets = valid.drop("eligible", "target_end", strict=False)
        return self._residual_targets

    def _aligned(self, frame: pl.DataFrame, rows: pl.Series) -> np.ndarray:
        frame = frame.sort("row_id")
        if not frame["row_id"].equals(rows, check_dtypes=True, null_equal=False):
            frame = pl.DataFrame({"row_id": rows}).join(frame, on="row_id", how="left")
        return frame["value"].to_numpy().astype(np.float64)

    def _library_matrix(self, members):
        """Standardized Valid member columns in library order; None marks a flat member."""
        rows = self._validation_targets()["row_id"]
        identities = tuple(identity for identity, _ in members)
        if self._residual_library is None or self._residual_library[0] != identities:
            columns = [_standardize(self._aligned(frame, rows)) for _, frame in members]
            used = [column for column, _ in filter(None, columns)]
            matrix = np.column_stack(used) if used else np.zeros((len(rows), 0))
            present = [column is not None for column in columns]
            self._residual_library = (identities, matrix, matrix.T @ matrix, present)
        return self._residual_library[1:]

    def library_correlations(self, members) -> list[list[float | None]]:
        """Pairwise Valid correlations of library members, in library order (map landmarks)."""
        matrix, gram, present = self._library_matrix(members)
        correlations = gram / max(1, matrix.shape[0])
        positions = iter(range(matrix.shape[1]))
        index = [next(positions) if flag else None for flag in present]
        return [
            [None if i is None or j is None else round(float(correlations[i, j]), 4) for j in index]
            for i in index
        ]

    def residual_ic(self, values: pl.DataFrame, members, direction: int):
        """Residual Valid IC scaled by sqrt(1 - library_r2); library_residual residual_ic_v1.

        values and members are committed Valid observations (row_id, value). The projection is
        unsupervised, so fitting it on Valid factor values uses no target information.
        Also returns the candidate's correlation with each member in library order.
        """
        targets = self._validation_targets()
        prepared = _standardize(self._aligned(values, targets["row_id"]))
        if prepared is None or direction not in {-1, 1}:
            return None, None, None
        candidate, finite = prepared
        matrix, gram, present = self._library_matrix(members)
        correlations = iter((matrix.T @ candidate / max(1, matrix.shape[0])).tolist())
        similarity = [round(next(correlations), 4) if flag else None for flag in present]
        residual, explained = library_projection(candidate, matrix, gram)
        frame = targets.with_columns(
            value=pl.Series(np.where(finite, residual * direction, np.nan)).fill_nan(None)
        )
        result = metric(frame, self.profile["metric"], "val", weights=self.weights.get("val")).value
        if result is None:
            return None, explained, similarity
        # Correlation ignores scale, so a shrunken copy of a member would keep its full IC.
        # Weighting by the unexplained share falls continuously to zero for duplicates.
        return result * math.sqrt(max(0.0, 1.0 - explained)), explained, similarity

    def evaluate(self, candidate: Candidate) -> EvaluationReport:
        return self.measure(candidate)[0]

    def measure(self, candidate: Candidate) -> tuple[EvaluationReport, FactorValues | None]:
        """The report with its Valid observation, held by the caller rather than by the cache.

        Concurrent evaluations admit later; by then the bounded caches may have evicted it.
        """
        started = time.perf_counter()
        try:
            compiled = compile_factor(
                candidate.expression, self.fields, self.registry, **self.compile_options
            )
        except (ValueError, TypeError) as exc:
            return EvaluationReport(
                None, (), None, None, time.perf_counter() - started, "compile_error", str(exc)
            ), None
        observation_id = fingerprint((compiled.factor_id, self.snapshot_id, self.context_id))
        evaluation_id = fingerprint(
            (observation_id, asdict(self.fold), self.profile, "ic-v5", self.diagnostics)
        )
        with self._lock:
            cached = self._cache.get(evaluation_id)
            if cached is not None:
                self._cache.move_to_end(evaluation_id)
        if cached is not None:
            report, observation = cached
            elapsed = time.perf_counter() - started
            return replace(report, cache_hit=True, elapsed_seconds=elapsed), observation
        try:
            values = execute(compiled, self.features, self.fields, self.registry, panel=self.panel)
            keys = ["row_id", "trading_day", "exchange", "instrument_id", "eligible"]
            if self.diagnostics:
                segment = "product_segment_id" if self.panel == "product" else "segment_id"
                keys += [k for k in ("timestamp", segment) if k in self.features.columns]
            if "product" in self.features.columns:
                keys.append("product")
            panel = self.features.select(keys).join(self.targets, on="row_id", validate="1:1")
            if (
                values.height != self.features.height
                or values["row_id"].n_unique() != values.height
            ):
                raise ValueError("observation row alignment failure")
            if not values["row_id"].sort().equals(self.features["row_id"].sort()):
                raise ValueError("observation row identity mismatch")
            panel = panel.join(values, on="row_id", validate="1:1")
            train, valid = split_panel(panel, self.fold.train), split_panel(panel, self.fold.val)
            coverage = (
                float(valid["value"].is_finite().fill_null(False).mean()) if valid.height else None
            )
            clipping = self._fit_clipping(train)
            train, valid = apply_clipping(train, clipping), apply_clipping(valid, clipping)
            if self.retain_training:
                self._remember_training(compiled.factor_id, train)
            train_metrics = metrics(
                train, self.profile["metric"], "train", weights=self.weights.get("train")
            )
            train_ic = train_metrics[0]
            direction = None if train_ic.value is None else -1 if train_ic.value < 0 else 1
            raw_metrics = metrics(
                valid, self.profile["metric"], "val_raw", weights=self.weights.get("val")
            )
            directed_metrics = tuple(
                replace(
                    m,
                    split="val",
                    value=None if direction is None or m.value is None else direction * m.value,
                )
                for m in raw_metrics
            )
            directed = directed_metrics[0]
            status = "success" if direction is not None else "undefined_train_metric"
            if direction is not None and directed.value is None:
                status = "undefined_val_metric"
            scored = pl.concat([train.select("value"), valid.select("value")]).filter(
                pl.col("value").is_finite().fill_null(False)
            )
            if scored.height and scored["value"].n_unique() == 1:
                status = "constant_factor"
            report = EvaluationReport(
                compiled.factor_id,
                tuple(
                    m
                    for group in zip(train_metrics, directed_metrics, raw_metrics, strict=True)
                    for m in group
                ),
                direction,
                coverage,
                time.perf_counter() - started,
                status,
                None if status == "success" else status,
                evaluation_id,
                False,
                evaluation_id,
                compiled.expression,
                search_diagnostics(
                    panel,
                    self.fold.train,
                    self.profile["metric"],
                    direction,
                    self.weights.get("train"),
                    factor_panel=self.panel,
                    clipping=clipping,
                    version=self.diagnostics,
                )
                if self.diagnostics
                else {},
                clipping,
            )
            observation = FactorValues(
                compiled.factor_id,
                self.snapshot_id,
                valid.select("row_id", "value").sort("row_id"),
                self.context_id,
            )
            self._remember(evaluation_id, report, observation)
            return report, observation
        except (
            ValueError,
            RuntimeError,
            pl.exceptions.PolarsError,
            pl.exceptions.PanicException,
        ) as exc:
            return EvaluationReport(
                compiled.factor_id,
                (),
                None,
                None,
                time.perf_counter() - started,
                "compute_error",
                str(exc),
                evaluation_id=evaluation_id,
            ), None
