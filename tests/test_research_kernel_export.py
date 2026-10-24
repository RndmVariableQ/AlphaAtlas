from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest

from alpha_atlas.assets import research_kernel
from alpha_atlas.assets.audit import audit
from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.market import ParqMarketData

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def kernel(monkeypatch):
    db = duckdb.connect(":memory:")
    db.execute("CREATE SCHEMA market")
    db.execute("""CREATE TABLE market.bars_1m (
        exchange VARCHAR, product VARCHAR, instrument_id VARCHAR, trading_day DATE,
        bar_time TIMESTAMPTZ, adj_close DOUBLE, adj_vwap DOUBLE, volume BIGINT,
        turnover DOUBLE, adj_cum_factor DOUBLE, vwap_flag VARCHAR, source VARCHAR,
        quality VARCHAR, is_dominant BOOLEAN, received_at TIMESTAMPTZ, ingest_seq BIGINT,
        adj_open DOUBLE, adj_high DOUBLE, adj_low DOUBLE, open_interest DOUBLE
    )""")

    def request(base, path, payload=None):
        if path == "/api/dataset":
            return {"source": "ricequant", "quality": "final", "interval": "1m"}
        assert path == "/api/query"
        assert payload["sql"].lstrip().lower().startswith(("select", "with"))
        cursor = db.execute(payload["sql"], payload["parameters"])
        return {"columns": [item[0] for item in cursor.description], "rows": cursor.fetchall()}

    monkeypatch.setattr(research_kernel, "_request", request)
    yield db, request
    db.close()


def minute_row(minute, *, instrument="M1", day=date(2020, 1, 2)):
    no_trade = minute in (6, 7)
    volume = 0 if no_trade else minute
    flag = "no_trade" if no_trade else {2: "clamped", 3: "proxy"}.get(minute, "ok")
    return [
        "DCE",
        "M",
        instrument,
        day,
        datetime(day.year, day.month, day.day, 9, minute, tzinfo=SHANGHAI),
        100.0 + minute,
        100.0 + minute,
        volume,
        volume * 1000.0,
        2.0,
        flag,
        "ricequant",
        "final",
        True,
        datetime(2020, 3, 1, tzinfo=SHANGHAI),
        1,
        99.5 + minute,
        102.0 + minute,
        98.0 + minute,
        1000.0 + minute * 10,
    ]


def insert_rows(db, rows):
    db.executemany("INSERT INTO market.bars_1m VALUES (" + ",".join(["?"] * 20) + ")", rows)


def test_adjusted_export_preserves_weights_gaps_night_sessions_and_contracts(kernel, tmp_path):
    db, _ = kernel
    rows = [minute_row(i) for i in (*range(1, 8), 9)]
    rows.append(minute_row(3, instrument="M2"))
    latest = minute_row(1)
    latest[5:7] = [111.0, 111.0]
    latest[-4:-1] = [110.0, 113.0, 109.0]
    latest[14] += timedelta(hours=1)
    latest[15] = 2
    rows.append(latest)
    night = minute_row(1)
    night[4] = datetime(2020, 1, 1, 21, 1, tzinfo=SHANGHAI)
    rows.append(night)
    excluded = minute_row(2, instrument="OTHER")
    excluded[13] = False
    rows.append(excluded)
    provisional = minute_row(2, instrument="UNFINISHED")
    provisional[12] = "preliminary"
    rows.append(provisional)
    insert_rows(db, rows)

    manifest = research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    assert manifest["status"] == "complete"
    assert manifest["source_frequency"] == "1m" and manifest["frequency"] == "5m"
    assert manifest["resampled"]
    assert manifest["fields"] == list(research_kernel.FEATURE_FIELDS)
    market = ParqMarketData(tmp_path, "all")
    assert market.snapshot_id()
    assert market.get_fields() == research_kernel.FEATURE_FIELDS
    features = market.load_features(fields=["adj_close", "adj_vwap"], end=date(2020, 1, 2))
    assert "volume" in features.columns
    assert features["adj_close"].null_count() == 0
    market._db.close()
    assert manifest["raw_source_rows"] == 11  # Includes the older duplicate minute.
    assert manifest["rows"] == 5
    part = manifest["partitions"][0]
    assert part["source_bars"] == 10
    assert part["missing_adj_vwap"] == 1
    frame = pl.read_parquet(tmp_path / part["path"])
    assert frame["timestamp"].dtype == pl.Datetime("us", "Asia/Shanghai")
    assert frame["row_id"].n_unique() == frame.height
    assert frame["instrument_id"].unique().sort().to_list() == ["M1", "M2"]
    first = frame.filter((pl.col("instrument_id") == "M1") & (pl.col("source_bars") == 5)).row(
        0, named=True
    )
    assert first["timestamp"] == datetime(2020, 1, 2, 9, 5, tzinfo=SHANGHAI)
    assert first["adj_close"] == 105
    assert first["adj_open"] == 110 and first["adj_high"] == 113 and first["adj_low"] == 100
    assert first["open_interest"] == 1050  # Closing stock, neither summed nor adjusted by 2.
    assert first["adj_vwap"] == pytest.approx((111 + 102 * 2 + 103 * 3 + 104 * 4 + 105 * 5) / 15)
    assert first["volume"] == 15 and first["amount"] == 15000
    assert first["vwap_proxy_bars"] == 1 and first["vwap_clamped_bars"] == 1
    tail = frame.filter(pl.col("source_bars") == 2).row(0, named=True)
    assert tail["adj_close"] == 107 and tail["adj_vwap"] is None
    assert tail["timestamp"] == datetime(2020, 1, 2, 9, 7, tzinfo=SHANGHAI)
    night = frame.filter(pl.col("timestamp").dt.hour() == 21).row(0, named=True)
    assert night["trading_day"] == date(2020, 1, 2)
    assert night["timestamp"].date() == date(2020, 1, 1)
    verified = audit(tmp_path)
    assert verified["passed"] and verified["research_ready"]
    assert verified["ohlc_fields"] == ["adj_open", "adj_high", "adj_low", "adj_close"]
    assert verified["missing_ohlc"] == 0


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf")])
def test_export_preserves_missing_ohlc_and_closing_interest(kernel, tmp_path, missing):
    db, _ = kernel
    rows = [minute_row(i) for i in range(1, 6)]
    rows[0][-4] = None
    rows[1][-3] = missing
    rows[2][-2] = missing
    rows[-1][5] = None
    rows[-1][-1] = None
    insert_rows(db, rows)
    manifest = research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    part = manifest["partitions"][0]
    frame = pl.read_parquet(tmp_path / part["path"])
    assert frame["adj_open"].to_list() == [None]
    assert frame["adj_high"].to_list() == [None]
    assert frame["adj_low"].to_list() == [None]
    assert frame["adj_close"].to_list() == [None]
    assert frame["open_interest"].to_list() == [None]
    assert frame["volume"].to_list() == [15]
    assert part["missing_open_interest"] == 1
    assert part["missing_adj_high"] == part["missing_adj_low"] == 1
    with ParqMarketData(tmp_path, "all") as market:
        features = market.load_features(fields=list(market.get_fields()), end=date(2020, 1, 2))
    assert features.height == 1 and not features["eligible"].any()
    assert not features["valid_bar"].any()
    verified = audit(tmp_path)
    assert not verified["passed"] and verified["research_ready"]
    assert verified["missing_ohlc"] == 1


def test_adjusted_ohlc_quality_is_checked_even_with_a_field_subset(kernel, tmp_path):
    db, _ = kernel
    rows = [minute_row(i) for i in range(1, 11)]
    rows[0][-4] = 1000.0  # First five-minute opening exceeds its high.
    insert_rows(db, rows)
    research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    with ParqMarketData(tmp_path, "all") as market:
        features = market.load_features(fields=["adj_close", "open_interest"], end=date(2020, 1, 2))
    assert features.height == 2
    assert features["valid_bar"].to_list() == [False, True]
    assert features["eligible"].to_list() == [False, True]
    assert features["adj_close"].to_list() == [None, 110.0]
    assert features["open_interest"].to_list() == [None, 1100.0]
    assert features["segment_id"][0] != features["segment_id"][1]
    verified = audit(tmp_path)
    assert verified["invalid_ohlc"] == 1 and not verified["passed"]


def test_full_export_rejects_an_existing_four_field_snapshot_without_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        research_kernel, "_request", lambda *args, **kwargs: pytest.fail("unexpected source access")
    )
    path = tmp_path / "manifest.json"
    atomic_json(
        path,
        {
            "kernel_url": "http://kernel.invalid",
            "status": "complete",
            "snapshot_id": "original-four-field-snapshot",
            "fields": ["adj_close", "adj_vwap", "volume", "amount"],
        },
    )
    original = path.read_bytes()
    with pytest.raises(ValueError, match="different feature schema"):
        research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    assert path.read_bytes() == original


def test_empty_kernel_does_not_publish_a_complete_export(kernel, tmp_path):
    with pytest.raises(ValueError, match="no final dominant-contract bars"):
        research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_export_resumes_only_uncommitted_months(kernel, tmp_path, monkeypatch):
    db, request = kernel
    insert_rows(db, [minute_row(1), minute_row(1, day=date(2020, 2, 3))])

    def fail_second_month(base, path, payload=None):
        if payload and payload.get("parameters") == ["2020-02-01", "2020-03-01"]:
            raise OSError("synthetic disconnect")
        return request(base, path, payload)

    monkeypatch.setattr(research_kernel, "_request", fail_second_month)
    with pytest.raises(OSError, match="synthetic disconnect"):
        research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    first_part = tmp_path / "bars/year=2020/2020-01.parq"
    saved = first_part.stat().st_mtime_ns
    queries = []

    def resume_request(base, path, payload=None):
        if payload and payload["parameters"]:
            queries.append(payload["parameters"])
        return request(base, path, payload)

    monkeypatch.setattr(research_kernel, "_request", resume_request)
    manifest = research_kernel.export_kernel("http://kernel.invalid", tmp_path)
    assert manifest["status"] == "complete" and manifest["rows"] == 2
    assert queries == [["2020-02-01", "2020-03-01"]]
    assert first_part.stat().st_mtime_ns == saved
