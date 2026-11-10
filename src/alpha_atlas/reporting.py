"""Read file evidence and compare only matching independent experiments."""

import html
import json
import math
import re
import statistics
import warnings
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import read_json
from alpha_atlas.storage import FORMAT_VERSION, RunStore
from alpha_atlas.terminal import _expression, metric_title


def _cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    return html.escape(str(value)).replace("|", "\\|").replace("\n", "<br>").replace("\r", "")


def _table(headers, rows) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows),
        "",
    ]


def write_report(directory: Path) -> Path:
    """Derive one Markdown report from committed JSON only. Caller holds the run lock."""
    spec = read_json(directory / "run.json")
    if spec.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported run format; old artifacts are not migrated")
    store = RunStore(directory)
    trials = store.trials()
    registrations = [read_json(p) for p in sorted((directory / "operators").glob("*.json"))]
    failures = (
        read_json(directory / "failures.json") if (directory / "failures.json").exists() else []
    )
    checkpoint = (
        read_json(directory / "checkpoint.json") if (directory / "checkpoint.json").exists() else {}
    )
    accepted = [t for t in trials if t["feedback"]["accepted"]]
    initial = spec.get("initial_library", {})
    initial_count = len(initial.get("members", []))
    used = sum(t["feedback"]["reason"] != "budget_rejected" for t in trials)
    elapsed = max(
        spec.get("total_seconds", 0),
        checkpoint.get("elapsed_seconds", 0),
        max((t["cumulative_seconds"] for t in trials), default=0),
    )
    fold, profile, warmup = spec["fold"], spec["profile"], spec.get("warmup", {})
    lines = [f"# 运行报告：{_cell(spec['run_id'])}", ""]
    lines += _table(
        ["项目", "记录"],
        [
            ("状态", spec["status"]),
            *(
                [("提前结束原因", spec["search_stop_reason"])]
                if spec.get("search_stop_reason")
                else []
            ),
            ("资产 / 股票池", f"{spec['asset']} / {spec['universe']}"),
            ("方法 / seed", f"{spec['method']} / {spec['seed']}"),
            ("实现", spec.get("implementation")),
            *(
                ([("数据 / 算子版本", spec["experiment_version"]["id"])])
                if "experiment_version" in spec
                else []
            ),
            ("数据快照", spec.get("snapshot_id")),
            ("频率", profile.get("frequency")),
            (
                "因子面板",
                "品种（跨主力合约）" if profile.get("panel") == "product" else "真实合约",
            ),
            ("fold", fold["id"]),
            ("train", f"{fold['train']['start']} ~ {fold['train']['end']}"),
            ("val", f"{fold['val']['start']} ~ {fold['val']['end']}"),
            ("允许字段", ", ".join(spec["fields"])),
            ("预热", f"{warmup['start']} ~ {warmup['end']}" if warmup else None),
            (
                "窗口规则",
                "以该运行版本的 DSL 规则为准；预热日期仅用于数据加载",
            ),
            (
                "目标",
                f"{profile['price_field']}，未来 {profile['target_horizon_bars']} Bar 对数收益",
            ),
            ("主指标", profile["metric"]),
            *(
                ([("上下文重置 trial", spec["context_reset_at_trial"])])
                if "context_reset_at_trial" in spec
                else []
            ),
        ],
    )
    lines += ["## 进度与成本", ""]
    lines += _table(
        ["项目", "数值"],
        [
            ("尝试预算", spec["attempts"]),
            ("已提交尝试", used),
            ("剩余尝试", max(0, spec["attempts"] - used)),
            ("trial 记录数（含预算拒绝）", len(trials)),
            *(
                [
                    ("初始成员", initial_count),
                    ("新增入库", len(accepted)),
                    ("库成员总数", initial_count + len(accepted)),
                    ("初始成员测量耗时（秒）", initial.get("elapsed_seconds", 0)),
                ]
                if initial_count
                else [("入库成员", len(accepted))]
            ),
            ("已记录活动时间（秒）", elapsed),
            ("已提交评估与准入耗时（秒）", sum(t["elapsed_seconds"] for t in trials)),
            ("已提交候选生成耗时（秒）", sum(t["generation_seconds"] for t in trials)),
            ("算子注册耗时（秒）", sum(r["feedback"]["elapsed_seconds"] for r in registrations)),
        ],
    )
    lines += [
        "耗时分项不相加为精确总成本；活动时间不含停机等待。强制退出未记录的尾部时间未知。",
        "",
    ]
    method_state = checkpoint.get("method_state", {})
    if spec.get("method") in {"annealing", "v1", "v1_async", "v2_sync", "v2_async", "v3_async"}:
        records = method_state.get("factor_trial_records", method_state.get("steps", []))
        lines += _table(["研究记录", "数量"], [("计划内因子尝试记录", len(records))])
    if "method_config" in spec:
        lines += ["## 搜索方法配置", ""]
        lines += _table(["参数", "值"], spec["method_config"].items())
    if spec.get("method") == "alphaschema" and method_state:
        history = method_state.get("history", [])
        lines += ["## AlphaSchema 计划搜索", ""]
        lines += _table(
            ["选择轮数", "已结束计划", "有测量奖励", "失败", "预算截断"],
            [
                (
                    method_state.get("round_index", 0),
                    len(history),
                    sum(p["status"] == "measured" for p in history),
                    sum(p["status"] == "failed" for p in history),
                    sum(p["status"] == "budget_partial" for p in history),
                )
            ],
        )
        lines += _table(
            ["轮次", "选择", "计划", "状态", "训练奖励", "最佳周期"],
            [
                (
                    p["round_index"] + 1,
                    p["selection_reason"],
                    p["plan"],
                    p["status"],
                    p["reward"],
                    p["best_period"],
                )
                for p in history
            ],
        )
    if "usage" in method_state:
        lines += _table(["模型调用 / token", "累计值"], method_state["usage"].items())
        lines += [
            "unknown_usage_requests 含失败、响应未落盘或供应商未返回完整用量的请求；"
            "token 是已记录用量，不代表精确费用。恢复可能重发响应尚未落盘的调用。",
            "",
        ]
    if "retrievals" in method_state:
        lines += _table(
            ["搜索状态", "数量"],
            [
                ("演化图节点", len(method_state["nodes"])),
                ("父因子检索轮次", len(method_state["retrievals"])),
                ("生成批次（含进行中/失败）", len(method_state["batches"])),
            ],
        )
    if "trees_started" in method_state:
        lines += _table(
            ["MCTS-LLM 搜索状态", "数量"],
            [
                ("已建立根节点", method_state["trees_started"]),
                ("公式节点", len(method_state["nodes"])),
                ("已提交搜索步骤", len(method_state["steps"])),
                ("当前树剩余局部扩展机会", method_state["tree_remaining"]),
            ],
        )
    pending = checkpoint.get("pending")
    for entry in pending if isinstance(pending, list) else [pending] if pending else []:
        factor_trial_index = entry.get("factor_trial_index", entry.get("trial_index"))
        state = "结果已提交，等待恢复反馈" if factor_trial_index <= len(trials) else "结果未提交"
        lines += [f"待完成 trial {factor_trial_index}：{state}。", ""]
    reasons = Counter(t["feedback"]["reason"] for t in trials)
    lines += ["## 评估与准入", ""]
    lines += (
        _table(["结果 / 原因", "次数"], sorted(reasons.items()))
        if reasons
        else ["暂无已提交 trial。", ""]
    )
    rules = spec["rules"]
    minimum = spec["profile"].get("min_train_val_ic")
    quality = (
        f"train、val 各自全区间的主指标 IC（训练方向）均 > {minimum}"
        if minimum is not None
        else f"正向 val IC ≥ {rules['min_abs_val_ic']}"
    )
    lines += [
        f"准入：{quality}；覆盖率 ≥ {rules['min_coverage']}；"
        f"查重共同有限行 ≥ {rules['min_corr_overlap']}；绝对 Spearman < {rules['max_abs_corr']}。",
        "",
    ]
    if "clip_quantiles" in rules:
        lower, upper = rules["clip_quantiles"]
        lines += [
            f"评分裁剪：因子值与标签分别使用 train 的 {100 * lower:g}%–{100 * upper:g}% "
            "线性分位边界；val 与 OOS 沿用冻结边界，不重新拟合。",
            "",
        ]
    if accepted:
        rows = []
        for trial in accepted:
            feedback = trial["feedback"]
            report = feedback["report"]
            for name in dict.fromkeys(m["name"] for m in report["metrics"]):
                values = {m["split"]: m["value"] for m in report["metrics"] if m["name"] == name}
                rows.append(
                    (
                        trial["factor_trial_index"],
                        _expression(feedback["candidate"]["expression"]),
                        metric_title(name),
                        values.get("train"),
                        report["direction"],
                        values.get("val_raw"),
                        values.get("val"),
                        report["coverage"],
                    )
                )
        lines += _table(
            [
                "trial",
                "入库公式",
                "指标",
                "train IC",
                "方向",
                "val 原始 IC",
                "val 正向 IC",
                "覆盖率",
            ],
            rows,
        )
        lines += ["入库进度：", ""]
        lines += _table(
            ["trial", "累计成员", "累计活动秒"],
            [
                (t["factor_trial_index"], t["library_size"], t["cumulative_seconds"])
                for t in accepted
            ],
        )
    else:
        lines += ["因子库为空。", ""]
    diagnostics = [
        (t["factor_trial_index"], t["feedback"]["report"].get("diagnostics", {}))
        for t in trials
        if t["feedback"].get("report")
    ]
    if any(d for _, d in diagnostics):
        lines += [
            "## 共享训练诊断",
            "",
            "仅为训练搜索反馈；不参与既有准入，不代表 OOS 或实际交易成本。",
            "",
        ]
        lines += _table(
            ["trial", "训练 ICIR", "有效日数", "换手", "单位", "有效转移数"],
            [
                (
                    i,
                    d.get("icir"),
                    d.get("icir_days"),
                    d.get("turnover"),
                    d.get("turnover_unit"),
                    d.get("turnover_observations"),
                )
                for i, d in diagnostics
                if d
            ],
        )
        if any("lag1_rank_ic" in d for _, d in diagnostics):
            lines += _table(
                [
                    "trial",
                    "原符号 RankIC",
                    "原符号 RankICIR",
                    "滞后一 Bar RankIC",
                    "正向衰减",
                    "滞后样本数",
                ],
                [
                    (
                        i,
                        d.get("rank_ic"),
                        d.get("rank_icir"),
                        d.get("lag1_rank_ic"),
                        d.get("lag1_gap"),
                        d.get("lag1_n_obs"),
                    )
                    for i, d in diagnostics
                    if "lag1_rank_ic" in d
                ],
            )
    regions = {}
    for trial in trials:
        for region in trial["feedback"]["candidate"].get("region_ids", []):
            counts = regions.setdefault(region, [0, 0])
            counts[0] += 1
            counts[1] += int(trial["feedback"]["accepted"])
    if regions:
        lines += ["区域统计（按 trial 标签统计，可重叠）：", ""]
        lines += _table(
            ["区域", "trial 数", "入库数"], [(k, *v) for k, v in sorted(regions.items())]
        )
    lines += ["## 算子注册", ""]
    if registrations:
        lines += _table(
            ["名称", "类型 / 作用域", "结果", "耗时秒", "错误"],
            [
                (
                    r["definition"]["name"],
                    f"{r['definition']['kind']} / {r['definition']['scope']}",
                    "通过" if r["feedback"]["accepted"] else "拒绝",
                    r["feedback"]["elapsed_seconds"],
                    r["feedback"]["error"],
                )
                for r in registrations
            ],
        )
    else:
        lines += ["本运行未提交新增算子。", ""]
    lines += ["## 中断与失败", ""]
    if failures:
        lines += _table(
            ["时间", "阶段", "trial", "类型", "信息"],
            [
                (
                    f["time"],
                    f["stage"],
                    f.get("factor_trial_index", f.get("trial_index")),
                    f["type"],
                    f["message"],
                )
                for f in failures
            ],
        )
    else:
        lines += ["没有已记录的运行异常。", ""]
    lines += ["## 独立 OOS", ""]
    oos_path = directory / "oos.json"
    if not oos_path.exists():
        oos_path = directory / "reports/oos.json"  # Read existing artifacts without migrating them.
    if not oos_path.exists() or spec["status"] != "frozen":
        lines += ["尚未生成可用 OOS 结果；报告不会触发 OOS 计算。", ""]
    else:
        oos = read_json(oos_path)
        if oos["results"]:
            lines += _table(
                ["因子 ID", "状态", "指标", "IC", "有效样本", "覆盖率", "错误"],
                [
                    (
                        r["expression_id"],
                        r["status"],
                        metric_title(m.get("name", "")),
                        m.get("value"),
                        m.get("n_obs"),
                        r.get("coverage"),
                        r.get("error"),
                    )
                    for r in oos["results"]
                    for m in r.get("metrics", [r.get("metric", {})])
                ],
            )
            lines += ["IC 为 — 表示未定义或计算失败；有效样本为 0 时无可评分样本。", ""]
        else:
            lines += ["OOS 已执行，冻结因子库为空。", ""]
        lines += ["OOS 是冻结方向下的 IC 审计，不代表可交易收益。", ""]
    lines += ["## 组合模型 Test", ""]
    model_test_path = directory / "model/test.json"
    if model_test_path.exists():
        tested = read_json(model_test_path)
        lines += _table(
            ["模型", "指标", "IC", "有效样本", "覆盖率"],
            [
                (
                    name,
                    metric_title(metric["name"]),
                    metric["value"],
                    metric["n_obs"],
                    model["coverage"],
                )
                for name, model in tested["reports"]["test"].items()
                for metric in model["metrics"]
            ],
        )
        lines += ["拟合与模型详情：[模型报告](model/report.md)。", ""]
    elif spec["status"] == "frozen" and not accepted and not initial_count:
        lines += ["冻结因子库为空，组合模型不适用。", ""]
    else:
        lines += ["尚未完成组合模型 Test；atlas finish 可补齐评估。", ""]
    similarity_path = directory / "library_similarity.json"
    lines += ["## Val 库内因子相似度", ""]
    if similarity_path.exists():
        similarity = read_json(similarity_path)
        lines += [
            f"窗口：{similarity['panel']}；共同有限行重新排名，所有不同因子对等权。",
            "",
        ]
        lines += _table(
            ["因子数", "有效 / 总配对", "均值", "中位数", "P90", "最大值", "≥ 0.7"],
            [
                (
                    similarity["factor_count"],
                    f"{similarity['valid_pairs']} / {similarity['pair_count']}",
                    similarity["mean_absolute_spearman"],
                    similarity["median_absolute_spearman"],
                    similarity["p90_absolute_spearman"],
                    similarity["max_absolute_spearman"],
                    similarity["pairs_at_least_0_7"],
                )
            ],
        )
        lines += ["完整结果：[library_similarity.json](library_similarity.json)。", ""]
    else:
        lines += ["尚未完成 Val 全库相似度；atlas finish 可补齐评估。", ""]
    lines += [
        "## 数据边界与记录",
        "",
        _cell(spec.get("continuity", "未记录连续性说明。")),
        "",
        "原始记录：[运行配置](run.json)、"
        f"[因子评估记录]({store.factor_trials_directory.name}/)、[算子记录](operators/)。",
        "报告仅汇总已落盘记录，不重新计算因子、评估或 OOS。",
        "",
    ]
    destination = directory / "report.md"
    temporary = destination.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(destination)
    return destination


def refresh_report(directory: Path) -> None:
    """A derived report must not undo a committed run or hide its original exception."""
    try:
        write_report(directory)
    except Exception as exc:
        warnings.warn(
            f"Report could not be refreshed ({type(exc).__name__}); "
            f"regenerate with: atlas report {directory}",
            RuntimeWarning,
            stacklevel=2,
        )


def _overview_row(text, heading, headers, row, identity, description):
    """Update one run's linked row, preserving handwritten rows and surrounding prose."""
    lines = text.splitlines()
    title = f"### {heading}"
    if title not in lines:
        section = "\n".join([title, "", description, "", *_table(headers, [row])]) + "\n"
        marker = "## 待开展实验"
        return (
            text.replace(marker, section + "\n" + marker, 1)
            if marker in text
            else text.rstrip() + "\n\n" + section
        )
    start = lines.index(title)
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"#{1,3} ", lines[i])), len(lines)
    )
    table = next((i for i in range(start + 1, end) if lines[i].startswith("| ")), None)
    if table is not None:
        displayed = lines[table].strip("| ").split(" | ")
        for name, label, error in (
            ("数据", "数据设置", "data"),
            ("Fold / seed", "Fold / seed", "fold/seed"),
        ):
            if name in headers and name not in displayed:
                column = headers.index(name)
                if f"**{label}**：{_cell(row[column])}。" not in lines[start + 1 : table]:
                    raise ValueError(f"overview common {error} setting changed: {heading}")
                headers = headers[:column] + headers[column + 1 :]
                row = row[:column] + row[column + 1 :]
        if headers[0] == "方法 / 模型" and any(
            line.startswith("**Fold / seed**：") for line in lines[start + 1 : table]
        ):
            label, fold_id, seed = row[0].rsplit(" / ", 2)
            if f"**Fold / seed**：{fold_id} / {seed}。" not in lines[start + 1 : table]:
                raise ValueError(f"overview common fold/seed setting changed: {heading}")
            row = [label, *row[1:]]
        optional = ("Linear 加权 Spearman", "LightGBM 加权 Spearman")
        if all(name in headers for name in optional) and not any(
            name in displayed for name in optional
        ):
            for name in optional:
                column = headers.index(name)
                headers = headers[:column] + headers[column + 1 :]
                row = row[:column] + row[column + 1 :]
    if table is None or lines[table] != "| " + " | ".join(headers) + " |":
        raise ValueError(f"overview table headers changed: {heading}")
    stop = table + 2
    while stop < end and lines[stop].startswith("| "):
        stop += 1
    matches = [i for i in range(table + 2, stop) if identity in lines[i]]
    if len(matches) > 1:
        raise ValueError("duplicate run rows in experiment overview")
    rendered = _table(headers, [row])[2]
    if matches:
        if headers[0] == "实验人" and row[0] is None:
            rendered = lines[matches[0]].partition(" | ")[0] + " | " + rendered.partition(" | ")[2]
        lines[matches[0]] = rendered
    else:
        lines.insert(stop, rendered)
        stop += 1
        end += 1
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    for i in range(start + 1, end):
        lines[i] = re.sub(
            r"截至 \d{4}-\d{2}-\d{2}，下表 \d+ 组结果",
            f"截至 {today}，下表 {stop - table - 2} 组结果",
            lines[i],
        )
        lines[i] = lines[i].replace("其余五组使用原生 5m 快照", "原生 5m 各组使用对应快照")
    return "\n".join(lines) + "\n"


def update_experiment_overview(root, directory, spec, oos, tested, similarity):
    """Legacy explicit publisher for complete, matching frozen-run outputs."""
    from alpha_atlas.checkpoint import run_lock

    identities = [item["expression_id"] for item in oos["results"]]
    count = spec["library_size"]
    if (
        len(identities) != count
        or len(set(identities)) != count
        or any(item["status"] != "success" for item in oos["results"])
        or similarity["factor_ids"] != identities
        or similarity["valid_pairs"] != count * (count - 1) // 2
        or (count and (tested is None or set(tested["reports"]["test"]) != {"linear", "lightgbm"}))
    ):
        raise ValueError(
            "all single-factor, model and similarity results are required before publishing"
        )
    futures = spec["profile"]["metric"].startswith("time_series_")
    primary = "time_series_weighted_pearson_ic" if futures else spec["profile"]["metric"]
    spearman = "time_series_weighted_spearman_ic" if futures else "cross_sectional_spearman"

    def value(report, name):
        return next(
            item["value"]
            for item in report["metrics"]
            if item["name"] == name and item["split"] == "test"
        )

    scores = [value(item, primary) for item in oos["results"]]
    finite = [score for score in scores if score is not None and math.isfinite(score)]
    secondary = [value(item, spearman) for item in oos["results"]]
    secondary = [score for score in secondary if score is not None and math.isfinite(score)]
    average = f"{statistics.mean(finite):.6f} / {statistics.median(finite):.6f}" if finite else "—"
    if len(finite) != count:
        average += f"，有效 {len(finite)} / {count}"
    secondary_average = (
        f"{statistics.mean(secondary):.6f} / {statistics.median(secondary):.6f}"
        if secondary
        else "—"
    )
    if len(secondary) != count:
        secondary_average += f"，有效 {len(secondary)} / {count}"
    positive = (
        f"{sum(score > 0 for score in finite)} / {len(finite)}，{sum(score > 0 for score in finite) / len(finite):.2%}"
        if finite
        else "—"
    )
    methods = {
        "alphaprobe": "AlphaPROBE",
        "alphaschema": "AlphaSchema",
        "react": "ReAct",
        "mcts_llm": "MCTS-LLM",
        "v1_async": "AlphaAtlas V1",
        "v2_sync": "AlphaAtlas V2",
        "v2_async": "AlphaAtlas V2",
        "v3_async": "AlphaAtlas V3",
        "random": "Random",
        "gp": "GP",
        "mcts": "UCT MCTS",
    }
    method = methods.get(spec["method"], spec["method"])
    relative = directory.resolve().relative_to(root.resolve(), walk_up=True).as_posix()
    identity = f"({relative}/report.md)"
    method_link = f"[{method}]{identity}"
    asset = spec["asset"]
    if asset.startswith("futures_kernel_"):
        fields = {4: "四", 8: "八"}.get(len(spec["fields"]), str(len(spec["fields"])))
        data = f"Kernel {fields}字段"
    else:
        data = f"原生 {spec['profile']['frequency']}" if asset == "futures" else asset
    clipping = spec["rules"].get("clip_quantiles")
    policy = f"{clipping[0] * 100:g}%–{clipping[1] * 100:g}%" if clipping else "无"
    data += f" / {spec['universe']} / 裁剪 {policy}"
    model = (spec.get("method_config") or {}).get("chat_model", "—")
    attempts = sum(
        trial["feedback"]["reason"] != "budget_rejected" for trial in RunStore(directory).trials()
    )
    combined = [
        value(tested["reports"]["test"][name], primary) if count else None
        for name in ("linear", "lightgbm")
    ]

    def number(item):
        return f"{item:.6f}" if item is not None else "—"

    scope = "期货" if futures else "A 股" if asset == "ashare" else asset
    main_headers = [
        "实验人",
        "方法",
        "数据",
        "模型",
        "Fold / seed",
        "尝试次数",
        "TEST 单因子加权 Pearson（均值 / 中位数）"
        if futures
        else "TEST 单因子主 IC（均值 / 中位数）",
        "TEST 单因子加权 Spearman（均值 / 中位数）" if futures else "单因子 Spearman 均值",
        "正 IC",
        "Linear 加权 Pearson" if futures else "Linear 主 IC",
        "LightGBM 加权 Pearson" if futures else "LightGBM 主 IC",
    ]
    main_row = [
        spec.get("researcher"),
        method_link,
        data,
        model,
        f"{spec['fold']['id']} / {spec['seed']}",
        attempts,
        average,
        secondary_average if futures else number(statistics.mean(secondary) if secondary else None),
        positive,
        *map(number, combined),
    ]
    if futures:
        main_headers += ["Linear 加权 Spearman", "LightGBM 加权 Spearman"]
        main_row += [
            number(value(tested["reports"]["test"][name], spearman) if count else None)
            for name in ("linear", "lightgbm")
        ]
    pair_headers = [
        "方法 / 模型",
        "数据",
        "尝试次数",
        "因子数",
        "因子对数",
        "平均绝对 Spearman",
        "中位数",
        "P90",
        "最大值",
        "≥ 0.7 的对数",
    ]
    pair_row = [
        f"{method_link} / {model} / {spec['fold']['id']} / {spec['seed']}",
        data,
        attempts,
        count,
        similarity["pair_count"],
        *[
            number(similarity[key])
            for key in (
                "mean_absolute_spearman",
                "median_absolute_spearman",
                "p90_absolute_spearman",
                "max_absolute_spearman",
            )
        ],
        similarity["pairs_at_least_0_7"],
    ]
    panel = similarity["panel"]
    fold = spec["fold"]
    main_heading = (
        f"{scope}因子搜索"
        if futures and fold["id"] == "fold1"
        else f"{scope}因子搜索：{fold['id']}"
    )
    pair_heading = (
        "期货库内因子相似度：统一 product 窗口"
        if futures and panel == "product" and fold["id"] == "fold1"
        else f"{scope}库内因子相似度：{fold['id']} / {panel} 窗口"
    )
    periods = "；".join(
        f"{name}：{fold[key]['start']}–{fold[key]['end']}"
        for name, key in (("Train", "train"), ("Val", "val"), ("Test", "test"))
    )
    # Reuse the existing single-writer lock for the shared derived Markdown document.
    with run_lock(root / "artifacts"):
        path = root / "experiment_overview.md"
        text = path.read_text(encoding="utf-8") if path.exists() else "# 实验总览\n"
        text = _overview_row(text, main_heading, main_headers, main_row, identity, periods + "。")
        text = _overview_row(
            text,
            pair_heading,
            pair_headers,
            pair_row,
            identity,
            f"Val：{fold['val']['start']}–{fold['val']['end']}；共同有限观测重新排名，全部配对等权。",
        )
        if "## 待开展实验" in text:
            before, pending = text.split("## 待开展实验", 1)
            lines, current_fold = [], None
            for line in pending.splitlines():
                if line.startswith("### "):
                    current_fold = line[4:].strip().lower()
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if (
                    current_fold == spec["fold"]["id"].lower()
                    and len(cells) == 4
                    and cells[:2] == [scope, methods.get(spec["method"], spec["method"])]
                ):
                    continue
                lines.append(line)
            text = before + "## 待开展实验" + "\n".join(lines) + "\n"
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return path


def write_model_report(directory: Path, fitted: dict, tested: dict | None = None):
    """Report downstream predictive IC separately from single-factor and trading results."""
    spec = fitted["spec"]
    preprocessing = fitted["fitted"]["preprocessing"]
    clipping = preprocessing.get("clip_quantiles")
    preprocessing_note = (
        f"特征与训练标签按拟合区间全局 P{clipping[0] * 100:g}–"
        f"P{clipping[1] * 100:g} 裁剪；特征以裁剪后的均值和标准差处理，test 标签保持原值。"
        if clipping is not None
        else "特征与标签使用原值；特征按拟合区间均值和标准差处理。"
    )
    centering_note = (
        "Linear 对特征和标签按品种在整个拟合区间去均值，并冻结预测偏移；"
        "LightGBM 使用全局预处理特征和按品种去均值的标签。"
        if preprocessing.get("product_demean")
        else "模型使用混合样本拟合，不做品种去均值。"
    )
    lines = [
        "# 因子库预测模型",
        "",
        f"搜索运行：{spec['search_run_id']}。主指标：{spec['primary_metric']}。",
        "",
        "这是独立的下游模型实验，不回写搜索结果，不是交易收益回测。",
        (
            "linear 为品种固定效应最小二乘回归；LightGBM 使用固定参数，无自动调参或早停。"
            if preprocessing.get("product_demean")
            else "linear 为带截距的最小二乘回归；LightGBM 使用固定参数，无自动调参或早停。"
        ),
        "模型与预处理在 train+val 合并集拟合；test 仅用于最终冻结后汇报。",
        "test 仅使用保存的模型，不重新训练、选因子或翻转预测方向。",
        "",
        f"模型拟合与搜索启动的源码记录一致：{'是' if spec['source_matches_search'] else '否'}。"
        "源码记录仅用于来源说明，不校验当前源码。",
        f"输入因子 {len(fitted['factor_ids'])} 个，有效列 {len(preprocessing['columns'])} 个，"
        f"训练行 {fitted['fitted']['training_rows']}。",
        "",
        "缺失值按训练均值填补；训练常量／全缺失列移除；所有有效因子均缺失的行不预测。",
        preprocessing_note,
        centering_note,
        "训练目标按样本等权拟合，指标聚合沿用资产主指标；两者权重口径不同。",
        "",
        "| 模型 | split | 指标 | IC | 覆盖率 | 预测行数 / 可评估行数 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    reports = {**fitted["reports"], **(tested["reports"] if tested else {})}
    for split, models in reports.items():
        for name, report in models.items():
            coverage = f"{report['coverage']:.2%}" if report["coverage"] is not None else "不可定义"
            for metric in report["metrics"]:
                value = f"{metric['value']:+.6f}" if metric["value"] is not None else "不可定义"
                lines.append(
                    f"| {name} | {split} | {metric['name']} | {value} | {coverage} | "
                    f"{report['predicted_rows']} / {report['eligible_rows']} |"
                )
    lines += ["", "## 因子列", "", "| 列 | 因子 ID | 使用 |", "| --- | --- | --- |"]
    for index, identity in enumerate(fitted["factor_ids"]):
        name = f"factor_{index}"
        lines.append(
            f"| {name} | {identity} | {'是' if name in preprocessing['columns'] else '否'} |"
        )
    lines += ["", "模型、预处理和配置：[fit.json](fit.json)。"]
    if tested:
        lines.append("独立测试指标：[test.json](test.json)。")
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare(root: Path) -> dict:
    rows, unsupported = [], []
    for path in sorted((root / "artifacts/runs").glob("*/run.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("format_version") != FORMAT_VERSION:
            unsupported.append({"run_dir": str(path.parent), "reason": "unsupported legacy format"})
            continue
        if spec["status"] != "frozen":
            continue
        trials = RunStore(path.parent).trials()
        checkpoint_path = path.parent / "checkpoint.json"
        model_usage = (
            read_json(checkpoint_path).get("method_state", {}).get("usage")
            if "method_config" in spec and checkpoint_path.exists()
            else None
        )
        reasons = {}
        used, curve = 0, []
        for trial in trials:
            reason = trial["feedback"]["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1
            used += int(reason != "budget_rejected")
            curve.append(
                {
                    "attempt": used,
                    "factor_trial_index": trial["factor_trial_index"],
                    "count": trial["library_size"]
                    - len(spec.get("initial_library", {}).get("members", [])),
                    "seconds": trial["cumulative_seconds"],
                }
            )
        rows.append(
            {
                **{
                    key: spec[key]
                    for key in (
                        "run_id",
                        "asset",
                        "universe",
                        "method",
                        "seed",
                        "snapshot_id",
                        "fields",
                        "rules",
                        "profile",
                        "total_seconds",
                    )
                },
                "fold": spec["fold"]["id"],
                "implementation": spec.get("implementation"),
                "method_config": spec.get("method_config"),
                "experiment_version": spec.get("experiment_version", {}).get("id"),
                "model_usage": model_usage,
                "attempts": used,
                "trial_records": len(trials),
                "accepted": spec["library_size"]
                - len(spec.get("initial_library", {}).get("members", [])),
                "initial_members": len(spec.get("initial_library", {}).get("members", [])),
                "reasons": reasons,
                "curve": curve,
            }
        )
    output = {
        "runs": rows,
        "unsupported": unsupported,
        "note": "Compare matching asset/universe/fold/snapshot/fields/rules/profile and disclose method "
        "search spaces, initial seeds and model costs; no combined winner.",
    }
    atomic_json(root / "artifacts/comparison.json", output)
    return output
