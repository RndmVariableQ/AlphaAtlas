"""Complete a frozen experiment using recorded bounds and saved downstream models."""

import hashlib
import itertools
import math
import statistics
from dataclasses import asdict
from datetime import date

import numpy as np
import polars as pl

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import read_json, run_lock
from alpha_atlas.config import load_toml
from alpha_atlas.contracts import fingerprint
from alpha_atlas.evaluation import FUTURES_METRICS, product_weights, split_panel
from alpha_atlas.history import warmup_period
from alpha_atlas.model import evaluate_models, factor_panel
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.reporting import (
    refresh_report,
    terminal_progress,
    update_experiment_overview,
    write_model_report,
)
from alpha_atlas.runner import (
    _check_config,
    _data_fields,
    _frozen_factor_result,
    _frozen_scores,
    _runtime,
    run_model,
)


def _raw_factor(scored, item, index, columns):
    # factor_panel stores oriented values; frozen clipping bounds describe original values.
    return scored.drop(columns).with_columns(
        (scored[f"factor_{index}"] * item["report"]["direction"]).alias("value")
    )


def _similarity(spec, frozen, panel, fold):
    scored = split_panel(panel, fold.val)
    validation_rows = scored.height
    columns = [f"factor_{index}" for index in range(len(frozen["factors"]))]
    values = pl.DataFrame(
        [
            _frozen_scores(spec, item, _raw_factor(scored, item, index, columns))["value"].rename(
                columns[index]
            )
            for index, item in enumerate(frozen["factors"])
        ]
    )
    del scored
    pairs = []
    total = len(columns) * (len(columns) - 1) // 2
    for batch in itertools.batched(itertools.combinations(columns, 2), 8, strict=False):
        expressions = []
        for index, (left, right) in enumerate(batch):
            a, b = pl.col(left), pl.col(right)
            common = (a.is_finite() & b.is_finite()).fill_null(False)
            expressions.extend(
                [
                    common.sum().alias(f"n{index}"),
                    pl.corr(a.filter(common), b.filter(common), method="spearman").alias(
                        f"r{index}"
                    ),
                ]
            )
        measured = values.select(expressions).row(0, named=True)
        for index, (left, right) in enumerate(batch):
            overlap, corr = measured[f"n{index}"], measured[f"r{index}"]
            if (
                overlap < spec["rules"]["min_corr_overlap"]
                or corr is None
                or not math.isfinite(corr)
            ):
                raise ValueError(f"incomplete Val similarity: {left}/{right}, overlap={overlap}")
            pairs.append(
                {
                    "left": left,
                    "right": right,
                    "overlap": overlap,
                    "absolute_spearman": min(abs(corr), 1.0),
                }
            )
        if len(pairs) % 80 == 0 or len(pairs) == total:
            terminal_progress("Val 库内相似度", 配对=f"{len(pairs)}/{total}")
    absolute = [pair["absolute_spearman"] for pair in pairs]
    return {
        "run_id": spec["run_id"],
        "config_fingerprint": spec["config_fingerprint"],
        "library_sha256": spec["library_sha256"],
        "snapshot_id": spec["snapshot_id"],
        "factor_ids": [item["compiled"]["factor_id"] for item in frozen["factors"]],
        "factor_count": len(columns),
        "panel": spec["profile"].get("panel", "contract"),
        "split": asdict(fold.val),
        "clip_quantiles": spec["rules"].get("clip_quantiles"),
        "validation_rows": validation_rows,
        "pair_count": total,
        "valid_pairs": len(pairs),
        "mean_absolute_spearman": statistics.mean(absolute) if absolute else None,
        "median_absolute_spearman": statistics.median(absolute) if absolute else None,
        "p90_absolute_spearman": float(np.quantile(absolute, 0.9)) if absolute else None,
        "max_absolute_spearman": max(absolute, default=None),
        "pairs_at_least_0_7": sum(value >= 0.7 for value in absolute),
        "min_pair_overlap": min((pair["overlap"] for pair in pairs), default=None),
        "pairs": pairs,
    }


def finish_experiment(root, directory):
    """Fit on Train/Val, evaluate all three outputs, then publish both overview rows."""
    initial = read_json(directory / "run.json")
    if initial["status"] != "frozen":
        raise ValueError("experiment completion requires a frozen run")
    _check_config(root, initial)
    # The public model entrypoint validates saved models and never refits an existing fit.
    # Its lock is released before taking the completion lock; no nested run locks.
    if initial["library_size"]:
        run_model(root, directory)
    with run_lock(directory):
        try:
            return _finish_experiment(root, directory)
        except BaseException as exc:
            terminal_progress(
                "实验收尾中断或失败 / 未填表",
                类型=type(exc).__name__,
                恢复=f"atlas finish {directory}",
            )
            raise
        finally:
            refresh_report(directory)


def _finish_experiment(root, directory):
    spec = read_json(directory / "run.json")
    if spec["status"] != "frozen":
        raise ValueError("experiment completion requires a frozen run")
    fold, profile = _check_config(root, spec)
    frozen_path = directory / "frozen/library.json"
    if hashlib.sha256(frozen_path.read_bytes()).hexdigest() != spec["library_sha256"]:
        raise ValueError("frozen library was modified")
    frozen = read_json(frozen_path)
    identities = [item["compiled"]["factor_id"] for item in frozen["factors"]]
    if len(identities) != spec["library_size"] or len(set(identities)) != len(identities):
        raise ValueError("frozen library membership mismatch")
    if warmup_period(fold.train.start) != spec["warmup"]:
        raise ValueError("frozen warmup budget changed")
    registry = OperatorRegistry(runtime=_runtime(root, frozen["runtime"]))
    registry.restore(frozen["operators"])
    oos_path, model_path, similarity_path = (
        directory / name for name in ("oos.json", "model/test.json", "library_similarity.json")
    )
    oos = read_json(oos_path) if oos_path.exists() else None
    tested = read_json(model_path) if model_path.exists() else None
    similarity = read_json(similarity_path) if similarity_path.exists() else None
    report_id = fingerprint(
        (spec["library_sha256"], spec["config_fingerprint"], spec["source_fingerprint"])
    )
    if oos is not None and (
        oos.get("run_id") != spec["run_id"]
        or oos.get("report_id") != report_id
        or [item["expression_id"] for item in oos["results"]] != identities
    ):
        raise ValueError("cached OOS report fingerprint or membership mismatch")
    fitted = read_json(directory / "model/fit.json") if identities else None
    if fitted is not None and (
        fitted["factor_ids"] != identities
        or fitted["spec"]["config"] != load_toml(root / "configs/model.toml")
    ):
        raise ValueError("saved model input or configuration changed")
    if tested is not None and (fitted is None or tested["spec"] != fitted["spec"]):
        raise ValueError("saved model test specification changed")
    if tested is not None and set(tested["reports"]["test"]) != {"linear", "lightgbm"}:
        raise ValueError("combined model Test is incomplete")
    if similarity is not None:
        expected = {
            "run_id": spec["run_id"],
            "config_fingerprint": spec["config_fingerprint"],
            "library_sha256": spec["library_sha256"],
            "snapshot_id": spec["snapshot_id"],
            "factor_ids": identities,
            "panel": profile.get("panel", "contract"),
            "split": {key: str(value) for key, value in asdict(fold.val).items()},
            "clip_quantiles": spec["rules"].get("clip_quantiles"),
        }
        if any(similarity.get(key) != value for key, value in expected.items()):
            raise ValueError("cached similarity input changed")
        count = len(identities) * (len(identities) - 1) // 2
        if (
            similarity["factor_count"] != len(identities)
            or similarity["pair_count"] != count
            or similarity["valid_pairs"] != count
            or len(similarity["pairs"]) != count
        ):
            raise ValueError("cached similarity is incomplete")
    need_oos = oos is None or any(item["status"] != "success" for item in oos["results"])
    need_model = bool(identities) and tested is None
    with ParqMarketData(
        root / profile["data_dir"], spec["universe"], allow_incomplete=spec["allow_incomplete"]
    ) as market:
        if market.snapshot_id() != spec["snapshot_id"]:
            raise ValueError("dataset changed after search")
        if need_oos or need_model or similarity is None:
            terminal_progress("实验收尾 / 加载行情", 运行=spec["run_id"])
            features = market.load_features(
                fields=_data_fields(spec["fields"], profile),
                start=date.fromisoformat(spec["warmup"]["start"]),
                end=fold.test.end if need_oos or need_model else fold.val.end,
            )
            panel, actual = factor_panel(features, frozen, spec, registry)
            if actual != identities:
                raise ValueError("frozen factor order changed")
            columns = [f"factor_{index}" for index in range(len(identities))]
            if need_oos:
                scored = split_panel(panel, fold.test)
                weights = (
                    product_weights(features, fold.test)
                    if profile["metric"] in FUTURES_METRICS
                    else None
                )
                previous = {item["expression_id"]: item for item in oos["results"]} if oos else {}
                results = []
                for index, item in enumerate(frozen["factors"]):
                    result = previous.get(identities[index])
                    if result is None or result["status"] != "success":
                        result = _frozen_factor_result(
                            spec, item, _raw_factor(scored, item, index, columns), weights
                        )
                    results.append(result)
                oos = {
                    "run_id": spec["run_id"],
                    "report_id": report_id,
                    "results": results,
                    "note": "Frozen library IC audit, not executable trading returns.",
                }
                atomic_json(oos_path, oos)
                del scored
            if need_model:
                tested = {
                    "spec": fitted["spec"],
                    "reports": {
                        "test": evaluate_models(
                            panel,
                            fitted["fitted"],
                            profile,
                            features,
                            fold.test,
                            "test",
                            fitted["spec"]["config"],
                        )
                    },
                }
                atomic_json(model_path, tested)
            if similarity is None:
                validation = split_panel(panel, fold.val)
                # The quadratic pair stage only needs Val, after all Test work has finished.
                del panel, features
                similarity = _similarity(spec, frozen, validation, fold)
                atomic_json(similarity_path, similarity)
    if identities:
        if set(tested["reports"]["test"]) != {"linear", "lightgbm"}:
            raise ValueError("combined model Test is incomplete")
        write_model_report(directory / "model", fitted, tested)
    overview = update_experiment_overview(root, directory, spec, oos, tested, similarity)
    terminal_progress("实验收尾完成 / 已填表", 总表=overview)
    return {"report": str(directory / "report.md"), "overview": str(overview)}
