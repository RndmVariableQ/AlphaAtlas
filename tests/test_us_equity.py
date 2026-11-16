import json
import shutil
from datetime import UTC, date, datetime, timedelta
from math import prod
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

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
