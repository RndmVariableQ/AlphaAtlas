"""Calendar reductions on observed daily feature panels, before monthly publication.

These reductions operate on a dated panel, not a fixed number of intraday bars. They are
data preparation operators; the ordinary series DSL runs on their daily/monthly outputs.
"""

from __future__ import annotations

import polars as pl

CALENDAR_VERSION = "observed_calendar_months_v1"
KEY = ["exchange", "product"]


def calendar_months(frame: pl.DataFrame, rules: list[dict]) -> pl.DataFrame:
    """Reduce observed days and insert null rows for absent calendar months.

    A last-value reduction never skips a missing last value. Other reductions require
    the declared count and finite-value coverage; covered reductions reweight only the
    observed values. Publication is separate and must occur strictly in the next month.
    """
    if not frame.height or frame.select(*KEY, "trading_day").is_duplicated().any():
        raise ValueError("calendar reductions require nonempty unique daily observations")
    if len({r["name"] for r in rules}) != len(rules):
        raise ValueError("duplicate calendar output name")
    expressions = []
    for rule in rules:
        column, op = pl.col(rule["source"]), rule["op"]
        if op == "last":
            value = column.last()
        else:
            coverage, minimum = rule.get("coverage", 1.0), rule.get("min_count", 1)
            if not 0 < coverage <= 1 or type(minimum) is not int or minimum < 1:
                raise ValueError("invalid calendar coverage/count")
            values = column.filter(column.is_finite())
            reductions = {
                "sum": values.sum(),
                "sum_squares": values.pow(2).sum(),
                "count": values.len().cast(pl.Float64),
                "mean": values.mean(),
                "max": values.max(),
                "compound": (values + 1).product() - 1,
            }
            if op not in reductions:
                raise ValueError(f"unknown calendar reduction: {op}")
            valid = (values.len() >= minimum) & (values.len() >= (pl.len() * coverage).ceil())
            value = pl.when(valid).then(reductions[op])
        expressions.append(value.alias(rule["name"]))
    source = frame.sort(KEY + ["trading_day"]).with_columns(
        pl.col("trading_day").dt.truncate("1mo").alias("month")
    )
    grid = (
        source.group_by(KEY)
        .agg(pl.date_ranges(pl.col("month").min(), pl.col("month").max(), interval="1mo"))
        .explode("month", empty_as_null=True)
    )
    return grid.join(
        source.group_by(KEY + ["month"]).agg(expressions),
        on=KEY + ["month"],
        how="left",
        validate="1:1",
    ).sort(KEY + ["month"])
