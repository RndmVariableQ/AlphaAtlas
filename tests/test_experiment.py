import itertools
import json
import logging
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from test_evaluation_library import average_rank
from test_pipeline import project as project

from alpha_atlas import experiment, reporting
from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.checkpoint import read_json
from alpha_atlas.contracts import Candidate, DateRange, Fold
from alpha_atlas.runner import run, run_model
from alpha_atlas.runner import test_frozen as evaluate_frozen


@pytest.fixture
def frozen_run(project):
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "experiment_overview.md",
        project / "experiment_overview.md",
    )
    directory = run(project, "ashare", "fold1", "atlas", 42, attempts=12)
    assert read_json(directory / "run.json")["library_size"] >= 2
    return directory


def finish_and_publish(root, directory):
    """Exercise the retained explicit publisher independently from normal completion."""
    experiment.finish_experiment(root, directory)
    model = directory / "model/test.json"
    return reporting.update_experiment_overview(
        root,
        directory,
        read_json(directory / "run.json"),
        read_json(directory / "oos.json"),
        read_json(model) if model.exists() else None,
        read_json(directory / "library_similarity.json"),
    )


def test_completion_matches_canonical_metrics_and_reuses_saved_outputs(
    project, frozen_run, monkeypatch
):
    original = {
        path: path.read_bytes()
        for path in [
            frozen_run / "run.json",
            frozen_run / "frozen/library.json",
            frozen_run / "checkpoint.json",
            *sorted((frozen_run / "factor_trials").glob("*.json")),
        ]
    }
    loads = []
    load = ParqMarketData.load_features

    def checked_load(self, **kwargs):
        loads.append(kwargs["end"])
        if kwargs["end"] > date(2020, 12, 31):
            assert (frozen_run / "model/fit.json").exists()
        return load(self, **kwargs)

    monkeypatch.setattr(ParqMarketData, "load_features", checked_load)
    overview_before = (project / "experiment_overview.md").read_bytes()
    result = experiment.finish_experiment(project, frozen_run)
    assert loads == [date(2020, 12, 31), date(2022, 12, 31)]
    oos = read_json(frozen_run / "oos.json")
    tested = read_json(frozen_run / "model/test.json")
    similarity = read_json(frozen_run / "library_similarity.json")
    assert (
        similarity["pair_count"]
        == similarity["factor_count"] * (similarity["factor_count"] - 1) // 2
    )
    assert similarity["valid_pairs"] == similarity["pair_count"]
    assert "Val 库内因子相似度" in (frozen_run / "report.md").read_text()
    assert "[模型报告](model/report.md)" in (frozen_run / "report.md").read_text()
    assert result == {"report": str(frozen_run / "report.md")}
    assert (project / "experiment_overview.md").read_bytes() == overview_before
    (frozen_run / "oos.json").unlink()
    canonical = evaluate_frozen(project, frozen_run)
    for actual, expected in zip(oos["results"], canonical["results"], strict=True):
        assert actual["expression_id"] == expected["expression_id"]
        assert actual["coverage"] == expected["coverage"]
        for a, b in zip(actual["metrics"], expected["metrics"], strict=True):
            assert {k: v for k, v in a.items() if k != "value"} == {
                k: v for k, v in b.items() if k != "value"
            }
            assert a["value"] == pytest.approx(b["value"], abs=1e-12)
    (frozen_run / "model/test.json").unlink()
    assert run_model(project, frozen_run, test=True)["reports"] == tested["reports"]
    saved_fit = (frozen_run / "model/fit.json").read_bytes()
    overview = (project / "experiment_overview.md").read_bytes()

    def forbid(*args, **kwargs):
        raise AssertionError("completed stages must not reload features or refit")

    monkeypatch.setattr(ParqMarketData, "load_features", forbid)
    monkeypatch.setattr("alpha_atlas.model.fit_models", forbid)
    assert experiment.finish_experiment(project, frozen_run) == result
    assert (project / "experiment_overview.md").read_bytes() == overview
    assert (frozen_run / "model/fit.json").read_bytes() == saved_fit
    assert all(path.read_bytes() == content for path, content in original.items())
    assert (frozen_run / "oos.json").exists()


def test_completion_reuses_external_run_outputs_through_symlink(project, frozen_run, monkeypatch):
    workspace = project / "comparison/workspace"
    workspace.mkdir(parents=True)
    for name in ("configs", "data", "artifacts"):
        (workspace / name).symlink_to(project / name, target_is_directory=True)
    alias = workspace / "artifacts/runs" / frozen_run.name
    result = experiment.finish_experiment(workspace, frozen_run)
    overview = workspace / "experiment_overview.md"
    assert not overview.exists()
    saved = {
        path: path.read_bytes()
        for path in (
            frozen_run / "run.json",
            frozen_run / "checkpoint.json",
            frozen_run / "frozen/library.json",
            frozen_run / "model/fit.json",
            frozen_run / "model/test.json",
            frozen_run / "oos.json",
            frozen_run / "library_similarity.json",
        )
    }

    def forbid(*args, **kwargs):
        raise AssertionError("completed stages must not reload features or refit")

    monkeypatch.setattr(ParqMarketData, "load_features", forbid)
    monkeypatch.setattr("alpha_atlas.model.fit_models", forbid)
    repeated = experiment.finish_experiment(workspace, alias)
    assert repeated["report"] == str(alias / "report.md")
    assert result["report"] == str(frozen_run / "report.md")
    assert not overview.exists()
    assert all(path.read_bytes() == content for path, content in saved.items())


@pytest.mark.parametrize("clipped", [False, True])
def test_similarity_reranks_each_common_intersection_and_reuses_frozen_clipping(
    clipped, monkeypatch
):
    raw = np.array(
        [
            [0, 1, 1, 2, 3, 4, np.nan, 6, np.inf],
            [2, 1, 3, 3, np.nan, 0, 1, 6, 4],
            [1, 0, 2, 4, 3, 1, 2, 5, 7],
        ],
        dtype=float,
    )
    directions = [-1, 1, -1]
    period = DateRange(date(2020, 1, 1), date(2020, 1, 1))
    fold = Fold(
        "synthetic",
        DateRange(date(2019, 1, 1), date(2019, 12, 31)),
        period,
        DateRange(date(2021, 1, 1), date(2021, 12, 31)),
    )
    clipping = (
        {"quantiles": [0.01, 0.99], "value": [0.5, 4.5], "target": [-1, 1]} if clipped else {}
    )
    spec = {
        "run_id": "synthetic",
        "config_fingerprint": "config",
        "library_sha256": "library",
        "snapshot_id": "data",
        "profile": {"panel": "product"},
        "rules": {"min_corr_overlap": 3, **({"clip_quantiles": [0.01, 0.99]} if clipped else {})},
    }
    frozen = {
        "factors": [
            {
                "compiled": {"factor_id": str(i)},
                "report": {"direction": direction, "clipping": clipping},
            }
            for i, direction in enumerate(directions)
        ]
    }
    panel = pl.DataFrame(
        {
            "row_id": range(9),
            "trading_day": [period.start] * 9,
            "target_end": [period.start] * 9,
            "target": np.linspace(-0.1, 0.1, 9),
            "eligible": [True] * 9,
            **{f"factor_{i}": raw[i] * direction for i, direction in enumerate(directions)},
        }
    )

    def forbid(*args, **kwargs):
        raise AssertionError("Val must never fit clipping bounds")

    monkeypatch.setattr("alpha_atlas.evaluation.clip_bounds", forbid)
    result = experiment._similarity(spec, frozen, panel, fold)
    expected = []
    for (i, j), measured in zip(itertools.combinations(range(3), 2), result["pairs"], strict=True):
        common = np.isfinite(raw[i]) & np.isfinite(raw[j])
        a, b = raw[i, common], raw[j, common]
        if clipped:
            a, b = np.clip(a, 0.5, 4.5), np.clip(b, 0.5, 4.5)
        corr = abs(np.corrcoef(average_rank(a), average_rank(b))[0, 1])
        expected.append(corr)
        assert measured["overlap"] == common.sum()
        assert measured["absolute_spearman"] == pytest.approx(corr, abs=1e-12)
    assert result["mean_absolute_spearman"] == pytest.approx(np.mean(expected))
    assert result["p90_absolute_spearman"] == pytest.approx(np.quantile(expected, 0.9))


def test_failure_does_not_publish_and_retry_only_computes_missing_stage(
    project, frozen_run, monkeypatch
):
    overview = (project / "experiment_overview.md").read_bytes()
    similarity = experiment._similarity

    def fail(*args, **kwargs):
        raise RuntimeError("injected similarity failure")

    monkeypatch.setattr(experiment, "_similarity", fail)
    with pytest.raises(RuntimeError, match="injected"):
        experiment.finish_experiment(project, frozen_run)
    assert (project / "experiment_overview.md").read_bytes() == overview
    saved = {
        path: path.read_bytes()
        for path in (
            frozen_run / "oos.json",
            frozen_run / "model/fit.json",
            frozen_run / "model/test.json",
        )
    }
    assert not (frozen_run / "library_similarity.json").exists()
    monkeypatch.setattr(experiment, "_similarity", similarity)

    def forbid(*args, **kwargs):
        raise AssertionError("saved IC and model stages must be reused")

    monkeypatch.setattr(experiment, "_frozen_factor_result", forbid)
    monkeypatch.setattr(experiment, "evaluate_models", forbid)
    monkeypatch.setattr("alpha_atlas.model.fit_models", forbid)
    load = ParqMarketData.load_features

    def checked(self, **kwargs):
        assert kwargs["end"] == date(2020, 12, 31)
        return load(self, **kwargs)

    monkeypatch.setattr(ParqMarketData, "load_features", checked)
    experiment.finish_experiment(project, frozen_run)
    assert all(path.read_bytes() == content for path, content in saved.items())
    assert (project / "experiment_overview.md").read_bytes() == overview


def test_completion_ignores_overview_layout_and_does_not_write_it(project, frozen_run):
    path = project / "experiment_overview.md"
    text = "# 自定义实验总览\n\n任意布局。\n"
    path.write_text(text, encoding="utf-8")
    result = experiment.finish_experiment(project, frozen_run)
    assert result == {"report": str(frozen_run / "report.md")}
    assert path.read_text(encoding="utf-8") == text
    assert (frozen_run / "oos.json").exists()
    assert (frozen_run / "model/test.json").exists()
    assert (frozen_run / "library_similarity.json").exists()


@pytest.mark.parametrize("change", ["benchmark", "model", "snapshot", "library", "similarity"])
def test_completed_cache_still_validates_inputs(project, frozen_run, monkeypatch, change):
    experiment.finish_experiment(project, frozen_run)
    overview = (project / "experiment_overview.md").read_bytes()
    if change == "benchmark":
        path = project / "configs/benchmark.toml"
        path.write_text(path.read_text().replace("min_coverage = 0.8", "min_coverage = 0.81"))
    elif change == "model":
        path = project / "configs/model.toml"
        path.write_text(path.read_text().replace("0.05", "0.1"))
    elif change == "snapshot":
        monkeypatch.setattr(ParqMarketData, "snapshot_id", lambda _: "changed")
    elif change == "library":
        path = frozen_run / "frozen/library.json"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        path = frozen_run / "library_similarity.json"
        cached = read_json(path)
        cached["library_sha256"] = "changed"
        atomic_json(path, cached)
    with pytest.raises(ValueError, match="changed|modified"):
        experiment.finish_experiment(project, frozen_run)
    assert (project / "experiment_overview.md").read_bytes() == overview


def test_empty_library_is_reported_without_fabricating_models(project):
    class Empty:
        def run(self, session):
            session.evaluate(Candidate("1"))

    directory = run(project, "ashare", "fold1", "empty", 42, attempts=5, method_impl=Empty())
    experiment.finish_experiment(project, directory)
    assert read_json(directory / "oos.json")["results"] == []
    assert read_json(directory / "library_similarity.json")["pair_count"] == 0
    assert not (directory / "model/fit.json").exists()
    assert not (directory / "model/test.json").exists()
    assert "组合模型不适用" in (directory / "report.md").read_text()
    assert not (project / "experiment_overview.md").exists()


def test_cli_run_and_frozen_resume_complete_by_default(project, monkeypatch, capsys):
    from alpha_atlas.cli import main

    logger = logging.getLogger("alpha_atlas.progress")
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "level", logger.level)
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atlas",
            "--root",
            str(project),
            "run",
            "--asset",
            "ashare",
            "--fold",
            "fold1",
            "--method",
            "atlas",
            "--attempts",
            "8",
            "--quiet",
        ],
    )
    main()
    result = json.loads(capsys.readouterr().out)
    directory = Path(result["run_dir"])
    assert "overview" not in result
    assert not (project / "experiment_overview.md").exists()
    assert (directory / "oos.json").exists()
    assert (directory / "model/test.json").exists()
    assert (directory / "library_similarity.json").exists()

    def forbid(*args, **kwargs):
        raise AssertionError("frozen resume must not repeat search")

    monkeypatch.setattr("alpha_atlas.runner.resume", forbid)
    monkeypatch.setattr(
        sys, "argv", ["atlas", "--root", str(project), "resume", str(directory), "--quiet"]
    )
    main()
    assert json.loads(capsys.readouterr().out) == result


def test_overview_rejects_partial_table_edits_and_keeps_the_document(project, frozen_run):
    finish_and_publish(project, frozen_run)
    path = project / "experiment_overview.md"
    text = path.read_text().replace("| 方法 / 模型 | 数据 |", "| 手动修改标题 | 数据 |")
    path.write_text(text)
    with pytest.raises(ValueError, match="table headers changed"):
        finish_and_publish(project, frozen_run)
    assert path.read_text() == text


@pytest.mark.parametrize("mismatch", [None, "data", "fold_seed"])
def test_completion_preserves_common_settings_without_restoring_columns(
    project, frozen_run, monkeypatch, mismatch
):
    finish_and_publish(project, frozen_run)
    path = project / "experiment_overview.md"
    lines = path.read_text().splitlines()
    settings_count = sum(line.startswith("**数据设置**：") for line in lines)
    for table in reversed(
        [
            i
            for i, line in enumerate(lines)
            if line.startswith("| ")
            and "数据" in line.split(" | ")
            and "A 股"
            in next((title for title in reversed(lines[:i]) if title.startswith("### ")), "")
        ]
    ):
        headers = lines[table].strip("| ").split(" | ")
        data = lines[table + 2].strip("| ").split(" | ")[headers.index("数据")]
        if mismatch == "data":
            data += " / different snapshot"
        fold_seed = "fold1 / 43" if mismatch == "fold_seed" else "fold1 / 42"
        keep = [i for i, name in enumerate(headers) if name not in {"数据", "Fold / seed"}]
        stop = table
        while stop < len(lines) and lines[stop].startswith("| "):
            cells = lines[stop].strip("| ").split(" | ")
            if stop >= table + 2 and headers[0] == "方法 / 模型":
                cells[0] = cells[0].removesuffix(" / fold1 / 42")
            lines[stop] = "| " + " | ".join(cells[i] for i in keep) + " |"
            stop += 1
        lines[table:table] = [f"**数据设置**：{data}。", "", f"**Fold / seed**：{fold_seed}。", ""]
    path.write_text("\n".join(lines) + "\n")
    before = path.read_bytes()

    def forbid(*args, **kwargs):
        raise AssertionError("Changing the overview layout must reuse saved evaluations")

    monkeypatch.setattr(ParqMarketData, "load_features", forbid)
    monkeypatch.setattr("alpha_atlas.model.fit_models", forbid)
    if mismatch:
        with pytest.raises(ValueError, match="common .* setting changed"):
            finish_and_publish(project, frozen_run)
    else:
        finish_and_publish(project, frozen_run)
        assert before.decode().count("**数据设置**：") == settings_count + 2
    assert path.read_bytes() == before


def test_incomplete_cached_model_test_is_not_published(project, frozen_run):
    experiment.finish_experiment(project, frozen_run)
    path = project / "experiment_overview.md"
    before = path.read_bytes()
    tested = read_json(frozen_run / "model/test.json")
    del tested["reports"]["test"]["lightgbm"]
    atomic_json(frozen_run / "model/test.json", tested)
    with pytest.raises(ValueError, match="model Test is incomplete"):
        experiment.finish_experiment(project, frozen_run)
    assert path.read_bytes() == before


def test_fold2_results_and_pending_tasks_are_kept_separate(project):
    path = project / "experiment_overview.md"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "experiment_overview.md", path)

    class Price:
        def run(self, session):
            assert session.evaluate(Candidate("$adj_close")).accepted

    directory = run(project, "ashare", "fold2", "react", 42, attempts=1, method_impl=Price())
    finish_and_publish(project, directory)
    text = path.read_text()
    assert "### A 股因子搜索：fold2" in text
    assert "### A 股库内因子相似度：fold2 / contract 窗口" in text
    assert "Val：2022-01-01–2023-12-31" in text
    pending = text.split("## 待开展实验", 1)[1]
    first = pending.split("### Fold1", 1)[1].split("### Fold2", 1)[0]
    second = pending.split("### Fold2", 1)[1].split("### Fold 待定", 1)[0]
    assert "| A 股 | ReAct |" in first
    assert "| A 股 | ReAct |" not in second
    assert "| 期货 | ReAct |" in second


def test_futures_existing_tables_preserve_manual_rows_and_researcher(project):
    overview = project / "experiment_overview.md"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "experiment_overview.md", overview)
    original = overview.read_text()
    rows = []
    for year in range(2015, 2023):
        for product, amount in (("P", 1.0), ("Q", 9.0)):
            for index in range(50):
                rows.append(
                    {
                        "timestamp": datetime(year, 1, 1, 9) + timedelta(minutes=5 * index),
                        "trading_day": date(year, 1, 1),
                        "exchange": "XSGE",
                        "product": product,
                        "instrument_id": f"{product}{year}",
                        "close": float(100 * np.exp(0.00001 * index**2)),
                        "amount": amount,
                    }
                )
    atomic_parq(with_row_id(pl.DataFrame(rows)), project / "data/futures/bars/fixture.parq")
    atomic_json(
        project / "data/futures/manifest.json",
        {"status": "complete", "snapshot_id": "synthetic-futures", "rows": len(rows)},
    )

    class Price:
        def run(self, session):
            assert session.evaluate(Candidate("$close")).accepted

    directory = run(
        project,
        "futures",
        "fold1",
        "price",
        42,
        attempts=1,
        field_names=["close"],
        method_impl=Price(),
    )
    finish_and_publish(project, directory)
    text = overview.read_text()
    assert all(line in text for line in original.splitlines() if line.startswith("| 汤子逸 |"))
    identity = f"(artifacts/runs/{directory.name}/report.md)"
    assert text.count(identity) == 2
    main_row = next(
        line for line in text.splitlines() if identity in line and line.startswith("| — |")
    )
    text = text.replace(main_row, main_row.replace("| — |", "| 人工姓名 |", 1), 1)
    overview.write_text(text)
    finish_and_publish(project, directory)
    assert overview.read_text() == text
    assert "| 人工姓名 |" in text
