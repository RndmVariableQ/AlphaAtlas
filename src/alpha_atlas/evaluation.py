"""Split-safe factor evaluation with explicit run-local observation references."""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from dataclasses import asdict, replace
from pathlib import Path

import polars as pl

from alpha_atlas.contracts import (
    Candidate,
    DateRange,
    EvaluationReport,
    FactorCorrelation,
    FactorValues,
    Fold,
    Metric,
    fingerprint,
)
from alpha_atlas.expressions import GROUP, compile_factor, execute
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.storage import ArrowCache


def build_targets(frame: pl.DataFrame, price: str, horizon: int) -> pl.DataFrame:
    if type(horizon) is not int or horizon < 1:
        raise ValueError("target horizon must be positive integer")
    group = GROUP + (["segment_id"] if "segment_id" in frame.columns else [])
    frame = frame.sort(GROUP + ["timestamp"])
    future = pl.col(price).shift(-horizon).over(group)
    valid = pl.col(price).is_finite() & (pl.col(price) > 0)
    if "target_eligible" in frame.columns:
        valid = valid & pl.col("target_eligible").fill_null(False)
    # Interior known invalid bars cannot be compressed into an apparently valid horizon.
    complete = (
        valid.cast(pl.Int32).rolling_sum(horizon + 1).shift(-horizon).over(group) == horizon + 1
    )
    return frame.select(
        "row_id",
        pl.when(complete & (future > 0)).then((future / pl.col(price)).log()).alias("target"),
        pl.col("trading_day").shift(-horizon).over(group).alias("target_end"),
    )


def split_panel(frame: pl.DataFrame, period: DateRange) -> pl.DataFrame:
    return frame.filter(
        pl.col("eligible").fill_null(False)
        & pl.col("trading_day").is_between(period.start, period.end)
        & pl.col("target_end").is_between(period.start, period.end)
        & pl.col("target").is_finite().fill_null(False)
    )


FUTURES_METRICS = (
    "time_series_equal_pearson_ic",
    "time_series_weighted_pearson_ic",
    "time_series_equal_spearman_ic",
    "time_series_weighted_spearman_ic",
)


def product_weights(features: pl.DataFrame, period: DateRange) -> pl.DataFrame:
    """Fixed split weights: sqrt of each product's total eligible, valid turnover."""
    return (
        features.filter(
            pl.col("trading_day").is_between(period.start, period.end)
            & pl.col("eligible").fill_null(False)
        )
        .group_by("product")
        .agg(
            pl.when(pl.col("amount").is_finite() & (pl.col("amount") >= 0))
            .then(pl.col("amount"))
            .sum()
            .sqrt()
            .alias("weight")
        )
        .filter(pl.col("weight").is_finite() & (pl.col("weight") > 0))
    )


def metrics(
    frame: pl.DataFrame, name: str, split: str, *, weights=None, correlation=None
) -> tuple[Metric, ...]:
    frame = frame.filter(
        pl.col("value").is_finite().fill_null(False) & pl.col("target").is_finite().fill_null(False)
    )
    if name == "cross_sectional_spearman":
        groups = ["trading_day"]
    elif name in FUTURES_METRICS:
        # Pool all contracts first; ranking and correlation happen within the whole product.
        groups = ["product"]
    else:
        raise ValueError(f"unsupported metric {name}")
    primary = "pearson" if "pearson" in name else "spearman"
    correlations = (
        (correlation,)
        if correlation
        else (
            primary,
            "spearman" if primary == "pearson" else "pearson",
        )
    )
    result = (
        frame.group_by(groups)
        .agg(
            *(pl.corr("value", "target", method=c).alias(c) for c in correlations),
            pl.len().alias("n"),
            pl.col("value").n_unique().alias("nx"),
            pl.col("target").n_unique().alias("ny"),
        )
        .filter((pl.col("n") >= 5) & (pl.col("nx") > 1) & (pl.col("ny") > 1))
        .sort(groups)
    )
    names = (name,)
    if name in FUTURES_METRICS:
        names += (
            name.replace("_weighted_", "_equal_")
            if "_weighted_" in name
            else name.replace("_equal_", "_weighted_"),
        )
    output = []
    for correlation in correlations:
        valid = result.filter(pl.col(correlation).is_finite()).rename({correlation: "ic"})
        for metric_name in names:
            observed = valid
            if "_weighted_" in metric_name:
                if weights is None:
                    observed = valid.head(0).with_columns(pl.lit(0.0).alias("weight"))
                else:
                    observed = valid.join(weights, on="product", how="inner", validate="1:1").sort(
                        groups
                    )
                value = (
                    observed.select(
                        (pl.col("ic") * pl.col("weight")).sum() / pl.col("weight").sum()
                    ).item()
                    if observed.height
                    else None
                )
            else:
                value = observed["ic"].mean() if observed.height else None
            if value is not None:
                value = max(-1.0, min(1.0, value)) if math.isfinite(value) else None
            label = metric_name.replace(primary, correlation)
            output.append(
                Metric(label, split, value, int(observed["n"].sum()), label, observed.height)
            )
    return tuple(output)


def metric(frame: pl.DataFrame, name: str, split: str, *, correlation=None, weights=None) -> Metric:
    correlation = correlation or ("pearson" if "pearson" in name else "spearman")
    return metrics(frame, name, split, correlation=correlation, weights=weights)[0]


def search_diagnostics(panel, period, name, direction, weights=None):
    """Train-only stability and explicit holdings turnover; never substitute missing statistics.

    ICIR uses daily primary IC (futures: first pool within product/day), sample std,
    at least two valid days. Futures turnover uses unit signed holdings per native
    bar and continuity segment. Stocks use daily demeaned ranks / gross exposure.
    Both turnovers are half the absolute change, excluding initial positions.
    """
    futures = name in FUTURES_METRICS
    result = {
        "version": "icir_turnover_v1",
        "split": "train",
        "icir": None,
        "icir_days": 0,
        "turnover": None,
        "turnover_observations": 0,
        "turnover_unit": "native_bar_unit_position" if futures else "daily_rank_portfolio",
    }
    if direction is None:
        return result
    scored = split_panel(panel, period).filter(pl.col("value").is_finite())
    groups = ["trading_day", "product"] if futures else ["trading_day"]
    daily = (
        scored.group_by(groups)
        .agg(
            pl.corr("value", "target", method="pearson" if "pearson" in name else "spearman")
            .mul(direction)
            .alias("ic"),
            pl.len().alias("n"),
        )
        .filter((pl.col("n") >= 5) & pl.col("ic").is_finite())
    )
    if futures:
        if "_weighted_" in name:
            daily = (
                daily.join(weights, on="product", how="inner")
                .sort("trading_day", "product")
                .group_by("trading_day")
                .agg(((pl.col("ic") * pl.col("weight")).sum() / pl.col("weight").sum()).alias("ic"))
            )
        else:
            daily = (
                daily.sort("trading_day", "product")
                .group_by("trading_day")
                .agg(pl.col("ic").mean())
            )
    daily = daily.sort("trading_day")
    result["icir_days"] = daily.height
    std = daily["ic"].std(ddof=1)
    if daily.height >= 2 and std is not None and std > 1e-12:
        result["icir"] = daily["ic"].mean() / std

    holdings = panel.filter(pl.col("trading_day").is_between(period.start, period.end))
    finite = pl.col("eligible").fill_null(False) & pl.col("value").is_finite().fill_null(False)
    if futures:
        group = GROUP + (["segment_id"] if "segment_id" in panel.columns else [])
        holdings = (
            holdings.sort(GROUP + ["timestamp"])
            .with_columns(pl.when(finite).then(pl.col("value").sign()).alias("position"))
            .with_columns(
                ((pl.col("position") - pl.col("position").shift().over(group)).abs() / 2).alias(
                    "change"
                )
            )
            .filter(pl.col("change").is_finite())
            .group_by("product")
            .agg(pl.col("change").mean(), pl.len().alias("n"))
        )
        if "_weighted_" in name:
            holdings = holdings.join(weights, on="product", how="inner").sort("product")
            value = holdings.select(
                (pl.col("change") * pl.col("weight")).sum() / pl.col("weight").sum()
            ).item()
        else:
            value = holdings.sort("product")["change"].mean()
        result["turnover_observations"] = int(holdings["n"].sum())
    else:
        # Keep the full observed clock: a missing/invalid snapshot must not be bridged.
        clock = holdings.select("trading_day").unique().sort("trading_day").with_row_index("step")
        holdings = holdings.filter(pl.col("eligible").fill_null(False)).join(
            clock, on="trading_day"
        )
        complete = (
            holdings.group_by("step")
            .agg(
                pl.col("value").is_finite().fill_null(False).all().alias("complete"),
                pl.len().alias("n"),
            )
            .filter(pl.col("complete") & (pl.col("n") >= 5))
            .select("step")
        )
        holdings = (
            holdings.join(complete, on="step")
            .with_columns(pl.col("value").rank().over("step").alias("rank"))
            .with_columns((pl.col("rank") - pl.col("rank").mean().over("step")).alias("rank"))
            .with_columns(
                (pl.col("rank") / pl.col("rank").abs().sum().over("step"))
                .fill_nan(0.0)
                .alias("position")
            )
            .select("step", *GROUP, "position")
        )
        previous = holdings.with_columns(pl.col("step") + 1).rename({"position": "previous"})
        adjacent = complete.join(complete.with_columns(pl.col("step") + 1), on="step")
        changes = (
            holdings.join(previous, on=["step", *GROUP], how="full", coalesce=True)
            .join(adjacent, on="step")
            .with_columns(
                (
                    (pl.col("position").fill_null(0) - pl.col("previous").fill_null(0)).abs() / 2
                ).alias("change")
            )
            .sort("step", *GROUP)
            .group_by("step")
            .agg(pl.col("change").sum())
            .sort("step")
        )
        value = changes["change"].mean()
        result["turnover_observations"] = changes.height
    result["turnover"] = value if value is not None and math.isfinite(value) else None
    return result


class EvaluationService:
    def __init__(
        self,
        features: pl.DataFrame,
        fold: Fold,
        profile: dict,
        fields: set[str],
        *,
        snapshot_id: str | None = None,
        context: dict | None = None,
        registry: OperatorRegistry | None = None,
        cache_dir: Path | None = None,
        cache_bytes: int = 512 * 1024 * 1024,
        disk_cache: ArrowCache | None = None,
        compile_options: dict | None = None,
        retain_training: bool = False,
        diagnostics: str = "",
    ):
        latest = features["trading_day"].max()
        if latest is not None and latest > fold.val.end:
            raise ValueError("search evaluator cannot receive OOS features")
        if features["row_id"].n_unique() != features.height:
            raise ValueError("duplicate row_id")
        if features.select(*GROUP, "timestamp").is_duplicated().any():
            raise ValueError("duplicate instrument timestamp")
        if any(
            c.startswith(("label_", "target")) and c != "target_eligible" for c in features.columns
        ):
            raise ValueError("targets must not enter the feature panel")
        self.features = features.clone()
        if diagnostics not in {"", "icir_turnover_v1"}:
            raise ValueError("unsupported search diagnostics")
        self.diagnostics = diagnostics
        self.fold = fold
        self.profile = json.loads(json.dumps(profile, default=str))
        self.fields = set(fields)
        self.snapshot_id = snapshot_id or fingerprint(features.hash_rows().to_list())
        self.context_id = fingerprint((context or {}, self.profile, sorted(fields), "quality-v2"))
        self.registry = registry or OperatorRegistry()
        self.compile_options = dict(compile_options or {})
        self.targets = build_targets(
            features, profile["price_field"], profile["target_horizon_bars"]
        )
        self.weights = (
            {s: product_weights(features, getattr(fold, s)) for s in ("train", "val")}
            if profile["metric"] in FUTURES_METRICS
            else {}
        )
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_bytes = max(0, cache_bytes)
        self.disk_cache = disk_cache or (
            ArrowCache((cache_dir,), self.cache_bytes) if cache_dir else None
        )
        self._cache: OrderedDict[str, tuple[EvaluationReport, FactorValues]] = OrderedDict()
        self._disk: dict[str, str] = {}
        self.retain_training = retain_training
        self._training: OrderedDict[str, pl.DataFrame] = OrderedDict()

    def _remember_training(self, identity, frame):
        keys = ["row_id", "trading_day", "exchange", "instrument_id", "value"]
        if "product" in frame.columns:
            keys.append("product")
        self._training[identity] = frame.select(keys)
        self._training.move_to_end(identity)
        while sum(f.estimated_size() for f in self._training.values()) > self.cache_bytes:
            if len(self._training) <= 1:
                break
            self._training.popitem(last=False)

    def _training_values(self, identity, candidate):
        if identity not in self._training:
            compiled = compile_factor(
                candidate.expression, self.fields, self.registry, **self.compile_options
            )
            if compiled.factor_id != identity:
                raise ValueError("training statistic factor identity mismatch")
            # Rebuild only requested missing values, using the existing MarketData panel.
            features = self.features.filter(pl.col("trading_day") <= self.fold.train.end)
            values = execute(compiled, features, self.fields, self.registry)
            panel = features.join(self.targets, on="row_id", validate="1:1").join(
                values, on="row_id", validate="1:1"
            )
            self._remember_training(identity, split_panel(panel, self.fold.train))
        self._training.move_to_end(identity)
        return self._training[identity]

    def factor_correlations(self, pairs, candidates, min_overlap):
        """Aggregate training-only Pearson statistics; no values or labels leave this service."""
        results = []
        aggregation = self.profile["metric"].replace("spearman", "pearson")
        for left, right in pairs:
            lhs = self._training_values(left, candidates[left])
            rhs = self._training_values(right, candidates[right]).select(
                "row_id", pl.col("value").alias("target")
            )
            overlap = lhs.join(rhs, on="row_id", validate="1:1")
            result = metric(
                overlap,
                self.profile["metric"],
                "train",
                correlation="pearson",
                weights=self.weights.get("train"),
            )
            value = result.value if result.n_obs >= min_overlap else None
            results.append(FactorCorrelation(left, right, value, result.n_obs, aggregation))
        return tuple(results)

    def observation(self, reference: str) -> FactorValues:
        if reference in self._cache:
            values = self._cache[reference][1]
            return replace(values, frame=values.frame.clone())
        if self.cache_dir and reference in self._disk:
            path = self.cache_dir / f"{reference}.arrow"
            if path in self.disk_cache and path.exists():
                data = self.disk_cache.read(path)
                return FactorValues(self._disk[reference], self.snapshot_id, data, self.context_id)
        raise ValueError("observation reference is not available in this run")

    def _remember(self, reference, report, observation):
        if self.cache_dir:
            path = self.cache_dir / f"{reference}.arrow"
            self.disk_cache.write(path, observation.frame)
            self._disk[reference] = observation.expression_id
            self._disk = {
                ref: identity
                for ref, identity in self._disk.items()
                if self.cache_dir / f"{ref}.arrow" in self.disk_cache
            }
        self._cache[reference] = (report, observation)
        while (
            sum(v.frame.estimated_size() for _, v in self._cache.values()) > self.cache_bytes
            and len(self._cache) > 1
        ):
            self._cache.popitem(last=False)

    def evaluate(self, candidate: Candidate) -> EvaluationReport:
        started = time.perf_counter()
        try:
            compiled = compile_factor(
                candidate.expression, self.fields, self.registry, **self.compile_options
            )
        except (ValueError, TypeError) as exc:
            return EvaluationReport(
                None, (), None, None, time.perf_counter() - started, "compile_error", str(exc)
            )
        observation_id = fingerprint((compiled.factor_id, self.snapshot_id, self.context_id))
        evaluation_id = fingerprint(
            (observation_id, asdict(self.fold), self.profile, "ic-v4", self.diagnostics)
        )
        if evaluation_id in self._cache:
            report, _ = self._cache[evaluation_id]
            self._cache.move_to_end(evaluation_id)
            return replace(report, cache_hit=True, elapsed_seconds=time.perf_counter() - started)
        try:
            values = execute(compiled, self.features, self.fields, self.registry)
            keys = ["row_id", "trading_day", "exchange", "instrument_id", "eligible"]
            if self.diagnostics:
                keys += [k for k in ("timestamp", "segment_id") if k in self.features.columns]
            if "product" in self.features.columns:
                keys.append("product")
            panel = self.features.select(keys).join(self.targets, on="row_id", validate="1:1")
            if (
                values.height != self.features.height
                or values["row_id"].n_unique() != values.height
            ):
                raise ValueError("observation row alignment failure")
            if not values["row_id"].sort().equals(self.features["row_id"].sort()):
                raise ValueError("observation row identity mismatch")
            panel = panel.join(values, on="row_id", validate="1:1")
            train, valid = split_panel(panel, self.fold.train), split_panel(panel, self.fold.val)
            if self.retain_training:
                self._remember_training(compiled.factor_id, train)
            train_metrics = metrics(
                train, self.profile["metric"], "train", weights=self.weights.get("train")
            )
            train_ic = train_metrics[0]
            direction = None if train_ic.value is None else -1 if train_ic.value < 0 else 1
            raw_metrics = metrics(
                valid, self.profile["metric"], "val_raw", weights=self.weights.get("val")
            )
            directed_metrics = tuple(
                replace(
                    m,
                    split="val",
                    value=None if direction is None or m.value is None else direction * m.value,
                )
                for m in raw_metrics
            )
            directed = directed_metrics[0]
            coverage = (
                float(valid["value"].is_finite().fill_null(False).mean()) if valid.height else None
            )
            status = "success" if direction is not None else "undefined_train_metric"
            if direction is not None and directed.value is None:
                status = "undefined_val_metric"
            scored = pl.concat([train.select("value"), valid.select("value")]).filter(
                pl.col("value").is_finite().fill_null(False)
            )
            if scored.height and scored["value"].n_unique() == 1:
                status = "constant_factor"
            report = EvaluationReport(
                compiled.factor_id,
                tuple(
                    m
                    for group in zip(train_metrics, directed_metrics, raw_metrics, strict=True)
                    for m in group
                ),
                direction,
                coverage,
                time.perf_counter() - started,
                status,
                None if status == "success" else status,
                evaluation_id,
                False,
                evaluation_id,
                compiled.expression,
                search_diagnostics(
                    panel,
                    self.fold.train,
                    self.profile["metric"],
                    direction,
                    self.weights.get("train"),
                )
                if self.diagnostics
                else {},
            )
            observation = FactorValues(
                compiled.factor_id,
                self.snapshot_id,
                valid.select("row_id", "value").sort("row_id"),
                self.context_id,
            )
            self._remember(evaluation_id, report, observation)
            return report
        except (
            ValueError,
            RuntimeError,
            pl.exceptions.PolarsError,
            pl.exceptions.PanicException,
        ) as exc:
            return EvaluationReport(
                compiled.factor_id,
                (),
                None,
                None,
                time.perf_counter() - started,
                "compute_error",
                str(exc),
                evaluation_id=evaluation_id,
            )
