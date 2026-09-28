from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from test_pipeline import project as project

from alpha_atlas.assets.common import atomic_json, atomic_parq
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.contracts import Expression
from alpha_atlas.expressions import compile_factor, execute, parse
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime
from alpha_atlas.timeframes import timeframe_panel

FIELDS = {
    f"{name}{suffix}"
    for name in ("open", "high", "low", "close", "volume", "amount", "open_interest")
    for suffix in ("", "_p1", "_p2")
}


def panel(n=48):
    stamps = [datetime(2025, 1, 2, 9, 5) + timedelta(minutes=5 * i) for i in range(n)]
    values = np.arange(n, dtype=float) + 100
    data = {
        "row_id": range(n),
        "exchange": ["X"] * n,
        "instrument_id": ["M"] * n,
        "timestamp": stamps,
        "trading_day": [date(2025, 1, 2)] * n,
        "bar_interval_minutes": [5] * n,
        "eligible": [True] * n,
        "valid_bar": [True] * n,
        "segment_id": [1] * n,
    }
    for leg, offset in [("", 0), ("_p1", 10), ("_p2", 20)]:
        data.update(
            {
                f"open{leg}": values + offset - 0.5,
                f"high{leg}": values + offset + 2,
                f"low{leg}": values + offset - 2,
                f"close{leg}": values + offset,
                f"volume{leg}": np.arange(n, dtype=float) + 1 + offset,
                f"amount{leg}": (values + offset) * 10,
                f"open_interest{leg}": values + offset + 1000,
            }
        )
        if leg:
            data[f"segment_id{leg}"] = [1] * n
            data[f"valid_bar{leg}"] = [True] * n
            data[f"instrument_id{leg}"] = [leg] * n
            data[f"exchange{leg}"] = ["X"] * n
    return pl.DataFrame(data)


def daily_panel():
    data = panel(12)
    stamps = [
        datetime(2025, 1, 2, 21, 5),
        datetime(2025, 1, 2, 23, 0),
        datetime(2025, 1, 3, 0, 30),
        datetime(2025, 1, 3, 15, 0),
        datetime(2025, 1, 3, 21, 5),  # Friday night belongs to Monday.
        datetime(2025, 1, 4, 0, 30),
        datetime(2025, 1, 6, 9, 5),
        datetime(2025, 1, 6, 15, 0),
        datetime(2025, 1, 6, 21, 5),
        datetime(2025, 1, 7, 0, 30),
        datetime(2025, 1, 7, 9, 5),
        datetime(2025, 1, 7, 15, 0),
    ]
    return data.with_columns(
        pl.Series("timestamp", stamps).dt.replace_time_zone("Asia/Shanghai"),
        pl.Series("trading_day", [date(2025, 1, d) for d in [3] * 4 + [6] * 4 + [7] * 4]),
    )


def values(text, data, registry=None):
    return execute(text, data, FIELDS, registry).sort("row_id")["value"].to_numpy()


@pytest.mark.parametrize("interval,size", [("15m", 3), ("30m", 6), ("60m", 12)])
@pytest.mark.parametrize("suffix", ["", "_p1", "_p2"])
@pytest.mark.parametrize(
    "field,aggregation",
    [
        ("open", "first"),
        ("close", "last"),
        ("high", "max"),
        ("low", "min"),
        ("volume", "sum"),
        ("amount", "sum"),
        ("open_interest", "last"),
    ],
)
def test_intraday_aggregation_and_exact_publication(interval, size, suffix, field, aggregation):
    data = panel()
    source = data[f"{field}{suffix}"].to_list()
    expected = []
    last = np.nan
    for i in range(len(source)):
        if (i + 1) % size == 0:
            window = source[i + 1 - size : i + 1]
            last = {
                "first": window[0],
                "last": window[-1],
                "min": min(window),
                "max": max(window),
                "sum": sum(window),
            }[aggregation]
        expected.append(last)
    np.testing.assert_allclose(
        values(f"${field}{suffix}@{interval}", data), expected, equal_nan=True
    )


@pytest.mark.parametrize("interval,size", [("15m", 3), ("30m", 6), ("60m", 12)])
def test_windows_run_on_coarse_bars_before_broadcast_and_mixed_windows_on_5m(interval, size):
    data = panel()
    coarse = data["close"].to_numpy()[size - 1 :: size]
    expected = np.full(data.height, np.nan)
    for i in range(1, len(coarse)):
        expected[(i + 1) * size - 1 : (i + 2) * size - 1] = np.mean(coarse[i - 1 : i + 1])
    text = f"TS_MEAN($close@{interval},2)"
    np.testing.assert_allclose(values(text, data), expected, equal_nan=True)
    # Scalars, assignments and composite expressions preserve the coarse scope.
    np.testing.assert_allclose(values(f"x={text}\nx*2", data), expected * 2, equal_nan=True)
    spread = values(f"$close-$close@{interval}", data)
    mixed = np.r_[np.nan, (spread[1:] + spread[:-1]) / 2]
    np.testing.assert_allclose(
        values(f"TS_MEAN($close-$close@{interval},2)", data), mixed, equal_nan=True
    )
    difference = values("$close@30m-$close_p1@60m", data)
    np.testing.assert_allclose(
        values("TS_MEAN($close@30m-$close_p1@60m,2)", data),
        np.r_[np.nan, (difference[1:] + difference[:-1]) / 2],
        equal_nan=True,
    )


@pytest.mark.parametrize("suffix", ["", "_p1", "_p2"])
def test_daily_night_weekend_and_confirmation_without_last_row_guess(suffix):
    data = daily_panel()
    offset = {"": 0, "_p1": 10, "_p2": 20}[suffix]
    np.testing.assert_allclose(
        values(f"$close{suffix}@1d", data),
        [np.nan] * 4 + [103 + offset] * 4 + [107 + offset] * 4,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        values(f"TS_MEAN($close{suffix}@1d,2)", data),
        [np.nan] * 8 + [105 + offset] * 4,
        equal_nan=True,
    )
    for field, expected in [("open", 99.5), ("high", 105), ("low", 98), ("open_interest", 1103)]:
        assert values(f"${field}{suffix}@1d", data)[4] == expected + offset
    assert values(f"$volume{suffix}@1d", data)[4] == 10 + 4 * offset
    assert values(f"$amount{suffix}@1d", data)[4] == 4060 + 40 * offset
    # A final observed bar (including a 15:00 timestamp) never certifies its own day.
    assert np.isnan(values(f"$close{suffix}@1d", data.head(4))).all()


@pytest.mark.parametrize("interval", ["15m", "30m", "60m", "1d"])
@pytest.mark.parametrize(
    "formula",
    [
        "$close@{interval}",
        "TS_MEAN($close_p1@{interval},2)",
        "TS_CORR($close_p1@{interval},$close_p2@{interval},3)",
        "TS_MEAN($close/$close_p1@{interval},2)",
        "TS_ARGMAX($high_p2@{interval},2)",
        "DELAY(FILLNA($close_p1@{interval},$close_p2@{interval}),2)",
    ],
)
def test_every_prefix_and_future_price_identity_perturbation_is_invariant(interval, formula):
    if interval == "1d":
        data = daily_panel()
        fourth_day = data.tail(4).with_columns(
            (pl.col("row_id") + 4).alias("row_id"),
            (pl.col("timestamp") + pl.duration(days=1)).alias("timestamp"),
            (pl.col("trading_day") + pl.duration(days=1)).alias("trading_day"),
            *[(pl.col(f) + 4).alias(f) for f in FIELDS],
        )
        data = pl.concat([data, fourth_day])
    else:
        data = panel(38)
    text = formula.format(interval=interval)
    full = values(text, data)
    assert np.isfinite(full).any(), "prefix invariance must exercise actual finite outputs"
    for stop in range(1, data.height + 1):
        np.testing.assert_allclose(values(text, data.head(stop)), full[:stop], equal_nan=True)
        changed = data.with_columns(
            *[
                pl.when(pl.col("row_id") >= stop)
                .then(pl.col(f) * 999)
                .otherwise(pl.col(f))
                .alias(f)
                for f in FIELDS
            ],
            pl.when(pl.col("row_id") >= stop)
            .then(9)
            .otherwise(pl.col("segment_id_p1"))
            .alias("segment_id_p1"),
        )
        np.testing.assert_allclose(values(text, changed)[:stop], full[:stop], equal_nan=True)


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), -float("inf")])
def test_null_publication_replaces_previous_value_and_does_not_drop_invalid_bucket(invalid):
    data = panel(24).with_columns(
        pl.when(pl.col("row_id") == 8).then(invalid).otherwise(pl.col("close")).alias("close")
    )
    result = values("$close@30m", data)
    assert result[5] == 105 and result[10] == 105
    assert np.isnan(result[11:17]).all() and result[17] == 117
    rolling = values("TS_MEAN($close@30m,2)", data)
    assert np.isnan(rolling[:23]).all() and rolling[23] == 120


@pytest.mark.parametrize("leg", ["p1", "p2"])
@pytest.mark.parametrize("invalid", [False, True])
def test_leg_changes_and_gaps_reset_only_dependent_broadcasts_and_windows(leg, invalid):
    base = panel(36)
    data = base.with_columns(
        pl.when(pl.col("row_id") >= 8).then(2).otherwise(1).alias(f"segment_id_{leg}"),
        pl.when(pl.col("row_id") == 8).then(not invalid).otherwise(True).alias(f"valid_bar_{leg}"),
    )
    np.testing.assert_allclose(
        values("TS_MEAN($close@30m,2)", data), values("TS_MEAN($close@30m,2)", base), equal_nan=True
    )
    result = values(f"$close_{leg}@30m", data)
    assert np.isnan(result[8:17]).all()  # Includes a bucket straddling the leg change.
    assert np.isfinite(result[17])
    # A mixed 5m window retains both legs' lineage after a coarse result is broadcast.
    result = values("TS_MEAN($close_p1@30m+$close_p2@60m+$close,2)", data)
    ready = 18 if leg == "p1" else 24
    assert np.isnan(result[8:ready]).all()
    assert np.isfinite(result[ready])


def test_main_segment_change_reentry_contracts_and_exchanges_never_share_history():
    data = panel(36).with_columns(
        pl.when(pl.col("row_id") >= 8).then(2).otherwise(1).alias("segment_id")
    )
    assert np.isnan(values("$close@30m", data)[8:17]).all()
    assert np.isnan(values("TS_MEAN($close@30m,2)", data)[:23]).all()
    other = data.with_columns(
        pl.lit("Y").alias("exchange"),
        (pl.col("row_id") + 100).alias("row_id"),
        (pl.col("close") + 1000).alias("close"),
    )
    third = data.with_columns(
        pl.lit("N").alias("instrument_id"),
        (pl.col("row_id") + 200).alias("row_id"),
        (pl.col("close") + 2000).alias("close"),
    )
    full = pl.concat([other, data, third]).sample(fraction=1, shuffle=True, seed=2)
    combined = values("TS_MEAN($close@30m,2)", full)
    for i, part in enumerate([data, other, third]):
        np.testing.assert_allclose(
            combined[i * 36 : (i + 1) * 36], values("TS_MEAN($close@30m,2)", part), equal_nan=True
        )


def test_daily_gap_and_leg_roll_at_confirmation_do_not_publish_previous_identity():
    data = daily_panel().with_columns(
        pl.when(pl.col("row_id") >= 4).then(2).otherwise(1).alias("segment_id_p1")
    )
    result = values("$close_p1@1d", data)
    assert np.isnan(result[:8]).all() and result[8] == 117
    data = data.with_columns(
        pl.when(pl.col("row_id") >= 8).then(2).otherwise(1).alias("segment_id")
    )
    assert np.isnan(values("$close@1d", data)[8:]).all()


@pytest.mark.parametrize("interval", ["15m", "30m", "60m", "1d"])
def test_custom_composite_numpy_and_numba_operators_keep_frequency(interval):
    data = daily_panel() if interval == "1d" else panel(48)
    registry = OperatorRegistry(runtime=NumbaRuntime())
    assert registry.register(
        OperatorDefinition("DOUBLE_MEAN", (("x", "series"), ("w", "window")), "TS_MEAN(x,w)*2")
    ).accepted
    definition = OperatorDefinition(
        "TF_MEAN",
        (("x", "series"), ("w", "window")),
        "def kernel(x,w):\n    return np.full(len(x), np.mean(x))",
        kind="group_batch",
        window_arg="w",
        history_offset=-1,
        golden="def golden(x,w):\n    return np.full(len(x), np.sum(x)/len(x))",
        examples=({"inputs": [[1.0, 3.0]], "params": [2], "expected": [2.0, 2.0]},),
    )
    assert registry.register(definition).accepted
    expected = values(f"TS_MEAN($close_p1@{interval},2)", data)
    np.testing.assert_allclose(
        values(f"TF_MEAN($close_p1@{interval},2)", data, registry), expected, equal_nan=True
    )
    np.testing.assert_allclose(
        values(f"DOUBLE_MEAN($close_p1@{interval},2)", data, registry), expected * 2, equal_nan=True
    )
    for op in ("TS_PROD", "TS_ARGMIN", "TS_ARGMAX", "TS_RANKCORR"):
        args = f"$close_p1@{interval}," + (f"$close_p2@{interval}," if op == "TS_RANKCORR" else "")
        actual = values(f"{op}({args}2)", data)
        assert np.isfinite(actual).any()


def test_cross_sectional_coarse_values_use_only_same_publication_time():
    data = panel(24)
    other = data.with_columns(
        pl.lit("N").alias("instrument_id"),
        (pl.col("row_id") + 100).alias("row_id"),
        (pl.col("close") * 2).alias("close"),
    )
    result = values("CS_RANK($close@30m)", pl.concat([data, other]))
    assert np.isnan(result[:5]).all()
    np.testing.assert_allclose(result[5:24], 0.5)
    np.testing.assert_allclose(result[29:], 1)
    np.testing.assert_allclose(
        values("TS_MEAN(CS_RANK($close@30m),2)", pl.concat([data, other]))[11:24], 0.5
    )


@pytest.mark.parametrize(
    "text",
    [
        "$close@5m",
        "$close@2d",
        "$close@1h",
        "$close@",
        "$close@@30m",
        "$label_close@30m",
        "$segment_id_p1@30m",
        "$unknown@30m",
        "$close@30m@60m",
    ],
)
def test_invalid_syntax_and_field_permissions(text):
    with pytest.raises(ValueError):
        compile_factor(text, FIELDS | {"unknown", "label_close", "segment_id_p1"})
    with pytest.raises(ValueError):
        compile_factor("$close_p1@30m", {"close"})


def test_ast_roundtrip_identity_metadata_and_empty_input():
    text = "x=$close_p1@30m\nTS_MEAN(x,2)"
    compiled = compile_factor(text, FIELDS)
    restored = Expression.from_dict(compiled.expression.to_dict())
    assert compile_factor(restored, FIELDS).factor_id == compiled.factor_id
    assert compiled.fields == ("close_p1",)
    assert compile_factor("TS_MEAN($close_p1,2)", FIELDS).factor_id != compiled.factor_id
    assert parse("$close_p1@30m").value == "close_p1@30m"
    data = panel()
    assert execute(compiled, data.head(0), FIELDS).height == 0
    for column in ("bar_interval_minutes", "trading_day", "segment_id", "segment_id_p1"):
        with pytest.raises(ValueError, match="metadata"):
            execute(compiled, data.drop(column), FIELDS)
    with pytest.raises(ValueError, match="native 5m"):
        values("$close@30m", data.with_columns(pl.lit(1440).alias("bar_interval_minutes")))
    with pytest.raises(ValueError, match="duplicate"):
        values("$close@30m", pl.concat([data, data.head(1)]))


def test_marketdata_loading_quality_isolation_and_target_invariance(tmp_path):
    from alpha_atlas.evaluation import build_targets

    source = panel(36).drop("bar_interval_minutes", "segment_id", "segment_id_p1", "segment_id_p2")
    source = source.with_columns(
        pl.when(pl.col("row_id") == 8).then(None).otherwise(pl.col("close_p1")).alias("close_p1")
    )
    atomic_parq(source, tmp_path / "bars/fixture.parq")
    atomic_json(
        tmp_path / "manifest.json",
        {"frequency": "5m", "status": "complete", "snapshot_id": "fixture"},
    )
    with ParqMarketData(tmp_path, "all") as market:
        data = market.load_features(fields=["close", "close_p1"], end=date(2025, 1, 2))
        base = market.load_features(fields=["close"], end=date(2025, 1, 2))
    assert data["bar_interval_minutes"].unique().to_list() == [5]
    result = values("$close_p1@30m", data)
    assert np.isnan(result[8:17]).all()
    assert build_targets(data, "close", 2).equals(build_targets(base, "close", 2))
    assert execute("$close@30m", base, {"close"}).equals(execute("$close@30m", data, {"close"}))
    # Aggregation is lazy/on demand; the stored native panel is never mutated.
    assert source.height == 36
    assert timeframe_panel(data, {"close"}, "30m").height == 6


def test_coarse_conditions_keep_boolean_type_and_unknown_after_mixing():
    data = panel(24)
    expected = np.r_[np.full(5, np.nan), np.zeros(6), data["close"].to_numpy()[11:]]
    np.testing.assert_allclose(
        values("IF_THEN_ELSE($close@30m>108,$close,0)", data), expected, equal_nan=True
    )
    np.testing.assert_allclose(
        values("IF_THEN_ELSE(AND($close@30m>108,$close<119),$close,0)", data),
        np.r_[expected[:19], np.zeros(5)],
        equal_nan=True,
    )
    assert np.isnan(values("TS_RATE($close@30m>108,2)", data)[:11]).all()
    assert values("TS_RATE($close@30m>108,2)", data)[11] == 0.5


def test_breaks_missing_endpoint_and_unfinished_tail_do_not_publish_early():
    data = panel(18).filter(~pl.col("row_id").is_in([5, 6, 7, 8]))
    # No 09:30 endpoint; at 09:50 the completed 09:30 bucket contains observed bars only.
    result = execute("$volume@30m", data, FIELDS).sort("row_id")
    assert result.filter(pl.col("row_id") == 9)["value"][0] == 15
    assert result.filter(pl.col("row_id") == 4)["value"][0] is None
    # Changing or dropping the unfinished 10:30 bucket cannot change earlier publications.
    prefix = data.filter(pl.col("row_id") <= 13)
    left = execute("$close@30m", data, FIELDS).filter(pl.col("row_id") <= 13).sort("row_id")
    assert left.equals(execute("$close@30m", prefix, FIELDS).sort("row_id"))
    # A single bar on the natural boundary is visible immediately, never one bar earlier.
    single = panel(6).tail(1)
    assert values("$close@30m", single)[0] == 105


def test_large_windows_and_shared_coarse_subtrees():
    data = panel(24)
    assert np.isnan(values("TS_MEAN($close@30m,1000000000)", data)).all()
    assert np.isnan(values("TS_PROD($close_p1@60m,1000000000)", data)).all()
    np.testing.assert_allclose(
        values("x=TS_MEAN($close_p1@30m,2)\nx+x+$close", data),
        values("TS_MEAN($close_p1@30m,2)", data) * 2 + data["close"].to_numpy(),
        equal_nan=True,
    )


def test_malformed_future_trading_day_cannot_publish_future_quotes_into_history():
    data = daily_panel().with_columns(
        pl.when(pl.col("row_id") == 9)
        .then(pl.lit(date(2025, 1, 3)))
        .otherwise(pl.col("trading_day"))
        .alias("trading_day")
    )
    with pytest.raises(ValueError, match="must not move backward"):
        values("$close_p1@1d", data)
    for field in ("timestamp", "trading_day", "exchange", "instrument_id"):
        bad = panel().with_columns(
            pl.when(pl.col("row_id") == 4).then(None).otherwise(pl.col(field)).alias(field)
        )
        with pytest.raises(ValueError, match="null native"):
            values("$close@30m", bad)


@pytest.mark.parametrize("interval", ["15m", "30m", "60m", "1d"])
@pytest.mark.parametrize("suffix", ["", "_p1", "_p2"])
def test_maturity_uses_last_observation_and_zero_volume_is_valid(interval, suffix):
    data = daily_panel() if interval == "1d" else panel(24)
    name = f"days_to_maturity{suffix}"
    data = data.with_columns(
        (200 - pl.col("row_id")).alias(name), pl.lit(0.0).alias(f"volume{suffix}")
    )
    result = execute(f"${name}@{interval}", data, {name}).sort("row_id")["value"]
    index, expected = {
        "15m": (2, 198),
        "30m": (5, 195),
        "60m": (11, 189),
        "1d": (4, 197),
    }[interval]
    assert result[index] == expected
    assert values(f"$volume{suffix}@{interval}", data)[index] == 0


@pytest.mark.parametrize("interval", ["15m", "30m", "60m", "1d"])
def test_timeframes_through_session_admission_freeze_and_oos(project, interval):
    from alpha_atlas.assets.common import with_row_id
    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.contracts import Candidate
    from alpha_atlas.runner import run
    from alpha_atlas.runner import test_frozen as evaluate_frozen

    parts = []
    for year in range(2015, 2023):
        for day in range(1, 11):
            part = panel().with_columns(
                pl.lit(f"M{year}").alias("instrument_id"),
                pl.lit("M").alias("product"),
                pl.lit(date(year, 1, day)).alias("trading_day"),
                pl.Series(
                    "timestamp",
                    [datetime(year, 1, day, 9, 5) + timedelta(minutes=i * 5) for i in range(48)],
                ),
                *[(pl.col(f) + (day - 1) * 48).alias(f) for f in FIELDS],
            )
            parts.append(part)
    data = with_row_id(pl.concat(parts)).drop(
        "bar_interval_minutes", "segment_id", "segment_id_p1", "segment_id_p2"
    )
    atomic_parq(data, project / "data/futures/bars/fixture.parq")
    atomic_json(
        project / "data/futures/manifest.json",
        {
            "status": "complete",
            "snapshot_id": "synthetic-timeframes",
            "frequency": "5m",
        },
    )
    formula = f"$close_p1@{interval}+$close_p2@{interval}"

    class Method:
        def run(self, session):
            assert session.get_context().frequency == "5m"
            assert session.get_fields() == ("close", "close_p1", "close_p2")
            rule = session.get_expression_rules()["timeframes"]
            assert rule["suffixes"] == ("@15m", "@30m", "@60m", "@1d")
            assert rule["fields"] == session.get_fields()
            # Adapter helper OHLC columns must never broaden the method's field permissions.
            assert session.evaluate(Candidate(f"$high_p1@{interval}")).reason == "compile_error"
            feedback = session.evaluate(Candidate(formula))
            assert feedback.report.status == "success"
            assert feedback.accepted, feedback.reason
            assert all(m.split != "test" for m in feedback.report.metrics)

    path = run(
        project,
        "futures",
        "fold1",
        "timeframe_fixture",
        42,
        attempts=2,
        field_names=["close_p1", "close_p2"],
        method_impl=Method(),
    )
    frozen_bytes = (path / "frozen/library.json").read_bytes()
    frozen = read_json(path / "frozen/library.json")
    assert len(frozen["factors"]) == 1
    compiled = frozen["factors"][0]["compiled"]
    assert compiled["fields"] == ["close_p1", "close_p2"]
    result = evaluate_frozen(project, path)
    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "success"
    assert result["results"][0]["coverage"] > 0.8
    assert evaluate_frozen(project, path) == result
    assert (path / "frozen/library.json").read_bytes() == frozen_bytes
