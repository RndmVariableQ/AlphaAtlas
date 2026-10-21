"""WRDS R1000 -> Alpha Atlas Parquet asset adapter (ENGINEERING SMOKE TEST ONLY).

No API calls are made on import. The caller passes an authenticated WRDS Connection
into acquire(). The resulting on-disk schema is read by the existing ParqMarketData.

This is NOT a research-quality point-in-time/total-return feed. See README.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id


HOLDINGS_SQL = """
WITH snapshot_dates AS (
    SELECT DISTINCT date::date AS snapshot_day
    FROM ftse_russell_us.idx_holdings_us
    WHERE date BETWEEN %(start)s::date AND %(end)s::date
    UNION
    SELECT MAX(date)::date
    FROM ftse_russell_us.idx_holdings_us
    WHERE date < %(start)s::date
), holdings AS (
    SELECT h.date::date AS snapshot_day,
           LEFT(UPPER(TRIM(h.cusip)), 8) AS cusip8,
           h.ticker, h.r1000_wt
    FROM ftse_russell_us.idx_holdings_us h
    JOIN snapshot_dates d ON d.snapshot_day = h.date::date
    WHERE h.r1000_wt > 0 AND h.cusip IS NOT NULL
)
SELECT h.snapshot_day, h.cusip8, h.ticker, h.r1000_wt,
       s.permno
FROM holdings h
LEFT JOIN crsp.stksecurityinfohist s
  ON LEFT(UPPER(TRIM(s.cusip)), 8) = h.cusip8
 AND h.snapshot_day BETWEEN s.secinfostartdt AND s.secinfoenddt
ORDER BY h.snapshot_day, h.cusip8, s.permno
"""


BARS_SQL = """
SELECT permno,
       dlycaldt::date AS trading_day,
       dlyopen::double precision AS open,
       dlyhigh::double precision AS high,
       dlylow::double precision AS low,
       dlyclose::double precision AS close,
       dlyvol::double precision AS volume,
       dlyprcvol::double precision AS amount
FROM crsp.dsf_v2
WHERE permno IN ({ids})
  AND dlycaldt BETWEEN %(start)s::date AND %(end)s::date
ORDER BY permno, dlycaldt
"""


def month_intervals(start: date, end: date):
    current = date(start.year, start.month, 1)
    while current <= end:
        if current.month == 12:
            following = date(current.year + 1, 1, 1)
        else:
            following = date(current.year, current.month + 1, 1)
        lower = max(start, current)
        upper = min(end, following - timedelta(days=1))
        yield f"{current.year}-{current.month:02d}", lower, upper
        current = following


def fetch_membership(db, start: date, end: date) -> tuple[pl.DataFrame, dict]:
    raw = pl.from_pandas(
        db.raw_sql(HOLDINGS_SQL, params={"start": start.isoformat(), "end": end.isoformat()})
    )
    if raw.is_empty():
        raise ValueError("No Russell 1000 holdings; check WRDS permissions and date range")
    raw = raw.with_columns(
        pl.col("snapshot_day").cast(pl.Date),
        pl.col("permno").cast(pl.Int64, strict=False),
        pl.col("cusip8").cast(pl.String),
    )
    unmatched = raw.filter(pl.col("permno").is_null())
    matched = raw.filter(pl.col("permno").is_not_null())
    # Zero-tolerance for ambiguous mappings; never pick arbitrary PERMNOs.
    conflicts = (
        matched.group_by("snapshot_day", "cusip8")
        .agg(pl.col("permno").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    if not conflicts.is_empty():
        raise ValueError(f"Ambiguous historical CUSIP matches: {conflicts.head(10)}")
    matched = matched.unique(subset=["snapshot_day", "cusip8", "permno"])
    duplicate_security = matched.select("snapshot_day", "permno").is_duplicated().any()
    if duplicate_security:
        raise ValueError("Multiple holdings share a snapshot/PERMNO; inspect security mapping")
    if matched.is_empty():
        raise ValueError("No Russell members linked to CRSP PERMNO")
    report = {
        "holding_rows": raw.height,
        "matched_rows": matched.height,
        "unmatched_rows": unmatched.height,
        "unmatched_sample": unmatched.select("snapshot_day", "cusip8", "ticker").head(20).to_dicts(),
    }
    return matched, report


def fetch_bars_month(db, permnos: list[int], start: date, end: date) -> pl.DataFrame:
    frames = []
    for i in range(0, len(permnos), 100):
        ids = ",".join(str(int(x)) for x in permnos[i : i + 100])
        # SQL identifiers are fixed by code; PERMNO values are coerced to integers.
        query = BARS_SQL.format(ids=ids)
        part = pl.from_pandas(
            db.raw_sql(query, params={"start": start.isoformat(), "end": end.isoformat()})
        )
        if not part.is_empty():
            frames.append(part)
    if not frames:
        raise ValueError(f"No CRSP bars for {start} through {end}")
    frame = pl.concat(frames, how="vertical_relaxed")
    return frame.with_columns(
        pl.col("permno").cast(pl.Int64),
        pl.col("trading_day").cast(pl.Date),
        *(pl.col(c).cast(pl.Float64, strict=False) for c in (
            "open", "high", "low", "close", "volume", "amount"
        )),
    )


def normalize_month(bars: pl.DataFrame, members: pl.DataFrame) -> pl.DataFrame:
    # Never select membership from the future. A snapshot dated D first affects
    # selection on D+1 (calendar day), a conservative engineering assumption.
    snapshot_dates = (
        members.select("snapshot_day").unique()
        .with_columns(pl.col("snapshot_day").dt.offset_by("1d").alias("effective_day"))
        .sort("effective_day")
    )
    day_lookup = (
        bars.select("trading_day").unique().sort("trading_day")
        .join_asof(
            snapshot_dates,
            left_on="trading_day", right_on="effective_day", strategy="backward"
        )
        .select("trading_day", "snapshot_day")
    )
    membership = (
        members.select("snapshot_day", "permno").unique()
        .with_columns(pl.lit(True).alias("in_r1000"))
    )
    bars = (
        bars.join(day_lookup, on="trading_day", how="left", validate="m:1")
        .join(membership, on=["snapshot_day", "permno"], how="left", validate="m:1")
        .with_columns(
            pl.col("in_r1000").fill_null(False),
            pl.col("permno").cast(pl.String).alias("instrument_id"),
            pl.lit("US").alias("exchange"),
            (
                pl.col("trading_day").cast(pl.Datetime("us")) + pl.duration(hours=16)
            ).dt.replace_time_zone("America/New_York").alias("timestamp"),
        )
        .drop("snapshot_day")
    )
    if bars.select("trading_day", "permno").is_duplicated().any():
        raise ValueError("Duplicate PERMNO/trading_day CRSP rows")
    # DO NOT call raw close a dividend-adjusted price, or dlyprcvol exact turnover.
    return with_row_id(bars).sort("timestamp", "exchange", "instrument_id")


def acquire(db, destination: Path, start: date, end: date, max_permnos: int) -> dict:
    if start > end:
        raise ValueError("start must not exceed end")
    if max_permnos < 0:
        raise ValueError("max_permnos must be nonnegative")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Destination must be empty: {destination}")
    members, links = fetch_membership(db, start, end)
    all_ids = sorted(set(members["permno"].to_list()))
    chosen = all_ids[:max_permnos] if max_permnos else all_ids
    subset = len(chosen) != len(all_ids)
    members = members.filter(pl.col("permno").is_in(chosen))
    if not members.filter(pl.col("snapshot_day") < start).height:
        print("WARNING: no membership anchor before start; early bars will be ineligible")

    membership_receipt = atomic_parq(members, destination / "metadata" / "r1000_snapshots.parq")
    atomic_json(destination / "metadata" / "cusip_link_audit.json", links)
    manifest = {
        "schema_version": 1,
        "asset_id": "us_equities",
        "provider": "wrds-crsp-ciz-ftse-russell",
        "purpose": "ENGINEERING_SMOKE_TEST_ONLY",
        "frequency": "1d",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "status": "downloading",
        "rows": 0,
        "partitions": [],
        "failures": [],
        "universes": ["all", "r1000"],
        "universe_all_semantics": "all exported PERMNOs, not all US common stocks",
        "membership_snapshot_lag": "one calendar day (illustrative)",
        "membership_sha256": membership_receipt["sha256"],
        "missing_membership_links": links["unmatched_rows"],
        "permno_count": len(chosen),
        "subset_selected_for_smoke": subset,
        "price_adjustment": "none: raw OHLC; NOT total-return-adjusted",
        "amount_semantics": "CRSP DlyPrcVol proxy; NOT exact transaction turnover",
        "target_warning": "raw close-based targets ignore dividends/delistings/splits",
        "crsp_returns_stored_as_features": False,
        "calendar_warning": "observed CRSP bars only; no independent US exchange calendar audit",
        "coverage_warning": "membership-day coverage not reconciled; do not treat audit pass as research ready",
        "source_version": "CIZ/CRSP v2; inspect tables against your WRDS subscription",
    }
    atomic_json(destination / "manifest.json", manifest)
    days = []
    try:
        for month, lower, upper in month_intervals(start, end):
            raw = fetch_bars_month(db, chosen, lower, upper)
            normalized = normalize_month(raw, members)
            path = destination / "bars" / f"year={month[:4]}" / f"{month}.parq"
            receipt = atomic_parq(normalized, path)
            receipt.update({
                "month": month,
                "start": str(normalized["trading_day"].min()),
                "end": str(normalized["trading_day"].max()),
                "status": "downloaded",
            })
            manifest["partitions"].append(receipt)
            manifest["rows"] += normalized.height
            days.append(normalized.select("trading_day"))
            atomic_json(destination / "manifest.json", manifest)
            print(f"{month}: {normalized.height:,} rows", flush=True)
        calendar = pl.concat(days).unique().sort("trading_day")
        atomic_parq(calendar, destination / "metadata" / "calendar.parq")
        manifest["actual_start"] = str(calendar["trading_day"].min())
        manifest["actual_end"] = str(calendar["trading_day"].max())
        manifest["snapshot_id"] = hashlib.sha256(json.dumps(
            ["wrds-r1000-smoke-v1", str(start), str(end), membership_receipt["sha256"],
             [(p["month"], p["sha256"]) for p in manifest["partitions"]]],
            sort_keys=True,
        ).encode()).hexdigest()
        manifest["status"] = "complete"  # completed export, not validated financial data
        atomic_json(destination / "manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "incomplete"
        manifest["failures"].append({"error": str(exc)})
        atomic_json(destination / "manifest.json", manifest)
        raise


