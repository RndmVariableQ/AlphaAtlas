"""Import the local Alpha Atlas CRSP/Compustat monthly export into MarketData."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.contracts import fingerprint

US_EQUITY_NON_FACTOR_FIELDS = {
    "date",
    "total_return",
    "permno",
    "gvkey",
    "iid",
    "gsector",
    "ggroup",
    "gind",
    "gsubind",
}

US_EQUITY_MARKET_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "vol",
    "prcvol",
    "market_cap",
    "shares_outstanding",
}


def _read_month(path: Path, month: str) -> tuple[pl.DataFrame, date, date]:
    raw = pl.read_parquet(path)
    required = {"date", "permno", "open", "high", "low", "close", "gsector", "total_return"}
    if missing := required - set(raw.columns):
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    if raw.is_empty():
        raise ValueError(f"{path.name} is empty")
    if any(name.startswith(("label", "target")) for name in raw.columns):
        raise ValueError(f"{path.name} contains target fields")

    frame = raw.rename({"date": "trading_day", "permno": "instrument_id"}).with_columns(
        pl.col("trading_day").cast(pl.Date),
        pl.col("instrument_id").cast(pl.String),
        pl.lit("US").alias("exchange"),
    )
    if frame.select(
        pl.any_horizontal(pl.col("trading_day", "instrument_id").is_null()).any()
    ).item():
        raise ValueError(f"{path.name} contains null (date, permno) keys")
    start, end = frame.select(
        pl.col("trading_day").min().alias("start"), pl.col("trading_day").max().alias("end")
    ).row(0)
    if start.isoformat()[:7] != month or end.isoformat()[:7] != month:
        raise ValueError(f"{path.name} contains dates outside {month}")

    frame = with_row_id(
        frame.with_columns(
            (pl.col("trading_day").cast(pl.Datetime("us")) + pl.duration(hours=16))
            .dt.replace_time_zone("America/New_York")
            .alias("timestamp")
        )
    ).sort("timestamp", "instrument_id")
    if frame["row_id"].n_unique() != frame.height:
        raise ValueError(f"{path.name} contains duplicate (date, permno) keys")
    return frame, start, end


def import_us_equity(source: Path, destination: Path) -> dict:
    paths = sorted(source.glob("part-*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no part-YYYY-MM.parquet files in {source}")
    if destination.exists():
        raise FileExistsError(f"dataset already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".us_equity-") as temp:
        staging = Path(temp) / "snapshot"
        partitions = []
        for path in paths:
            month = path.stem.removeprefix("part-")
            if date.fromisoformat(f"{month}-01").isoformat()[:7] != month:
                raise ValueError(f"invalid monthly partition name: {path.name}")
            frame, start, end = _read_month(path, month)
            relative = Path("bars") / f"year={month[:4]}" / f"{month}.parq"
            part = atomic_parq(frame, staging / relative)
            part.update(
                path=str(destination / relative),
                month=month,
                source=path.name,
                start=start.isoformat(),
                end=end.isoformat(),
            )
            partitions.append(part)
        manifest = {
            "schema_version": 1,
            "asset_id": "us_equity",
            "provider": "crsp_compustat",
            "frequency": "1d",
            "universes": ["all"],
            "status": "complete",
            "actual_start": partitions[0]["start"],
            "actual_end": partitions[-1]["end"],
            "rows": sum(part["rows"] for part in partitions),
            "snapshot_id": fingerprint(
                ["atlas-data-us-equity-v1", [p["sha256"] for p in partitions]]
            ),
            "partitions": partitions,
            "coverage_policy": "Complete import of supplied partitions. Coverage is observed only.",
        }
        atomic_json(staging / "manifest.json", manifest)
        staging.rename(destination)
    return manifest
