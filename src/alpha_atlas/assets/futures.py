"""Resumable native RiceQuant 5m acquisition, with real-contract dominant mapping."""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id

BAR_FIELDS = ("open", "high", "low", "close", "volume", "amount", "open_interest")


def far_contract_mapping(mapping: pl.DataFrame, instruments: pl.DataFrame) -> pl.DataFrame:
    """Choose the first two later maturities that were listed on each historical trading day.

    The anchor mapping must already be point-in-time (the exporter uses prior-close rule 0).
    No current/future volume, OI, quotes, or subsequent listing is used to choose either leg.
    """
    inst = instruments.select(
        "instrument_id",
        "exchange",
        pl.col("underlying_symbol").alias("product"),
        *[
            pl.col(c).cast(pl.String).str.to_date(strict=False).alias(c)
            for c in ("listed_date", "de_listed_date", "maturity_date")
        ],
    )
    anchors = mapping.join(
        inst.select("instrument_id", "exchange", "maturity_date"),
        on="instrument_id",
        how="left",
        validate="m:1",
    )
    if anchors.select(
        pl.any_horizontal(pl.col("exchange", "maturity_date").is_null()).any()
    ).item():
        raise ValueError("anchor contract exchange/maturity is missing")
    if anchors.select("exchange", "instrument_id", "trading_day").is_duplicated().any():
        raise ValueError("duplicate anchor contract day")
    # Process products separately to keep the historical listing join small.
    parts = []
    for key, current in anchors.partition_by("product", as_dict=True, maintain_order=True).items():
        future = inst.filter(pl.col("product") == key[0]).rename(
            {"instrument_id": "__far_id", "maturity_date": "__far_maturity"}
        )
        pairs = (
            current.join(future, on=["exchange", "product"], how="inner")
            .filter(
                (pl.col("__far_maturity") > pl.col("maturity_date"))
                & (pl.col("listed_date") <= pl.col("trading_day"))
                & (pl.col("de_listed_date") >= pl.col("trading_day"))
            )
            .sort("trading_day", "instrument_id", "__far_maturity", "__far_id")
        )
        keys = ["exchange", "instrument_id", "trading_day"]
        pairs = pairs.with_columns(pl.col("__far_id").cum_count().over(keys).alias("__leg"))
        for leg in (1, 2):
            current = current.join(
                pairs.filter(pl.col("__leg") == leg).select(
                    *keys,
                    pl.col("__far_id").alias(f"instrument_id_p{leg}"),
                    pl.col("exchange").alias(f"exchange_p{leg}"),
                    (pl.col("__far_maturity") - pl.col("trading_day"))
                    .dt.total_days()
                    .alias(f"days_to_maturity_p{leg}"),
                ),
                on=keys,
                how="left",
                validate="1:1",
            )
        parts.append(
            current.with_columns(
                (pl.col("maturity_date") - pl.col("trading_day"))
                .dt.total_days()
                .alias("days_to_maturity")
            ).drop("maturity_date")
        )
    if not parts:
        raise ValueError("no anchor contract days")
    return pl.concat(parts).sort("exchange", "instrument_id", "trading_day")


def attach_far_bars(
    anchors: pl.DataFrame, bars: pl.DataFrame, mapping: pl.DataFrame
) -> pl.DataFrame:
    """Attach native 5m legs by exact end timestamp AND trading day; never fill or use asof.

    `bars` contains normalized actual-contract observations, including auxiliary contracts.
    The anchor OHLC/row_id/target identity is preserved. Missing leg observations remain null.
    """
    keys = ["exchange", "instrument_id", "trading_day"]
    observation_keys = [*keys, "timestamp"]
    if bars.select(observation_keys).is_duplicated().any():
        raise ValueError("duplicate auxiliary contract timestamp")
    if anchors.select(observation_keys).is_duplicated().any():
        raise ValueError("duplicate anchor timestamp")
    extra = [c for c in mapping.columns if c not in {*keys, "product"}]
    if set(extra) & set(anchors.columns):
        raise ValueError("anchor panel already has auxiliary fields")
    result = anchors.join(mapping.select(*keys, *extra), on=keys, how="left", validate="m:1")
    if result["days_to_maturity"].null_count():
        raise ValueError("anchor day is missing from far-contract mapping")
    for leg in (1, 2):
        result = result.join(
            bars.select(*observation_keys, *BAR_FIELDS).rename(
                {c: f"{c}_p{leg}" for c in ("exchange", "instrument_id", *BAR_FIELDS)}
            ),
            on=[f"exchange_p{leg}", f"instrument_id_p{leg}", "trading_day", "timestamp"],
            how="left",
            validate="m:1",
        )
    return result


def connect(env_file: Path | None = None):
    import rqdatac
    from dotenv import dotenv_values

    key = os.environ.get("RICEQUANT_LICENSE_KEY")
    if not key and env_file:
        key = dotenv_values(env_file).get("RICEQUANT_LICENSE_KEY")
    if not key:
        raise ValueError("RICEQUANT_LICENSE_KEY is missing; provide --env-file or environment")
    rqdatac.init(username="license", password=key, use_pool=True, max_pool_size=2)
    return rqdatac


def months(start: date, end: date):
    current = date(start.year, start.month, 1)
    while current <= end:
        nxt = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        from datetime import timedelta

        yield max(start, current), min(end, nxt - timedelta(days=1))
        current = nxt


def normalize_bars(raw, mapping: pl.DataFrame, instruments: pl.DataFrame) -> pl.DataFrame:
    if raw is None or len(raw) == 0:
        raise ValueError("provider returned no bars")
    frame = (
        pl.from_pandas(raw.reset_index())
        .rename(
            {
                "order_book_id": "instrument_id",
                "datetime": "timestamp",
                "trading_date": "trading_day",
                "total_turnover": "amount",
            }
        )
        .with_columns(
            pl.col("timestamp").cast(pl.Datetime("us")).dt.replace_time_zone("Asia/Shanghai"),
            pl.col("trading_day").cast(pl.Date),
        )
    )
    frame = frame.join(
        mapping.select("trading_day", "instrument_id", "product"),
        on=["trading_day", "instrument_id"],
        how="inner",
    )
    frame = frame.join(
        instruments.select("instrument_id", "exchange", "contract_multiplier"),
        on="instrument_id",
        how="left",
        validate="m:1",
    )
    frame = with_row_id(frame).sort("timestamp", "exchange", "instrument_id")
    if frame.height == 0 or frame["exchange"].null_count():
        raise ValueError("empty normalized bars or unresolved instrument exchange")
    if frame["row_id"].n_unique() != frame.height:
        raise ValueError("duplicate futures observation keys or row hash collision")
    return frame


def fetch_futures(
    destination: Path,
    start: date,
    end: date,
    *,
    env_file: Path | None = None,
    workers: int = 2,
    with_far_contracts: bool = False,
) -> dict:
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = "futures_curve" if with_far_contracts else "futures"
        if existing.get("asset_id", "futures") != expected:
            raise ValueError("use a separate export directory for a different contract scope")
    rq = connect(env_file)
    metadata = destination / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata / "provider_instruments.parq"
    if not metadata_path.exists():
        atomic_parq(pl.from_pandas(rq.all_instruments(type="Future")), metadata_path)
    source = pl.read_parquet(metadata_path)
    # Ignore synthetic continuous/index symbols; only delivery contracts with a true maturity.
    inst = source.filter(
        pl.col("order_book_id").str.contains(r"\d{4}$")
        & (pl.col("listed_date").str.to_date(strict=False) <= end)
        & (pl.col("de_listed_date").str.to_date(strict=False) >= start)
    ).rename({"order_book_id": "instrument_id"})
    symbols = inst["underlying_symbol"].unique().sort().to_list()
    request = f"ricequant|5m|dominant_rule0|{start}|{end}"
    if with_far_contracts:
        request += "|later_maturities=2"
    signature = hashlib.sha256(request.encode()).hexdigest()
    manifest = {
        "schema_version": 1,
        "asset_id": "futures_curve" if with_far_contracts else "futures",
        "frequency": "5m",
        "provider": "ricequant",
        "requested_start": str(start),
        "requested_end": str(end),
        "snapshot_id": signature,
        "status": "mapping",
        "scope": "all_products_dominant_real_contracts",
        "dominant_rule": 0,
        "adjust_type": "none",
        "resampled": False,
        "products_requested": symbols,
        "partitions": [],
        "failures": [],
        "mapping_timing": "rule0 uses previous close OI; changes on next trading day",
        "warmup": "contract-local only; initial dominant window may have insufficient history",
    }
    if with_far_contracts:
        manifest.update(
            scope="dominant_real_contracts_with_two_later_maturities",
            auxiliary_alignment="exact 5m end timestamp and trading_day; no filling",
            auxiliary_selection="first two later maturities listed on each historical trading_day",
            bar_time_label="end",
        )
    atomic_json(manifest_path, manifest)
    frames, failures = [], []
    for i, symbol in enumerate(symbols):
        path = metadata / "dominant" / f"{symbol}_{start}_{end}.parq"
        try:
            if path.exists():
                frame = pl.read_parquet(path)
            else:
                result = rq.futures.get_dominant(symbol, start_date=start, end_date=end, rule=0)
                if result is None or len(result) == 0:
                    failures.append({"product": symbol, "stage": "mapping", "error": "no mapping"})
                    continue
                frame = (
                    pl.from_pandas(result.rename("instrument_id").reset_index())
                    .rename({"date": "trading_day"})
                    .with_columns(
                        pl.col("trading_day").cast(pl.Date), pl.lit(symbol).alias("product")
                    )
                )
                frame = frame.drop_nulls("instrument_id")
                atomic_parq(frame, path)
            frames.append(frame)
        except Exception as exc:
            failures.append({"product": symbol, "stage": "mapping", "error": type(exc).__name__})
        print(f"futures mapping {i + 1}/{len(symbols)} {symbol}", flush=True)
    if not frames:
        raise ValueError("no dominant mappings available")
    mapping = pl.concat(frames).sort("trading_day", "product")
    curve = far_contract_mapping(mapping, inst) if with_far_contracts else None
    atomic_parq(mapping, metadata / "dominant_daily.parq")
    atomic_parq(inst, metadata / "instruments.parq")
    calendar = pl.DataFrame({"trading_day": rq.get_trading_dates(start, end)})
    atomic_parq(calendar, metadata / "calendar.parq")
    manifest["products_mapped"] = mapping["product"].unique().sort().to_list()
    manifest["mapping_rows"] = mapping.height
    manifest["failures"] = failures
    manifest["status"] = "downloading"
    atomic_json(manifest_path, manifest)

    def download(lower: date, upper: date) -> dict:
        selected = mapping.filter(pl.col("trading_day").is_between(lower, upper))
        curve_days = (
            curve.filter(pl.col("trading_day").is_between(lower, upper))
            if curve is not None
            else None
        )
        requested = selected
        if curve_days is not None:
            requested = pl.concat(
                [
                    selected.select("trading_day", "instrument_id", "product"),
                    *[
                        curve_days.select(
                            "trading_day",
                            pl.col(f"instrument_id_p{i}").alias("instrument_id"),
                            "product",
                        ).drop_nulls("instrument_id")
                        for i in (1, 2)
                    ],
                ]
            ).unique()
        filename = f"{lower.year:04d}-{lower.month:02d}"
        path = destination / "bars" / f"year={lower.year}" / f"{filename}.parq"
        receipt = path.with_suffix(".json")
        if path.exists() and receipt.exists():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            if saved.get("request_signature") == signature:
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() == saved["sha256"]:
                        return saved
        ids = requested["instrument_id"].unique().sort().to_list()
        if not ids:
            return {"month": filename, "rows": 0, "status": "no mapped contracts"}
        last_error = None
        for attempt in range(3):
            try:
                # Fetch actual contracts directly at native 5m; discard non-dominant dates only.
                raw = rq.get_price(
                    ids,
                    start_date=lower,
                    end_date=upper,
                    frequency="5m",
                    adjust_type="none",
                    expect_df=True,
                )
                frame = normalize_bars(raw, requested, inst)
                if curve_days is not None:
                    anchors = frame.join(
                        selected.select("trading_day", "instrument_id"),
                        on=["trading_day", "instrument_id"],
                        how="inner",
                        validate="m:1",
                    )
                    frame = attach_far_bars(anchors, frame, curve_days)
                observed = frame.select("trading_day", "instrument_id").unique()
                missing = selected.join(observed, on=["trading_day", "instrument_id"], how="anti")
                if missing.height:
                    atomic_parq(missing, metadata / "missing" / f"{filename}.parq")
                part = atomic_parq(frame, path)
                part.update(
                    {
                        "month": filename,
                        "request_signature": signature,
                        "start": str(frame["trading_day"].min()),
                        "end": str(frame["trading_day"].max()),
                        "missing_mapped_days": missing.height,
                        "request_contracts": len(ids),
                        "status": "downloaded",
                    }
                )
                if curve_days is not None:
                    part["auxiliary_coverage"] = {
                        f"p{i}": {
                            "selected_rows": frame[f"instrument_id_p{i}"].is_not_null().sum(),
                            "observed_rows": frame[f"close_p{i}"]
                            .is_finite()
                            .fill_null(False)
                            .sum(),
                            "unavailable_rows": frame[f"instrument_id_p{i}"].null_count(),
                        }
                        for i in (1, 2)
                    }
                    part["missing_auxiliary_bars"] = sum(
                        c["selected_rows"] - c["observed_rows"]
                        for c in part["auxiliary_coverage"].values()
                    )
                atomic_json(receipt, part)
                return part
            except Exception as exc:
                last_error = type(exc).__name__
                time.sleep(1 + attempt)
        return {"month": filename, "status": "failed", "error": last_error, "rows": 0}

    tasks = list(months(start, end))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(download, lo, hi): (lo, hi) for lo, hi in tasks}
        for future in as_completed(pending):
            part = future.result()
            manifest["partitions"].append(part)
            manifest["rows"] = sum(p["rows"] for p in manifest["partitions"])
            if part["status"] == "failed":
                manifest["failures"].append(part)
            atomic_json(manifest_path, manifest)
            print(
                f"futures {len(manifest['partitions'])}/{len(tasks)} "
                f"{part['month']}: {part['rows']:,} {part['status']}",
                flush=True,
            )
    manifest["partitions"].sort(key=lambda p: p["month"])
    manifest["missing_mapped_days"] = sum(
        p.get("missing_mapped_days", 0) for p in manifest["partitions"]
    )
    if with_far_contracts:
        manifest["missing_auxiliary_bars"] = sum(
            p.get("missing_auxiliary_bars", 0) for p in manifest["partitions"]
        )
    manifest["status"] = (
        "complete"
        if not manifest["failures"]
        and not manifest["missing_mapped_days"]
        and not manifest.get("missing_auxiliary_bars", 0)
        else "incomplete"
    )
    manifest["actual_start"] = min(
        (p["start"] for p in manifest["partitions"] if "start" in p), default=None
    )
    manifest["actual_end"] = max(
        (p["end"] for p in manifest["partitions"] if "end" in p), default=None
    )
    atomic_json(manifest_path, manifest)
    return manifest


def merge_futures(destination: Path) -> dict:
    """Export completed partitions to one file without changing acquisition completeness."""
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["status"] not in {"complete", "incomplete"}:
        raise ValueError("finish acquisition before merging")
    parts = [p for p in manifest["partitions"] if p["status"] == "downloaded"]
    if not parts:
        raise ValueError("no downloaded partitions to merge")
    panel = pl.scan_parquet([p["path"] for p in parts])
    stats = (
        panel.select(
            pl.len().alias("rows"),
            pl.struct("exchange", "instrument_id", "timestamp").n_unique().alias("keys"),
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    if stats["rows"] != sum(p["rows"] for p in parts) or stats["rows"] != stats["keys"]:
        raise ValueError("partition row counts or observation keys are inconsistent")
    # Outside bars/ so MarketData never reads both the partitions and their combined copy.
    path = destination / "bars_5m.parq"
    temporary = path.with_suffix(".partial")
    panel.sort("timestamp", "exchange", "instrument_id").sink_parquet(
        temporary, compression="zstd", statistics=True
    )
    if pl.scan_parquet(temporary).select(pl.len()).collect().item() != stats["rows"]:
        raise ValueError("merged row count differs from source partitions")
    temporary.replace(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {
        "path": str(path),
        "rows": stats["rows"],
        "bytes": path.stat().st_size,
        "sha256": digest,
        "source_partitions": len(parts),
        "acquisition_status": manifest["status"],
    }
    manifest["merged_file"] = result
    atomic_json(manifest_path, manifest)
    return result
