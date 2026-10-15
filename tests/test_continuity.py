from datetime import date, datetime, timedelta

import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.evaluation import build_targets
from alpha_atlas.expressions import execute


def test_native_contract_reentry_missing_days_and_normal_breaks(tmp_path):
    directory = tmp_path / "futures"
    days = [date(2020, 1, d) for d in (3, 6, 7, 8, 9)]
    mapping = pl.DataFrame(
        {"trading_day": days, "product": ["P"] * 5, "instrument_id": ["A", "B", "A", "A", "A"]}
    )
    rows = []
    for day, contract in zip(days, ["A", "B", "A", "A", "A"], strict=True):
        if day == days[3]:
            continue
        for minute in (5, 10, 65):  # normal session break, not a missing-day reset
            price = 10.0 + minute
            rows.append(
                {
                    "timestamp": datetime.combine(day, datetime.min.time())
                    + timedelta(minutes=minute),
                    "trading_day": day,
                    "exchange": "X",
                    "instrument_id": contract,
                    "product": "P",
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
            )
    atomic_parq(with_row_id(pl.DataFrame(rows)), directory / "bars/sample.parq")
    atomic_parq(mapping, directory / "metadata/dominant_daily.parq")
    atomic_parq(pl.DataFrame({"trading_day": days}), directory / "metadata/calendar.parq")
    atomic_json(directory / "manifest.json", {"status": "complete", "snapshot_id": "S"})
    with ParqMarketData(directory, "all") as market:
        data = market.load_features(fields=["close"], end=days[-1])
    a = data.filter(pl.col("instrument_id") == "A")
    assert a["segment_id"].n_unique() == 3
    for day in (days[0], days[2], days[4]):
        assert a.filter(pl.col("trading_day") == day)["segment_id"].n_unique() == 1
    values = execute("DELAY($close,1)", data, {"close"})
    joined = data.join(values, on="row_id").sort("instrument_id", "timestamp")
    assert joined.filter(pl.col("instrument_id") == "A")["value"].to_list() == [
        None,
        15.0,
        20.0,
        None,
        15.0,
        20.0,
        None,
        15.0,
        20.0,
    ]
    targets = build_targets(data, "close", 1)
    assert targets["target"].null_count() == 4

    # Product factors continue through dominant-contract switches, while targets stay local.
    product_segments = (
        data.group_by("trading_day")
        .agg(
            pl.col("product_segment_id").n_unique().alias("segments"),
            pl.col("product_segment_id").first().alias("segment"),
        )
        .sort("trading_day")
    )
    assert product_segments["segments"].to_list() == [1, 1, 1, 1]
    assert product_segments["segment"][:3].n_unique() == 1
    assert product_segments["segment"][3] != product_segments["segment"][2]

    first_roll_bar = data.filter(pl.col("trading_day") == days[1]).sort("timestamp").row(0)
    product_values = execute("TS_MEAN($close,2)", data, {"close"}, panel="product")
    contract_values = execute("TS_MEAN($close,2)", data, {"close"})
    assert product_values.filter(pl.col("row_id") == first_roll_bar[0])["value"][0] == 45
    assert contract_values.filter(pl.col("row_id") == first_roll_bar[0])["value"][0] is None

    last_old_contract = (
        data.filter((pl.col("trading_day") == days[0]) & (pl.col("instrument_id") == "A"))
        .sort("timestamp")
        .tail(1)["row_id"][0]
    )
    assert targets.filter(pl.col("row_id") == last_old_contract)["target"][0] is None


def test_same_identifier_other_exchange_does_not_share_history():
    data = pl.DataFrame(
        {
            "row_id": range(4),
            "exchange": ["X", "X", "Y", "Y"],
            "instrument_id": ["A"] * 4,
            "timestamp": [datetime(2020, 1, d) for d in [1, 2, 1, 2]],
            "eligible": [True] * 4,
            "close": [1.0, 2.0, 100.0, 200.0],
        }
    )
    assert execute("TS_MEAN($close,2)", data, {"close"})["value"].to_list() == [
        None,
        1.5,
        None,
        150.0,
    ]
