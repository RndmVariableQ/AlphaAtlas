"""Audit a fixed CTA catalogue on train/val, freeze it, then measure every factor on test."""

from __future__ import annotations

import argparse
import gc
import hashlib
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

import polars as pl

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import config_fingerprint, read_json
from alpha_atlas.config import asset_config, load_fold, load_toml
from alpha_atlas.contracts import Expression
from alpha_atlas.evaluation import (
    EvaluationService,
    build_targets,
    metrics,
    product_weights,
    split_panel,
)
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.factor_libraries import load_library
from alpha_atlas.history import one_year_before, warmup_period
from alpha_atlas.library import FactorLibrary
from alpha_atlas.runner import _check_config, source_fingerprint
from alpha_atlas.storage import FORMAT_VERSION, RunStore


def test_scores(features, panel, compiled, profile, fold, weights, direction):
    """Use the same platform metrics; preserve raw test IC when train direction is undefined."""
    values = execute(compiled, features, set(compiled.fields))
    observed = split_panel(panel.join(values, on="row_id", validate="1:1"), fold.test)
    raw = metrics(observed, profile["metric"], "test_raw", weights=weights)
    directed = tuple(
        replace(
            m,
            split="test",
            value=None if direction is None or m.value is None else direction * m.value,
        )
        for m in raw
    )
    return {
        "metrics": [asdict(m) for m in (*directed, *raw)],
        "coverage": observed["value"].is_finite().fill_null(False).mean()
        if observed.height
        else None,
        "eligible_rows": observed.height,
        "status": "success" if raw[0].value is not None else "undefined_test_metric",
    }


def write_summary(directory):
    spec, frozen = read_json(directory / "run.json"), read_json(directory / "frozen/library.json")
    oos = read_json(directory / "oos.json")
    results = {r["expression_id"]: r for r in oos["results"]}
    primary = spec["profile"]["metric"]

    def number(value):
        return "—" if value is None else f"{value:+.6f}"

    def percent(value):
        return "—" if value is None else f"{value:.1%}"

    lines = [
        "# Futures CTA 固定因子评估 / " + spec["fold"]["id"],
        "",
        f"原生 5m，名义每日 {spec['reference']['bars_per_day']} 根；目标：未来 12 根 Bar 对数收益。",
        "主指标为按品种计算的 Pearson IC，再按区间 √成交额加权汇总。",
        "train 原始 IC 决定方向，表内 train/val/test 均按该方向统一调整。未重新按 val/test 选方向。",
        "全部可计算定义在 test 前冻结；test 不按 train/val 入库结果筛选。",
        "入库栏是按定义顺序执行原有 train/val 质量、覆盖和相关性门槛的结果。",
        "",
    ]
    for split in ("train", "val", "test"):
        period = spec["fold"][split]
        lines.append(f"- {split}: {period['start']} ~ {period['end']}")
    lines += [
        "",
        "| 因子 | train 方向 | train IC | val IC | test IC | train覆盖 | val覆盖 | test覆盖 | 入库 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in frozen["factors"]:
        report = item["report"]
        test = results[item["compiled"]["factor_id"]]
        measured = {m["split"]: m["value"] for m in report["metrics"] if m["name"] == primary}
        test_ic = next(
            (
                m["value"]
                for m in test.get("metrics", [])
                if m["name"] == primary and m["split"] == "test"
            ),
            None,
        )
        direction = report["direction"]
        train = measured.get("train")
        train = None if train is None or direction is None else train * direction
        row = [
            item["candidate"]["name"],
            str(direction) if direction is not None else "—",
            number(train),
            number(measured.get("val")),
            number(test_ic),
            percent(item["train_coverage"]),
            percent(report["coverage"]),
            percent(test.get("coverage")),
            item["admission_reason"],
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## 缺少输入，未计算", "", "| 因子 | 缺少字段 |", "|---|---|"]
    for entry in spec["availability"]:
        if entry["missing_fields"]:
            lines.append(f"| {entry['name']} | {', '.join(entry['missing_fields'])} |")
    lines += [
        "",
        "四种 IC（Pearson/Spearman × 品种等权/√成交额加权）、原始及定向结果、有效样本数/品种数",
        "见 frozen/library.json 和 oos.json。train/val 原始结果分别为 train、val_raw；",
        "test 原始结果为 test_raw。— 表示无法计算，不代表 IC 为 0。",
        "",
        "数据源：RiceQuant 原生 5m，真实合约且只保留主力日期；不拼接合约或重新进入后的连续段。",
        "预热从 train 开始前一年加载。名义日窗口不是精确交易日对齐，SMA 交叉是源 EMA 的适配。",
        "覆盖率分母为该区间 eligible 且目标有效的行；IC 仅由因子也有效的行计算。",
        "长窗口、换月和已知数据间断会降低覆盖率；未建立完整日内时段表，不能识别所有缺 Bar。",
        "没有组合预测增益、收益率或交易成本回测；test 结果只用于这次冻结评估。",
        "",
        f"数据快照：`{spec['snapshot_id']}`。固定定义版本：`{spec['reference']['version']}`。",
    ]
    destination = directory / "report.md"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def evaluate(root: Path, fold_id: str, bars_per_day: int, *, asset: str = "futures"):
    started = time.perf_counter()
    profile = asset_config(root, asset)
    fold = load_fold(root, fold_id)
    rules = load_toml(root / "configs/benchmark.toml")
    reference = load_library(
        "futures_cta", frequency=profile["frequency"], bars_per_day=bars_per_day
    )
    with ParqMarketData(root / profile["data_dir"], "all") as market:
        candidates = reference.candidates(market.columns)
        availability = reference.inspect(market.columns)
        fields = sorted(
            {f for c in candidates for f in compile_factor(c.expression, market.columns).fields}
        )
        snapshot_id = market.snapshot_id()
        print(
            f"Load train/val: {len(candidates)}/22 definitions, bars_per_day={bars_per_day}",
            flush=True,
        )
        features = market.load_features(
            fields=[*fields, "amount"], start=one_year_before(fold.train.start), end=fold.val.end
        )
    directory = (
        root
        / "artifacts/reference-evaluations"
        / f"{asset}-cta-{fold_id}-bpd{bars_per_day}-{uuid.uuid4().hex[:10]}"
    )
    directory.mkdir(parents=True)
    spec = {
        "format_version": FORMAT_VERSION,
        "run_id": directory.name,
        "asset": asset,
        "universe": "all",
        "fold": asdict(fold),
        "profile": profile,
        "rules": rules,
        "fields": fields,
        "reference": asdict(reference),
        "availability": availability,
        "source_fingerprint": source_fingerprint(),
        "snapshot_id": snapshot_id,
        "warmup": warmup_period(fold.train.start),
        "implementation": "fixed_reference_audit_v1",
        "status": "running",
        "scope": "all_available_definitions; admission does not select OOS",
    }
    spec["config_fingerprint"] = config_fingerprint(spec)
    atomic_json(directory / "run.json", spec)
    print(f"OUTPUT={directory}\nTrain/val rows={features.height:,}", flush=True)
    options = {
        "max_nodes": rules["max_expression_nodes"],
        "max_depth": rules["max_expression_depth"],
    }
    evaluator = EvaluationService(
        features,
        fold,
        profile,
        set(fields),
        snapshot_id=snapshot_id,
        context={"run_id": directory.name},
        compile_options=options,
        retain_training=True,
        cache_bytes=0,
    )
    library = FactorLibrary(
        rules,
        min_train_val_ic=profile.get("min_train_val_ic"),
        snapshot_id=snapshot_id,
        context_id=evaluator.context_id,
        directory=directory / "cache/members",
    )
    store = RunStore(directory)
    archive = []
    for index, candidate in enumerate(candidates, 1):
        step = time.perf_counter()
        compiled = compile_factor(candidate.expression, set(fields), **options)
        report = evaluator.evaluate(candidate)
        values = evaluator.observation(report.observation_ref) if report.observation_ref else None

        def commit(feedback, index=index, step=step, compiled=compiled):
            store.record(
                index,
                feedback,
                time.perf_counter() - step,
                0,
                time.perf_counter() - started,
                feedback.library_version,
                compiled=compiled,
            )

        feedback = library.consider(candidate, report, values, trial_index=index, commit=commit)
        train = evaluator._training_values(compiled.factor_id, candidate)
        archive.append(
            {
                "candidate": asdict(candidate),
                "compiled": asdict(compiled),
                "report": asdict(report),
                "train_coverage": train["value"].is_finite().fill_null(False).mean()
                if train.height
                else None,
                "train_eligible_rows": train.height,
                "admission_reason": feedback.reason,
            }
        )
        print(
            f"Train/val {index}/{len(candidates)} {candidate.name}: "
            f"{report.status}, {feedback.reason}, {time.perf_counter() - step:.1f}s",
            flush=True,
        )
    frozen_path = directory / "frozen/library.json"
    atomic_json(
        frozen_path, {"format_version": FORMAT_VERSION, "scope": spec["scope"], "factors": archive}
    )
    spec.update(
        status="frozen",
        library_size=len(archive),
        total_seconds=time.perf_counter() - started,
        library_sha256=hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
    )
    atomic_json(directory / "run.json", spec)
    del evaluator, library, features, train, values
    gc.collect()
    # Reuse the platform's existing config/source/snapshot and frozen-file checks before test.
    spec = read_json(directory / "run.json")
    fold, profile = _check_config(root, spec)
    if hashlib.sha256(frozen_path.read_bytes()).hexdigest() != spec["library_sha256"]:
        raise ValueError("frozen library was modified")
    frozen = read_json(frozen_path)
    with ParqMarketData(root / profile["data_dir"], "all") as market:
        if market.snapshot_id() != snapshot_id:
            raise ValueError("dataset changed after freeze")
        print("Frozen. Load test with original warmup history.", flush=True)
        features = market.load_features(
            fields=[*fields, "amount"], start=one_year_before(fold.train.start), end=fold.test.end
        )
    targets = build_targets(features, profile["price_field"], profile["target_horizon_bars"])
    panel = features.select("row_id", "trading_day", "eligible", "product").join(
        targets, on="row_id", validate="1:1"
    )
    weights = product_weights(features, fold.test)
    oos = {"run_id": directory.name, "results": []}
    for index, item in enumerate(frozen["factors"], 1):
        step = time.perf_counter()
        compiled = compile_factor(
            Expression.from_dict(item["compiled"]["expression"]), set(fields), **options
        )
        if compiled.factor_id != item["compiled"]["factor_id"]:
            raise ValueError("frozen expression identity mismatch")
        try:
            result = test_scores(
                features, panel, compiled, profile, fold, weights, item["report"]["direction"]
            )
        except (ValueError, RuntimeError, pl.exceptions.PolarsError) as exc:
            result = {"status": "compute_error", "error": str(exc)}
        oos["results"].append(
            {"name": item["candidate"]["name"], "expression_id": compiled.factor_id, **result}
        )
        atomic_json(directory / "oos.json", oos)
        print(
            f"Test {index}/{len(archive)} {item['candidate']['name']}: "
            f"{result['status']}, {time.perf_counter() - step:.1f}s",
            flush=True,
        )
    destination = write_summary(directory)
    print(f"REPORT={destination}\nTotal: {time.perf_counter() - started:.1f}s", flush=True)
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fold", choices=["fold1", "fold2"], required=True)
    parser.add_argument("--bars-per-day", type=int, required=True)
    parser.add_argument("--asset", choices=["futures", "futures_curve"], default="futures")
    args = parser.parse_args()
    evaluate(args.root.resolve(), args.fold, args.bars_per_day, asset=args.asset)
