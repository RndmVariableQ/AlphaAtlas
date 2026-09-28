import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.runner import run
from alpha_atlas.runner import test_frozen as evaluate_frozen


@pytest.fixture
def project(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    rng = np.random.default_rng(7)
    rows = []
    for year in range(2015, 2027):
        for day in range(24):
            for instrument in range(8):
                price = 20 + instrument + day * (instrument + 1) * 0.05
                price += rng.normal(0, 0.05)
                rows.append(
                    {
                        "timestamp": datetime(year, 1, 1, 15) + timedelta(days=day),
                        "trading_day": date(year, 1, 1) + timedelta(days=day),
                        "exchange": "XSHG",
                        "instrument_id": f"{instrument:06d}.XSHG",
                        "close": price,
                        "adj_close": price,
                        "volume": float(100 + instrument * 10 + day),
                        "amount": price * (100 + instrument * 10 + day),
                        "in_union1800": True,
                        "in_hs300": instrument < 4,
                        "in_zz1000": instrument >= 4,
                    }
                )
    frame = with_row_id(pl.DataFrame(rows))
    atomic_parq(frame, tmp_path / "data/ashare/bars/fixture.parq")
    atomic_json(
        tmp_path / "data/ashare/manifest.json",
        {"status": "complete", "snapshot_id": "synthetic", "rows": len(rows)},
    )
    return tmp_path


def test_market_restricts_labels_and_preserves_warmup(project):
    with ParqMarketData(project / "data/ashare", "hs300") as market:
        frame = market.load_features(fields=["close"], end=date(2020, 12, 31))
        assert frame["instrument_id"].n_unique() == 8
        assert frame.filter(pl.col("eligible"))["instrument_id"].n_unique() == 4
        with pytest.raises(ValueError):
            market.load_features(fields=["label_return"], end=date(2020, 1, 1))


def test_bad_ohlc_and_trading_flags_are_quarantined_without_dropping_rows(project):
    path = project / "data/ashare/bars/fixture.parq"
    frame = pl.read_parquet(path).with_columns(
        pl.col("close").alias("open"),
        (pl.col("close") + 1).alias("high"),
        (pl.col("close") - 1).alias("low"),
        pl.lit(False).alias("is_st"),
        pl.lit(False).alias("is_suspended"),
    )
    first_id, second_id = frame["row_id"][:2].to_list()
    frame = frame.with_columns(
        pl.when(pl.col("row_id") == first_id)
        .then(pl.col("high") + 10)
        .otherwise(pl.col("open"))
        .alias("open"),
        (pl.col("row_id") == second_id).alias("is_st"),
    )
    atomic_parq(frame, path)
    with ParqMarketData(project / "data/ashare", "union1800") as market:
        loaded = market.load_features(fields=["close"], end=date(2026, 12, 31))
    assert loaded.height == frame.height
    assert loaded.filter(pl.col("row_id") == first_id)["close"][0] is None
    assert loaded.filter(pl.col("row_id").is_in([first_id, second_id]))["eligible"].sum() == 0


def test_fold2_uses_2019_through_2021_for_training(project):
    from alpha_atlas.config import load_fold

    fold = load_fold(project, "fold2")
    assert fold.train.start == date(2019, 1, 1)
    assert fold.train.end == date(2021, 12, 31)
    assert fold.val.start == date(2022, 1, 1)
    assert fold.val.end == date(2023, 12, 31)
    assert fold.test.start == date(2024, 1, 1)
    assert fold.test.end == date(2025, 12, 31)


@pytest.mark.parametrize("method", ["random", "gp", "mcts", "atlas"])
def test_end_to_end_isolation_and_oos_freeze(project, method):
    path = run(project, "ashare", "fold1", method, 42, attempts=5)
    spec = json.loads((path / "run.json").read_text())
    assert spec["status"] == "frozen"
    assert spec["attempts"] == 5
    result = evaluate_frozen(project, path)
    assert len(result["results"]) == spec["library_size"]
    assert evaluate_frozen(project, path) == result
    original = (path / "frozen/library.json").read_text()
    (path / "frozen/library.json").write_text(original + " ")
    with pytest.raises(ValueError, match="modified"):
        evaluate_frozen(project, path)
    second = run(project, "ashare", "fold1", method, 42, attempts=5)
    assert path != second
    left = json.loads((second / "frozen/library.json").read_text())["factors"]
    right = json.loads(original)["factors"]
    assert len(left) == len(right)
    for a, b in zip(left, right, strict=True):
        assert a["candidate"] == b["candidate"]
        assert a["report"]["direction"] == b["report"]["direction"]
        assert a["report"]["coverage"] == pytest.approx(b["report"]["coverage"])
        for ma, mb in zip(a["report"]["metrics"], b["report"]["metrics"], strict=True):
            assert ma["value"] == pytest.approx(mb["value"])


def test_cached_oos_cannot_bypass_config_checks(project):
    path = run(project, "ashare", "fold1", "random", 42, attempts=1)
    evaluate_frozen(project, path)
    spec = json.loads((path / "run.json").read_text())
    spec["fields"].append("future_field")
    atomic_json(path / "run.json", spec)
    with pytest.raises(ValueError, match="configuration was modified"):
        evaluate_frozen(project, path)


def test_empty_library_freezes_and_reports(project):
    from alpha_atlas.contracts import Candidate

    class EmptyMethod:
        def run(self, session):
            session.evaluate(Candidate("1"))

    path = run(project, "ashare", "fold1", "constant", 0, attempts=1, method_impl=EmptyMethod())
    assert evaluate_frozen(project, path)["results"] == []


@pytest.mark.parametrize("minimum", [0.005, 1.0])
def test_futures_dual_metrics_through_freeze_oos_and_report(project, minimum):
    from alpha_atlas.contracts import Candidate
    from alpha_atlas.evaluation import FUTURES_METRICS

    config_path = project / "configs/assets/futures.toml"
    config_path.write_text(
        config_path.read_text().replace(
            "min_train_val_ic = 0.005", f"min_train_val_ic = {minimum}"
        ),
        encoding="utf-8",
    )
    expected_accepted = minimum < 1
    rows = []
    for year in range(2015, 2023):
        for day in (1, 2):
            for product, amount in [("P", 1.0), ("Q", 9.0)]:
                for i in range(80):
                    rows.append(
                        {
                            "timestamp": datetime(year, 1, day, 9) + timedelta(minutes=5 * i),
                            "trading_day": date(year, 1, day),
                            "exchange": "XSGE",
                            "product": product,
                            "instrument_id": f"{product}{year}{day}",
                            "close": float(100 * np.exp(0.00001 * i**2)),
                            "amount": amount,
                        }
                    )
    atomic_parq(with_row_id(pl.DataFrame(rows)), project / "data/futures/bars/fixture.parq")
    atomic_json(
        project / "data/futures/manifest.json",
        {"status": "complete", "snapshot_id": "synthetic-futures", "rows": len(rows)},
    )

    class CloseMethod:
        def run(self, session):
            context = session.get_context()
            assert session.get_fields() == ("close",)
            assert context.metric == FUTURES_METRICS[1]
            assert session.evaluate(Candidate("$amount")).reason == "compile_error"
            feedback = session.evaluate(Candidate("$close"))
            assert feedback.accepted is expected_accepted
            assert feedback.reason == ("accepted" if expected_accepted else "quality_threshold")
            assert len(feedback.report.metrics) == 12

    path = run(
        project,
        "futures",
        "fold1",
        "close",
        0,
        attempts=2,
        field_names=["close"],
        method_impl=CloseMethod(),
    )
    saved = (path / "frozen/library.json").read_bytes()
    result = evaluate_frozen(project, path)
    spec = json.loads((path / "run.json").read_text())
    assert spec["profile"]["min_train_val_ic"] == minimum
    assert spec["rules"]["max_abs_corr"] == 0.7
    report = (path / "report.md").read_text(encoding="utf-8")
    assert f"train、val 各自全区间的主指标 IC（训练方向）均 > {minimum}" in report
    assert "绝对 Spearman < 0.7" in report
    if not expected_accepted:
        assert result["results"] == []
        return
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["status"] == "success" and "metric" not in entry
    names = list(reversed(FUTURES_METRICS[:2]))
    assert [m["name"] for m in entry["metrics"]] == names + [
        n.replace("pearson", "spearman") for n in names
    ]
    assert all(
        m["value"] == pytest.approx(1) and m["n_groups"] == 2 and m["split"] == "test"
        for m in entry["metrics"][2:]
    )
    assert evaluate_frozen(project, path) == result
    assert (path / "frozen/library.json").read_bytes() == saved
    report = (path / "report.md").read_text(encoding="utf-8")
    assert report.count("√成交额加权") == 4
    assert report.count("品种等权") == 4
    assert all(0 < m["value"] < 1 and m["n_groups"] == 2 for m in entry["metrics"][:2])
