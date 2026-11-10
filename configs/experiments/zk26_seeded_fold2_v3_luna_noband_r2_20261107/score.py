"""Score Luna no-band r2 with the validated repeat-2 evaluator and archive its results."""

import importlib.util
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).resolve().parent
OUTPUT = ROOT / "artifacts/comparisons" / CONFIG.name
ARCHIVE = ROOT / "docs/records/zookeeper26_fold2/v3_luna_noband_r2_20261107"
prior_path = ROOT / "configs/experiments/zk26_seeded_fold2_v3_repeat2_20261106/score.py"
prior_spec = importlib.util.spec_from_file_location("b26_repeat2_score", prior_path)
prior = importlib.util.module_from_spec(prior_spec)
prior_spec.loader.exec_module(prior)
prior.CONFIG, prior.OUTPUT = CONFIG, OUTPUT


def publish(results):
    lookup = {(row["budget"], row["model"]): row for row in results}
    assert len(results) == 4 and set(lookup) == {
        (budget, model) for budget in (200, 300) for model in ("linear", "lightgbm")
    }
    assert all(row["cohort"] == "Luna" for row in results)
    destination = ARCHIVE / "luna"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("fit.json", "test.json", "factors.jsonl", "results.jsonl", "scores.json"):
        shutil.copyfile(OUTPUT / "luna" / name, destination / name)
    shutil.copyfile(CONFIG / "luna.json", destination / "scope.json")
    shutil.copyfile(CONFIG / "plan.json", ARCHIVE / "plan.json")
    prior.write(ARCHIVE / "scores.json", results)
    report = [
        "# B26 AlphaAtlas V3 不分档：Luna 第二次运行",
        "",
        "独立从 B26 开始；Fold2、seed 42、300 次新增尝试，保留 200 次前缀。",
        "搜索取消回看跨度分档（scale_bands=none），其余配置与前三次 V3 相同。自身搜索冻结、配对模型保存后立即评估。",
        "Linear OLS；LightGBM 默认叶样本数 20、L2=0，其余参数沿用原配对版本。",
        "Pearson、Spearman IC 均为 Val 末固定名单的共同 73 品种、2,326,761 行；",
        "WMSE 为 74 品种、2,348,430 行。Δ 相对同模型同样本 B26，品种 √成交额权重固定。",
        "95% 区间使用按年分层、全市场同步 20 交易日块，2,000 次配对重采样、seed 20261102。",
        "Spearman 每次抽样后在品种内重新排名；表内 WMSE 沿用 Luna 的原始目标。",
        "",
        "| 次数 | 新增 / 总成员 / 入模列数 | Linear ΔPearson IC | Linear ΔSpearman IC | LightGBM ΔPearson IC | LightGBM ΔSpearman IC | Linear WMSE 下降 | LightGBM WMSE 下降 |",
        "| ---: | ---: | --- | --- | --- | --- | ---: | ---: |",
    ]
    for budget in (200, 300):
        linear, light = (lookup[budget, model] for model in ("linear", "lightgbm"))
        size = f"{light['admitted_count']} / {light['total_columns']} / {light['retained_columns']}"
        report.append(f"| {budget} | {size} | " + " | ".join(prior.cells(linear, light)) + " |")
    linear, light = (lookup[300, model] for model in ("linear", "lightgbm"))
    report += [
        "",
        f"运行：`{light['run_id']}`。",
        "用户指定的同 seed 独立调用；历史 Test 已查看，结果不反馈搜索。",
        "区块区间不包含搜索随机性。完整结果同时保留原始、变换后目标 WMSE 及区间。",
        "[完整指标](scores.json)、[计划](plan.json)、",
        f"[执行与恢复](../../../../configs/experiments/{CONFIG.name}/README.md)。",
        "",
    ]
    (ARCHIVE / "report.md").write_text("\n".join(report))
    records = ROOT / "docs/records/experiments.md"
    heading = "## 2026-11-07 B26 AlphaAtlas V3 不分档：Luna 第二次运行完成"
    if heading not in records.read_text():
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        with records.open("a") as stream:
            stream.write(
                f"\n\n{heading}\n\n完成时间：{stamp}。运行 `{light['run_id']}`，"
                f"300 次尝试，新增 {light['admitted_count']} 个因子。"
                "\n取消回看跨度分档，其余沿用原搜索配置、B26 和 seed 42；自身搜索与配对模型冻结后评估并保存结果。"
                "\n73 品种加权 Pearson／Spearman ΔIC、95% 区间；保留 74 品种 WMSE。"
                "\n两种 WMSE 已独立数值复核。属于历史诊断重复，不据此选择因子或调整参数。"
                "\n\n[完整结果](zookeeper26_fold2/v3_luna_noband_r2_20261107/report.md)。\n"
            )
    print(ARCHIVE / "report.md", flush=True)


if __name__ == "__main__":
    publish(prior.evaluate("luna"))
