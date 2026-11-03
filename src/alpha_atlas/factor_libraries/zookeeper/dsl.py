"""Version 2: executable daily/monthly DSL with explicit calendar reductions.

Version 1 remains available for historical frozen runs and independent parity checks.
No supplier reads or target labels are used here.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.operators.calendar import CALENDAR_VERSION, KEY, calendar_months

VERSION = "zookeeper_monthly_dsl_v2"


def recipe():
    folder = Path(__file__).parent
    result = json.loads((folder / "pipeline.json").read_text())
    result["factors"] = [
        json.loads(s) for s in (folder / "factors_v2.jsonl").read_text().splitlines()
    ]
    return result


def _panel(frame, time, calendar=None, leg=None):
    work = frame.sort(KEY + [time]).with_columns(
        pl.col(time).cast(pl.Datetime("us")).alias("timestamp"),
        pl.col("product").alias("instrument_id"),
        pl.lit(True).alias("eligible"),
    )
    if calendar is None:
        work = work.with_columns(pl.lit(0).alias("product_segment_id"))
    else:
        work = work.join(calendar, on="trading_day", validate="m:1")
        changed = pl.col("calendar_index").diff().over(KEY) != 1
        if leg:
            changed = changed | (pl.col(leg) != pl.col(leg).shift(1).over(KEY)).fill_null(True)
        work = work.with_columns(
            changed.fill_null(True).cast(pl.Int64).cum_sum().over(KEY).alias("product_segment_id")
        ).drop("calendar_index")
    return work.with_row_index("row_id")


def _expressions(frame, expressions, registry):
    for item in expressions:
        compiled = compile_factor(item["expression"], set(frame.columns), registry)
        values = execute(compiled, frame, set(frame.columns), registry, panel="product")
        frame = frame.join(
            values.rename({"value": item["name"]}),
            on="row_id",
            validate="1:1",
            maintain_order="left",
        )
    return frame


def compute_monthly(base, curve, *, plan=None, registry=None):
    plan = recipe() if plan is None else plan
    registry = registry or OperatorRegistry()
    if plan["version"] != VERSION or plan["calendar_version"] != CALENDAR_VERSION:
        raise ValueError("unsupported monthly recipe version")
    for frame in (base, curve):
        if not frame.height or frame.select(*KEY, "trading_day").is_duplicated().any():
            raise ValueError("nonempty unique daily observations required")
    calendar = pl.DataFrame(
        {"trading_day": sorted(set(base["trading_day"]) | set(curve["trading_day"]))}
    ).with_row_index("calendar_index")
    daily = _expressions(_panel(base, "trading_day", calendar), plan["daily"], registry)
    raw_curve = curve
    for leg in (0, 1):
        panel = _panel(curve, "trading_day", calendar, f"leg{leg}")
        values = _expressions(panel, [plan["curve_daily"][leg]], registry)
        column = plan["curve_daily"][leg]["name"]
        raw_curve = raw_curve.join(
            values.select(*KEY, "trading_day", column), on=KEY + ["trading_day"], validate="1:1"
        )
    monthly = calendar_months(daily, plan["base_reductions"]).join(
        calendar_months(raw_curve, plan["curve_reductions"]),
        on=KEY + ["month"],
        how="left",
        validate="1:1",
    )
    monthly = _panel(monthly, "month")
    # The catalogue is a topologically ordered calculation plan. Helpers are ordinary DSL.
    rows = plan["factors"]
    monthly = _expressions(monthly, [r for r in rows if r["name"] != "ivolaBGR"], registry)
    monthly = _expressions(monthly, plan["risk_returns"], registry)
    monthly = _expressions(monthly, [r for r in rows if r["name"] == "ivolaBGR"], registry)
    return monthly.select(
        *KEY, "month", *(pl.col(r["name"]).alias(r["prepared_field"]) for r in rows)
    )
