"""Native RQ stock prices, historical index membership and PIT financial evidence."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.assets.futures import connect, months
from alpha_atlas.config import load_toml

INDEXES = {"hs300": "000300.XSHG", "zz500": "000905.XSHG", "zz1000": "000852.XSHG"}


def classify_missing_members(missing: pl.DataFrame, instruments: pl.DataFrame) -> pl.DataFrame:
    """Retain index evidence while separating dates outside the provider listing lifetime."""
    lifetime = instruments.select(
        pl.col("order_book_id").alias("instrument_id"),
        pl.col("listed_date").cast(pl.String).str.to_date(strict=False),
        pl.col("de_listed_date").cast(pl.String).str.to_date(strict=False),
    )
    return missing.join(lifetime, on="instrument_id", how="left", validate="m:1").with_columns(
        pl.when(pl.col("trading_day") < pl.col("listed_date"))
        .then(pl.lit("before_listing"))
        .when(pl.col("trading_day") >= pl.col("de_listed_date"))
        .then(pl.lit("on_or_after_delisting"))
        .otherwise(pl.lit("unresolved"))
        .alias("reason")
    )


def reconcile_coverage(destination: Path, manifest: dict) -> dict:
    """Resolve only documented non-listed member dates; other gaps block completion."""
    metadata = destination / "metadata"
    missing_parts = []
    for part in manifest["partitions"]:
        if part.get("missing_member_days", 0):
            missing_parts.append(
                pl.read_parquet(metadata / "missing" / f"{part['month']}.parq").select(
                    "trading_day", "instrument_id"
                )
            )
    manifest["missing_member_days"] = sum(p.height for p in missing_parts)
    manifest["outside_listing_member_days"] = 0
    manifest["unresolved_missing_member_days"] = 0
    if missing_parts:
        classified = classify_missing_members(
            pl.concat(missing_parts), pl.read_parquet(metadata / "instruments.parq")
        ).sort("trading_day", "instrument_id")
        atomic_parq(classified, metadata / "coverage_exceptions.parq")
        unresolved = classified.filter(pl.col("reason") == "unresolved").height
        manifest["unresolved_missing_member_days"] = unresolved
        manifest["outside_listing_member_days"] = classified.height - unresolved
    manifest["coverage_policy"] = (
        "Preserve provider membership even on/after delisting; no synthetic prices. "
        "Only missing observations inside the listing lifetime block acquisition completion."
    )
    manifest["status"] = (
        "complete"
        if not manifest["failures"] and not manifest["unresolved_missing_member_days"]
        else "incomplete"
    )
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def shift_factor_dates(frame: pl.DataFrame, calendar: pl.DataFrame) -> pl.DataFrame:
    """Make a provider's daily PIT observation available on the next exchange trading day."""
    dates = (
        calendar.sort("trading_day")
        .with_columns(pl.col("trading_day").shift(-1).alias("available_day"))
        .rename({"trading_day": "source_day"})
    )
    return frame.join(dates, on="source_day", how="left").drop_nulls("available_day")


def _wide_boolean(raw, name: str) -> pl.DataFrame:
    raw = raw.copy()
    raw.index.name = "trading_day"
    return (
        pl.from_pandas(raw.reset_index())
        .unpivot(index="trading_day", variable_name="instrument_id", value_name=name)
        .with_columns(pl.col("trading_day").cast(pl.Date))
    )


def fetch_ashare(
    destination: Path,
    start: date,
    end: date,
    *,
    env_file: Path | None,
    root: Path,
    workers: int = 2,
) -> dict:
    rq = connect(env_file)
    config = load_toml(root / "configs/fundamentals.toml")
    fields = config["daily_fields"]
    statement_fields = config["statement_fields"]
    metadata = destination / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(
        json.dumps(["ricequant-stock-v1", str(start), str(end), fields, statement_fields]).encode()
    ).hexdigest()
    request_path = metadata / "acquisition_request.json"
    if request_path.exists():
        previous = json.loads(request_path.read_text(encoding="utf-8"))
        if previous["signature"] != signature:
            raise ValueError("different request in this dataset directory; use a new directory")
    atomic_json(
        request_path,
        {
            "signature": signature,
            "start": start,
            "end": end,
            "daily_fields": fields,
            "statement_fields": statement_fields,
        },
    )
    manifest_path = destination / "manifest.json"
    manifest = {
        "schema_version": 1,
        "asset_id": "ashare",
        "provider": "ricequant",
        "frequency": "1d",
        "requested_start": str(start),
        "requested_end": str(end),
        "snapshot_id": signature,
        "status": "mapping",
        "partitions": [],
        "failures": [],
        "universes": ["union1800", "hs300", "zz500", "zz1000"],
        "daily_fundamental_fields": fields,
        "statement_fields": statement_fields,
        "fundamental_availability": "RQ PIT daily observations shifted one trading day",
        "statement_vintages": "all versions with info_date <= requested end",
        "price_adjustment": "raw plus cumulative historical ex_cum_factor at ex_date",
        "eligibility": "PIT membership, not ST, not suspended; history kept for warmup",
        "units": {"prices": "CNY", "volume": "shares", "amount": "CNY"},
    }
    atomic_json(manifest_path, manifest)
    calendar_path = metadata / "calendar.parq"
    if not calendar_path.exists():
        atomic_parq(
            pl.DataFrame(
                {
                    "trading_day": rq.get_trading_dates(
                        start - timedelta(days=60), end + timedelta(days=7)
                    )
                }
            ),
            calendar_path,
        )
    calendar = pl.read_parquet(calendar_path).with_columns(pl.col("trading_day").cast(pl.Date))
    pieces = []
    for universe, index_id in INDEXES.items():
        for year in range(start.year, end.year + 1):
            lo, hi = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
            path = metadata / "membership" / f"{universe}_{year}.parq"
            if path.exists():
                frame = pl.read_parquet(path)
            else:
                response = rq.index_components(index_id, start_date=lo, end_date=hi)
                rows = [
                    (dt.date(), instrument, universe)
                    for dt, ids in response.items()
                    for instrument in ids
                ]
                frame = pl.DataFrame(
                    rows,
                    schema=[
                        ("trading_day", pl.Date),
                        ("instrument_id", pl.String),
                        ("universe", pl.String),
                    ],
                    orient="row",
                )
                atomic_parq(frame, path)
            pieces.append(frame)
            print(f"ashare membership {universe} {year}: {frame.height:,}", flush=True)
    membership = pl.concat(pieces).unique()
    flags = (
        membership.with_columns(pl.lit(True).alias("member"))
        .pivot(on="universe", index=["trading_day", "instrument_id"], values="member")
        .rename({name: "in_" + name for name in INDEXES})
        .fill_null(False)
    )
    flags = flags.with_columns(pl.lit(True).alias("in_union1800"))
    atomic_parq(flags, metadata / "membership_daily.parq")
    ids = flags["instrument_id"].unique().sort().to_list()
    manifest["historical_instruments"] = len(ids)
    atomic_json(manifest_path, manifest)

    instruments_path = metadata / "instruments.parq"
    if not instruments_path.exists():
        inst = pl.from_pandas(rq.all_instruments(type="CS"))
        atomic_parq(inst.filter(pl.col("order_book_id").is_in(ids)), instruments_path)

    # Download all report vintages, including pre-2015 context needed for early-2015 inputs.
    for index in range(0, len(ids), 200):
        path = destination / "fundamentals/events" / f"batch_{index // 200:03d}.parq"
        if not path.exists():
            raw = rq.get_pit_financials_ex(
                ids[index : index + 200],
                fields=statement_fields,
                start_quarter=f"{start.year - 1}q1",
                end_quarter=f"{end.year}q{(end.month - 1) // 3 + 1}",
                date=end,
                statements="all",
            )
            frame = pl.from_pandas(raw.reset_index()).rename({"order_book_id": "instrument_id"})
            frame = frame.filter(pl.col("info_date").cast(pl.Date) <= end)
            atomic_parq(frame, path)
        print(f"ashare statement vintages {min(index + 200, len(ids))}/{len(ids)}", flush=True)

    ex_path = metadata / "adjustment_events.parq"
    if not ex_path.exists():
        raw = rq.get_ex_factor(ids, start_date="1990-01-01", end_date=end)
        adjustment = pl.from_pandas(raw.reset_index()).rename({"order_book_id": "instrument_id"})
        adjustment = adjustment.with_columns(pl.col("ex_date").cast(pl.Date))
        atomic_parq(adjustment, ex_path)
    adjustment = pl.read_parquet(ex_path).select("instrument_id", "ex_date", "ex_cum_factor")
    adjustment = adjustment.sort("ex_date")
    manifest["status"] = "downloading"
    atomic_json(manifest_path, manifest)

    def download(lower: date, upper: date) -> dict:
        download_started = time.perf_counter()
        month = f"{lower.year}-{lower.month:02d}"
        path = destination / "bars" / f"year={lower.year}" / f"{month}.parq"
        receipt = path.with_suffix(".json")
        if receipt.exists() and path.exists():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if saved.get("request_signature") == signature and saved["sha256"] == checksum:
                return saved
        last_error = None
        for attempt in range(3):
            try:
                raw = rq.get_price(
                    ids,
                    start_date=lower,
                    end_date=upper,
                    frequency="1d",
                    adjust_type="none",
                    skip_suspended=False,
                    expect_df=True,
                )
                prices = (
                    pl.from_pandas(raw.reset_index())
                    .rename(
                        {
                            "order_book_id": "instrument_id",
                            "date": "trading_day",
                            "total_turnover": "amount",
                        }
                    )
                    .with_columns(pl.col("trading_day").cast(pl.Date))
                )
                prior = calendar.filter(pl.col("trading_day") < lower)["trading_day"].max()
                source_path = destination / "fundamentals/daily" / f"{month}.parq"
                if source_path.exists():
                    funda = pl.read_parquet(source_path)
                else:
                    raw_funda = rq.get_factor(
                        ids, fields, start_date=prior, end_date=upper, expect_df=True
                    )
                    funda = (
                        pl.from_pandas(raw_funda.reset_index())
                        .rename({"order_book_id": "instrument_id", "date": "source_day"})
                        .with_columns(pl.col("source_day").cast(pl.Date))
                    )
                    atomic_parq(funda, source_path)
                funda = shift_factor_dates(funda, calendar).rename(
                    {
                        "available_day": "trading_day",
                        "source_day": "fundamentals_source_day",
                        **{name: "funda_" + name for name in fields},
                    }
                )
                frame = prices.join(
                    funda, on=["trading_day", "instrument_id"], how="left", validate="1:1"
                )
                monthly_flags = flags.filter(pl.col("trading_day").is_between(lower, upper))
                frame = frame.join(
                    monthly_flags, on=["trading_day", "instrument_id"], how="left", validate="1:1"
                ).with_columns(
                    pl.col(["in_" + n for n in INDEXES] + ["in_union1800"]).fill_null(False)
                )
                for name, method in [("is_st", rq.is_st_stock), ("is_suspended", rq.is_suspended)]:
                    raw_flag = method(ids, start_date=lower, end_date=upper)
                    frame = frame.join(
                        _wide_boolean(raw_flag, name),
                        on=["trading_day", "instrument_id"],
                        how="left",
                    )
                frame = (
                    frame.sort("trading_day")
                    .join_asof(
                        adjustment,
                        left_on="trading_day",
                        right_on="ex_date",
                        by="instrument_id",
                        strategy="backward",
                        check_sortedness=False,
                    )
                    .rename({"ex_cum_factor": "adjfactor"})
                    .with_columns(
                        pl.col("adjfactor").fill_null(1.0),
                        pl.col("instrument_id").str.split(".").list.last().alias("exchange"),
                        (pl.col("trading_day").cast(pl.Datetime("us")) + pl.duration(hours=15))
                        .dt.replace_time_zone("Asia/Shanghai")
                        .alias("timestamp"),
                    )
                )
                frame = frame.with_columns(
                    [
                        (pl.col(c) * pl.col("adjfactor")).alias("adj_" + c)
                        for c in ["open", "high", "low", "close"]
                    ]
                )
                frame = with_row_id(frame).sort("timestamp", "exchange", "instrument_id")
                if frame["row_id"].n_unique() != frame.height:
                    raise ValueError("duplicate stock observation keys")
                if frame.filter(pl.col("fundamentals_source_day") >= pl.col("trading_day")).height:
                    raise ValueError("fundamental availability violation")
                missing = monthly_flags.join(
                    frame.select("trading_day", "instrument_id"),
                    on=["trading_day", "instrument_id"],
                    how="anti",
                )
                if missing.height:
                    atomic_parq(missing, metadata / "missing" / f"{month}.parq")
                part = atomic_parq(frame, path)
                part.update(
                    {
                        "month": month,
                        "request_signature": signature,
                        "start": str(frame["trading_day"].min()),
                        "end": str(frame["trading_day"].max()),
                        "missing_member_days": missing.height,
                        "status": "downloaded",
                        "download_seconds": time.perf_counter() - download_started,
                    }
                )
                atomic_json(receipt, part)
                return part
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
                time.sleep(attempt + 1)
        return {"month": month, "status": "failed", "rows": 0, "error": last_error}

    tasks = list(months(start, end))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download, lo, hi) for lo, hi in tasks]
        for future in as_completed(futures):
            part = future.result()
            manifest["partitions"].append(part)
            manifest["rows"] = sum(p["rows"] for p in manifest["partitions"])
            if part["status"] == "failed":
                manifest["failures"].append(part)
            atomic_json(manifest_path, manifest)
            print(
                f"ashare RQ {len(manifest['partitions'])}/{len(tasks)} "
                f"{part['month']}: {part['rows']:,} {part['status']}",
                flush=True,
            )
    manifest["partitions"].sort(key=lambda p: p["month"])
    successful = [p for p in manifest["partitions"] if "start" in p]
    if successful:
        manifest["actual_start"] = min(p["start"] for p in successful)
        manifest["actual_end"] = max(p["end"] for p in successful)
    return reconcile_coverage(destination, manifest)
