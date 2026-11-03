"""Freeze and evaluate all 26 adapted Zookeeper factors with the shared Atlas protocol."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import time
import uuid
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import numpy as np

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.zookeeper import ZookeeperMarketData
from alpha_atlas.checkpoint import config_fingerprint, read_json
from alpha_atlas.config import asset_config, load_fold, load_toml
from alpha_atlas.contracts import DateRange, Expression
from alpha_atlas.evaluation import (
    EvaluationService,
    apply_clipping,
    build_targets,
    metrics,
    product_weights,
    split_panel,
)
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.factor_libraries.zookeeper import VERSION, candidates, definitions
from alpha_atlas.library import FactorLibrary
from alpha_atlas.runner import check_config, source_fingerprint
from alpha_atlas.storage import FORMAT_VERSION, RunStore

ASSET = "futures_kernel_5m_full"


def freeze(root: Path, fold_id: str):
    started = time.perf_counter()
    profile = asset_config(root, ASSET)
    fold = load_fold(root, fold_id)
    rules = load_toml(root / "configs/benchmark.toml")
    fields = [row["prepared_field"] for row in definitions()]
    directory = (
        root
        / "artifacts/reference-evaluations"
        / (f"zookeeper26-{fold_id}-{uuid.uuid4().hex[:10]}")
    )
    directory.mkdir(parents=True)
    derived_data = root / "data/zookeeper" / directory.name
    print(f"OUTPUT={directory}\nPreparing train/val only, through {fold.val.end}", flush=True)
    with ZookeeperMarketData(root) as market:
        snapshot = market.snapshot_id()
        provenance = market.provenance()
        features = market.load_features(fields=fields, start=fold.train.start, end=fold.val.end)
        market.export_monthly(derived_data / "monthly_train_val.parq")
    spec = {
        "format_version": FORMAT_VERSION,
        "run_id": directory.name,
        "asset": ASSET,
        "universe": "all",
        "fold": asdict(fold),
        "profile": profile,
        "rules": rules,
        "fields": fields,
        "reference": {"version": VERSION, "definitions": definitions()},
        "source_fingerprint": source_fingerprint(),
        "snapshot_id": snapshot,
        "data_provenance": provenance,
        "derived_data_dir": str(derived_data.relative_to(root)),
        "warmup": {"policy": "all_available_history_for_fixed_calendar_factors", **provenance},
        "implementation": "zookeeper_fixed_audit_v1",
        "status": "running",
        "scope": "all_26_fixed_definitions; admission does not select OOS",
    }
    spec["config_fingerprint"] = config_fingerprint(spec)
    atomic_json(directory / "run.json", spec)
    options = {
        "max_nodes": rules["max_expression_nodes"],
        "max_depth": rules["max_expression_depth"],
    }
    evaluator = EvaluationService(
        features,
        fold,
        profile,
        set(fields),
        snapshot_id=snapshot,
        context={"run_id": directory.name},
        compile_options=options,
        retain_training=True,
        training_cache_factors=1,
        cache_bytes=0,
        clip_quantiles=rules.get("clip_quantiles"),
    )
    library = FactorLibrary(
        rules,
        min_train_val_ic=profile.get("min_train_val_ic"),
        snapshot_id=snapshot,
        context_id=evaluator.context_id,
        directory=directory / "cache/members",
    )
    store = RunStore(directory)
    archive = []
    for index, candidate in enumerate(candidates(), 1):
        step = time.perf_counter()
        compiled = compile_factor(candidate.expression, set(fields), **options)
        report = evaluator.evaluate(candidate)
        if report.status in {"compile_error", "compute_error"}:
            raise RuntimeError(f"{candidate.name}: {report.failure_reason}")
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

        feedback = library.consider(
            candidate, report, values, factor_trial_index=index, commit=commit
        )
        train = evaluator._training_values(compiled.factor_id, candidate)
        archive.append(
            {
                "candidate": asdict(candidate),
                "compiled": asdict(compiled),
                "report": asdict(report),
                "train_coverage": train["value"].is_finite().fill_null(False).mean(),
                "train_eligible_rows": train.height,
                "admission_reason": feedback.reason,
            }
        )
        print(
            f"Train/val {index}/26 {candidate.name}: {report.status}, {feedback.reason}, "
            f"{time.perf_counter() - step:.1f}s",
            flush=True,
        )
    frozen_path = directory / "frozen/library.json"
    atomic_json(
        frozen_path,
        {"format_version": FORMAT_VERSION, "definitions": definitions(), "factors": archive},
    )
    spec.update(
        status="frozen",
        library_size=len(archive),
        total_seconds=time.perf_counter() - started,
        library_sha256=hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
    )
    atomic_json(directory / "run.json", spec)
    print(f"Frozen all 26 definitions, train directions and clipping: {directory}", flush=True)
    return directory


def test(root: Path, directory: Path):
    spec = read_json(directory / "run.json")
    fold, profile = check_config(root, spec)
    if spec["status"] != "frozen":
        raise ValueError("test requires a frozen catalogue")
    frozen_path = directory / "frozen/library.json"
    if hashlib.sha256(frozen_path.read_bytes()).hexdigest() != spec["library_sha256"]:
        raise ValueError("frozen library was modified")
    frozen = read_json(frozen_path)
    if frozen["definitions"] != list(definitions()):
        raise ValueError("factor definitions changed after freeze")
    with ZookeeperMarketData(root) as market:
        if market.snapshot_id() != spec["snapshot_id"]:
            raise ValueError("source snapshots or preparation version changed after freeze")
        print(f"Frozen. Preparing OOS through {fold.test.end}", flush=True)
        features = market.load_features(
            fields=spec["fields"], start=fold.test.start, end=fold.test.end
        )
        market.export_monthly(root / spec["derived_data_dir"] / "monthly_through_test.parq")
    targets = build_targets(features, profile["price_field"], profile["target_horizon_bars"])
    panel = features.select("row_id", "trading_day", "eligible", "product").join(
        targets, on="row_id", validate="1:1"
    )
    periods = {"test": fold.test} | {
        str(year): DateRange(
            max(date(year, 1, 1), fold.test.start), min(date(year, 12, 31), fold.test.end)
        )
        for year in range(fold.test.start.year, fold.test.end.year + 1)
    }
    weights = {name: product_weights(features, period) for name, period in periods.items()}
    oos = {"run_id": directory.name, "results": []}
    for index, item in enumerate(frozen["factors"], 1):
        step = time.perf_counter()
        compiled = compile_factor(
            Expression.from_dict(item["compiled"]["expression"]), set(spec["fields"])
        )
        if compiled.factor_id != item["compiled"]["factor_id"]:
            raise ValueError("frozen expression identity mismatch")
        report = item["report"]
        if bool(report["clipping"]) != (spec["rules"].get("clip_quantiles") is not None):
            raise ValueError("frozen clipping policy mismatch")
        values = execute(compiled, features, set(spec["fields"]), panel=profile["panel"])
        joined = panel.join(values, on="row_id", validate="1:1")
        result = {
            "name": item["candidate"]["name"],
            "expression_id": compiled.factor_id,
            "by_year": {},
        }
        for name, period in periods.items():
            observed = split_panel(joined, period)
            coverage = observed["value"].is_finite().fill_null(False).mean()
            observed = apply_clipping(observed, report["clipping"])
            raw = metrics(observed, profile["metric"], name + "_raw", weights=weights[name])
            direction = report["direction"]
            directed = tuple(
                replace(
                    m,
                    split=name,
                    value=None if direction is None or m.value is None else direction * m.value,
                )
                for m in raw
            )
            scored = {
                "metrics": [asdict(m) for m in (*directed, *raw)],
                "coverage": coverage,
                "eligible_rows": observed.height,
                "status": "success" if directed[0].value is not None else "undefined_metric",
            }
            if name == "test":
                result.update(scored)
            else:
                result["by_year"][name] = scored
        oos["results"].append(result)
        atomic_json(directory / "oos.json", oos)
        print(
            f"Test {index}/26 {result['name']}: {result['status']}, {time.perf_counter() - step:.1f}s",
            flush=True,
        )
    write_summary(directory)
    print(f"REPORT={directory / 'report.md'}", flush=True)
    return directory


def write_summary(directory):
    spec = read_json(directory / "run.json")
    archive = read_json(directory / "frozen/library.json")["factors"]
    oos = read_json(directory / "oos.json")
    primary = spec["profile"]["metric"]
    records = []
    for item, result in zip(archive, oos["results"], strict=True):
        report = item["report"]
        directed_train = [
            {
                **m,
                "split": "train",
                "value": None
                if m["value"] is None or report["direction"] is None
                else m["value"] * report["direction"],
            }
            for m in report["metrics"]
            if m["split"] == "train"
        ]
        records.append(
            {
                "name": result["name"],
                "version": VERSION,
                "expression_id": result["expression_id"],
                "direction": report["direction"],
                "train": directed_train,
                "val": [m for m in report["metrics"] if m["split"] == "val"],
                "test": [m for m in result["metrics"] if m["split"] == "test"],
                "train_coverage": item["train_coverage"],
                "val_coverage": report["coverage"],
                "test_coverage": result["coverage"],
                "admission_reason": item["admission_reason"],
                "by_year": result["by_year"],
                "clipping": report["clipping"],
            }
        )
    (directory / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in records),
        encoding="utf-8",
    )

    def score(row, split):
        return next((m["value"] for m in row[split] if m["name"] == primary), None)

    def num(value):
        return "—" if value is None else f"{value:+.6f}"

    def pct(value):
        return "—" if value is None else f"{value:.1%}"

    scores = [score(r, "test") for r in records if score(r, "test") is not None]
    summary = {
        "factors": 26,
        "test_defined": len(scores),
        "positive_test_ic": sum(v > 0 for v in scores),
        "test_mean": float(np.mean(scores)) if scores else None,
        "test_median": float(np.median(scores)) if scores else None,
        "admitted": sum(r["admission_reason"] == "accepted" for r in records),
    }
    atomic_json(directory / "summary.json", summary)
    lines = [
        "# Zookeeper 26/37 固定目录诊断 / " + spec["fold"]["id"],
        "",
        "固定目录的中国期货代理适配，非论文海外市场月度策略绩效复现。",
        "月末信号下月使用；评价目标保持未来 12 根 5m adj_close 对数收益，同一真实合约连续段。",
        "主指标为品种内时序 Pearson，再按该 split 的 √成交额加权；同时保存四种 IC。",
        "方向及 1%–99% 裁剪边界仅由 train 拟合；val/test 沿用。全 26 项冻结后均测 test，未按准入筛选。",
        "全历史预热支持 60 月窗口；与旧实验一年预热不同。含股指/国债的 all 品种集合保持当前平台口径。",
        "",
    ]
    for split in ("train", "val", "test"):
        period = spec["fold"][split]
        lines.append(f"- {split}: {period['start']} ~ {period['end']}")
    lines += [
        "",
        f"有效 test：{len(scores)}/26；正 IC：{summary['positive_test_ic']}；均值 {num(summary['test_mean'])}；中位数 {num(summary['test_median'])}；train/val 准入 {summary['admitted']}/26。",
        "",
        "| 因子 | train方向 | train IC | val IC | test IC | train覆盖 | val覆盖 | test覆盖 | 准入结果 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in records:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    str(row["direction"]),
                    *(num(score(row, s)) for s in ("train", "val", "test")),
                    *(pct(row[s + "_coverage"]) for s in ("train", "val", "test")),
                    row["admission_reason"],
                ]
            )
            + " |"
        )
    lines += [
        "",
        "覆盖率分母为 split 内 eligible 且目标有效的行；缺失不填补。未定义 IC 不是 0。",
        "八字段来自既有 Kernel 1m→5m 派生数据；日 VWAP 是结算价代理。9 个期限结构及 BGR 另用原生远月数据。",
        "远月 manifest 仍为 incomplete（2 个已记录辅助 Bar 缺失）；本次显式使用现有观测并保留空值，未改写源清单。",
        "远月用主力及其后两档未复权收盘；各期限收益只在同一合约相邻交易日计算，换腿日不当作收益。",
        "缺失、上市年限、长窗口及残差设计矩阵秩不足会降低覆盖率。26 个定义不等于 26 个可准入因子。",
        "这些是预测 IC，未测交易收益、交易成本或与挖掘因子组合后的增量收益。test 已查看，后续开发不能继续把它当盲测。",
        "",
        "逐年 OOS、四种 IC、样本数、裁剪边界见 results.jsonl；原始未定向 IC 见 frozen/library.json、oos.json。",
        f"准备后数据身份：`{spec['snapshot_id']}`；定义版本：`{VERSION}`。",
        "",
    ]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fold", choices=["fold1", "fold2"], default="fold2")
    parser.add_argument("--stage", choices=["all", "freeze", "test"], default="all")
    parser.add_argument(
        "--run", type=Path, help="existing frozen directory, required for --stage test"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.stage == "test" and args.run is None:
        parser.error("--stage test requires --run")
    directory = args.run if args.stage == "test" else freeze(root, args.fold)
    gc.collect()
    if args.stage != "freeze":
        test(root, directory.resolve())
