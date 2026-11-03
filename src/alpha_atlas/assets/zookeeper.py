"""Read-only preparation of monthly Zookeeper fields from existing local MarketData."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.contracts import fingerprint
from alpha_atlas.factor_libraries.zookeeper import VERSION, definitions
from alpha_atlas.factor_libraries.zookeeper.compute import KEY, align_monthly, compute_monthly

BASE_FIELDS = ["adj_vwap", "volume", "amount", "open_interest"]
CURVE_FIELDS = ["close", "close_p1", "close_p2"]


def daily_base(features):
    """Daily volume-weighted adjusted VWAP, using exactly its valid volume weights."""
    valid = (
        pl.col("adj_vwap").is_finite()
        & (pl.col("adj_vwap") > 0)
        & pl.col("volume").is_finite()
        & (pl.col("volume") > 0)
    )
    return (
        features.sort(KEY + ["timestamp"])
        .group_by(KEY + ["trading_day"], maintain_order=True)
        .agg(
            (
                (pl.col("adj_vwap") * pl.col("volume")).filter(valid).sum()
                / pl.col("volume").filter(valid).sum()
            ).alias("price"),
            pl.when(pl.col("volume").is_finite().fill_null(False).all())
            .then(pl.col("volume").sum())
            .alias("volume"),
            pl.when(pl.col("amount").is_finite().fill_null(False).all())
            .then(pl.col("amount").sum())
            .alias("amount"),
            pl.col("open_interest").last().alias("oi"),
        )
        .with_columns(pl.col("price").fill_nan(None))
    )


def daily_curve(features):
    """Synchronous month-end curve inputs; keep missing or invalid last bars missing."""
    columns = []
    for j, suffix in enumerate(("", "_p1", "_p2")):
        valid = pl.col("valid_bar" + suffix) & (pl.col("close" + suffix) > 0)
        columns.extend(
            [
                pl.when(valid).then(pl.col("close" + suffix)).last().alias(f"F{j}"),
                pl.when(valid).then(pl.col("instrument_id" + suffix)).last().alias(f"leg{j}"),
            ]
        )
    return (
        features.sort(KEY + ["timestamp"])
        .group_by(KEY + ["trading_day"], maintain_order=True)
        .agg(columns)
    )


class ZookeeperMarketData:
    """Prepare 26 fields without changing the native target rows or underlying exports.

    Auxiliary incompleteness is explicitly retained in provenance. Missing legs are never
    filled. The known incomplete curve snapshot can therefore be audited as a proxy input,
    without falsely changing its manifest to 'complete'. No supplier requests are made.
    """

    def __init__(
        self,
        root: Path,
        *,
        plan=None,
        registry=None,
        base_dir="data/futures_kernel_5m_full",
        curve_dir="data/futures_curve",
    ):
        self.plan, self.registry = plan, registry
        self.base = ParqMarketData(root / base_dir, "all")
        try:
            self.curve = ParqMarketData(root / curve_dir, "all", allow_incomplete=True)
        except Exception:
            self.base.close()
            raise
        self.columns = self.base.columns | set(self.get_fields())
        self.monthly = None

    def provenance(self):
        value = {
            "version": VERSION,
            "base_snapshot": self.base.snapshot_id(),
            "curve_snapshot": self.curve.snapshot_id(),
            "base_status": self.base.manifest["status"],
            "curve_status": self.curve.manifest["status"],
            "curve_missing_auxiliary_bars": self.curve.manifest.get("missing_auxiliary_bars"),
            "missing_auxiliary_policy": "use observed values; preserve nulls; no filling",
            "base_frequency": "existing kernel 1m aggregated to 5m; not native 5m",
            "curve_frequency": "RiceQuant native 5m; raw dominant and two later maturities",
            "history_start": self.base.manifest["actual_start"],
            "curve_history_start": self.curve.manifest["actual_start"],
        }
        if self.plan is not None:
            value.update(
                version=self.plan["version"],
                recipe=self.plan,
                operators={s.name: s.version for s in self.registry.catalog()},
            )
        return value

    def snapshot_id(self):
        return fingerprint(self.provenance())

    def get_fields(self):
        rows = definitions() if self.plan is None else self.plan["factors"]
        return tuple(row["prepared_field"] for row in rows)

    def _daily(self, market, fields, end, aggregate):
        start = date.fromisoformat(market.manifest["actual_start"])
        frames = []
        for year in range(start.year, end.year + 1):
            features = market.load_features(
                fields=fields,
                start=max(start, date(year, 1, 1)),
                end=min(end, date(year, 12, 31)),
            )
            if features.height:
                frames.append(aggregate(features))
        return pl.concat(frames)

    def load_features(self, *, fields: list[str], end: date, start: date | None = None):
        if missing := set(fields) - self.columns:
            raise ValueError(f"unsupported fields: {sorted(missing)}")
        derived = set(fields) & set(self.get_fields())
        raw = sorted(set(fields) - derived | {"adj_close", "amount"})
        if derived:
            daily = self._daily(self.base, BASE_FIELDS, end, daily_base)
            curve = self._daily(self.curve, CURVE_FIELDS, end, daily_curve)
            if self.plan is None:
                self.monthly = compute_monthly(daily, curve)
            else:
                from alpha_atlas.factor_libraries.zookeeper.dsl import (
                    compute_monthly as compute_dsl,
                )

                self.monthly = compute_dsl(daily, curve, plan=self.plan, registry=self.registry)
        features = self.base.load_features(fields=raw, start=start, end=end)
        if derived:
            features = align_monthly(features, self.monthly, fields=self.get_fields())
            features = features.drop(sorted(set(self.get_fields()) - derived))
        return features

    def export_monthly(self, path: Path):
        """Export derived observations under the caller's local data directory."""
        if self.monthly is None:
            raise ValueError("load derived features before exporting monthly values")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.monthly.write_parquet(path)

    def close(self):
        self.base.close()
        self.curve.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
