from typing import Literal

import numpy as np
import polars as pl
from scipy.special import stdtrit

MIN_DOF = 5
T_SCORE_CAP = 2.0
CS_RANK_SCALE = 1.0
QUANTILE_PRECISION = 12


def winsorize(
    x: pl.Expr,
    partition_by: list[str],
    lower: float,
    upper: float,
) -> pl.Expr:
    if not 0 <= lower < upper <= 1:
        raise ValueError("clip_quantiles must satisfy 0 <= lower < upper <= 1")

    lower_bound = x.quantile(lower, interpolation="linear").over(partition_by)
    upper_bound = x.quantile(upper, interpolation="linear").over(partition_by)

    return x.clip(lower_bound, upper_bound)


def standardize(
    x: pl.Expr,
    partition_by: list[str],
    method: Literal["demean", "demedian", "z_score", "iqr", "mad"] = "z_score",
) -> pl.Expr:
    """
    demean:   x - mean
    demedian: x - median
    z_score:  (x - mean) / std
    iqr:      (x - median) / (IQR / 1.349)
    mad:      (x - median) / (MAD / 0.6745)
    """
    if method not in {"demean", "demedian", "z_score", "iqr", "mad"}:
        raise ValueError(f"Unknown standardization method: {method!r}")

    center = x.mean() if method in {"demean", "z_score"} else x.median()
    centered = x - center.over(partition_by)

    if method in {"demean", "demedian"}:
        return centered

    has_variance = x.drop_nulls().n_unique().over(partition_by) > 1

    scale = {
        "z_score": x.std(ddof=1),
        "iqr": (
            x.quantile(quantile=0.75, interpolation="linear")
            - x.quantile(quantile=0.25, interpolation="linear")
        )
        / 1.349,
        "mad": (x - x.median()).abs().median() / 0.6745,
    }[method].over(partition_by)

    return pl.when(has_variance & (scale > 0)).then(centered / scale)


def percentile_rank(x: pl.Expr, partition_by: list[str]) -> pl.Expr:
    """
    p = rank(x) / (n + 1) => (0, 1)
    """
    return x.rank(method="average").over(partition_by) / (x.count().over(partition_by) + 1)


def t_score(x: pl.Expr, partition_by: list[str]) -> pl.Expr:
    r"""
    t = F^{-1}_{\nu}(p)
    F_{\nu}: CDF of Student's t distribution with \nu = max(n, MIN_DOF).
    """
    dof = x.count().over(partition_by).clip(lower_bound=MIN_DOF)
    pct = percentile_rank(x, partition_by)

    return pl.map_batches(
        [dof, pct], lambda series: stdtrit(*series), return_dtype=pl.Float64
    ).clip(-T_SCORE_CAP, T_SCORE_CAP)


def cs_rank(x: pl.Expr, partition_by: list[str]) -> pl.Expr:
    """
    r = 2 * scale * ((rank(x) - 1) / (n - 1) - 0.5) => [-scale, scale]
    """
    rank = x.rank(method="average").over(partition_by)
    count = x.count().over(partition_by)

    return pl.when(count > 1).then(2 * CS_RANK_SCALE * ((rank - 1) / (count - 1) - 0.5))


def weighted_mean(
    x: pl.Expr,
    weight: pl.Expr,
    partition_by: list[str] | None = None,
) -> pl.Expr:
    w = pl.when(x.is_not_null()).then(weight)
    mean = pl.when(w.sum() > 0).then((w * x).sum() / w.sum())
    return mean.over(partition_by) if partition_by else mean


def weighted_demean(
    x: pl.Expr,
    weight: pl.Expr,
    partition_by: list[str] | None = None,
) -> pl.Expr:
    return x - weighted_mean(x, weight, partition_by)


def solve_betas(
    moments: pl.DataFrame,
    risk_factors: list[str],
    date_col: str,
) -> pl.DataFrame:
    r"""
    Solve beta_t = (X_t' W_t X_t)^{+} X_t' W_t y_t by date
    where X and y have already been weighted-demeaned within partitions

    XtWX: (n_dates, n_factors, n_factors) from columns _xx_{i}*{j}
    XtWy: (n_dates, n_factors, 1)         from columns _xy_{i}*y
    beta: (n_dates, n_factors)            as columns _b_{i}
    """
    n_factors = len(risk_factors)

    xtwx = (
        moments.select(f"_xx_{i}*{j}" for i in risk_factors for j in risk_factors)
        .to_numpy()
        .reshape(-1, n_factors, n_factors)
    )

    xtwy = moments.select(f"_xy_{i}*y" for i in risk_factors).to_numpy().reshape(-1, n_factors, 1)

    betas = (np.linalg.pinv(xtwx) @ xtwy).reshape(-1, n_factors)

    return moments.select(date_col).hstack(
        pl.DataFrame(
            betas,
            schema=[f"_b_{i}" for i in risk_factors],
            orient="row",
        )
    )


def weighted_corr(x: pl.Expr, y: pl.Expr, weight: pl.Expr) -> pl.Expr:
    x = x.cast(pl.Float64)
    y = y.cast(pl.Float64)
    w = weight.cast(pl.Float64)

    valid = x.is_finite() & y.is_finite() & w.is_finite() & (w > 0)
    x, y, w = x.filter(valid), y.filter(valid), w.filter(valid)

    dx = weighted_demean(x, w)
    dy = weighted_demean(y, w)

    s_xy = (w * dx * dy).sum()
    s_xx = (w * dx * dx).sum()
    s_yy = (w * dy * dy).sum()

    return pl.when((x.n_unique() > 1) & (y.n_unique() > 1)).then(s_xy / (s_xx * s_yy).sqrt())


def pearson_ic(
    x: pl.Expr,
    y: pl.Expr,
    weight: pl.Expr | None = None,
) -> pl.Expr:
    if weight is not None:
        return weighted_corr(x, y, weight)

    x, y = x.cast(pl.Float64), y.cast(pl.Float64)
    valid = x.is_finite() & y.is_finite()
    x, y = x.filter(valid), y.filter(valid)

    return pl.when((x.n_unique() > 1) & (y.n_unique() > 1)).then(pl.corr(x, y))


def spearman_ic(
    x: pl.Expr,
    y: pl.Expr,
    weight: pl.Expr | None = None,
) -> pl.Expr:
    x, y = x.cast(pl.Float64), y.cast(pl.Float64)
    valid = x.is_finite() & y.is_finite()

    if weight is None:
        x, y = x.filter(valid), y.filter(valid)
        return pl.when((x.n_unique() > 1) & (y.n_unique() > 1)).then(
            pl.corr(x, y, method="spearman")
        )

    w = weight.cast(pl.Float64)
    valid &= w.is_finite() & (w > 0)

    return weighted_corr(
        x.filter(valid).rank(method="average"),
        y.filter(valid).rank(method="average"),
        w.filter(valid),
    )


def newey_west_tstat(x: pl.Expr, lags: int) -> pl.Expr:
    r"""
    Newey-West t-statistic for H0: mean(x) = 0.

    t = mean(x) / sqrt(S / T)

    S = \gamma_0 + 2 * \sum_{l=1}^{L} (1 - l / (L + 1)) \gamma_l
    \gamma_l = \sum_{t=l+1}^T e_t e_{t-k} / T
    e_t = x_t - mean(x)
    """
    if lags < 0:
        raise ValueError("lags must be nonnegative")

    x = pl.when(x.is_finite()).then(x)
    n = x.count().cast(pl.Float64)
    mean = x.mean()
    e = x - mean

    s = (e * e).sum()

    for lag in range(1, lags + 1):
        s += 2 * (1 - lag / (lags + 1)) * (e * e.shift(lag)).sum()

    return pl.when(s > 0).then(mean / (s / n**2).sqrt())
