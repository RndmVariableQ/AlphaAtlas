import polars as pl
from math import isclose
from typing import Literal
from itertools import accumulate
from collections.abc import Sequence
from stats import (
    winsorize, standardize, percentile_rank, t_score,
    cs_rank, weighted_demean, solve_betas, QUANTILE_PRECISION
)


def quantize_factor(
        x: pl.Expr, partition_by: list[str],
        bucket_sizes: Sequence[float] | None = None,
) -> pl.Expr:
    """
    (0, edge_1]          -> 1
    (edge_i, edge_{i+1}] -> i + 1
    (edge_{K-1}, 1]      -> K

    where K = len(bucket_sizes) and edge_i = sum(bucket_sizes[:i])
    """
    if bucket_sizes is None:
        bucket_sizes = (0.2,) * 5

    if (
            any(size <= 0 for size in bucket_sizes)
            or not isclose(sum(bucket_sizes), b=1.0, rel_tol=0.0, abs_tol=1e-9)
    ):
        raise ValueError('bucket_sizes must be positive and sum to 1')

    pct = percentile_rank(x, partition_by).round(QUANTILE_PRECISION)
    edges = (
        round(edge, QUANTILE_PRECISION)
        for edge in accumulate(bucket_sizes[:-1], initial=0.0)
    )

    return sum(
        ((pct > edge).cast(pl.Int8) for edge in edges),
        start=pl.lit(0, dtype=pl.Int8),
    )


def transform_factor(
        factor_data: pl.DataFrame | pl.LazyFrame,
        factor_col: str,
        output_col: str,
        quantile_col: str | None = None,
        date_col: str = 'date',
        asset_col: str = 'key',
        groupby: list[str] | None = None,
        bucket_sizes: Sequence[float] | None = None,
        higher_is_better: bool = True,
        method: Literal[
            'demean', 'demedian', 'std', 'iqr', 'mad', 'rank', 't'
        ] = 'std',
        winsor_limits: tuple[float, float] | None = None,
) -> pl.DataFrame:
    """
    demean:   optional winsorization -> subtract the group mean
    demedian: optional winsorization -> subtract the group median
    std:      optional winsorization -> mean/std standardization
    iqr:      optional winsorization -> median/IQR standardization
    mad:      optional winsorization -> median/MAD standardization
    rank:     cross-sectional rank   -> [-scale, scale]
    t:        percentile rank        -> inverse Student-t CDF -> cap
    """
    groupby = groupby or []
    partition_by = [date_col, *groupby]

    signed = (
            pl.col(factor_col).cast(pl.Float64)
            * (1.0 if higher_is_better else -1.0)
    )

    valid = (
            signed.is_finite()
            & pl.all_horizontal(pl.col(partition_by).is_not_null())
    )

    x = pl.when(valid).then(signed)

    if method in {'demean', 'demedian', 'std', 'iqr', 'mad'}:
        if winsor_limits is not None:
            lower, upper = winsor_limits
            x_winsorized = winsorize(x, partition_by, lower, upper)

        else:
            x_winsorized = x

        factor = standardize(x_winsorized, partition_by, method)

    elif winsor_limits is not None:
        raise ValueError(
            f'winsor_limits is not applicable when method={method!r}'
        )

    elif method == 'rank':
        factor = cs_rank(x, partition_by)

    elif method == 't':
        factor = t_score(x, partition_by)

    else:
        raise ValueError(f'Unknown transformation method: {method!r}')

    columns = [date_col, asset_col, *groupby, factor.alias(output_col)]

    if quantile_col is not None:
        columns.append(
            pl.when(factor.is_not_null())
            .then(quantize_factor(x, partition_by, bucket_sizes))
            .alias(quantile_col)
        )

    return factor_data.lazy().select(columns).collect()


def neutralize_factor(
        factor_data: pl.DataFrame | pl.LazyFrame,
        factor_col: str,
        output_col: str,
        date_col: str = 'date',
        asset_col: str = 'key',
        groupby: list[str] | None = None,
        risk_factors: Sequence[str] = (),
        weight_col: str | None = None,
        winsor_limits: tuple[float, float] | None = None,
) -> pl.DataFrame:
    r"""
    y = D \alpha + X \beta + \epsilon

    1. Winsorize y within date-group partitions, if requested.
    2. Weighted-demean y and X within date-group partitions.
    3. Estimate beta by date using WLS without an intercept.
    4. Return \epsilon = \tilde{y} - \tilde{X} \hat{\beta}.

    The residual factor is null wherever the factor, risk exposure,
    partition label, or weight is invalid.
    """
    groupby = groupby or []
    partition_by = [date_col, *groupby]
    risk_factors = list(risk_factors)

    y = pl.col(factor_col).cast(pl.Float64)

    weight = (
        pl.col(weight_col).cast(pl.Float64)
        if weight_col is not None
        else pl.lit(value=1.0, dtype=pl.Float64)
    )

    valid = (
            pl.all_horizontal(
                pl.col([
                    factor_col, *risk_factors
                ]).cast(pl.Float64).is_finite()
            )
            & pl.all_horizontal(pl.col(partition_by).is_not_null())
            & weight.is_finite() & (weight > 0)
    )

    lf = factor_data.lazy().select([
        date_col, asset_col, *groupby,
        *(
            pl.when(valid)
            .then(pl.col(factor).cast(pl.Float64))
            .alias(factor)
            for factor in risk_factors
        ),
        pl.when(valid).then(y).alias('_y'),
        pl.when(valid).then(weight).alias('_w'),
    ])

    if winsor_limits is not None:
        lower, upper = winsor_limits
        lf = lf.with_columns(
            winsorize(pl.col('_y'), partition_by, lower, upper).alias('_y')
        )

    lf = lf.with_columns(
        weighted_demean(pl.col(col), pl.col('_w'), partition_by).alias(col)
        for col in ['_y', *risk_factors]
    )

    if not risk_factors:
        return (
            lf.select([
                date_col, asset_col, *groupby, pl.col('_y').alias(output_col),
            ])
            .collect()
        )

    df = lf.collect()

    moments = df.group_by(date_col).agg(
        *(
            (pl.col('_w') * pl.col(i) * pl.col(j))
            .sum().alias(f'_xx_{i}*{j}')
            for i in risk_factors
            for j in risk_factors
        ),
        *(
            (pl.col('_w') * pl.col(i) * pl.col('_y'))
            .sum().alias(f'_xy_{i}*y') for i in risk_factors
        ),
    )

    betas = solve_betas(moments, risk_factors, date_col)

    fitted = pl.sum_horizontal(
        pl.col(f'_b_{i}') * pl.col(i) for i in risk_factors
    )

    return (
        df.join(betas, on=date_col, how='left')
        .select([
            date_col, asset_col, *groupby,
            (pl.col('_y') - fitted).alias(output_col),
        ])
    )
