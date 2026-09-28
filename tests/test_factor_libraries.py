import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.factor_libraries import load_library


def panel(n=610):
    rows = []
    for product in range(3):
        for t in range(n):
            close = 100 + product * 20 + t * (product + 1) * 0.03 + np.sin(t / 7 + product)
            rows.append(
                {
                    "row_id": len(rows),
                    "timestamp": datetime(2020, 1, 1) + timedelta(minutes=5 * t),
                    "exchange": "X",
                    "instrument_id": str(product),
                    "product": str(product),
                    "segment_id": 1,
                    "eligible": True,
                    "close": close,
                    "high": close + 2,
                    "low": close - 1,
                    "open_interest": 1000.0 + 3 * t + product,
                    "close_p1": close + 3 + np.cos(t / 9),
                    "close_p2": close + 4,
                    "days_to_maturity": 10.0,
                    "days_to_maturity_p1": 100.0,
                    "segment_id_p1": 1,
                    "segment_id_p2": 1,
                    "roll_return": 0.001 * np.sin(t / 11),
                    "basis": 0.01 * np.cos(t / 13),
                    "broker_net": 30.0 + np.sin(t / 7),
                    "ls_ratio": 1.0 + 0.1 * np.sin(t / 7),
                    "virtual_ratio": 2.0 + 0.2 * np.cos(t / 11),
                    "inventory": 100.0 + t + np.sin(t / 7),
                    "warehouse_receipt": 50.0 + t + np.cos(t / 9),
                    "spot_profit": 5.0 + np.sin(t / 11),
                }
            )
    return pl.DataFrame(rows)


def tail_reference(g, scale):
    """Independent scalar NumPy reference for every adapted formula at the final row."""
    arrays = {name: g[name].to_numpy() for name in g.columns}
    c = arrays["close"]

    def lag(x, days):
        return x[-1 - days * scale]

    def z(name):
        x = arrays[name]
        return (x[-1] - x[-60 * scale :].mean()) / x[-60 * scale :].std(ddof=1)

    hv = np.log(c[1:] / c[:-1])[-20 * scale :].std(ddof=1) * np.sqrt(252 * scale)
    hh, ll = arrays["high"][-55 * scale :].max(), arrays["low"][-55 * scale :].min()
    pn, pf = arrays["close"][-1], arrays["close_p1"][-1]
    dd = arrays["days_to_maturity_p1"][-1] - arrays["days_to_maturity"][-1]
    carry = (pn / pf - 1) * 365 / dd
    oi = arrays["open_interest"]
    return {
        "tsmom_252": c[-1] / lag(c, 252) - 1,
        "tsmom_252_21": lag(c, 21) / lag(c, 252) - 1,
        "tsmom_63": c[-1] / lag(c, 63) - 1,
        "breakout_55": np.clip((c[-1] - (hh + ll) / 2) / ((hh - ll) / 2), -1, 1),
        "sma_xover_20_100": (c[-20 * scale :].mean() - c[-100 * scale :].mean()) / (hv * c[-1]),
        "carry_ann": carry,
        "roll_return_63": arrays["roll_return"][-63 * scale :].sum(),
        "basis_mom_20": arrays["basis"][-1] - lag(arrays["basis"], 20),
        "vol_scaled_carry": carry / hv,
        "ts_slope": np.log(pf / pn) / dd,
        "ts_curvature": arrays["close_p2"][-1] - 2 * arrays["close_p1"][-1] + c[-1],
        "oi_price_confirm_20": np.sign(c[-1] - lag(c, 20)) * np.log(oi[-1] / lag(oi, 20)),
        "broker_net_chg_5": (arrays["broker_net"][-1] - lag(arrays["broker_net"], 5)) / oi[-1],
        "ls_ratio_z": z("ls_ratio"),
        "virtual_ratio_chg": arrays["virtual_ratio"][-1] - lag(arrays["virtual_ratio"], 20),
        "inventory_mom_20": np.log(arrays["inventory"][-1] / lag(arrays["inventory"], 20)),
        "receipt_mom_20": np.log(
            arrays["warehouse_receipt"][-1] / lag(arrays["warehouse_receipt"], 20)
        ),
        "spot_profit_z": z("spot_profit"),
        "lowvol": -hv,
        "st_reversal_5": -(c[-1] / lag(c, 5) - 1),
    }


@pytest.mark.parametrize("frequency,scale", [("1d", 1), ("5m", 2)])
def test_all_formulas_against_independent_numerical_reference(frequency, scale):
    library = load_library("futures_cta", frequency=frequency, bars_per_day=scale)
    data = panel()
    groups = data.partition_by("instrument_id", maintain_order=True)
    expected = [tail_reference(g, scale) for g in groups]
    for cross, base in [("xsmom_252_21", "tsmom_252_21"), ("xsmom_63", "tsmom_63")]:
        raw = np.array([r[base] for r in expected])
        z = (raw - raw.mean()) / raw.std(ddof=1)
        for row, value in zip(expected, z, strict=True):
            row[cross] = value
    assert len(library.factors) == 22
    assert len({f.source_name for f in library.factors}) == 22
    for factor in library.factors:
        compiled = compile_factor(factor.expression, set(data.columns))
        assert compiled.fields == factor.fields
        # Source metadata creates no competing identity: the normal DSL AST is authoritative.
        assert (
            compile_factor(compiled.expression, set(data.columns)).factor_id == compiled.factor_id
        )
        result = execute(compiled, data, set(data.columns))
        actual = result.filter(pl.col("row_id").is_in([g["row_id"][-1] for g in groups]))["value"]
        np.testing.assert_allclose(actual.to_numpy(), [r[factor.name] for r in expected], rtol=1e-8)


def test_explicit_window_scale_and_source_mapping():
    library = load_library("futures_cta", bars_per_day=48)
    factors = {f.name: f for f in library.factors}
    momentum = compile_factor(factors["tsmom_252"].expression, {"close"})
    assert momentum.lookback == 252 * 48
    assert factors["sma_xover_20_100"].source_name == "ema_xover_20_100"
    assert "SMA adaptation" in factors["sma_xover_20_100"].note
    assert "365" in factors["carry_ann"].expression  # maturity days are not multiplied by 48
    assert library.frequency == "5m" and library.bars_per_day == 48
    with pytest.raises(FrozenInstanceError):
        library.bars_per_day = 1


@pytest.mark.parametrize("scale", [None, 0, -1, 1.5, True])
def test_five_minute_scale_requires_explicit_positive_integer(scale):
    with pytest.raises(ValueError, match="positive integer"):
        load_library("futures_cta", bars_per_day=scale)


def test_invalid_library_or_frequency():
    with pytest.raises(ValueError, match="unknown"):
        load_library("unknown")
    with pytest.raises(ValueError, match="frequency"):
        load_library("futures_cta", frequency="1h")
    for scale in [0, 2, True, 1.0]:
        with pytest.raises(ValueError, match="1d requires"):
            load_library("futures_cta", frequency="1d", bars_per_day=scale)


def test_missing_inputs_are_visible_and_candidates_use_normal_submission_type():
    library = load_library("futures_cta", bars_per_day=48)
    rows = {r["name"]: r for r in library.inspect({"close", "high", "low", "open_interest"})}
    assert rows["carry_ann"]["status"] == "missing_fields"
    assert set(rows["carry_ann"]["missing_fields"]) == {
        "close_p1",
        "days_to_maturity",
        "days_to_maturity_p1",
    }
    assert rows["oi_price_confirm_20"]["status"] == "available"
    candidates = library.candidates({"close", "high", "low", "open_interest"})
    assert len(candidates) == 10
    assert {c.name for c in candidates} == {
        n for n, r in rows.items() if r["status"] == "available"
    }
    assert len(library.candidates({"close"})) == 8
    assert library.candidates(set()) == ()
    rows["tsmom_252"]["expression"] = "future data"
    assert library.inspect({"close"})[0]["expression"] != "future data"
    for candidate in candidates:
        compile_factor(candidate.expression, {"close", "high", "low", "open_interest"})


def test_disabled_library_does_not_import_optional_definitions():
    code = (
        "import sys,json; from alpha_atlas.factor_libraries import load_library; "
        "assert load_library() is None; "
        "print(json.dumps('alpha_atlas.factor_libraries.futures_cta' in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) is False


def test_windows_reset_at_exchange_contract_and_continuity_boundaries():
    factor = next(
        f for f in load_library("futures_cta", bars_per_day=2).factors if f.name == "st_reversal_5"
    )
    data = panel(30).filter(pl.col("instrument_id") == "0")
    # Same product across all groups must never become the window identity.
    other_exchange = data.with_columns(
        pl.lit("Y").alias("exchange"), (pl.col("row_id") + 100).alias("row_id")
    )
    other_contract = data.with_columns(
        pl.lit("1").alias("instrument_id"), (pl.col("row_id") + 200).alias("row_id")
    )
    data = pl.concat([data, other_exchange, other_contract]).with_columns(
        pl.when(pl.col("row_id") % 100 >= 15).then(2).otherwise(1).alias("segment_id")
    )
    result = data.join(execute(factor.expression, data, {"close"}), on="row_id")
    for g in result.partition_by("exchange", "instrument_id", "segment_id"):
        assert g["value"][:10].null_count() == 10
        assert g["value"][10:].null_count() == 0
    changed = data.with_columns(
        pl.when(pl.col("exchange") == "Y").then(pl.col("close") * 2).otherwise(pl.col("close"))
    )
    left = execute(factor.expression, data, {"close"})
    right = execute(factor.expression, changed, {"close"})
    assert left.filter(pl.col("row_id") < 30).equals(right.filter(pl.col("row_id") < 30))


def test_full_window_zero_denominator_and_causality():
    factors = {f.name: f for f in load_library("futures_cta", frequency="1d").factors}
    data = panel(300).filter(pl.col("instrument_id") == "0")
    for name, lookback in [("tsmom_252", 252), ("lowvol", 20), ("sma_xover_20_100", 99)]:
        result = execute(factors[name].expression, data, set(data.columns))
        assert result["value"][:lookback].null_count() == lookback
        assert result["value"][lookback] is not None
        prefix = execute(factors[name].expression, data.head(280), set(data.columns))
        assert prefix.equals(result.head(280))
    for name, bad in [
        ("carry_ann", data.with_columns(pl.col("days_to_maturity").alias("days_to_maturity_p1"))),
        ("breakout_55", data.with_columns(pl.lit(1.0).alias("high"), pl.lit(1.0).alias("low"))),
        ("ls_ratio_z", data.with_columns(pl.lit(1.0).alias("ls_ratio"))),
        ("inventory_mom_20", data.with_columns(pl.lit(-1.0).alias("inventory"))),
    ]:
        result = execute(factors[name].expression, bad, set(bad.columns))
        if name == "inventory_mom_20":
            # Ratio of two negative inputs is positive: inherit DSL math, no extra policy.
            assert result["value"][20] == 0
        else:
            assert result["value"].null_count() == bad.height


def test_cross_section_excludes_ineligible_members():
    factor = next(
        f for f in load_library("futures_cta", frequency="1d").factors if f.name == "xsmom_63"
    )
    data = panel(90).with_columns((pl.col("instrument_id") != "2").alias("eligible"))
    result = data.join(execute(factor.expression, data, {"close"}), on="row_id")
    assert result.filter(~pl.col("eligible"))["value"].drop_nulls().is_empty()
    last = result.filter(pl.col("timestamp") == data["timestamp"].max()).filter(pl.col("eligible"))
    np.testing.assert_allclose(sorted(last["value"]), [-1 / np.sqrt(2), 1 / np.sqrt(2)], atol=1e-9)
