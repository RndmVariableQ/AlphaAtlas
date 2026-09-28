"""DuckDB-backed read-only MarketData over portable .parq exports."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from alpha_atlas.assets.common import KEYS


class ParqMarketData:
    def __init__(self, directory: Path, universe: str, *, allow_incomplete: bool = False):
        self.directory = directory
        self.universe = universe
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest["status"] != "complete" and not allow_incomplete:
            raise ValueError("dataset is incomplete; inspect manifest before formal experiments")
        self._db = duckdb.connect(":memory:")
        glob = str(directory / "bars/**/*.parq").replace("\\", "/").replace("'", "''")
        self._db.execute(
            f"CREATE VIEW bars AS SELECT * FROM read_parquet('{glob}', "
            "union_by_name=true, hive_partitioning=false)"
        )
        self.columns = {row[0] for row in self._db.execute("DESCRIBE bars").fetchall()}
        if any(c.startswith("label_") for c in self.columns):
            raise ValueError("labels must not be stored in feature partitions")
        if universe != "all" and f"in_{universe}" not in self.columns:
            raise ValueError(f"unknown universe {universe}")

    def snapshot_id(self) -> str:
        parts = sorted(p.get("sha256", "") for p in self.manifest.get("partitions", []))
        metadata = []
        for name in ("calendar.parq", "dominant_daily.parq"):
            path = self.directory / "metadata" / name
            if path.exists():
                with path.open("rb") as stream:
                    metadata.append((name, hashlib.file_digest(stream, "sha256").hexdigest()))
        payload = json.dumps(["market-quality-v2", self.manifest["snapshot_id"], parts, metadata])
        return hashlib.sha256(payload.encode()).hexdigest()

    def load_features(
        self, *, fields: list[str], end: date, start: date | None = None
    ) -> pl.DataFrame:
        if any(f.startswith("label_") or f.startswith("target") for f in fields):
            raise ValueError("targets are not feature fields")
        missing = set(fields) - self.columns
        if missing:
            raise ValueError(f"unsupported fields: {sorted(missing)}")
        extra = ["product"] if "product" in self.columns else []
        flags = [f"in_{self.universe}"] if self.universe != "all" else []
        trading_flags = [c for c in ["is_st", "is_suspended"] if c in self.columns]
        quality_fields = [c for c in ["open", "high", "low", "close"] if c in self.columns]
        legs = [f"p{i}" for i in (1, 2) if any(f.endswith(f"_p{i}") for f in fields)]
        far_columns = [
            f"{name}_{leg}"
            for leg in legs
            for name in ("exchange", "instrument_id", "open", "high", "low", "close")
        ]
        if missing := set(far_columns) - self.columns:
            raise ValueError(f"far-contract fields require identity and OHLC: {sorted(missing)}")
        columns = list(
            dict.fromkeys(
                KEYS + extra + flags + trading_flags + quality_fields + fields + far_columns
            )
        )
        quoted = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
        # Keep pre-membership bars for TS warmup, apply eligibility only during scoring.
        result = self._db.execute(
            f"SELECT {quoted} FROM bars WHERE trading_day <= ? AND (? IS NULL OR trading_day >= ?) "
            "ORDER BY exchange, instrument_id, timestamp",
            [end, start, start],
        ).pl()
        eligible = pl.col(flags[0]).cast(pl.Boolean) if flags else pl.lit(True)
        for flag in trading_flags:
            eligible = eligible & (~pl.col(flag).cast(pl.Boolean).fill_null(True))
        valid_bar = pl.lit(True)
        if set(quality_fields) == {"open", "high", "low", "close"}:
            valid_bar = (
                pl.all_horizontal(pl.col(quality_fields).is_finite())
                & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
                & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
            ).fill_null(False)
        result = result.with_columns(valid_bar.alias("valid_bar"))
        result = result.with_columns((eligible & pl.col("valid_bar")).alias("eligible"))
        result = result.with_columns(
            [
                pl.when(pl.col("valid_bar")).then(pl.col(name)).otherwise(None).alias(name)
                for name in set(fields + quality_fields)
            ]
        )
        result = self._continuity(result, end)
        group = ["exchange", "instrument_id", "segment_id"]
        for leg in legs:
            o, h, low, c = (pl.col(f"{name}_{leg}") for name in ("open", "high", "low", "close"))
            valid = (
                pl.all_horizontal(x.is_finite() for x in (o, h, low, c))
                & (h >= pl.max_horizontal(o, c, low))
                & (low <= pl.min_horizontal(o, c, h))
                & pl.col(f"instrument_id_{leg}").is_not_null()
                & pl.col(f"exchange_{leg}").is_not_null()
            ).fill_null(False)
            result = result.with_columns(valid.alias(f"valid_bar_{leg}"))
            identity_changed = pl.any_horizontal(
                (pl.col(f"{key}_{leg}") != pl.col(f"{key}_{leg}").shift().over(group)).fill_null(
                    True
                )
                for key in ("exchange", "instrument_id")
            )
            breaks = identity_changed | ~valid | ~valid.shift().over(group).fill_null(False)
            result = result.with_columns(
                breaks.cast(pl.Int64).cum_sum().over(group).alias(f"segment_id_{leg}"),
                *[
                    pl.when(valid).then(pl.col(f)).alias(f)
                    for f in set(fields + far_columns)
                    if f.endswith(f"_{leg}")
                    and f not in {f"exchange_{leg}", f"instrument_id_{leg}"}
                ],
            )
        frequency = self.manifest.get("frequency")
        if frequency in {"5m", "1d"}:
            result = result.with_columns(
                pl.lit(5 if frequency == "5m" else 1440).alias("bar_interval_minutes")
            )
        return result

    def _continuity(self, frame: pl.DataFrame, end: date) -> pl.DataFrame:
        """Observed native bars; calendar gaps and dominant re-entry reset history.

        No intraday schedule is fabricated. Unknown within-day missing bars remain a
        disclosed limitation. Synthetic fixtures may supply their own observed calendar.
        """
        group = ["exchange", "instrument_id"]
        calendar_path = self.directory / "metadata/calendar.parq"
        calendar = (
            pl.read_parquet(calendar_path).select("trading_day")
            if calendar_path.exists()
            else frame.select("trading_day").unique()
        )
        calendar = (
            calendar.filter(pl.col("trading_day") <= end)
            .sort("trading_day")
            .with_row_index("__day")
        )
        days = (
            frame.select(*group, "trading_day")
            .unique()
            .join(calendar, on="trading_day", how="left")
        )
        days = days.sort(group + ["trading_day"])
        gap = (pl.col("__day").diff().over(group) != 1).fill_null(True)
        if "product" in frame.columns:
            mapping_path = self.directory / "metadata/dominant_daily.parq"
            mapping = (
                (
                    pl.read_parquet(mapping_path).select("product", "instrument_id", "trading_day")
                    if mapping_path.exists()
                    else frame.select("product", "instrument_id", "trading_day").unique()
                )
                .filter(pl.col("trading_day") <= end)
                .sort("product", "trading_day")
            )
            mapping = mapping.with_columns(
                (pl.col("instrument_id") != pl.col("instrument_id").shift().over("product"))
                .fill_null(True)
                .cast(pl.Int64)
                .cum_sum()
                .over("product")
                .alias("__dominant_segment")
            )
            selected = (
                frame.select(*group, "product", "trading_day")
                .unique()
                .join(
                    mapping,
                    on=["product", "instrument_id", "trading_day"],
                    how="left",
                    validate="m:1",
                )
            )
            days = days.join(selected, on=group + ["trading_day"], validate="1:1").sort(
                group + ["trading_day"]
            )
            gap = gap | (pl.col("__dominant_segment").diff().over(group) != 0).fill_null(True)
        days = days.with_columns(gap.cast(pl.Int64).cum_sum().over(group).alias("__daily_segment"))
        result = frame.join(
            days.select(*group, "trading_day", "__daily_segment"),
            on=group + ["trading_day"],
            validate="m:1",
        ).sort(group + ["timestamp"])
        valid = pl.col("valid_bar")
        if "is_suspended" in result.columns:
            valid = valid & ~pl.col("is_suspended").fill_null(True)
        result = result.with_columns(valid.alias("target_eligible"))
        breaks = (
            (pl.col("__daily_segment").diff().over(group) != 0).fill_null(True)
            | ~valid
            | ~valid.shift().over(group).fill_null(False)
        )
        return result.with_columns(
            breaks.cast(pl.Int64).cum_sum().over(group).alias("segment_id")
        ).drop("__daily_segment")

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
