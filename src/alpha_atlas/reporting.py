"""Read file evidence and compare only matching independent experiments."""

import html
import json
import logging
import sys
import unicodedata
import warnings
from collections import Counter
from pathlib import Path

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import read_json
from alpha_atlas.storage import FORMAT_VERSION, RunStore


def _display_width(text):
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
        for c in text
    )


def _terminal_wrap(value, width, *, preserve_spaces=False):
    text = "—" if value is None else str(value)
    text = (
        "".join(" " if c.isspace() else c for c in text)
        if preserve_spaces
        else " ".join(text.split())
    )
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
    lines, line = [], ""
    for char in text:
        if _display_width(line + char) > width:
            lines.append(line)
            line = ""
        line += char
    return [*lines, line]


def _terminal_grid(rows, widths):
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    lines = [border]
    for row in rows:
        wrapped = [_terminal_wrap(value, width) for value, width in zip(row, widths, strict=True)]
        for index in range(max(map(len, wrapped))):
            cells = [parts[index] if index < len(parts) else "" for parts in wrapped]
            lines.append(
                "| "
                + " | ".join(
                    cell + " " * (width - _display_width(cell))
                    for cell, width in zip(cells, widths, strict=True)
                )
                + " |"
            )
        lines.append(border)
    return "\n".join(lines)


def terminal_progress(title, **values):
    """Compact progress lines on stderr; no terminal state or file writes."""
    logger = logging.getLogger("alpha_atlas.progress")
    if logger.isEnabledFor(logging.INFO):
        details = "  ·  ".join(
            f"{key}={value if value is not None else '—'}" for key, value in values.items()
        )
        text = f"[{title}]" + (f" {details}" if details else "")
        logger.info("\n".join("  " + line for line in _terminal_wrap(text, 77)))


class TerminalStream:
    """Print provider deltas on stderr, including Qwen think tags split across chunks."""

    def __init__(self, *, summary=False):
        self.summary = summary
        self.channel = None
        self.thinking = False
        self.buffer = ""
        self.column = 0

    def __enter__(self):
        return self

    def _write(self, channel, text):
        if not logging.getLogger("alpha_atlas.progress").isEnabledFor(logging.INFO):
            return
        text = "".join(
            c if c != "\t" else " "
            for c in text
            if c in "\n\t" or not unicodedata.category(c).startswith("C")
        )
        if not text.strip() and self.channel != channel:
            return
        output = []
        if self.channel != channel:
            title = "模型思考 · 流式" if channel == "reasoning" else "模型输出 · 流式"
            if self.summary:
                title = "摘要 / " + title
            output.append(f"\n  [{title}]\n")
            self.channel, self.column = channel, 0
        for char in text:
            width = _display_width(char)
            if char == "\n" or self.column + width > 75:
                output.append("\n")
                self.column = 0
                if char == "\n":
                    continue
            if not self.column:
                output.append("    ")
            output.append(char)
            self.column += width
        sys.stderr.write("".join(output))
        sys.stderr.flush()

    def delta(self, channel, text):
        if channel == "restart":
            self.__exit__(None, None, None)
            self.channel = None
            self.thinking = False
            self.buffer = ""
            self.column = 0
            return
        if channel == "reasoning":
            self._write(channel, text)
            return
        self.buffer += text
        tags = ("<think>", "</think>")
        while self.buffer:
            found = [(self.buffer.find(tag), tag) for tag in tags if tag in self.buffer]
            if found:
                index, tag = min(found)
                self._write("reasoning" if self.thinking else "content", self.buffer[:index])
                self.thinking = tag == "<think>"
                self.buffer = self.buffer[index + len(tag) :]
                continue
            # Hold only a possible partial tag; everything else is visible immediately.
            tail = max(
                (n for tag in tags for n in range(1, len(tag)) if self.buffer.endswith(tag[:n])),
                default=0,
            )
            end = len(self.buffer) - tail
            self._write("reasoning" if self.thinking else "content", self.buffer[:end])
            self.buffer = self.buffer[end:]
            break

    def __exit__(self, exc_type, exc, traceback):
        if self.buffer:
            self._write("reasoning" if self.thinking else "content", self.buffer)
        if self.channel is not None:
            sys.stderr.write("\n\n")
            sys.stderr.flush()
        if exc_type is not None:
            terminal_progress("模型请求未完成", 状态="本次未获得完整回复")


def terminal_evaluation(candidate, *, index, limit):
    terminal_progress("评估候选", 进度=f"{index}/{limit}", 名称=candidate.name or "未命名")
    expression = _expression(
        candidate.expression
        if isinstance(candidate.expression, str)
        else candidate.expression.to_dict()
    )
    logger = logging.getLogger("alpha_atlas.progress")
    logger.info(
        "  表达式：\n%s",
        "\n".join(
            "    " + part
            for line in expression.splitlines()
            for part in _terminal_wrap(line, 75, preserve_spaces=True)
        ),
    )


def terminal_candidate(feedback, *, index, limit, elapsed, total):
    """Aligned results followed by admission, only after the trial has been committed."""
    logger = logging.getLogger("alpha_atlas.progress")
    if not logger.isEnabledFor(logging.INFO):
        return
    report = feedback.report
    status = "已入库" if feedback.accepted else "未入库"
    lines = [f"[候选完成 {index}/{limit}]  {status}  ·  库内 {feedback.library_version}"]
    coverage = f"{report.coverage:.2%}" if report.coverage is not None else "—"
    direction = f"{report.direction:+d}" if report.direction is not None else "—"
    missing = report.coverage is None or report.direction is None
    lines.append(
        f"覆盖率 {coverage}  ·  方向 {direction}  ·  评估 {elapsed:.2f}s  ·  累计 {total:.2f}s"
    )
    if not feedback.accepted:
        lines.append(f"原因：{feedback.reason}")
    names = tuple(dict.fromkeys(m.name for m in report.metrics if m.split in {"train", "val"}))
    if names:
        rows = [("指标", "训练 IC（原始）", "验证 IC（定向）")]
        for index, name in enumerate(names):
            measured = {m.split: m.value for m in report.metrics if m.name == name}
            label = metric_title(name) + (" *" if index == 0 else "")
            numbers = [
                f"{measured[s]:+.6f}" if measured.get(s) is not None else "—"
                for s in ("train", "val")
            ]
            missing |= "—" in numbers
            rows.append((label, *numbers))
        lines += [""] + [
            "".join(
                cell + " " * max(0, width - _display_width(cell))
                for cell, width in zip(row, (30, 18, 18), strict=True)
            )
            for row in rows
        ]
        lines.append(f"* 主指标：{names[0]}")
    if missing:
        lines.append("— 表示无法计算")
    if feedback.accepted:
        identity = (report.expression_id or "")[:12]
        candidate = feedback.candidate
        name = candidate.name or _expression(
            candidate.expression
            if isinstance(candidate.expression, str)
            else candidate.expression.to_dict()
        )
        lines += ["", f"[因子入库] {name}  ·  ID {identity}"]
    logger.info(
        "\n".join(
            "  " + part.rstrip()
            for line in lines
            for part in _terminal_wrap(line, 77, preserve_spaces=True)
        )
        + "\n"
    )


def terminal_oos(report, directory):
    """Render already computed OOS results; persisted JSON is unchanged."""
    lines = [f"OOS 结果：{report['run_id']}", ""]
    names = tuple(
        dict.fromkeys(
            m.get("name", "").replace("pearson", "spearman")
            for item in report["results"]
            for m in item.get("metrics", [item.get("metric", {})])
            if m
        )
    ) or ("",)
    for name in names if report["results"] else ():
        rows = [("因子 ID", "状态", "有效样本", "组数", "覆盖率", "Spearman IC", "Pearson IC")]
        for item in report["results"]:
            measured = {m.get("name", ""): m for m in item.get("metrics", [item.get("metric", {})])}
            rank = measured.get(name, {})
            linear = (
                measured.get(name.replace("spearman", "pearson"), {}) if "spearman" in name else {}
            )

            counts = []
            for key in ("n_obs", "n_groups"):
                a, b = rank.get(key), linear.get(key)
                values = (a, b) if linear and rank and a != b else (a if rank else b,)
                counts.append("/".join(f"{n:,}" if n is not None else "—" for n in values))

            coverage = item.get("coverage")
            status = {"success": "成功", "compute_error": "计算失败"}.get(
                item["status"], item["status"]
            )
            rows.append(
                (
                    item["expression_id"][:12],
                    status,
                    *counts,
                    f"{coverage:.2%}" if coverage is not None else None,
                    *(
                        f"{m['value']:+.6f}" if m.get("value") is not None else None
                        for m in (rank, linear)
                    ),
                )
            )
        lines += [metric_label(name), _terminal_grid(rows, (12, 8, 10, 6, 8, 11, 11)), ""]
    if report["results"]:
        lines.append("因子 ID 显示前 12 位；— 表示未记录、未定义或计算失败。")
        lines.append("样本/组数不同时按 Spearman/Pearson 列出；旧报告未记录 Pearson 时不补算。")
    else:
        lines.append("冻结因子库为空，没有 OOS 结果。")
    errors = [
        (item["expression_id"][:12], item["error"])
        for item in report["results"]
        if item.get("error")
    ]
    if errors:
        lines += ["错误详情：", _terminal_grid(errors, (12, 60))]
    lines += ["", "OOS 使用冻结方向，不代表可交易收益。", f"JSON：{directory / 'oos.json'}"]
    return "\n".join(lines)


def metric_label(name):
    if "_weighted_" in name or name.endswith("sqrt_amount"):
        return "√成交额加权"
    if "_equal_" in name or name.endswith("equal_product"):
        return "品种等权"
    return "IC"


def metric_text(items):
    return " / ".join(
        f"{metric_title(m.name)} {m.value:+.6f}"
        if m.value is not None
        else f"{metric_title(m.name)} —"
        for m in items
    )


def metric_title(name):
    correlation = "Pearson" if "pearson" in name else "Spearman"
    return f"{metric_label(name)} {correlation}"


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


def _expression(value) -> str:
    if isinstance(value, str):
        return value
    if value["op"] in {"field", "const"}:
        return ("$" if value["op"] == "field" else "") + str(value["value"])
    arguments = [_expression(a) for a in value.get("args", [])]
    if value.get("value") is not None:
        arguments.append(str(value["value"]))
    return f"{value['op'].upper()}({', '.join(arguments)})"


def write_report(directory: Path) -> Path:
    """Derive one Markdown report from committed JSON only. Caller holds the run lock."""
    spec = read_json(directory / "run.json")
    if spec.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported run format; old artifacts are not migrated")
    trials = RunStore(directory).trials()
    registrations = [read_json(p) for p in sorted((directory / "operators").glob("*.json"))]
    failures = (
        read_json(directory / "failures.json") if (directory / "failures.json").exists() else []
    )
    checkpoint = (
        read_json(directory / "checkpoint.json") if (directory / "checkpoint.json").exists() else {}
    )
    accepted = [t for t in trials if t["feedback"]["accepted"]]
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
            ("资产 / 股票池", f"{spec['asset']} / {spec['universe']}"),
            ("方法 / seed", f"{spec['method']} / {spec['seed']}"),
            ("实现", spec.get("implementation")),
            ("数据快照", spec.get("snapshot_id")),
            ("频率", profile.get("frequency")),
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
            ("入库成员", len(accepted)),
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
    if "method_config" in spec:
        lines += ["## 搜索方法配置", ""]
        lines += _table(["参数", "值"], spec["method_config"].items())
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
    if pending:
        state = (
            "结果已提交，等待恢复反馈" if pending["trial_index"] <= len(trials) else "结果未提交"
        )
        lines += [f"待完成 trial {pending['trial_index']}：{state}。", ""]
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
    if accepted:
        rows = []
        for trial in accepted:
            feedback = trial["feedback"]
            report = feedback["report"]
            for name in dict.fromkeys(m["name"] for m in report["metrics"]):
                values = {m["split"]: m["value"] for m in report["metrics"] if m["name"] == name}
                rows.append(
                    (
                        trial["trial_index"],
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
            [(t["trial_index"], t["library_size"], t["cumulative_seconds"]) for t in accepted],
        )
    else:
        lines += ["因子库为空。", ""]
    diagnostics = [
        (t["trial_index"], t["feedback"]["report"].get("diagnostics", {}))
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
                (f["time"], f["stage"], f.get("trial_index"), f["type"], f["message"])
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
    lines += [
        "## 数据边界与记录",
        "",
        _cell(spec.get("continuity", "未记录连续性说明。")),
        "",
        "原始记录：[运行配置](run.json)、[逐次记录](trials/)、[算子记录](operators/)。",
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


def write_model_report(directory: Path, fitted: dict, tested: dict | None = None):
    """Report downstream predictive IC separately from single-factor and trading results."""
    spec = fitted["spec"]
    preprocessing = fitted["fitted"]["preprocessing"]
    lines = [
        "# 因子库预测模型",
        "",
        f"搜索运行：{spec['search_run_id']}。主指标：{spec['primary_metric']}。",
        "",
        "这是独立的下游模型实验，不回写搜索结果，不是交易收益回测。",
        "linear 为带截距的最小二乘回归；LightGBM 使用固定参数，无自动调参或早停。",
        "模型与预处理在 train+val 合并集拟合；test 仅用于最终冻结后汇报。",
        "test 仅使用保存的模型，不重新训练、选因子或翻转预测方向。",
        "",
        f"模型拟合与搜索启动的源码记录一致：{'是' if spec['source_matches_search'] else '否'}。"
        "源码记录仅用于来源说明，不校验当前源码。",
        f"输入因子 {len(fitted['factor_ids'])} 个，有效列 {len(preprocessing['columns'])} 个，"
        f"训练行 {fitted['fitted']['training_rows']}。",
        "",
        "缺失值按训练均值填补；训练常量／全缺失列移除；所有有效因子均缺失的行不预测。",
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
                    "trial_index": trial["trial_index"],
                    "count": trial["library_size"],
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
                "model_usage": model_usage,
                "attempts": used,
                "trial_records": len(trials),
                "accepted": spec["library_size"],
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
