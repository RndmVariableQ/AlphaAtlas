"""Terminal progress, streaming status and compact metric display; never decides results."""

import logging
import shutil
import sys
import threading
import time
import unicodedata

_PROGRESS_TEXT = ""
_ACTIVE_STREAM = None
_STATUS_VISIBLE = False
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


def _clear_terminal_status():
    global _STATUS_VISIBLE
    if _STATUS_VISIBLE:
        if sys.stderr.isatty():
            sys.stderr.write("\r\x1b[K")
            sys.stderr.flush()
        _STATUS_VISIBLE = False


def terminal_progress(title, *, transient=False, **values):
    """Keep events in the transcript and refresh transient work on one TTY row."""
    logger = logging.getLogger("alpha_atlas.progress")
    if not logger.isEnabledFor(logging.INFO):
        return
    global _PROGRESS_TEXT, _STATUS_VISIBLE
    details = " · ".join(f"{key}={value}" for key, value in values.items() if value is not None)
    text = f"{title}" + (f" │ {details}" if details else "")
    if transient:
        _PROGRESS_TEXT = text
        if _ACTIVE_STREAM is not None:
            with _ACTIVE_STREAM.lock:
                _ACTIVE_STREAM.notice = text
                _ACTIVE_STREAM._write_line()
            return
        if sys.stderr.isatty():
            line = _fit_terminal_line(
                f"  … {text}", _terminal_width(), measure=_stream_display_width
            )
            sys.stderr.write("\r" + line + "\x1b[K")
            sys.stderr.flush()
            _STATUS_VISIBLE = True
            return
        # A file is a transcript, not a terminal: keep each request without animation or clipping.
        lines = _terminal_wrap(f"› {text}", _terminal_width() - 2)
        logger.info("\n".join("  " + line for line in lines))
        return

    lines = ["  " + line for line in _terminal_wrap(title, _terminal_width() - 2)]
    for key, value in values.items():
        if value is not None:
            lines.extend(
                "    " + line for line in _terminal_wrap(f"{key}: {value}", _terminal_width() - 4)
            )
    if _ACTIVE_STREAM is not None:
        with _ACTIVE_STREAM.lock:
            _clear_terminal_status()
            logger.info("\n".join(lines))
            _ACTIVE_STREAM.notice = text
            _ACTIVE_STREAM._write_line()
    else:
        _PROGRESS_TEXT = text
        _clear_terminal_status()
        logger.info("\n".join(lines) + "\n")


class TerminalStream:
    """Show response text on one terminal line without exposing model reasoning."""

    def __init__(self, *, summary=False, enabled=True):
        self.summary = summary
        self.requested = enabled
        self.started = None
        self.stop = None
        self.thread = None
        self.interactive = False
        self.enabled = False
        self.lock = threading.RLock()
        self.response = ""
        self.pending = ""
        self.thinking = False
        self.activity = "等待首字"
        self.last_delta = None
        self.tick = 0
        self.status = ""
        self.notice = None

    def __enter__(self):
        global _ACTIVE_STREAM
        logger = logging.getLogger("alpha_atlas.progress")
        if not self.requested or not logger.isEnabledFor(logging.INFO):
            return self
        self.enabled = True
        self.started = time.monotonic()
        self.interactive = sys.stderr.isatty()
        self.status = "摘要生成" if self.summary else _PROGRESS_TEXT or "模型回复"
        if self.interactive:
            _ACTIVE_STREAM = self
            self.stop = threading.Event()
            self.thread = threading.Thread(target=self._refresh, daemon=True)
            self.thread.start()
        return self

    def _refresh(self):
        while not self.stop.is_set():
            with self.lock:
                self._write_line()
                self.tick += 1
            self.stop.wait(0.15)

    def _write_line(self, *, done=False, failed=False):
        global _STATUS_VISIBLE
        sys.stderr.write("\r" + self._render_line(done=done, failed=failed) + "\x1b[K")
        sys.stderr.flush()
        _STATUS_VISIBLE = True

    def _render_line(self, *, done=False, failed=False):
        width = _terminal_width()
        measure = _stream_display_width
        now = time.monotonic()
        elapsed = int(now - self.started)
        status = self.notice or self.status
        marker = "!" if failed else "✓" if done else "|/-\\"[self.tick % 4]
        elapsed_text = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        activity = "请求失败" if failed else "完成" if done else self.activity
        suffix = f" │ {elapsed_text} · {activity}"
        if not done and not failed and self.last_delta is not None:
            idle = int(now - self.last_delta)
            if idle >= 30:
                suffix += f" · 无新内容 {idle}s"
        status_line = _fit_terminal_line(f"  {marker} {status}", width, measure=measure)
        response_width = min(32, width - measure(status_line + suffix + " → "))
        if self.response and not failed and response_width >= 12:
            return (
                status_line
                + suffix
                + " → "
                + _fit_terminal_line(
                    self.response, response_width, preserve_tail=True, measure=measure
                )
            )
        line = _fit_terminal_line(status_line, max(2, width - measure(suffix)), measure=measure)
        return _fit_terminal_line(line + suffix, width, measure=measure)

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
                self.activity = "等待首字"
                self.last_delta = None
            elif channel in {"content", "reasoning"} and text:
                self.notice = None
                self.last_delta = time.monotonic()
                if channel == "content":
                    self._content_delta(text)
                self.activity = "思考中" if channel == "reasoning" or self.thinking else "接收回复"
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
            if _ACTIVE_STREAM is self:
                _ACTIVE_STREAM = None
        if self.enabled and exc_type is not None:
            terminal_progress("模型请求失败", 阶段=self.status, 类型=exc_type.__name__)


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


def _terminal_width():
    if sys.stderr.isatty():
        return max(24, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
    return 78


def terminal_evaluation(candidate, *, index, limit, research_plan_index=None):
    scope = (
        f"[计划 #{research_plan_index} · 评估] " if research_plan_index is not None else "[评估] "
    )
    terminal_progress(
        scope + "评估候选", transient=True, 进度=f"{index}/{limit}", 名称=candidate.name or "未命名"
    )


def terminal_candidate(
    feedback, *, index, limit, elapsed, total, research_plan_index=None, initial_count=0
):
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
    coverage = f"{report.coverage:.1%}" if report and report.coverage is not None else "—"
    status = "入库" if feedback.accepted else "拒绝"
    width = _terminal_width()
    scope = (
        f"[计划 #{research_plan_index} · 评估] " if research_plan_index is not None else "[评估] "
    )
    line = f"{scope}{'✓' if feedback.accepted else '!'} {status} · {index}/{limit}"
    line += (
        f" · 初始 {initial_count} / 新增 {feedback.library_version - initial_count}"
        if initial_count
        else f" · 已入库 {feedback.library_version}"
    )
    duration = f"耗时 {elapsed:.1f}s / 累计 {total:.1f}s"

    expression = _expression(feedback.candidate.expression) or "—"
    lines = ["  " + part for part in _terminal_wrap(line, width - 2)]
    lines.extend(
        "    " + part
        for part in _terminal_wrap(f"名称: {feedback.candidate.name or '未命名'}", width - 4)
    )
    direction = {1: "正向 (+1)", -1: "反向 (-1)"}.get(report.direction if report else None, "—")
    lines.extend(
        "    " + part
        for part in _terminal_wrap(f"覆盖: {coverage} · 因子方向: {direction}", width - 4)
    )
    if report:
        lines.extend("  " + part for part in _candidate_metric_lines(report, primary_name))
    lines.append("    公式:")
    lines.extend(
        "      " + part for part in _terminal_wrap(expression, width - 6, preserve_spaces=True)
    )
    continuation = "    "
    if not feedback.accepted:
        lines.extend(
            continuation + part
            for part in _terminal_wrap(f"原因: {feedback.reason}", width - len(continuation))
        )
    elif report and report.status != "success":
        lines.append(f"{continuation}评估状态: {report.status}")
    lines.append(f"    {duration}")
    _clear_terminal_status()
    logger.info("\n".join(lines) + "\n")


def _candidate_metric_lines(report, primary_name):
    names = list(
        dict.fromkeys(
            m.name
            for m in report.metrics
            if m.split == "train" and metric_label(m.name) != "品种等权"
        )
    )
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
        lines.append("  * 主指标")
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
        if metric_label(m.name) != "品种等权"
    )


def metric_title(name):
    correlation = "Pearson" if "pearson" in name else "Spearman"
    return f"{metric_label(name)} {correlation}"


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
