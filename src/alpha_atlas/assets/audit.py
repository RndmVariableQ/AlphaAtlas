"""Explicit export integrity audit. This module is part of the data-adapter boundary."""

import hashlib
import json
from pathlib import Path

import duckdb

from alpha_atlas.assets.common import KEYS, atomic_json
from alpha_atlas.assets.us_equity import US_EQUITY_MARKET_FIELDS, US_EQUITY_NON_FACTOR_FIELDS


def _column_quality(db, columns):
    expressions = []
    for name, dtype in columns:
        quoted = '"' + name.replace('"', '""') + '"'
        expressions.append(f"count(*) - count({quoted})")
        expressions.append(
            f"count(*) FILTER (WHERE NOT isfinite({quoted}))"
            if dtype in {"FLOAT", "DOUBLE", "REAL"}
            else "0"
        )
    counts = db.execute(f"SELECT {', '.join(expressions)} FROM bars").fetchone()
    return {
        name: {"null_count": nulls, "nonfinite_count": nonfinite}
        for (name, _), nulls, nonfinite in zip(columns, counts[::2], counts[1::2], strict=True)
    }


def audit(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    us_equity = manifest.get("asset_id") == "us_equity"
    hash_mismatches = []
    for part in manifest["partitions"]:
        month = part["month"]
        path = directory / "bars" / f"year={month[:4]}" / f"{month}.parq"
        if not path.exists():
            hash_mismatches.append(month)
            continue
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != part.get("sha256"):
                hash_mismatches.append(month)
    glob = str(directory / "bars/**/*.parq").replace("\\", "/").replace("'", "''")
    with duckdb.connect(":memory:") as db:
        db.execute(
            f"CREATE VIEW bars AS SELECT * FROM read_parquet('{glob}', "
            "union_by_name=true, hive_partitioning=false)"
        )
        columns = [(row[0], row[1]) for row in db.execute("DESCRIBE bars").fetchall()]
        names = [name for name, _ in columns]
        ohlc_fields = ["open", "high", "low", "close"]
        if not set(ohlc_fields) <= set(names):
            ohlc_fields = ["adj_open", "adj_high", "adj_low", "adj_close"]
        if not set(ohlc_fields) <= set(names):
            raise ValueError("integrity audit requires complete raw or adjusted OHLC fields")
        prices = [f'"{name}"' for name in ohlc_fields]
        opening, high, low, closing = prices
        if us_equity:
            prices = [f"(CASE WHEN isfinite({price}) THEN {price} END)" for price in prices]
        check_open, check_high, check_low, check_close = prices
        invalid_ohlc = (
            f"{check_high} < {check_low} OR {check_high} < {check_open} "
            f"OR {check_high} < {check_close} OR {check_low} > {check_open} "
            f"OR {check_low} > {check_close}"
        )
        missing_ohlc = " OR ".join(
            f'"{name}" IS NULL OR NOT isfinite("{name}")' for name in ohlc_fields
        )
        counts = db.execute(f"""
            SELECT count(*) AS row_count, min(trading_day) first_day, max(trading_day) last_day,
                   count(DISTINCT (exchange, instrument_id)) instruments,
                   count(*) - count(DISTINCT row_id) duplicate_row_ids,
                   count(*) FILTER (WHERE exchange IS NULL OR instrument_id IS NULL
                     OR timestamp IS NULL OR trading_day IS NULL) null_keys,
                   count(*) FILTER (WHERE {invalid_ohlc}) invalid_ohlc,
                   count(*) FILTER (
                       WHERE {closing} IS NULL OR NOT isfinite({closing})
                   ) missing_close,
                   count(*) FILTER (WHERE {missing_ohlc}) missing_ohlc
            FROM bars
        """).fetchone()
        result = dict(
            zip(
                [
                    "rows",
                    "first_day",
                    "last_day",
                    "instruments",
                    "duplicate_row_ids",
                    "null_keys",
                    "invalid_ohlc",
                    "missing_close",
                    "missing_ohlc",
                ],
                counts,
                strict=True,
            )
        )
        result["ohlc_fields"] = ohlc_fields
        result["label_columns_in_features"] = [c for c in names if c.startswith("label_")]
        result["column_count"] = len(names)
        if us_equity:
            metadata = set(KEYS) | US_EQUITY_NON_FACTOR_FIELDS | US_EQUITY_MARKET_FIELDS
            result["fundamental_columns"] = [name for name in names if name not in metadata]
            result["column_quality"] = _column_quality(db, columns)
        else:
            result["fundamental_columns"] = [c for c in names if c.startswith("funda_")]
        if "in_union1800" in names:
            result["universe_rows"] = dict(
                db.execute("""
                SELECT 'union1800', count(*) FROM bars WHERE in_union1800
                UNION ALL SELECT 'hs300', count(*) FROM bars WHERE in_hs300
                UNION ALL SELECT 'zz500', count(*) FROM bars WHERE in_zz500
                UNION ALL SELECT 'zz1000', count(*) FROM bars WHERE in_zz1000
            """).fetchall()
            )
        if "product" in names:
            result["products"] = db.execute("SELECT count(DISTINCT product) FROM bars").fetchone()[
                0
            ]
        if "fundamentals_source_day" in names:
            result["fundamental_availability_violations"] = db.execute(
                "SELECT count(*) FROM bars WHERE fundamentals_source_day >= trading_day"
            ).fetchone()[0]
            fields = result["fundamental_columns"]
            coverage_sql = ", ".join(
                'avg(CASE WHEN isfinite("' + name + '") THEN 1.0 ELSE 0.0 END)' for name in fields
            )
            result["fundamental_finite_coverage"] = dict(
                zip(fields, db.execute(f"SELECT {coverage_sql} FROM bars").fetchone(), strict=True)
            )
        trading_flags = [name for name in ["is_st", "is_suspended"] if name in names]
        if trading_flags:
            result["missing_trading_flags"] = db.execute(
                "SELECT count(*) FROM bars WHERE "
                + " OR ".join('"' + name + '" IS NULL' for name in trading_flags)
            ).fetchone()[0]
        # Polars converts timezone-aware timestamps with the standard library's zoneinfo.
        result["anomalies"] = (
            db.execute(
                f"SELECT timestamp, exchange, instrument_id, {opening}, {high}, {low}, {closing} "
                f"FROM bars WHERE {invalid_ohlc} LIMIT 20"
            )
            .pl()
            .rows()
        )
        yearly = db.execute(
            "SELECT year(trading_day), count(*) FROM bars GROUP BY 1 ORDER BY 1"
        ).fetchall()
        result["rows_by_year"] = {str(year): count for year, count in yearly}
    result["manifest_status"] = manifest["status"]
    result["partition_hash_mismatches"] = hash_mismatches
    result["missing_member_days"] = manifest.get("missing_member_days", 0)
    result["outside_listing_member_days"] = manifest.get("outside_listing_member_days", 0)
    result["unresolved_missing_member_days"] = manifest.get("unresolved_missing_member_days", 0)
    result["passed"] = (
        not hash_mismatches
        and not result["duplicate_row_ids"]
        and not result["null_keys"]
        and not result["invalid_ohlc"]
        and not result["missing_close"]
        and not result["missing_ohlc"]
        and not result["label_columns_in_features"]
        and not result.get("fundamental_availability_violations", 0)
        and not result.get("missing_trading_flags", 0)
        and not any(col["nonfinite_count"] for col in result.get("column_quality", {}).values())
        and result["rows"] == manifest["rows"]
        and manifest["status"] == "complete"
    )
    result["research_ready"] = (
        not hash_mismatches
        and not result["duplicate_row_ids"]
        and not result["null_keys"]
        and not result["label_columns_in_features"]
        and not result.get("fundamental_availability_violations", 0)
        and result["rows"] == manifest["rows"]
        and manifest["status"] == "complete"
    )
    if us_equity:
        result["quality_policy"] = (
            "Column quality counts null and nonfinite values. OHLC inconsistencies exclude a "
            "row from scoring. Partial rows can be scored with a valid factor and target."
        )
    else:
        result["quality_policy"] = (
            "Export anomalies are preserved. MarketData masks invalid OHLC observations, retains "
            "row positions, and excludes them from scoring. passed is strict raw integrity; "
            "research_ready accounts for this explicit quarantine policy."
        )
    atomic_json(directory / "audit.json", result)
    return result
