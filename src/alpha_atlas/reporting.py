"""Read file evidence and compare only matching independent experiments."""

import html
import json
import logging
import math
import re
import shutil
import statistics
import sys
import threading
import time
import unicodedata
import warnings
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import read_json
from alpha_atlas.storage import FORMAT_VERSION, RunStore

_PROGRESS_TEXT = ""
_ACTIVE_STREAM = None
_REPLY_TAIL_COLUMNS = 512


def _display_width(text):
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
        for c in text
    )


def _stream_display_width(text):
    """Count ambiguous glyphs wide so live rows fit CJK terminals conservatively."""
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WFA" else 1
        for c in text
    )


def _terminal_wrap(value, width, *, preserve_spaces=False):
    text = "—" if value is None else str(value)
    if preserve_spaces:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "".join(
            "\n" if c == "\n" else " " if c.isspace() else c
            for c in text
            if c == "\n" or c.isspace() or not unicodedata.category(c).startswith("C")
        )
        source_lines = text.split("\n")
    else:
        text = " ".join(text.split())
        text = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
        source_lines = [text]

    lines = []
    for source_line in source_lines:
        line = ""
        for char in source_line:
            if _display_width(line + char) > width:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
    return lines


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
    """Print a compact one-line status, or refresh an active response line."""
    logger = logging.getLogger("alpha_atlas.progress")
    if logger.isEnabledFor(logging.INFO):
        details = "  ·  ".join(
            f"{key}={value if value is not None else '—'}" for key, value in values.items()
        )
        text = f"{title}" + (f" │ {details}" if details else "")
        if _ACTIVE_STREAM is not None:
            with _ACTIVE_STREAM.lock:
                _ACTIVE_STREAM.notice = text
                _ACTIVE_STREAM._write_line()
            return
        global _PROGRESS_TEXT
        _PROGRESS_TEXT = text
        logger.info(_fit_progress_line(f"  › {text}", _terminal_width()))


class TerminalStream:
    """Show response text on one terminal line without exposing model reasoning."""

    def __init__(self, *, summary=False):
        self.summary = summary
        self.started = None
        self.stop = None
        self.thread = None
        self.interactive = False
        self.enabled = False
        self.lock = threading.RLock()
        self.response = ""
        self.pending = ""
        self.thinking = False
        self.tick = 0
        self.status = ""
        self.notice = None

    def __enter__(self):
        global _ACTIVE_STREAM
        logger = logging.getLogger("alpha_atlas.progress")
        if not logger.isEnabledFor(logging.INFO):
            return self
        self.enabled = True
        self.started = time.monotonic()
        self.interactive = sys.stderr.isatty()
        self.status = _PROGRESS_TEXT or ("摘要生成" if self.summary else "模型回复")
        if self.interactive:
            _ACTIVE_STREAM = self
            self.stop = threading.Event()
            self.thread = threading.Thread(target=self._refresh, daemon=True)
            self.thread.start()
        else:
            logger.info(_fit_progress_line(f"  … {self.status} │ 生成中", _terminal_width()))
        return self

    def _refresh(self):
        while not self.stop.is_set():
            with self.lock:
                self._write_line()
                self.tick += 1
            self.stop.wait(0.15)

    def _write_line(self, *, done=False, failed=False):
        sys.stderr.write("\r" + self._render_line(done=done, failed=failed) + "\x1b[K")
        sys.stderr.flush()

    def _render_line(self, *, done=False, failed=False):
        width = _terminal_width()
        measure = _stream_display_width
        elapsed = int(time.monotonic() - self.started)
        status = self.notice or self.status
        status = _compact_annealing_status(status)
        marker = "!" if failed else "✓" if done else "|/-\\"[self.tick % 4]
        elapsed_text = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        if failed:
            return _fit_terminal_line(
                f"  {marker} {status} │ 请求失败 · {elapsed_text}", width, measure=measure
            )
        suffix = f" │ {elapsed_text}"
        if self.response:
            suffix += " → "
            response_width = min(32, max(12, (width - 38) // 2))
            status_width = max(12, width - measure(suffix) - response_width)
            status_line = _fit_progress_line(f"  {marker} {status}", status_width, measure=measure)
            response_line = _fit_terminal_line(
                self.response, response_width, preserve_tail=True, measure=measure
            )
            line = status_line + suffix + response_line
            return (
                _fit_terminal_line(line, width, preserve_tail=True, measure=measure)
                if measure(line) > width
                else line
            )
        activity = "完成" if done else "生成中"
        return _fit_terminal_line(
            f"  {marker} {status} │ {elapsed_text} · {activity}", width, measure=measure
        )

    def _append_response(self, text):
        self.response += text
        if (
            len(self.response) <= _REPLY_TAIL_COLUMNS
            and _display_width(self.response) <= _REPLY_TAIL_COLUMNS
        ):
            return
        tail, used = [], 0
        for char in reversed(self.response):
            char_width = _display_width(char)
            if used + char_width > _REPLY_TAIL_COLUMNS or len(tail) >= _REPLY_TAIL_COLUMNS:
                break
            tail.append(char)
            used += char_width
        self.response = "".join(reversed(tail))

    def _content_delta(self, text):
        self.pending += text
        while self.pending:
            lowered = self.pending.lower()
            markers = (
                ("</think>", "</thinking>", "</analysis>", "</reasoning>")
                if self.thinking
                else ("<think>", "<thinking>", "<analysis>", "<reasoning>")
            )
            matches = [(lowered.find(marker), marker) for marker in markers]
            matches = [(position, marker) for position, marker in matches if position >= 0]
            if matches:
                position, marker = min(matches)
                if not self.thinking:
                    self._append_response(self.pending[:position])
                self.pending = self.pending[position + len(marker) :]
                self.thinking = not self.thinking
                continue
            keep = max(map(len, markers)) - 1
            if self.thinking:
                self.pending = self.pending[-keep:] if keep else ""
            elif len(self.pending) > keep:
                self._append_response(self.pending[:-keep] if keep else self.pending)
                self.pending = self.pending[-keep:] if keep else ""
            break

    def delta(self, channel, text):
        if not self.enabled:
            return
        with self.lock:
            if channel == "restart":
                self.response = ""
                self.pending = ""
                self.thinking = False
            elif channel == "content":
                self.notice = None
                self._content_delta(text)
            else:
                return
            if self.interactive:
                self._write_line()
                self.tick += 1

    def __exit__(self, exc_type, exc, traceback):
        global _ACTIVE_STREAM
        if self.thread is not None:
            self.stop.set()
            self.thread.join()
            with self.lock:
                if exc_type is None and not self.thinking and self.pending:
                    self._append_response(self.pending)
                self.pending = ""
                self._write_line(done=exc_type is None, failed=exc_type is not None)
                sys.stderr.write("\n")
                sys.stderr.flush()
            if _ACTIVE_STREAM is self:
                _ACTIVE_STREAM = None
        elif self.enabled:
            logger = logging.getLogger("alpha_atlas.progress")
            if exc_type is not None:
                terminal_progress("模型请求失败")
            else:
                with self.lock:
                    if not self.thinking and self.pending:
                        self._append_response(self.pending)
                    self.pending = ""
                    logger.info(self._render_line(done=True))


def _fit_terminal_line(text, width, *, preserve_tail=False, measure=_display_width):
    """Clip a terminal preview to one row, keeping the newest response characters visible."""
    text = "".join(" " if char.isspace() else char for char in text)
    text = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    if measure(text) <= width:
        return text
    if preserve_tail:
        kept = []
        used = measure("…")
        for char in reversed(text):
            char_width = measure(char)
            if used + char_width > width:
                break
            kept.append(char)
            used += char_width
        return "…" + "".join(reversed(kept))
    kept = []
    used = measure("…")
    for char in text:
        char_width = measure(char)
        if used + char_width > width:
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + "…"


def _compact_annealing_status(status):
    if not status.startswith(("退火 │ ", "区域退火 │ ")):
        return status
    parts = [part.strip() for part in status.replace(" │ ", "  ·  ").split("  ·  ")]
    title = parts[0]
    if title == "区域退火":
        title = "退火"
    progress = next((part for part in parts[1:] if part.startswith("进度=")), None)
    agent = next((part for part in parts[1:] if part.startswith("Agent=")), None)
    details = " · ".join(part for part in (progress, agent) if part)
    return f"{title} │ {details}" if details else title


def _terminal_width():
    if sys.stderr.isatty():
        return max(24, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
    return 78


def _fit_progress_line(text, width, *, measure=_display_width):
    """Keep the start and end of long statuses visible on a single terminal row."""
    text = "".join(" " if char.isspace() else char for char in text)
    text = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    if measure(text) <= width:
        return text
    ellipsis_width = measure("…")
    left_width = (width - ellipsis_width) // 2
    right_width = width - left_width - ellipsis_width
    left = _fit_terminal_line(text, left_width, measure=measure)
    right = _fit_terminal_line(text, right_width, preserve_tail=True, measure=measure)
    return left[:-1] + "…" + right[1:]


def terminal_evaluation(candidate, *, index, limit):
    terminal_progress("评估候选", 进度=f"{index}/{limit}", 名称=candidate.name or "未命名")


def terminal_candidate(feedback, *, index, limit, elapsed, total):
    """Print the committed outcome, primary evaluation and full candidate expression."""
    logger = logging.getLogger("alpha_atlas.progress")
    if not logger.isEnabledFor(logging.INFO):
        return
    report = feedback.report
    primary_name = (
        next((metric.name for metric in report.metrics if metric.split == "train"), None)
        if report
        else None
    )
    measured = (
        {
            metric.split: metric.value
            for metric in report.metrics
            if metric.name == primary_name and metric.split in {"train", "val"}
        }
        if report and primary_name
        else {}
    )
    numbers = "/".join(
        f"{measured[split]:+.4f}" if measured.get(split) is not None else "—"
        for split in ("train", "val")
    )
    coverage = f"{report.coverage:.1%}" if report and report.coverage is not None else "—"
    status = "入库" if feedback.accepted else "拒绝"
    width = _terminal_width()
    line = (
        f"  {'✓' if feedback.accepted else '!'} {status} · {index}/{limit}"
        f" · 已入库 {feedback.library_version} · IC(train/val) {numbers} · 覆盖 {coverage}"
    )
    duration = f"耗时 {elapsed:.1f}s / 累计 {total:.1f}s"
    if _display_width(line) + 3 + _display_width(duration) <= width:
        line += " · " + duration

    expression = _expression(feedback.candidate.expression) or "—"
    if feedback.candidate.name:
        expression = f"{feedback.candidate.name} = {expression}"
    prefix = "  ↳ 公式: "
    wrapped = _terminal_wrap(expression, width - _display_width(prefix), preserve_spaces=True)
    lines = [_fit_terminal_line(line, width), prefix + wrapped[0]]
    continuation = "     "
    lines.extend(continuation + part for part in wrapped[1:])
    if not feedback.accepted:
        reason = _terminal_wrap(feedback.reason, width - _display_width(continuation))[0]
        lines.append(f"{continuation}原因: {reason}")
    elif report and report.status != "success":
        lines.append(f"{continuation}评估状态: {report.status}")
    if report:
        metric_lines = _candidate_metric_lines(report, primary_name)
        if metric_lines:
            lines.append("")
            lines.extend(metric_lines)
    logger.info("\n".join(lines))


def _candidate_metric_lines(report, primary_name):
    names = list(dict.fromkeys(m.name for m in report.metrics if m.split == "train"))
    if not names:
        return []
    train = {m.name: m.value for m in report.metrics if m.split == "train"}
    valid = {m.name: m.value for m in report.metrics if m.split == "val"}
    labels = [metric_title(name) + (" *" if name == primary_name else "") for name in names]
    label_width = max(_display_width("指标"), *(_display_width(label) for label in labels)) + 2
    train_header, val_header = "训练 IC（原始）", "验证 IC（定向）"
    train_width = max(_display_width(train_header), 9)
    val_width = max(_display_width(val_header), 9)

    def pad(value, width):
        return value + " " * max(0, width - _display_width(value))

    lines = [
        "  "
        + pad("指标", label_width)
        + "  "
        + pad(train_header, train_width)
        + "  "
        + pad(val_header, val_width)
    ]
    for name, label in zip(names, labels, strict=True):
        train_value = f"{train[name]:+.6f}" if train.get(name) is not None else "—"
        val_value = f"{valid[name]:+.6f}" if valid.get(name) is not None else "—"
        lines.append(
            "  "
            + pad(label, label_width)
            + "  "
            + pad(train_value, train_width)
            + "  "
            + pad(val_value, val_width)
        )
    if primary_name:
        lines.append(f"  * 主指标：{primary_name}")
    return lines


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
    if not isinstance(value, dict):
        value = value.to_dict()
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
    elif spec["status"] == "frozen" and not accepted:
        lines += ["冻结因子库为空，组合模型不适用。", ""]
    else:
        lines += ["尚未完成组合模型 Test；atlas finish 会补齐后自动填表。", ""]
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
        lines += ["尚未完成 Val 全库相似度；atlas finish 会补齐后自动填表。", ""]
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
    """Publish both tables together only from complete, matching frozen-run outputs."""
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
    positive = (
        f"{sum(score > 0 for score in finite)} / {len(finite)}，{sum(score > 0 for score in finite) / len(finite):.2%}"
        if finite
        else "—"
    )
    methods = {
        "alphaprobe": "AlphaPROBE",
        "react": "ReAct",
        "mcts_llm": "MCTS-LLM",
        "v1_async": "AlphaAtlas+Annealing V1 Async",
        "random": "Random",
        "gp": "GP",
        "mcts": "UCT MCTS",
    }
    method = methods.get(spec["method"], spec["method"])
    relative = directory.relative_to(root).as_posix()
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
        "正 IC",
        "单因子加权 Spearman 均值" if futures else "单因子 Spearman 均值",
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
        positive,
        number(statistics.mean(secondary) if secondary else None),
        *map(number, combined),
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
