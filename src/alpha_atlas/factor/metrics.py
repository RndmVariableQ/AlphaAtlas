"""Forward returns from daily security returns on the observed market calendar."""

from collections.abc import Sequence

import polars as pl


def compute_forward_returns(
    return_data: pl.DataFrame | pl.LazyFrame,
    horizons: Sequence[int],
    return_col: str = "total_return",
    date_col: str = "date",
    asset_col: str | Sequence[str] = "key",
) -> pl.DataFrame:
    """Compound the next h sessions. Requires one row per security and observed date."""
    if any(type(h) is not int or h < 1 for h in horizons):
        raise ValueError("horizons must be positive integers")

    assets = [asset_col] if isinstance(asset_col, str) else list(asset_col)
    source = return_data.lazy()
    calendar = (
        source.select(date_col)
        .unique()
        .sort(date_col)
        .with_row_index("_t")
        .with_columns(pl.col("_t").cast(pl.Int64))
    )
    index = pl.col("_t")
    daily = pl.col(return_col).cast(pl.Float64)
    log_return = pl.when(daily.is_finite() & (daily >= -1)).then(daily.log1p())
    return (
        source.join(calendar, on=date_col, how="inner")
        .sort([*assets, date_col])
        .with_columns(
            pl.when((index.shift(-h) - index).over(assets) == h)
            .then(log_return.rolling_sum(h, min_samples=h).shift(-h).over(assets).exp() - 1)
            .alias(f"forward_return_{h}")
            for h in horizons
        )
        .drop("_t")
        .collect()
    )
