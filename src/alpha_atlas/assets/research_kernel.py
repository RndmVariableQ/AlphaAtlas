"""Export the user's research-kernel adjusted prices through its read-only SQL API."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id

FEATURE_FIELDS = (
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_vwap",
    "volume",
    "amount",
    "open_interest",
)

MONTHS_SQL = """
SELECT strftime(trading_day, '%Y-%m') AS month, count(*) AS source_rows
FROM market.bars_1m
WHERE source = 'ricequant' AND quality = 'final' AND is_dominant
GROUP BY month ORDER BY month
"""

# Match the kernel's session-aware MarketData aggregation: five consecutive minutes,
# resetting at any gap, with the final observed minute as the output timestamp.
EXPORT_SQL = """
WITH canonical AS (
    SELECT exchange, product, instrument_id, trading_day, bar_time,
           adj_open, adj_high, adj_low, adj_close, adj_vwap,
           volume, turnover, open_interest, adj_cum_factor, vwap_flag
    FROM market.bars_1m
    WHERE source = 'ricequant' AND quality = 'final' AND is_dominant
      AND trading_day >= ?::DATE AND trading_day < ?::DATE
    QUALIFY row_number() OVER (
        PARTITION BY exchange, instrument_id, bar_time
        ORDER BY received_at DESC, ingest_seq DESC
    ) = 1
), breaks AS (
    SELECT *, CASE WHEN bar_time - lag(bar_time) OVER (
        PARTITION BY exchange, product, instrument_id, trading_day ORDER BY bar_time
    ) = INTERVAL '1 minute' THEN 0 ELSE 1 END AS session_break
    FROM canonical
), sessions AS (
    SELECT *, sum(session_break) OVER (
        PARTITION BY exchange, product, instrument_id, trading_day ORDER BY bar_time
    ) AS session_id
    FROM breaks
), buckets AS (
    SELECT *, (row_number() OVER (
        PARTITION BY exchange, product, instrument_id, trading_day, session_id
        ORDER BY bar_time
    ) - 1) // 5 AS bucket_id,
    isfinite(adj_vwap) AND volume > 0 AS usable_vwap
    FROM sessions
)
SELECT exchange, product, instrument_id, trading_day::VARCHAR AS trading_day,
       epoch_us(max(bar_time)) AS timestamp_us,
       first(adj_open ORDER BY bar_time) AS adj_open,
       CASE WHEN bool_and(coalesce(isfinite(adj_high), false))
            THEN max(adj_high) ELSE NULL END AS adj_high,
       CASE WHEN bool_and(coalesce(isfinite(adj_low), false))
            THEN min(adj_low) ELSE NULL END AS adj_low,
       last(adj_close ORDER BY bar_time) AS adj_close,
       sum(adj_vwap * volume) FILTER (WHERE usable_vwap)
           / nullif(sum(volume) FILTER (WHERE usable_vwap), 0) AS adj_vwap,
       sum(volume)::BIGINT AS volume, sum(turnover) AS amount,
       last(open_interest ORDER BY bar_time) AS open_interest,
       last(adj_cum_factor ORDER BY bar_time) AS adj_cum_factor,
       count(*) AS source_bars,
       count(*) FILTER (WHERE vwap_flag = 'proxy') AS vwap_proxy_bars,
       count(*) FILTER (WHERE vwap_flag = 'clamped') AS vwap_clamped_bars,
       count(*) FILTER (WHERE vwap_flag = 'no_trade') AS vwap_no_trade_bars
FROM buckets
GROUP BY exchange, product, instrument_id, trading_day, session_id, bucket_id
ORDER BY timestamp_us, exchange, instrument_id
"""

SCHEMA = {
    "exchange": pl.String,
    "product": pl.String,
    "instrument_id": pl.String,
    "trading_day": pl.String,
    "timestamp_us": pl.Int64,
    "adj_open": pl.Float64,
    "adj_high": pl.Float64,
    "adj_low": pl.Float64,
    "adj_close": pl.Float64,
    "adj_vwap": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
    "open_interest": pl.Float64,
    "adj_cum_factor": pl.Float64,
    "source_bars": pl.UInt32,
    "vwap_proxy_bars": pl.UInt32,
    "vwap_clamped_bars": pl.UInt32,
    "vwap_no_trade_bars": pl.UInt32,
}


def _request(base: str, path: str, payload: dict | None = None):
    request = Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=300) as response:
        return json.load(response)


def _frame(body: dict) -> pl.DataFrame:
    if body["columns"] != list(SCHEMA):
        raise ValueError("research kernel returned unexpected export columns")
    frame = (
        pl.DataFrame(body["rows"], schema=SCHEMA, orient="row")
        .with_columns(
            pl.col("trading_day").str.to_date(),
            pl.from_epoch("timestamp_us", time_unit="us")
            .dt.replace_time_zone("UTC")
            .dt.convert_time_zone("Asia/Shanghai")
            .alias("timestamp"),
        )
        .drop("timestamp_us")
    )
    keys = ["exchange", "instrument_id", "timestamp"]
    if frame.select(pl.any_horizontal(pl.col(*keys, "trading_day").is_null()).any()).item():
        raise ValueError("research kernel export contains null observation keys")
    if frame.select(keys).is_duplicated().any():
        raise ValueError("research kernel export contains duplicate observation keys")
    if frame.filter(~pl.col("source_bars").is_between(1, 5)).height:
        raise ValueError("research kernel export has invalid five-minute bucket sizes")
    return with_row_id(frame)


def export_kernel(base_url: str, destination: Path) -> dict:
    """Copy adjusted dominant-contract features; never connect to the source database."""
    url = urlsplit(base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError("kernel URL must be HTTP(S) without credentials, query or fragment")
    base = base_url.rstrip("/")
    manifest_path = destination / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and previous["kernel_url"] != base:
        raise ValueError("existing export belongs to a different research kernel")
    if previous and previous.get("fields") != list(FEATURE_FIELDS):
        raise ValueError(
            "existing export has a different feature schema; use a new output directory"
        )
    if previous and previous["status"] == "complete" and previous.get("snapshot_id"):
        return previous
    dataset = _request(base, "/api/dataset")
    if any(
        dataset.get(key) != value
        for key, value in {"source": "ricequant", "quality": "final", "interval": "1m"}.items()
    ):
        raise ValueError("export requires the research kernel's RiceQuant final 1m dataset")
    body = _request(base, "/api/query", {"sql": MONTHS_SQL, "parameters": []})
    months = [dict(zip(body["columns"], row, strict=True)) for row in body["rows"]]
    if not months:
        raise ValueError("research kernel has no final dominant-contract bars to export")
    manifest = previous or {
        "schema_version": 2,
        "asset_id": "futures_kernel_5m_full",
        "kernel_url": base,
        "source_view": "market.bars_1m",
        "source_dataset": dataset,
        "source_frequency": "1m",
        "frequency": "5m",
        "provider": "ricequant via research kernel",
        "scope": "all products; final dominant real-contract records",
        "resampled": True,
        "created_at": datetime.now(UTC).isoformat(),
        "fields": list(FEATURE_FIELDS),
        "adjustment": "kernel values; price multiplied by dominant-day adj_cum_factor",
        "aggregation": {
            "buckets": "five consecutive 1m bars per exchange/product/contract/trading_day; "
            "reset on gaps; retain partial tails; timestamp is the final observed minute",
            "adj_open": "first value, preserving null",
            "adj_high": "maximum only when all source highs are finite; otherwise null",
            "adj_low": "minimum only when all source lows are finite; otherwise null",
            "adj_close": "last value, preserving null",
            "adj_vwap": "sum(adj_vwap*volume)/sum(volume) over finite prices and volume>0; "
            "null when no positive weight; preserves the kernel's clamped/proxy inputs",
            "volume": "sum",
            "amount": "sum of kernel turnover",
            "open_interest": "last value, preserving null; no summation or price adjustment",
        },
        "partitions": [],
    }
    manifest["status"] = "downloading"
    manifest["raw_source_rows"] = sum(row["source_rows"] for row in months)
    atomic_json(manifest_path, manifest)
    completed = {row["month"] for row in manifest["partitions"]}
    for index, month in enumerate(months, 1):
        name = month["month"]
        if name in completed:
            continue
        lower = date.fromisoformat(name + "-01")
        upper = date(lower.year + (lower.month == 12), lower.month % 12 + 1, 1)
        body = _request(
            base,
            "/api/query",
            {"sql": EXPORT_SQL, "parameters": [lower.isoformat(), upper.isoformat()]},
        )
        frame = _frame(body)
        if frame.is_empty():
            raise ValueError(f"research kernel returned no bars for {name}")
        path = destination / "bars" / f"year={lower.year}" / f"{name}.parq"
        receipt = atomic_parq(frame, path)
        receipt["path"] = str(path.relative_to(destination))
        receipt.update(
            month=name,
            start=str(frame["trading_day"].min()),
            end=str(frame["trading_day"].max()),
            products=sorted(frame["product"].unique().to_list()),
            **{f"missing_{field}": frame[field].null_count() for field in FEATURE_FIELDS},
            **{
                f"nonfinite_{field}": (~frame[field].is_finite()).fill_null(False).sum()
                for field in FEATURE_FIELDS
            },
            **{key: frame[key].sum() for key in SCHEMA if key.endswith("_bars")},
        )
        manifest["partitions"].append(receipt)
        manifest["rows"] = sum(row["rows"] for row in manifest["partitions"])
        atomic_json(manifest_path, manifest)
        print(f"export {index}/{len(months)} {name}: {frame.height:,} five-minute bars", flush=True)
    manifest["actual_start"] = min(row["start"] for row in manifest["partitions"])
    manifest["actual_end"] = max(row["end"] for row in manifest["partitions"])
    manifest["products"] = sorted({p for row in manifest["partitions"] for p in row["products"]})
    identity = {
        "source_dataset": manifest["source_dataset"],
        "aggregation": manifest["aggregation"],
        "partitions": [(row["month"], row["sha256"]) for row in manifest["partitions"]],
    }
    manifest["snapshot_id"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()
    manifest["completed_at"] = datetime.now(UTC).isoformat()
    manifest["status"] = "complete"
    atomic_json(manifest_path, manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8791")
    parser.add_argument("--output", type=Path, default=Path("data/futures_kernel_5m_full"))
    args = parser.parse_args()
    result = export_kernel(args.url, args.output)
    print(
        json.dumps({"status": result["status"], "rows": result["rows"], "output": str(args.output)})
    )
