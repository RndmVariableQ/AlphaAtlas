import json
import logging
import runpy
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "v3async10_luna_residual_gate_fold2_20261109",
        "v3async10_luna_residual_gate_r2_fold2_20261109",
        "v3async10_luna_residual_gate_r3_fold2_20261109",
    ],
)
def test_historical_v3_entry_does_not_publish_overview(monkeypatch, tmp_path, capsys, name):
    root = Path(__file__).resolve().parents[1]
    script = root / "artifacts/comparisons" / name / "run.py"
    if not script.exists():
        pytest.skip("historical local artifact is not present")
    overview = root / "experiment_overview.md"
    before = overview.read_bytes()
    directory = tmp_path / "synthetic-run"
    completed = []
    monkeypatch.setattr(sys, "argv", [str(script)])
    monkeypatch.setattr("alpha_atlas.runner.run", lambda *args, **kwargs: directory)
    monkeypatch.setattr(
        "alpha_atlas.experiment.finish_experiment", lambda root, run: completed.append(run)
    )

    def unexpected_publish(*args, **kwargs):
        pytest.fail("historical run attempted to publish the overview")

    monkeypatch.setattr("alpha_atlas.reporting.update_experiment_overview", unexpected_publish)
    logger = logging.getLogger("alpha_atlas.progress")
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "level", logger.level)
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    runpy.run_path(str(script), run_name="__main__")

    assert completed == [directory]
    assert json.loads(capsys.readouterr().out) == {"run_dir": str(directory)}
    assert overview.read_bytes() == before


def test_historical_similarity_report_does_not_write_overview(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/similarity.py"
    if not script.exists():
        pytest.skip("historical local artifact is not present")
    publish = runpy.run_path(str(script))["publish"]
    publish.__globals__["ROOT"] = tmp_path
    records = tmp_path / "docs/records/experiments.md"
    records.parent.mkdir(parents=True)
    records.write_text("existing records")
    overview = tmp_path / "experiment_overview.md"
    overview.write_text("existing overview")

    publish([])

    assert overview.read_text() == "existing overview"
    assert "Val/product 库内相似度补评" in records.read_text()
