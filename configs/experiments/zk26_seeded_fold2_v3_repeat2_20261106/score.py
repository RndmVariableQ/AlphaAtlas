"""Score the two frozen V3 repeats using the existing Val-end B26 protocol."""

import argparse
import importlib.util
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from alpha_atlas.evaluation import paired_block_bootstrap, paired_prediction_metrics

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).resolve().parent
OUTPUT = ROOT / "artifacts/comparisons" / CONFIG.name
ARCHIVE = ROOT / "docs/records/zookeeper26_fold2/v3_repeat2_20261106"
PRIOR = ROOT / "docs/records/zookeeper26_fold2/val_end_products_20261106"
BASE = ROOT / "artifacts/comparisons/zk26_lgbm_defaults_20261106"
KEYS = ["row_id", "trading_day", "product", "target"]
helper_path = ROOT / "artifacts/comparisons/zk26_haiku_v3_val_end_products_20261106/evaluate.py"
helper_spec = importlib.util.spec_from_file_location("b26_val_end", helper_path)
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)
read, write = helper.read, helper.write


def evaluate(group):
    cohort, target = read(PRIOR / "cohort.json"), read(BASE / "transformed_target.json")
    expected = read(PRIOR / "summary.json")
    assert set(cohort["products"]) <= set(target["product_clipped_label_means"])
    folder = OUTPUT / group
    fit_bytes, test_bytes = (folder / "fit.json").read_bytes(), (folder / "test.json").read_bytes()
    fit, test = json.loads(fit_bytes), json.loads(test_bytes)
    assert fit["spec"] == test["spec"]
    assert fit["fitted"]["training_rows"] == 5296605
    source = next(s for s in fit["spec"]["sources"] if s["method"] == "v3_async")
    assert source["snapshot_id"] == cohort["source_snapshot"]
    run = read(ROOT / source["directory"] / "run.json")
    assert run["status"] == "frozen" and run["attempts"] == 300
    for arm in fit["fitted"]["arms"].values():
        if "reuse" in arm:
            continue
        text = arm["models"]["lightgbm"]["model_text"]
        assert "[min_data_in_leaf: 20]" in text and "[lambda_l2: 0]" in text
        np.testing.assert_allclose(arm["preprocessing"]["label_bounds"], target["label_bounds"])
    score_path = folder / "scores.json"
    if score_path.exists():
        rows = read(score_path)
        assert {(r["budget"], r["model"]) for r in rows} == {
            (budget, model) for budget in (200, 300) for model in ("linear", "lightgbm")
        }
        assert all(
            r["run_id"] == source["run_id"]
            and r["source_test"] == str((folder / "test.json").relative_to(ROOT))
            and r["ic_products"] == expected["ic_products"]
            for r in rows
        )
        print(f"Reused {group} scores for the saved frozen models and Test predictions", flush=True)
        return rows
    reference_test = read(BASE / "haiku_v3/test.json")
    reference = pl.read_parquet(
        ROOT / reference_test["predictions"],
        columns=[*KEYS, "baseline_linear", "baseline_lightgbm"],
    )
    frame = pl.read_parquet(ROOT / test["predictions"])
    assert frame.select(KEYS).equals(reference.select(KEYS))
    for model in ("linear", "lightgbm"):
        np.testing.assert_allclose(
            frame[f"baseline_{model}"], reference[f"baseline_{model}"], rtol=1e-12, atol=1e-15
        )
    frame = frame.filter(pl.col("product").is_in(cohort["products"]))
    weights = pl.DataFrame(test["results"][0]["test"]["per_product"]).select("product", "weight")
    weights = weights.filter(pl.col("product").is_in(cohort["products"]))
    assert weights.height == 74
    results = []
    for row in test["results"]:
        assert row["budget_reached"] and row["actual_attempts"] == row["budget"]
        model, arm = row["model"], row["arm"]
        raw = frame.select(
            *KEYS,
            pl.col(f"baseline_{model}").alias("baseline"),
            pl.col(f"{arm}_{model}").alias("prediction"),
        )
        adjusted = helper.transformed(raw, target)
        point, adjusted_point = (paired_prediction_metrics(f, weights) for f in (raw, adjusted))
        assert point["ic_products"] == expected["ic_products"]
        assert point["ic_rows"] == 2326761 and point["weighted_rows"] == 2348430
        for metric, key in (("pearson", "ic"), ("spearman", "spearman_ic")):
            np.testing.assert_allclose(
                point["metrics"][f"weighted_{metric}"]["baseline"],
                expected["baseline"][model][key],
                rtol=0,
                atol=1e-12,
            )
        raw_boot = helper.boot(raw, weights)
        adjusted_boot = helper.boot(adjusted, weights)
        rank_boot = paired_block_bootstrap(raw, weights, correlation="spearman")
        assert rank_boot["delta_weighted_spearman"]["valid_replicates"] == 2000
        helper.check_numpy(raw, weights, point)
        helper.check_numpy(adjusted, weights, adjusted_point)
        results.append(
            {
                "cohort": "Haiku" if group == "haiku" else "Luna",
                "run_id": source["run_id"],
                "budget": row["budget"],
                "model": model,
                "admitted_count": row["admitted_count"],
                "total_columns": len(row["columns"]),
                "retained_columns": row["retained_columns"],
                "ic": point["metrics"]["weighted_pearson"],
                "ic_interval_95": raw_boot["delta_weighted_pearson"]["interval_95"],
                "spearman_ic": point["metrics"]["weighted_spearman"],
                "spearman_ic_interval_95": rank_boot["delta_weighted_spearman"]["interval_95"],
                "ic_products": point["ic_products"],
                "ic_rows": point["ic_rows"],
                "raw_wmse": helper.wmse(point, raw_boot["relative_mse_reduction"]),
                "transformed_wmse": helper.wmse(
                    adjusted_point, adjusted_boot["relative_mse_reduction"]
                ),
                "bootstrap": {"block_days": 20, "repeats": 2000, "seed": 20261102},
                "source_test": str((folder / "test.json").relative_to(ROOT)),
                "per_product": point["per_product"],
            }
        )
        print(f"Scored {group} {row['budget']} {model}", flush=True)
    assert fit_bytes == (folder / "fit.json").read_bytes()
    assert test_bytes == (folder / "test.json").read_bytes()
    write(folder / "scores.json", results)
    return results


def n(value):
    return f"{value:+.6f}".replace("-", "−")


def cells(linear, light):
    values = []
    for row in (linear, light):
        for key in ("ic", "spearman_ic"):
            interval = ", ".join(n(v) for v in row[f"{key}_interval_95"])
            values.append(f"{n(row[key]['delta'])} [{interval}]")
    kind = "raw_wmse" if linear["cohort"] == "Luna" else "transformed_wmse"
    values.extend(f"{n(row[kind]['relative_reduction'] * 100)}%" for row in (linear, light))
    return values


def publish(results):
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    lookup = {(r["cohort"], r["budget"], r["model"]): r for r in results}
    cohorts = [name for name in ("Luna", "Haiku") if (name, 300, "linear") in lookup]
    for group in ("haiku", "luna"):
        if group.title() not in cohorts:
            continue
        destination = ARCHIVE / group
        destination.mkdir(exist_ok=True)
        for name in ("fit.json", "test.json", "factors.jsonl", "results.jsonl", "scores.json"):
            shutil.copyfile(OUTPUT / group / name, destination / name)
        shutil.copyfile(CONFIG / f"{group}.json", destination / "scope.json")
    write(ARCHIVE / "scores.json", results)
    shutil.copyfile(CONFIG / "plan.json", ARCHIVE / "plan.json")
    report = [
        "# B26 AlphaAtlas V3：Haiku 5.5 / Luna 第二次运行",
        "",
        f"已完成评估：{'、'.join(cohorts)}。其余组完成后补入本报告。"
        if len(cohorts) < 2
        else "Haiku 5.5、Luna 两组均已完成评估。",
        "",
        "两组独立从 B26 重新搜索；Fold2、seed 42、各 300 次新增尝试，保留 200 次前缀。",
        "初始库、方法源码、提示词与并发沿用各自首轮。下游 Linear OLS；LightGBM 默认叶样本数 20、L2=0。",
        "Val 末固定名单：IC 共同 73 品种、2,326,761 行，WMSE 74 品种、2,348,430 行。",
        "Δ 均相对同模型同样本的 B26；方括号为 95% 区间，固定品种 √成交额权重。",
        "区间按年分层、全市场同步 20 交易日块、2,000 次配对重采样，seed 20261102；Spearman 每次重新排名。",
        "Luna 报告原始目标 WMSE，Haiku 报告变换后目标 WMSE；完整数据保留两者。",
        "",
        "| 模型后端 / 运行 | 次数 | 新增 / 总成员 / 入模列数 | Linear ΔPearson IC | Linear ΔSpearman IC | LightGBM ΔPearson IC | LightGBM ΔSpearman IC | Linear WMSE 下降 | LightGBM WMSE 下降 |",
        "| --- | ---: | ---: | --- | --- | --- | --- | ---: | ---: |",
    ]
    for cohort in ("Luna", "Haiku"):
        if cohort not in cohorts:
            continue
        for budget in (200, 300):
            linear, light = (lookup[cohort, budget, model] for model in ("linear", "lightgbm"))
            size = f"{light['admitted_count']} / {light['total_columns']} / {light['retained_columns']}"
            report.append(
                f"| {cohort} / `{light['run_id'][-10:]}` | {budget} | {size} | "
                + " | ".join(cells(linear, light))
                + " |"
            )
    report += [
        "",
        "用户指定的同 seed 重复，不是全方法多 seed 比较。历史 Test 已被查看，结果不反馈搜索。",
        "Luna 按用户要求独立冻结、拟合后先评估；其结果不进入 Haiku 搜索。",
        "[全部指标和区间](scores.json)、[实验计划](plan.json)、",
        f"[运行入口](../../../../configs/experiments/{CONFIG.name}/README.md)。",
        "",
    ]
    (ARCHIVE / "report.md").write_text("\n".join(report))
    records = ROOT / "docs/records/experiments.md"
    heading = f"## 2026-11-06 B26 AlphaAtlas V3：{'、'.join(cohorts)} 第二次运行完成"
    if heading not in records.read_text():
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        with records.open("a") as stream:
            stream.write(
                f"\n\n{heading}\n\n完成时间：{stamp}。已评估 {'、'.join(cohorts)}，"
                "各 300 次，沿用 seed 42、初始 B26、原搜索配置和源码。"
                "\n各组在自身搜索和配对模型冻结后读取历史 Test；Luna 按用户要求先评估，结果不回传 Haiku。"
                "\n分别报告 73 品种 Pearson／Spearman ΔIC 及区间，"
                "保留 74 品种 WMSE。两种 WMSE 已用逐行权重独立复核；模型与原始 Test 产物保持不变。"
                "\n属于用户指定的历史诊断重复，不据此筛选因子或调整参数。"
                "\n\n[两组完整结果](zookeeper26_fold2/v3_repeat2_20261106/report.md)。\n"
            )
    print(ARCHIVE / "report.md", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("haiku", "luna"), action="append")
    args = parser.parse_args()
    selected = args.group or ("haiku", "luna")
    results = [row for group in selected for row in evaluate(group)]
    for group in ("haiku", "luna"):
        if group not in selected and (OUTPUT / group / "scores.json").exists():
            results.extend(evaluate(group))
    publish(results)
