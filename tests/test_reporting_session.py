import json
import logging
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest
from test_evaluation_library import RULES, evaluator
from test_numba_runtime import definition
from test_pipeline import project as project

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import read_json
from alpha_atlas.contracts import Candidate
from alpha_atlas.library import FactorLibrary
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime
from alpha_atlas.reporting import compare, write_report
from alpha_atlas.runner import run
from alpha_atlas.runner import test_frozen as evaluate_frozen
from alpha_atlas.session import SearchSession


def test_context_is_immutable_current_and_run_local(project):
    snapshots = []

    class Method:
        def run(self, session):
            context = session.get_context()
            snapshots.append(context)
            catalog_before = session.list_operators()
            assert len(catalog_before) == 60
            assert session.get_fields() == ("adj_close", "volume")
            assert set(asdict(context)) == {
                "asset",
                "frequency",
                "target",
                "metric",
                "remaining_attempts",
            }
            assert context.asset == "ashare" and context.frequency == "1d"
            assert context.target.price_field == "adj_close" and context.target.horizon_bars == 5
            assert context.remaining_attempts == 2
            rules = session.get_expression_rules()
            assert rules["max_nodes"] == 100 and rules["max_depth"] == 20
            assert session.get_operator("LAG")["name"] == "DELAY"
            assert "LAG" in session.get_operator("DELAY")["aliases"]
            assert session.get_operator("TS_STD")["minimum_window"] == 2
            assert session.get_operator("TS_KURT")["minimum_window"] == 4
            with pytest.raises(ValueError, match="must be text"):
                session.get_operator(1)
            with pytest.raises(ValueError, match="unknown operator"):
                session.get_operator("MISSING")
            with pytest.raises(FrozenInstanceError):
                context.remaining_attempts = 9999
            with pytest.raises(FrozenInstanceError):
                context.target.horizon_bars = 9999
            catalog_before[0]["name"] = "changed"
            details = session.get_operator("TS_MEAN")
            details["version"] = "changed"
            assert session.get_operator("TS_MEAN")["version"] != "changed"
            assert session.list_operators()[0]["name"] != "changed"
            assert session.get_context() == context
            recipe = OperatorDefinition(
                "MEAN_TWICE",
                (("x", "series"), ("n", "window")),
                "TS_MEAN(TS_MEAN(x,n),n)",
                description="两次平滑",
            )
            registration = session.register_operator(recipe)
            assert registration.accepted
            assert not session.register_operator(recipe).accepted
            catalog = {o["name"]: session.get_operator(o["name"]) for o in session.list_operators()}
            assert len(catalog) == 61 and len(catalog_before) == 60
            custom = catalog["MEAN_TWICE"]
            assert custom["version"] == registration.operator_id
            assert custom["kind"] == "composite" and custom["history"] == "expanded"
            assert custom["args"] == ("series", "window") and custom["parameter_names"] == (
                "x",
                "n",
            )
            assert custom["description"] == "两次平滑"
            assert session.evaluate(Candidate("MEAN_TWICE($volume, 7)")).report.status == "success"
            assert (
                session.evaluate(Candidate("MEAN_TWICE($volume, 20)")).report.status
                != "compile_error"
            )
            assert session.get_context().remaining_attempts == 0
            assert context.remaining_attempts == 2

    for _ in range(2):
        path = run(
            project,
            "ashare",
            "fold2",
            "context",
            42,
            attempts=2,
            field_names=["volume"],
            method_impl=Method(),
        )
        assert read_json(path / "run.json")["snapshot_id"]
    assert snapshots[0] == snapshots[1]  # No run identity or dates exposed.


@pytest.mark.parametrize("scope", ["ts", "cs"])
def test_context_describes_code_operator_history_and_excludes_rejected(
    tmp_path, monkeypatch, scope
):
    from dataclasses import replace

    from alpha_atlas.storage import RunStore

    runtime = NumbaRuntime()
    monkeypatch.setattr(runtime, "validate", lambda *args, **kwargs: ("synthetic gate",))
    registry = OperatorRegistry(runtime=runtime)
    session = SearchSession(
        evaluator(registry=registry), FactorLibrary(RULES), RunStore(tmp_path), 1
    )
    submitted = (
        definition()
        if scope == "ts"
        else OperatorDefinition(
            "CUSTOM_CS",
            (("x", "series"),),
            "def kernel(x): return x.copy()",
            kind="group_batch",
            scope="cs",
            description="当前截面",
        )
    )
    feedback = session.register_operator(submitted)
    assert feedback.accepted
    bad = replace(submitted, name="bad_name")
    assert not session.register_operator(bad).accepted
    catalog = {o["name"]: session.get_operator(o["name"]) for o in session.list_operators()}
    assert len(catalog) == 61 and "bad_name" not in catalog
    operator = catalog[submitted.name]
    assert operator["kind"] == "group_batch" and operator["scope"] == scope
    assert operator["output"] == "series" and operator["version"] == feedback.operator_id
    assert operator["history_bars"] == submitted.history
    assert operator["window_arg"] == submitted.window_arg
    assert operator["history_offset"] == submitted.history_offset
    assert operator["parameter_names"] == tuple(p for p, _ in submitted.parameters)


def test_report_budget_registration_empty_library_and_oos(project):
    class Method:
        def run(self, session):
            definition = OperatorDefinition("DOUBLE", (("x", "series"),), "ADD(x,x)")
            assert session.register_operator(definition).accepted
            assert not session.register_operator(definition).accepted
            session.evaluate_many([Candidate("1"), Candidate("$volume"), Candidate("$adj_close")])

    path = run(project, "ashare", "fold1", "report", 42, attempts=1, method_impl=Method())
    report = (path / "report.md").read_text(encoding="utf-8")
    assert "| 已提交尝试 | 1 |" in report
    assert "| trial 记录数（含预算拒绝） | 3 |" in report
    assert "| budget_rejected | 2 |" in report
    assert "| 剩余尝试 | 0 |" in report and "因子库为空" in report
    assert "DOUBLE" in report and "operator already exists" in report
    assert "尚未生成可用 OOS" in report
    assert not (path / "reports").exists()
    comparison = compare(project)["runs"][0]
    assert comparison["attempts"] == 1 and comparison["trial_records"] == 3
    assert [p["attempt"] for p in comparison["curve"]] == [1, 1, 1]
    evaluate_frozen(project, path)
    assert (path / "oos.json").exists()
    assert "OOS 已执行，冻结因子库为空" in (path / "report.md").read_text(encoding="utf-8")


def test_report_rebuild_only_reads_records_and_renders_members_and_failed_oos(project, monkeypatch):
    path = run(project, "ashare", "fold1", "atlas", 42, attempts=5)
    frozen = read_json(path / "frozen/library.json")
    assert frozen["factors"]
    identity = frozen["factors"][0]["compiled"]["factor_id"]
    atomic_json(
        path / "oos.json",
        {
            "run_id": path.name,
            "results": [
                {
                    "expression_id": identity,
                    "status": "compute_error",
                    "error": "bad | <script>\nline",
                },
                {
                    "expression_id": "no-samples",
                    "status": "success",
                    "metric": {"value": None, "n_obs": 0},
                },
            ],
        },
    )
    before = {p: p.read_bytes() for p in path.rglob("*.json")}

    def forbid(*args, **kwargs):
        raise AssertionError("report must not compute or read market data")

    monkeypatch.setattr("alpha_atlas.evaluation.EvaluationService.evaluate", forbid)
    monkeypatch.setattr("alpha_atlas.runner.ParqMarketData.load_features", forbid)
    monkeypatch.setattr("alpha_atlas.runner.execute", forbid)
    report = write_report(path).read_text(encoding="utf-8")
    assert "val 原始 IC" in report and "val 正向 IC" in report and "累计成员" in report
    assert "compute_error" in report and "bad \\| &lt;script&gt;<br>line" in report
    assert "有效样本为 0" in report
    assert all(p.read_bytes() == content for p, content in before.items())
    result = subprocess.run(
        [sys.executable, "-m", "alpha_atlas.cli", "report", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["report"] == str(path / "report.md")
    assert (path / "report.md").read_text(encoding="utf-8") == report


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_report_after_failure_or_interruption(project, error):
    class Method:
        def run(self, session):
            session.evaluate(Candidate("1"))
            raise error("method failed | <detail>")

    with pytest.raises(error):
        run(project, "ashare", "fold1", "failure", 42, attempts=2, method_impl=Method())
    path = next((project / "artifacts/runs").iterdir())
    report = (path / "report.md").read_text(encoding="utf-8")
    status = "failed" if error is RuntimeError else "interrupted"
    assert f"| 状态 | {status} |" in report
    assert "method_run" in report and "method failed \\| &lt;detail&gt;" in report


def test_report_io_failure_does_not_undo_freeze_or_hide_method_exception(project, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("report disk error")

    monkeypatch.setattr("alpha_atlas.reporting.write_report", fail)
    with pytest.warns(RuntimeWarning, match="Report could not be refreshed"):
        path = run(project, "ashare", "fold1", "random", 42, attempts=1)
    assert read_json(path / "run.json")["status"] == "frozen"

    class Method:
        def run(self, session):
            raise ValueError("primary error")

    with pytest.warns(RuntimeWarning, match="Report could not be refreshed"):
        with pytest.raises(ValueError, match="primary error"):
            run(project, "ashare", "fold1", "failure", 42, attempts=1, method_impl=Method())


def test_progress_lines_handle_chinese_wrapping_and_control_characters(caplog):
    from alpha_atlas.reporting import _display_width, terminal_progress

    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        terminal_progress(
            "评估候选", 公式="中文 / DELTA($close,20) " * 8, 备注="空值\n\x1b\r\t", IC=None
        )
    message = caplog.records[-1].getMessage()
    lines = message.splitlines()
    assert all(line.startswith("  ") and _display_width(line) <= 79 for line in lines)
    assert not any(line.startswith(("+", "|")) for line in lines)
    assert "中文" in message and "—" in message and "\x1b" not in message


@pytest.mark.parametrize("defined", [True, False])
def test_candidate_results_align_metrics_and_print_admission_name(caplog, defined):
    from dataclasses import replace

    from alpha_atlas.contracts import EvaluationReport, Metric, TrialFeedback
    from alpha_atlas.reporting import _display_width, terminal_candidate

    name = "time_series_weighted_pearson_ic"
    metrics = (
        Metric(name, "train", -0.031132, 100, name),
        Metric(name, "val", 0.018719 if defined else None, 100, name),
        Metric(name, "val_raw", -0.018719, 100, name),
    )
    report = EvaluationReport("factor", metrics, -1, 0.9966, 1.35, "success")
    feedback = TrialFeedback(
        Candidate("TS_MEAN($close,12)", name="短期价格均值"),
        report,
        defined,
        "accepted" if defined else "undefined_val_metric",
        library_version=1,
    )
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        terminal_candidate(feedback, index=1, limit=100, elapsed=1.35, total=26.27)
    message = caplog.records[-1].getMessage()
    lines = message.strip().splitlines()
    assert not any(line.startswith(("+", "|")) for line in lines)
    assert all(_display_width(line) <= 79 for line in lines)
    assert ("[因子入库] 短期价格均值" in message) == defined
    assert "候选完成 1/100" in message and "方向 -1" in message and "99.66%" in message
    assert f"* 主指标：{name}" in message
    header = next(line for line in lines if "训练 IC" in line)
    row = next(line for line in lines if "Pearson" in line)
    assert _display_width(row[: row.index("-0.031132")]) == _display_width(
        header[: header.index("训练")]
    )
    if defined:
        assert "无法计算" not in message and "未定义" not in message
        assert "+0.018719" in row and "-0.018719" not in message and "原因" not in message
        assert _display_width(row[: row.index("+0.018719")]) == _display_width(
            header[: header.index("验证")]
        )
        with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
            terminal_candidate(
                replace(feedback, accepted=False, reason="behavior_duplicate"),
                index=9,
                limit=100,
                elapsed=1.89,
                total=183.07,
            )
        duplicate = caplog.records[-1].getMessage()
        assert "behavior_duplicate" in duplicate and "无法计算" not in duplicate
        assert "[因子入库]" not in duplicate
    else:
        assert "—" in row and "原因：undefined_val_metric" in message
        assert "— 表示无法计算" in message


def test_cli_progress_stays_on_stderr_and_quiet_preserves_results(project):
    def cli(*args, parse_json=True):
        result = subprocess.run(
            [sys.executable, "-m", "alpha_atlas.cli", "--root", str(project), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return (json.loads(result.stdout) if parse_json else result.stdout), result.stderr

    args = ("run", "--asset", "ashare", "--fold", "fold1", "--method", "atlas", "--attempts", "3")
    result, output = cli(*args)
    assert "开始搜索 / 加载行情" in output and "行情就绪" in output
    assert output.index("评估候选") < output.index("候选完成")
    assert output.count("候选完成") == 3 and "搜索完成" in output
    report, output = cli("test", result["run_dir"], "--json")
    assert report["results"] and "OOS 计算" in output and "OOS 完成" in output
    cached, output = cli("test", result["run_dir"], "--json")
    assert cached == report and "OOS 缓存命中" in output
    quiet, output = cli("test", result["run_dir"], "--quiet", "--json")
    assert quiet == report and output == ""
    table, output = cli("test", result["run_dir"], parse_json=False)
    assert "OOS 缓存命中" in output
    assert "OOS 结果" in table and "有效样本" in table and "覆盖率" in table
    assert report["results"][0]["expression_id"][:12] in table
    quiet_table, output = cli("test", result["run_dir"], "--quiet", parse_json=False)
    assert quiet_table == table and output == ""
    assert read_json(Path(result["run_dir"]) / "oos.json") == report
    second, output = cli(*args, "--quiet")
    assert output == ""
    first_trials = read_json(Path(result["run_dir"]) / "trials/00000001.json")
    second_trials = read_json(Path(second["run_dir"]) / "trials/00000001.json")
    assert first_trials["feedback"]["accepted"] == second_trials["feedback"]["accepted"]
    assert (
        first_trials["feedback"]["report"]["metrics"]
        == second_trials["feedback"]["report"]["metrics"]
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_oos_table_dual_metrics_missing_values_errors_and_empty_library(tmp_path, legacy):
    from alpha_atlas.reporting import _display_width, terminal_oos

    report = {
        "run_id": "synthetic",
        "results": [
            {
                "expression_id": "a" * 64,
                "status": "success",
                "coverage": 0.9956,
                "metrics": [
                    {
                        "name": "time_series_weighted_spearman_ic",
                        "value": 0.006592,
                        "n_obs": 2206218,
                        "n_groups": 72,
                    },
                    {
                        "name": "time_series_equal_spearman_ic",
                        "value": None,
                        "n_obs": 0,
                        "n_groups": 0,
                    },
                    {
                        "name": "time_series_weighted_pearson_ic",
                        "value": -0.012345,
                        "n_obs": 2206218,
                        "n_groups": 72,
                    },
                    {
                        "name": "time_series_equal_pearson_ic",
                        "value": 0.123456,
                        "n_obs": 10,
                        "n_groups": 1,
                    },
                ],
            },
            {"expression_id": "b" * 64, "status": "compute_error", "error": "合成错误\n\x1b" * 20},
        ],
    }
    measured = report["results"][0]["metrics"]
    measured[:] = measured[2:] + measured[:2]  # Primary Pearson comes first in new reports.
    if legacy:
        for item in measured:
            _, _, weighting, correlation, _ = item["name"].split("_")
            suffix = "sqrt_amount" if weighting == "weighted" else "equal_product"
            item["name"] = f"time_series_{correlation}_{suffix}"
    table = terminal_oos(report, tmp_path)
    assert "√成交额加权" in table and "品种等权" in table
    assert "+0.006592" in table and "2,206,218" in table and "99.56%" in table
    assert "—" in table and "计算失败" in table and "合成错误" in table
    assert "\x1b" not in table and "a" * 64 not in table
    assert str(tmp_path / "oos.json") in table
    main_table = table.split("因子 ID 显示")[0]
    assert {
        _display_width(line) for line in main_table.splitlines() if line.startswith(("|", "+"))
    } == {88}
    weighted, equal = main_table.split("品种等权")
    assert weighted.count("因子 ID") == equal.count("因子 ID") == 1
    for part in (weighted, equal):
        assert part.index("a" * 12) < part.index("b" * 12)
        assert part.count("a" * 12) == part.count("b" * 12) == 1
    assert "+0.006592" not in equal
    assert "-0.012345" in weighted and "+0.123456" in equal
    assert "0/10" in equal and "0/1" in equal
    for line in main_table.splitlines():
        if "因子 ID" in line:
            assert [c.strip() for c in line.split("|")][-3:-1] == ["Spearman IC", "Pearson IC"]
    assert "冻结因子库为空" in terminal_oos({"run_id": "empty", "results": []}, tmp_path)


def test_saved_spearman_only_report_does_not_invent_pearson(tmp_path):
    from alpha_atlas.reporting import terminal_oos

    report = {
        "run_id": "old",
        "results": [
            {
                "expression_id": "a" * 64,
                "status": "success",
                "metric": {
                    "name": "cross_sectional_spearman",
                    "value": 0.25,
                    "n_obs": 100,
                    "n_groups": 10,
                },
                "coverage": 1.0,
            }
        ],
    }
    table = terminal_oos(report, tmp_path)
    row = next(line for line in table.splitlines() if "a" * 12 in line)
    assert [c.strip() for c in row.split("|")][-3:-1] == ["+0.250000", "—"]


def test_cli_saved_oos_is_readonly_after_source_change(project, monkeypatch, capsys):
    from alpha_atlas.cli import main

    path = run(project, "ashare", "fold1", "atlas", 42, attempts=3)
    result = evaluate_frozen(project, path)
    before = {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}
    monkeypatch.setattr("alpha_atlas.runner.source_fingerprint", lambda: "changed")
    assert evaluate_frozen(project, path) == result

    # Viewing old evidence has no dependency on current computation or market access.
    def forbid(*args, **kwargs):
        raise AssertionError("saved report must not compute or read market data")

    monkeypatch.setattr("alpha_atlas.runner.test_frozen", forbid)
    monkeypatch.setattr("alpha_atlas.runner.ParqMarketData.load_features", forbid)
    monkeypatch.setattr("alpha_atlas.reporting.write_report", forbid)
    monkeypatch.setattr(sys, "argv", ["atlas", "report", str(path), "--oos"])
    capsys.readouterr()
    main()
    output = capsys.readouterr().out
    assert "只读展示" in output and "未按当前代码重新验证或计算" in output
    assert result["results"][0]["expression_id"][:12] in output
    assert before.keys() == {p for p in path.rglob("*") if p.is_file()}
    # Neither source changes nor the read-only viewer rewrite saved evidence.
    assert all(p.read_bytes() == content for p, content in before.items())


def test_cli_saved_oos_missing_does_not_create_files(tmp_path, monkeypatch, capsys):
    from alpha_atlas.cli import main

    monkeypatch.setattr(sys, "argv", ["atlas", "report", str(tmp_path), "--oos"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "no saved oos.json" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_progress_precedes_slow_data_load_and_failure_has_no_completion(
    project, monkeypatch, caplog
):
    def fail(*args, **kwargs):
        assert "加载行情" in caplog.records[-1].getMessage()
        raise RuntimeError("synthetic load failure")

    monkeypatch.setattr("alpha_atlas.runner.ParqMarketData.load_features", fail)
    with caplog.at_level(logging.INFO, logger="alpha_atlas.progress"):
        with pytest.raises(RuntimeError, match="load failure"):
            run(project, "ashare", "fold1", "atlas", 42, attempts=1)
    assert "运行失败" in caplog.text and "搜索完成" not in caplog.text
