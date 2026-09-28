from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from alpha_atlas.assets.common import atomic_json, atomic_parq, with_row_id
from alpha_atlas.assets.futures import BAR_FIELDS, attach_far_bars, far_contract_mapping
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.evaluation import build_targets
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime


def inputs():
    inst = pl.DataFrame(
        {
            "instrument_id": ["M", "A", "B", "F", "OTHER"],
            "exchange": ["X", "X", "X", "X", "Y"],
            "underlying_symbol": ["P"] * 5,
            "listed_date": ["2019-01-01"] * 3 + ["2020-01-08", "2019-01-01"],
            "de_listed_date": ["2021-01-01"] * 5,
            "maturity_date": ["2020-06-01", "2020-07-01", "2020-08-01", "2020-06-15", "2020-06-10"],
        }
    )
    main = pl.DataFrame(
        {
            "trading_day": [date(2020, 1, 7), date(2020, 1, 8)],
            "instrument_id": ["M", "M"],
            "product": ["P", "P"],
        }
    )
    rows = []
    for day in range(2):
        for i in range(6):
            stamp = datetime(2020, 1, 6 + day, 21) + timedelta(minutes=5 * (i + 1))
            for symbol, offset in [("M", 0), ("A", 10), ("B", 20), ("F", 30)]:
                price = float(100 + offset + day * 6 + i)
                rows.append(
                    {
                        "exchange": "X",
                        "instrument_id": symbol,
                        "product": "P",
                        "timestamp": stamp,
                        "trading_day": date(2020, 1, 7 + day),
                        "open": price,
                        "high": price + 1,
                        "low": price - 1,
                        "close": price,
                        "volume": 10.0 + i,
                        "amount": price * 10,
                        "open_interest": 100.0 + i,
                    }
                )
    raw = with_row_id(pl.DataFrame(rows))
    anchors = raw.filter(pl.col("instrument_id") == "M")
    return inst, main, raw, anchors


def load_panel(tmp_path, wide, fields):
    atomic_parq(wide, tmp_path / "bars/fixture.parq")
    atomic_json(tmp_path / "manifest.json", {"status": "complete", "snapshot_id": "synthetic"})
    with ParqMarketData(tmp_path, "all") as market:
        return market.load_features(fields=fields, end=date(2020, 1, 8))


def test_historical_listing_and_exchange_determine_legs_without_future_ranking():
    inst, main, _, _ = inputs()
    mapping = far_contract_mapping(main, inst)
    assert mapping["instrument_id_p1"].to_list() == ["A", "F"]
    assert mapping["instrument_id_p2"].to_list() == ["B", "A"]
    assert mapping["days_to_maturity_p1"][0] == (date(2020, 7, 1) - date(2020, 1, 7)).days
    assert mapping["exchange_p1"].to_list() == ["X", "X"]
    prefix = far_contract_mapping(main.head(1), inst)
    assert prefix.equals(mapping.head(1))
    with pytest.raises(ValueError, match="duplicate anchor"):
        far_contract_mapping(pl.concat([main, main.head(1)]), inst)
    sparse = far_contract_mapping(main, inst.filter(pl.col("instrument_id").is_in(["M", "A"])))
    assert sparse["instrument_id_p2"].null_count() == 2


def test_exact_timestamp_and_trading_day_join_never_uses_future_bar():
    inst, main, raw, anchors = inputs()
    mapping = far_contract_mapping(main, inst)
    timestamp = anchors["timestamp"][1]
    # Remove the exact quote but retain the next quote, with a deliberately huge future value.
    missing = raw.filter(~((pl.col("instrument_id") == "A") & (pl.col("timestamp") == timestamp)))
    missing = missing.with_columns(
        pl.when((pl.col("instrument_id") == "A") & (pl.col("timestamp") > timestamp))
        .then(99999.0)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    wide = attach_far_bars(anchors, missing, mapping)
    assert wide.filter(pl.col("timestamp") == timestamp)["close_p1"][0] is None
    assert wide["close_p1"][0] == 110
    assert wide.select(anchors.columns).equals(anchors)
    # A same-clock bar attributed to another trading day must also not match (night sessions).
    wrong_day = raw.with_columns(
        pl.when((pl.col("instrument_id") == "A") & (pl.col("timestamp") == timestamp))
        .then(pl.lit(date(2020, 1, 8)))
        .otherwise(pl.col("trading_day"))
        .alias("trading_day")
    )
    assert attach_far_bars(anchors, wrong_day, mapping)["close_p1"][1] is None
    with pytest.raises(ValueError, match="duplicate auxiliary"):
        attach_far_bars(anchors, pl.concat([raw, raw.head(1)]), mapping)


def test_far_leg_switch_only_resets_dependent_windows_and_never_targets(tmp_path):
    inst, main, raw, anchors = inputs()
    wide = attach_far_bars(anchors, raw, far_contract_mapping(main, inst))
    fields = ["close", "close_p1", "close_p2", "volume_p1", "open_interest_p2"]
    data = load_panel(tmp_path, wide, fields)
    assert data["segment_id"].n_unique() == 1
    assert data["segment_id_p1"].n_unique() == 2
    ordinary = execute("TS_MEAN($close, 3)", data, set(fields))["value"]
    assert ordinary[6] == pytest.approx(105)
    for expression in [
        "TS_MEAN($close_p1, 3)",
        "TS_MEAN($close / $close_p1, 3)",
        "TS_CORR($close_p1, $close_p2, 3)",
        "TS_PROD($close_p1, 3)",
        "TS_ARGMAX($close_p1, 3)",
        "TS_RANKCORR($close_p1, $close_p2, 3)",
        "TS_MEAN(CS_ZSCORE($close_p1), 3)",
    ]:
        result = execute(expression, data, set(fields))["value"]
        assert result[6:8].null_count() == 2
    # Derived expression lineage persists through nested windows and ordinary arithmetic.
    nested = execute("TS_MEAN(TS_MEAN($close_p1, 2) - $close, 2)", data, set(fields))["value"]
    assert nested[6:8].null_count() == 2 and nested[8] is not None
    base = load_panel(tmp_path / "base", anchors, ["close"])
    assert build_targets(data, "close", 2).equals(build_targets(base, "close", 2))
    assert ordinary.equals(execute("TS_MEAN($close, 3)", base, {"close"})["value"])
    # Missing input cannot be reclassified as usable via a fallback around an identity change.
    filled = execute("DELAY(FILLNA($close_p1, $close), 2)", data, set(fields))["value"]
    assert filled[6:8].null_count() == 2


def test_bad_auxiliary_bar_preserves_anchor_and_breaks_far_history(tmp_path):
    inst, main, raw, anchors = inputs()
    wide = attach_far_bars(anchors, raw, far_contract_mapping(main, inst))
    stamp = anchors["timestamp"][2]
    bad = wide.with_columns(
        pl.when(pl.col("timestamp") == stamp)
        .then(0.0)
        .otherwise(pl.col("high_p1"))
        .alias("high_p1")
    )
    data = load_panel(tmp_path, bad, ["close", "close_p1", "volume_p1"])
    assert data["eligible"].all() and data["target_eligible"].all()
    assert data["close_p1"][2] is None and data["volume_p1"][2] is None
    result = execute("TS_MEAN($close_p1, 2)", data, {"close_p1"})["value"]
    assert result[2] is None and result[3] is None and result[4] is not None
    with pytest.raises(ValueError, match="identity and OHLC"):
        load_panel(tmp_path / "bad", wide.drop("instrument_id_p1"), ["close_p1"])
    with pytest.raises(ValueError, match="continuity metadata"):
        execute("TS_MEAN($close_p1, 2)", data.drop("segment_id_p1"), {"close_p1"})
    for name in ("instrument_id_p1", "exchange_p2", "segment_id_p1", "valid_bar_p1"):
        with pytest.raises(ValueError):
            compile_factor(f"${name}", {name})


def test_future_perturbation_and_appending_rows_do_not_change_history(tmp_path):
    inst, main, raw, anchors = inputs()
    mapping = far_contract_mapping(main, inst)
    wide = attach_far_bars(anchors, raw, mapping)
    fields = {"close", "close_p1", "close_p2", "volume_p1"}
    full = load_panel(tmp_path / "full", wide, list(fields))
    prefix = load_panel(tmp_path / "prefix", wide.head(5), list(fields))
    cutoff = wide["timestamp"][4]
    changed_raw = raw.with_columns(
        [
            pl.when(pl.col("timestamp") > cutoff)
            .then(pl.col(f) * 1.5)
            .otherwise(pl.col(f))
            .alias(f)
            for f in BAR_FIELDS
        ]
    )
    changed = load_panel(
        tmp_path / "changed", attach_far_bars(anchors, changed_raw, mapping), list(fields)
    )
    for text in [
        "$close_p1 / $close - 1",
        "TS_MEAN($close_p1, 3)",
        "TS_CORR($close_p1, $close_p2, 3)",
        "TS_ZSCORE($volume_p1, 3)",
    ]:
        left = execute(text, full, fields).head(5)
        for right in (execute(text, prefix, fields), execute(text, changed, fields).head(5)):
            assert left["row_id"].equals(right["row_id"])
            np.testing.assert_allclose(left["value"], right["value"], equal_nan=True)


def test_group_batch_uses_far_leg_continuity(tmp_path):
    inst, main, raw, anchors = inputs()
    wide = attach_far_bars(anchors, raw, far_contract_mapping(main, inst))
    data = load_panel(tmp_path, wide, ["close_p1"])
    registry = OperatorRegistry(runtime=NumbaRuntime())
    definition = OperatorDefinition(
        "FAR_MEAN",
        (("x", "series"), ("w", "window")),
        "def kernel(x,w):\n    return np.full(len(x), np.mean(x))",
        kind="group_batch",
        window_arg="w",
        history_offset=-1,
        golden="def golden(x,w):\n    return np.ones(len(x)) * np.sum(x) / len(x)",
        examples=({"inputs": [[1.0, 3.0]], "params": [2], "expected": [2.0, 2.0]},),
    )
    assert registry.register(definition).accepted
    actual = execute("FAR_MEAN($close_p1, 3)", data, {"close_p1"}, registry)["value"]
    expected = execute("TS_MEAN($close_p1, 3)", data, {"close_p1"})["value"]
    np.testing.assert_allclose(actual, expected, equal_nan=True)


@pytest.mark.parametrize("missing,failed", [(False, False), (True, False), (False, True)])
def test_optional_native_export_records_missing_auxiliary_bars(
    tmp_path, monkeypatch, missing, failed
):
    from alpha_atlas.assets import futures

    inst, main, raw, _ = inputs()
    ids = {"M": "P2006", "A": "P2007", "B": "P2008", "F": "P2009", "OTHER": "Q2005"}
    inst = inst.with_columns(
        pl.col("instrument_id").replace_strict(ids).alias("order_book_id"),
        pl.lit(10).alias("contract_multiplier"),
    ).drop("instrument_id")
    raw = raw.with_columns(pl.col("instrument_id").replace_strict(ids))
    absent = (pl.col("instrument_id") == "P2007") & (pl.col("timestamp") == raw["timestamp"][4])
    if missing:
        raw = raw.filter(~absent)

    class Provider:
        def __init__(self):
            self.futures = self

        def all_instruments(self, *, type):
            assert type == "Future"
            return inst.to_pandas()

        def get_dominant(self, symbol, *, start_date, end_date, rule):
            assert symbol == "P" and rule == 0
            assert (start_date, end_date) == (date(2020, 1, 7), date(2020, 1, 8))
            return (
                main.with_columns(pl.lit("P2006").alias("instrument_id"))
                .to_pandas()
                .set_index("trading_day")["instrument_id"]
                .rename_axis("date")
            )

        def get_trading_dates(self, start, end):
            return [date(2020, 1, 7), date(2020, 1, 8)]

        def get_price(self, ids, **kwargs):
            assert set(ids) == {"P2006", "P2007", "P2008", "P2009"}
            assert kwargs["frequency"] == "5m" and kwargs["adjust_type"] == "none"
            if failed:
                raise RuntimeError("synthetic provider failure")
            return (
                raw.select("instrument_id", "timestamp", "trading_day", *BAR_FIELDS)
                .rename(
                    {
                        "instrument_id": "order_book_id",
                        "timestamp": "datetime",
                        "trading_day": "trading_date",
                        "amount": "total_turnover",
                    }
                )
                .to_pandas()
                .set_index(["order_book_id", "datetime"])
            )

    monkeypatch.setattr(futures, "connect", lambda _: Provider())
    monkeypatch.setattr(futures.time, "sleep", lambda _: None)
    result = futures.fetch_futures(
        tmp_path, date(2020, 1, 7), date(2020, 1, 8), with_far_contracts=True, workers=1
    )
    if failed:
        assert result["status"] == "incomplete" and result["rows"] == 0
        assert result["actual_start"] is None and result["actual_end"] is None
        assert result["partitions"][0]["status"] == "failed"
        return
    assert result["rows"] == 12
    assert result["status"] == ("incomplete" if missing else "complete")
    assert result["missing_auxiliary_bars"] == int(missing)
    with ParqMarketData(tmp_path, "all", allow_incomplete=missing) as market:
        data = market.load_features(fields=["close", "close_p1", "volume_p2"], end=date(2020, 1, 8))
    assert data["close_p1"].null_count() == int(missing)
    assert data["instrument_id"].unique().to_list() == ["P2006"]
    assert data["instrument_id_p1"][:6].unique().to_list() == ["P2007"]
    assert data["instrument_id_p1"][6:].unique().to_list() == ["P2009"]


def test_existing_main_dataset_is_not_overwritten_by_curve_export(tmp_path, monkeypatch):
    from alpha_atlas.assets import futures

    atomic_json(tmp_path / "manifest.json", {"asset_id": "futures", "status": "complete"})

    def connect(_):
        raise AssertionError("must reject before contacting the provider")

    monkeypatch.setattr(futures, "connect", connect)
    with pytest.raises(ValueError, match="separate export directory"):
        futures.fetch_futures(tmp_path, date(2020, 1, 7), date(2020, 1, 8), with_far_contracts=True)


def test_merge_preserves_missing_values_and_incomplete_status(tmp_path):
    from alpha_atlas.assets.futures import merge_futures

    inst, main, raw, anchors = inputs()
    wide = attach_far_bars(anchors, raw, far_contract_mapping(main, inst)).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("close_p2")
    )
    parts = []
    for number, frame in enumerate([wide.tail(6), wide.head(6)]):
        part = atomic_parq(frame, tmp_path / "bars" / f"{number}.parq")
        part["status"] = "downloaded"
        parts.append(part)
    # A stale partition outside this manifest must not enter the merged export.
    atomic_parq(wide, tmp_path / "bars/stale.parq")
    manifest = {"status": "incomplete", "partitions": parts, "missing_auxiliary_bars": 12}
    atomic_json(tmp_path / "manifest.json", manifest)
    for _ in range(2):
        result = merge_futures(tmp_path)
        assert result["rows"] == 12 and result["acquisition_status"] == "incomplete"
        assert pl.read_parquet(result["path"]).equals(
            wide.sort("timestamp", "exchange", "instrument_id")
        )
    parts.append(parts[0])
    atomic_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="observation keys"):
        merge_futures(tmp_path)
    assert pl.read_parquet(result["path"]).height == 12


def test_merge_refuses_active_or_empty_acquisition(tmp_path):
    from alpha_atlas.assets.futures import merge_futures

    for status, message in [("downloading", "finish acquisition"), ("incomplete", "no downloaded")]:
        atomic_json(tmp_path / "manifest.json", {"status": status, "partitions": []})
        with pytest.raises(ValueError, match=message):
            merge_futures(tmp_path)
