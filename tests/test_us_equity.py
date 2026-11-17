import json
import logging
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from math import prod
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from alpha_atlas import cli
from alpha_atlas.assets.audit import audit
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.assets.us_equity import import_us_equity
from alpha_atlas.config import asset_config
from alpha_atlas.contracts import Candidate
from alpha_atlas.evaluation import compute_factor_values
from alpha_atlas.expressions import compile_factor
from alpha_atlas.factor.metrics import compute_forward_returns
from alpha_atlas.factor.transform import transform_factor
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.runner import run
from alpha_atlas.runner import test_frozen as evaluate_frozen

ROOT = Path(__file__).resolve().parents[1]


def test_factor_transform_uses_each_date_and_sector(monkeypatch):
    def forbid_quantization(*args, **kwargs):
        pytest.fail("factor scoring must skip quantization")

    monkeypatch.setattr("alpha_atlas.factor.transform.quantize_factor", forbid_quantization)
    signal = np.r_[-1000.0, np.arange(1.0, 100.0), 10000.0]
    clipped = np.clip(signal, 1.0, 99.0)
    expected = (clipped - clipped.mean()) / clipped.std(ddof=1)
    rows = []
    for day in (date(2024, 1, 2), date(2024, 1, 3)):
        for sector in (10, 20):
            offset = 10_000.0 * (sector + day.day)
            rows += [(day, sector, True, x + offset) for x in signal]
    rows += [(rows[0][0], 10, False, -1e9), (rows[0][0], None, True, 5.0)]
    frame = (
        pl.DataFrame(rows, schema=["trading_day", "gsector", "eligible", "signal"], orient="row")
        .with_row_index("row_id")
        .with_columns(
            pl.col("row_id").cast(pl.String).alias("instrument_id"),
            pl.lit("US").alias("exchange"),
            pl.col("trading_day").cast(pl.Datetime).alias("timestamp"),
        )
    )
    registry = OperatorRegistry()
    fields = {"signal"}
    values = compute_factor_values(
        compile_factor("$signal", fields, registry),
        frame,
        fields,
        registry,
        asset_config(ROOT, "us_equity"),
    ).sort("row_id")["value"]
    assert values.head(404).to_numpy() == pytest.approx(np.tile(expected, 4))
    assert values.tail(2).to_list() == [None, None]


@pytest.mark.parametrize(
    "sizes, expected", [(None, [1, 2, 3, 4, 5]), ((0.5, 0.5), [1, 1, 1, 2, 2])]
)
def test_transform_can_return_optional_buckets(sizes, expected):
    frame = pl.DataFrame({"date": [date(2021, 1, 4)] * 5, "key": range(5), "signal": range(5)})
    scored = transform_factor(frame, "signal", "score", quantile_col="bucket", bucket_sizes=sizes)
    assert scored["bucket"].to_list() == expected


def test_forward_returns_compound_consecutive_future_sessions():
    dates = [date(2025, 1, 1) + timedelta(days=day) for day in range(23)]
    rates = [0.5] + [(-1) ** i * i / 1000 for i in range(1, 23)]
    full = pl.DataFrame({"date": dates, "key": ["full"] * len(dates), "total_return": rates})
    gap = full.filter(pl.col("date") != dates[7]).with_columns(pl.lit("gap").alias("key"))
    lost = full.with_columns(
        pl.lit("loss").alias("key"),
        pl.when(pl.col("date") == dates[1])
        .then(-1.0)
        .otherwise(pl.col("total_return"))
        .alias("total_return"),
    )
    bad = full.with_columns(
        pl.lit("invalid").alias("key"),
        pl.when(pl.col("date") == dates[1])
        .then(float("inf"))
        .otherwise(pl.col("total_return"))
        .alias("total_return"),
    )
    data = pl.concat([full, gap, lost, bad]).reverse()
    result = compute_forward_returns(data.lazy(), [21])
    first = dict(
        result.filter(pl.col("date") == dates[0]).select("key", "forward_return_21").rows()
    )
    assert first["full"] == pytest.approx(prod(1 + rate for rate in rates[1:22]) - 1)
    assert first["loss"] == -1.0
    assert first["gap"] is None and first["invalid"] is None
    assert result.filter(pl.col("date") == dates[-1])["forward_return_21"].null_count() == 4


def _write_month(source, year):
    rows = []
    for day in range(25):
        for sector in (10, 20):
            for instrument in range(10):
                close = 25.0
                rows.append(
                    {
                        "date": date(year, 1, 1) + timedelta(days=day),
                        "permno": sector * 100 + instrument,
                        "gsector": sector,
                        "ggroup": sector * 100,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "vol": float(sector * 100 + instrument),
                        "prcvol": float(sector * 100 + instrument) * close,
                        "market_cap": 100.0 + instrument,
                        "shares_outstanding": 40.0 + instrument,
                        "total_return": instrument / 1000,
                        "at": float(sector * 100 + instrument),
                        "sale_lag1y": float(sector + instrument),
                    }
                )
    path = source / f"part-{year}-01.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


@pytest.mark.parametrize("requested_horizon", [None, 7])
def test_import_then_search_and_score_us_equity(tmp_path, requested_horizon):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    source = tmp_path / "atlas_data"
    source.mkdir()
    for year in (2009, 2010, 2022, 2025):
        _write_month(source, year)
    test_month = source / "part-2025-01.parquet"
    test_rows = pl.read_parquet(test_month)
    first_day = pl.col("date") == date(2025, 1, 1)
    missing_unrelated = first_day & (pl.col("permno") == 1001)
    missing_factor_input = first_day & (pl.col("permno") == 1002)
    test_rows.with_columns(
        pl.when(~missing_unrelated).then(pl.col("high", "sale_lag1y")),
        pl.when(~missing_factor_input).then(pl.col("at")),
    ).write_parquet(test_month)
    destination = tmp_path / "data/us_equity"
    manifest = import_us_equity(source, destination)
    assert manifest["rows"] == 2000
    assert manifest["actual_start"] == "2009-01-01"
    assert manifest["actual_end"] == "2025-01-25"
    assert (source / "part-2025-01.parquet").is_file()
    fields = (
        "open",
        "high",
        "low",
        "close",
        "vol",
        "prcvol",
        "market_cap",
        "shares_outstanding",
        "at",
        "sale_lag1y",
    )
    with ParqMarketData(destination, "all") as market:
        assert market.get_fields() == fields
        with pytest.raises(ValueError, match="US equity factor fields exclude metadata"):
            market.load_features(fields=["total_return"], end=date(2009, 1, 1))
        features = market.load_features(fields=["at", "open"], end=date(2009, 1, 1))
        first = features.row(0, named=True)
        assert first["instrument_id"] == "1000" and first["gsector"] == 10
        assert first["ggroup"] == 1000
        assert first["timestamp"] == datetime(2009, 1, 1, 21, tzinfo=UTC)
        assert "close" not in features.columns
        assert first["open"] == 25.0
        assert first["total_return"] == 0.0 and first["at"] == 1000.0

    horizon = requested_horizon or 21

    class AssetsSearch:
        def run(self, session):
            assert session.get_fields() == fields
            context = session.get_context()
            assert context.target.horizon_bars == horizon
            assert context.target.return_type == "compounded_return"
            feedback = session.evaluate(Candidate("$at + $vol"))
            assert feedback.accepted, feedback.reason
            assert feedback.report.metrics[1].value > 0.99

    directory = run(
        tmp_path,
        "us_equity",
        "us_equity",
        "random",
        42,
        1,
        method_impl=AssetsSearch(),
        target_horizon=requested_horizon,
    )
    spec = json.loads((directory / "run.json").read_text())
    assert spec["profile"]["target_horizon_bars"] == horizon
    assert spec.get("target_horizon_override") == requested_horizon
    result = evaluate_frozen(tmp_path, directory)["results"]
    assert len(result) == 1 and result[0]["status"] == "success"
    assert result[0]["metrics"][0]["value"] > 0.99
    rows_with_target = (25 - horizon) * 20
    assert result[0]["metrics"][0]["n_obs"] == rows_with_target - 1
    assert result[0]["coverage"] == pytest.approx((rows_with_target - 1) / rows_with_target)


def test_failed_import_does_not_publish_partial_months(tmp_path):
    _write_month(tmp_path, 2022)
    path = _write_month(tmp_path, 2023)
    duplicate = pl.read_parquet(path).head(1)
    pl.concat([duplicate, duplicate]).write_parquet(path)
    destination = tmp_path / "data/us_equity"
    with pytest.raises(ValueError, match=r"duplicate \(date, permno\) keys"):
        import_us_equity(tmp_path, destination)
    assert not destination.exists()


def test_audit_reports_timezone_aware_anomaly(tmp_path):
    part = _write_month(tmp_path, 2010)
    anomaly = (
        pl.read_parquet(part)
        .head(1)
        .with_columns(
            pl.lit(24.0).alias("high"),
            pl.lit(None, dtype=pl.Float64).alias("sale_lag1y"),
            pl.lit(float("inf")).alias("vol"),
        )
    )
    anomaly.write_parquet(part)
    destination = tmp_path / "data/us_equity"
    import_us_equity(tmp_path, destination)

    result = audit(destination)
    assert result["invalid_ohlc"] == 1 and not result["passed"] and result["research_ready"]
    assert result["fundamental_columns"] == ["at", "sale_lag1y"]
    quality = result["column_quality"]
    assert len(quality) == result["column_count"]
    assert quality["sale_lag1y"] == {"null_count": 1, "nonfinite_count": 0}
    assert quality["vol"] == {"null_count": 0, "nonfinite_count": 1}
    assert quality["gsector"] == quality["timestamp"] == {"null_count": 0, "nonfinite_count": 0}
    ((timestamp, exchange, instrument, opening, high, low, closing),) = result["anomalies"]
    assert isinstance(timestamp.tzinfo, ZoneInfo)
    assert timestamp == datetime(2010, 1, 1, 21, tzinfo=UTC)
    assert (exchange, instrument, opening, high, low, closing) == ("US", "1000", 25, 24, 24.75, 25)
    report = json.loads((destination / "audit.json").read_text())
    assert datetime.fromisoformat(report["anomalies"][0][0]) == timestamp
    with ParqMarketData(destination, "all") as market:
        row = market.load_features(fields=["at"], end=date(2010, 1, 1)).row(0, named=True)
    assert row["at"] is None and not row["eligible"]
    assert row["total_return"] == 0.0


def test_named_us_equity_cli_search_only(tmp_path, monkeypatch, capsys):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    source = tmp_path / "atlas_data"
    source.mkdir()
    for year in (2009, 2010, 2022, 2025):
        _write_month(source, year)
    destination = tmp_path / "data/us_equity"
    import_us_equity(source, destination)
    name = "us_equity_v3asyc_deepseek"
    config_path = tmp_path / "configs/versions" / f"{name}.json"
    version = json.loads(config_path.read_text())
    with ParqMarketData(destination, "all") as market:
        version["fields"] = list(market.get_fields())
        version["data"]["sources"]["base"]["snapshot_id"] = market.snapshot_id()
    config_path.write_text(json.dumps(version))

    class SyntheticSearch:
        def __init__(self):
            self.candidates = iter(["$at", "$at * $at", "$at / $market_cap"])
            self.feedback = []

        def set_session(self, session):
            self.session = session

        def ask(self, context, count=1):
            assert count == 1
            assert context.asset == "us_equity" and context.frequency == "1d"
            assert context.target.horizon_bars == 21
            assert context.target.return_type == "compounded_return"
            return [Candidate(next(self.candidates))]

        def tell(self, feedback):
            self.feedback.extend(feedback)

    search = SyntheticSearch()

    def make_synthetic_method(method, seed, options):
        assert (method, seed) == ("v3_async", 42)
        assert options == version["method_configs"]["v3_async"]
        return search

    def forbid_oos(*args, **kwargs):
        pytest.fail("--search-only must not evaluate OOS")

    monkeypatch.setattr("alpha_atlas.runner.make_method", make_synthetic_method)
    monkeypatch.setattr("alpha_atlas.experiment.finish_experiment", forbid_oos)
    logger = logging.getLogger("alpha_atlas.progress")
    for attribute in ("handlers", "level", "propagate"):
        monkeypatch.setattr(logger, attribute, getattr(logger, attribute))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atlas",
            "run",
            "--config",
            name,
            "--method",
            "v3_async",
            "--attempts",
            "3",
            "--search-only",
        ],
    )
    cli.main()

    directory = Path(json.loads(capsys.readouterr().out)["run_dir"])
    spec = json.loads((directory / "run.json").read_text())
    assert spec["status"] == "frozen" and spec["attempts"] == 3
    assert spec["experiment_version"] == version
    assert spec["snapshot_id"] == version["data"]["sources"]["base"]["snapshot_id"]
    assert len(list((directory / "factor_trials").glob("*.json"))) == 3
    assert len(search.feedback) == 3
    assert all(feedback.report.status == "success" for feedback in search.feedback)
    assert search.feedback[0].accepted
    frozen = json.loads((directory / "frozen/library.json").read_text())
    assert frozen["factors"] and not (directory / "oos.json").exists()

    evaluator = search.session._evaluator
    assert evaluator.features["trading_day"].max() <= date(2022, 12, 31)
    metadata = evaluator.features.select("row_id", "trading_day", "gsector", "instrument_id")
    first_cohort = (pl.col("trading_day") == date(2022, 1, 1)) & (pl.col("gsector") == 10)
    targets = metadata.join(evaluator.targets, on="row_id").filter(first_cohort)
    targets = targets.sort("instrument_id")
    raw = (1 + np.arange(10) / 1000) ** 21 - 1
    clipped = np.clip(raw, *np.quantile(raw, [0.01, 0.99]))
    assert targets["target_raw"].to_numpy() == pytest.approx(raw)
    assert targets["target"].to_numpy() == pytest.approx(clipped - clipped.mean())

    observation = evaluator.observation(search.feedback[0].report.observation_ref).frame
    scores = metadata.join(observation, on="row_id").filter(first_cohort).sort("instrument_id")
    signal = np.arange(1000.0, 1010.0)
    signal = np.clip(signal, *np.quantile(signal, [0.01, 0.99]))
    assert scores["value"].to_numpy() == pytest.approx(
        (signal - signal.mean()) / signal.std(ddof=1)
    )
