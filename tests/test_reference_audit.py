import importlib.util
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest


def test_fixed_audit_freezes_all_definitions_and_reports_test_after_rejection(
    tmp_path, monkeypatch
):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "reference_audit", root / "scripts/evaluate_reference.py"
    )
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    shutil.copytree(root / "configs", tmp_path / "configs")
    config = tmp_path / "configs/assets/futures.toml"
    config.write_text(
        config.read_text().replace("min_train_val_ic = 0.005", "min_train_val_ic = 1.0")
    )
    rows = []
    for year in (2016, 2019, 2021):
        for product in range(2):
            for t in range(300):
                stamp = datetime(year, 1, 1, 9) + timedelta(minutes=5 * t)
                close = 100 + product * 10 + t * 0.03 + np.sin(t / 10 + product)
                rows.append(
                    {
                        "row_id": len(rows),
                        "timestamp": stamp,
                        "trading_day": stamp.date(),
                        "exchange": "X",
                        "instrument_id": f"{year}-{product}",
                        "product": str(product),
                        "segment_id": 1,
                        "eligible": True,
                        "target_eligible": True,
                        "close": close,
                        "high": close + 1,
                        "low": close - 1,
                        "open_interest": 1000.0 + t,
                        "amount": 100.0,
                    }
                )
    data = pl.DataFrame(rows)
    calls = []

    class Market:
        columns = set(data.columns)

        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def snapshot_id(self):
            return "synthetic-fixed-audit"

        def load_features(self, *, fields, start, end):
            calls.append(end.year)
            if end.year == 2022:
                paths = list((tmp_path / "artifacts/reference-evaluations").glob("*/run.json"))
                assert len(paths) == 1
                saved = json.loads(paths[0].read_text())
                assert saved["status"] == "frozen"
                assert saved["library_sha256"]
            return data.filter(pl.col("trading_day") <= end)

    monkeypatch.setattr(audit, "ParqMarketData", Market)
    directory = audit.evaluate(tmp_path, "fold1", 1)
    assert calls == [2020, 2022]
    frozen = json.loads((directory / "frozen/library.json").read_text())
    oos = json.loads((directory / "oos.json").read_text())
    assert len(frozen["factors"]) == len(oos["results"]) == 10
    assert all(f["admission_reason"] != "accepted" for f in frozen["factors"])
    assert all(r["status"] == "success" for r in oos["results"])
    for item, result in zip(frozen["factors"], oos["results"], strict=True):
        direction = item["report"]["direction"]
        raw = {m["name"]: m for m in result["metrics"] if m["split"] == "test_raw"}
        for m in result["metrics"]:
            if m["split"] == "test":
                assert m["value"] == pytest.approx(raw[m["name"]]["value"] * direction)
        assert 0 < item["train_coverage"] <= 1
        assert result["eligible_rows"] > 0
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "inventory_mom_20" in report and "train覆盖" in report
    assert "quality_threshold" in report
