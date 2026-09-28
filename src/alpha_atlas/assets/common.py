from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

KEYS = ["row_id", "timestamp", "trading_day", "exchange", "instrument_id"]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def atomic_parq(frame: pl.DataFrame, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    frame.write_parquet(tmp, compression="zstd", statistics=True)
    tmp.replace(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "rows": frame.height, "bytes": path.stat().st_size, "sha256": digest}


def with_row_id(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.struct("exchange", "instrument_id", "timestamp").hash(seed=20260909).alias("row_id")
    )
