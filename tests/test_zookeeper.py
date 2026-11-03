import importlib.util
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from alpha_atlas.assets.zookeeper import daily_base, daily_curve
from alpha_atlas.checkpoint import read_json
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.factor_libraries.zookeeper import candidates, definitions
from alpha_atlas.factor_libraries.zookeeper.compute import (
    _bgr,
    _monthly_curve,
    _portfolio_mean,
    align_monthly,
    compute_monthly,
)
from alpha_atlas.runner import run_model


@pytest.fixture(scope="module")
def daily():
    days = []
    day = date(2012, 1, 2)
    while day <= date(2019, 12, 31):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    base, curve = [], []
    for k in range(8):
        t = np.arange(len(days))
        r = 0.0003 + 0.003 * np.sin(t / (9 + k) + k) + 0.001 * np.cos(t / 35 + k)
        price = (80 + k * 12) * np.cumprod(1 + r)
        for i, day in enumerate(days):
            base.append(
                {
                    "exchange": "X",
                    "product": str(k),
                    "trading_day": day,
                    "price": price[i],
                    "volume": float(100 + i + k),
                    "amount": float((100 + i + k) * price[i] * 10),
                    "oi": float(1000 + 2 * i + k),
                }
            )
            curve.append(
                {
                    "exchange": "X",
                    "product": str(k),
                    "trading_day": day,
                    "F0": price[i],
                    "F1": price[i] * (1.01 + 0.003 * np.sin(i / 40 + k)),
                    "F2": price[i] * 1.03,
                    "leg0": f"{k}-0",
                    "leg1": f"{k}-1",
                    "leg2": f"{k}-2",
                }
            )
    return pl.DataFrame(base), pl.DataFrame(curve)


@pytest.fixture(scope="module")
def monthly(daily):
    return compute_monthly(*daily)


def test_portable_catalogue_and_exclusions():
    rows = definitions()
    assert len(rows) == len(candidates()) == 26
    assert sum(r["input_group"] == "eight_fields" for r in rows) == 16
    assert sum(r["input_group"] == "curve" for r in rows) == 9
    path = Path(__file__).parents[1] / "src/alpha_atlas/factor_libraries/zookeeper/excluded.jsonl"
    excluded = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(excluded) == 11
    assert len({r["name"] for r in (*rows, *excluded)}) == 37
    assert all(r["description"] and r["formula"] and r["adaptation_notes"] for r in rows)
    assert all(c.expression == r["expression"] for c, r in zip(candidates(), rows, strict=True))
    rows[0]["description"] = "changed copy"
    assert definitions()[0]["description"] != "changed copy"


def test_independent_calendar_and_distribution_formulas(daily, monthly):
    base, curve = daily
    base = base.filter(pl.col("product") == "0")
    rows = monthly.filter(pl.col("product") == "0")
    last = rows.row(-1, named=True)
    prices = base["price"].to_numpy()
    days = base["trading_day"].to_list()
    returns = prices[1:] / prices[:-1] - 1
    trailing = returns[-250:]
    centered = trailing - trailing.mean()
    assert last["zk_var"] == pytest.approx(np.std(trailing, ddof=1))
    assert last["zk_skew"] == pytest.approx(np.mean(centered**3) / np.mean(centered**2) ** 1.5)
    assert last["zk_kurt"] == pytest.approx(np.mean(centered**4) / np.mean(centered**2) ** 2 - 3)
    historical_vol = np.array(
        [np.std(returns[i - 249 : i + 1], ddof=1) for i in range(len(returns) - 250, len(returns))]
    )
    assert last["zk_vov"] == pytest.approx(np.std(historical_vol, ddof=1))
    # Calendar endpoints independently verify 1, 12, 36 and 60 month compounding.
    for name, end_day in [
        ("mom1", date(2019, 11, 30)),
        ("mom12", date(2018, 12, 31)),
        ("val36", date(2016, 12, 31)),
        ("val60", date(2014, 12, 31)),
    ]:
        index = max(i for i, day in enumerate(days) if day <= end_day)
        assert last["zk_" + name] == pytest.approx(prices[-1] / prices[index] - 1)
    latest = returns[np.array([d.year == 2019 and d.month == 12 for d in days[1:]])]
    assert last["zk_max"] == pytest.approx(latest.max())
    calendar_year = returns[np.array([d.year == 2019 for d in days[1:]])]
    assert last["zk_cv"] == pytest.approx(calendar_year.var(ddof=1) / calendar_year.mean())
    amount = base.filter(pl.col("trading_day") >= date(2019, 12, 1))["amount"].to_numpy()
    assert last["zk_iliq"] == pytest.approx(1 / np.mean(amount / np.abs(latest)))
    raw_curve = curve.filter(pl.col("product") == "0")
    previous_year = raw_curve.filter(pl.col("trading_day") <= date(2018, 12, 31)).row(
        -1, named=True
    )
    latest_curve = raw_curve.row(-1, named=True)
    assert last["zk_bmom"] == pytest.approx(
        latest_curve["F0"] / previous_year["F0"] - latest_curve["F1"] / previous_year["F1"]
    )
    assert last["zk_dOI"] == pytest.approx(rows["OI"][-1] - rows["OI"][-2])
    assert last["zk_dmaOI"] == pytest.approx(
        rows["OI"][-1] / np.mean(rows["OI"][-13:-1].to_numpy()) - 1
    )
    assert last["zk_atv"] == pytest.approx(
        rows["V"][-1] / np.mean(rows["V"][-13:-1].to_numpy()) - 1
    )
    assert last["zk_val"] == pytest.approx(rows["P"][-1] / np.mean(rows["P"][-55:-42].to_numpy()))
    assert last["zk_seasonality"] == pytest.approx(rows["R"][-12])
    source = curve.filter(pl.col("product") == "0").row(-1, named=True)
    assert last["zk_basis"] == pytest.approx(source["F0"] / source["F1"])
    assert last["zk_rbasis"] == pytest.approx(source["F0"] * source["F2"] / source["F1"] ** 2)
    for name in ("basis", "rbasis", "bmom"):
        assert last["zk_" + name + "12"] == pytest.approx(
            np.mean(rows["zk_" + name][-12:].to_numpy())
        )
    for name in ("basis12", "bmom12", "rbasis"):
        assert last["zk_l2" + name] == pytest.approx(rows["zk_" + name][-3])
    assert all(last[r["prepared_field"]] is not None for r in definitions())


def test_curve_return_omits_contract_jump_and_insufficient_month():
    days = [date(2020, 1, i) for i in range(1, 21)]
    frame = pl.DataFrame(
        {
            "trading_day": days,
            "F0": [100.0] * 10 + [900.0] * 10,
            "F1": [110.0] * 20,
            "F2": [120.0] * 20,
            "leg0": ["A"] * 10 + ["B"] * 10,
            "leg1": ["C"] * 20,
            "leg2": ["D"] * 20,
        }
    )
    rows = _monthly_curve(frame, {d: i for i, d in enumerate(days)})
    assert rows[0]["C0"] == 0  # The 800% contract switch is never a return.
    assert rows[0]["curve_return_coverage0"] == 0.9
    bad = frame.with_columns(pl.Series("leg0", [str(i) for i in range(20)]))
    assert np.isnan(_monthly_curve(bad, {d: i for i, d in enumerate(days)})[0]["C0"])


def test_causality_null_history_and_month_alignment(daily, monthly):
    cutoff = date(2018, 12, 31)
    prefix = compute_monthly(*(f.filter(pl.col("trading_day") <= cutoff) for f in daily))
    expected = monthly.filter(pl.col("month") <= cutoff)
    assert prefix.equals(expected)
    assert monthly.filter(pl.col("month") < date(2017, 2, 1))["zk_val60"].null_count() > 0
    features = pl.DataFrame(
        {
            "exchange": ["X"] * 3,
            "product": ["0"] * 3,
            "trading_day": [date(2019, 12, 31), date(2020, 1, 2), date(2020, 3, 2)],
            "row_id": [9, 8, 7],
        }
    )
    aligned = align_monthly(features, monthly)
    product = monthly.filter(pl.col("product") == "0")
    assert aligned["row_id"].to_list() == [9, 8, 7]
    assert aligned["zk_mom1"].to_list() == [product["zk_mom1"][-2], product["zk_mom1"][-1], None]
    # An absent trading day breaks daily history instead of compressing time.
    removed_day = date(2019, 12, 16)
    interrupted = compute_monthly(
        daily[0].filter(~((pl.col("product") == "0") & (pl.col("trading_day") == removed_day))),
        daily[1],
    ).filter((pl.col("product") == "0") & (pl.col("month") == date(2019, 12, 1)))
    assert interrupted["zk_var"][0] is None
    assert interrupted["zk_mom1"][0] is None


def test_daily_weighting_and_invalid_last_curve_bar():
    features = pl.DataFrame(
        {
            "exchange": ["X"] * 3,
            "product": ["P"] * 3,
            "trading_day": [date(2020, 1, 1)] * 3,
            "timestamp": [datetime(2020, 1, 1, 9, 5 * i) for i in range(3)],
            "adj_vwap": [10.0, 20.0, None],
            "volume": [1.0, 3.0, 100.0],
            "amount": [10.0, 60.0, 0.0],
            "open_interest": [10.0, 11.0, 12.0],
        }
    )
    observed = daily_base(features)
    assert observed["price"][0] == 17.5
    assert observed["oi"][0] == 12
    missing = daily_base(features.with_columns(pl.lit(None, dtype=pl.Float64).alias("amount")))
    assert missing["amount"][0] is None
    for suffix in ("", "_p1", "_p2"):
        features = features.with_columns(
            pl.Series("close" + suffix, [10.0, 11.0, 12.0]),
            pl.Series("instrument_id" + suffix, ["A", "A", "A"]),
            pl.Series("valid_bar" + suffix, [True, True, suffix != "_p1"]),
        )
    curve = daily_curve(features)
    assert curve["F0"][0] == 12.0
    assert curve["F1"][0] is None
    assert curve["leg1"][0] is None


def test_published_monthly_fields_ignore_partial_month_and_future_changes(daily, monthly):
    # Midnight is not a trading-day boundary: Friday night can belong to next month.
    days = [date(2019, 6, 14), date(2019, 6, 28), date(2019, 7, 1)]
    features = pl.DataFrame(
        {
            "exchange": ["X"] * 3,
            "product": ["0"] * 3,
            "instrument_id": ["P"] * 3,
            "trading_day": days,
            "timestamp": [
                datetime(2019, 6, 14, 10),
                datetime(2019, 6, 28, 15),
                datetime(2019, 6, 28, 21, 5),
            ],
            "row_id": [17, 3, 11],
            "product_segment_id": [1] * 3,
        }
    )
    full = align_monthly(features, monthly)
    for cutoff in days:
        observed = features.filter(pl.col("trading_day") <= cutoff)
        prefix = compute_monthly(*(d.filter(pl.col("trading_day") <= cutoff) for d in daily))
        assert align_monthly(observed, prefix).equals(full.filter(pl.col("trading_day") <= cutoff))
    changed = []
    for source in daily:
        floats = [name for name, dtype in source.schema.items() if dtype == pl.Float64]
        changed.append(
            source.with_columns(
                pl.when(pl.col("trading_day") >= date(2019, 7, 1))
                .then(pl.col(floats) * 1000 + 123)
                .otherwise(pl.col(floats))
                .name.keep()
            )
        )
    assert align_monthly(features, compute_monthly(*changed)).equals(full)
    source_month = monthly.filter(
        (pl.col("product") == "0") & (pl.col("month") == date(2019, 6, 1))
    )
    assert full["zk_mom1"][-1] == source_month["zk_mom1"][0]
    # Every frozen expression must execute exactly the named prepared column, even shuffled.
    fields = {r["prepared_field"] for r in definitions()}
    for definition in definitions():
        compiled = compile_factor(definition["expression"], fields)
        assert compiled.expression.op == "field" and not compiled.dependencies
        actual = execute(compiled, full.reverse(), fields, panel="product").sort("row_id")
        expected = full.select("row_id", pl.col(definition["prepared_field"]).alias("value"))
        assert actual.equals(expected.sort("row_id"))


def test_bgr_ols_uses_lagged_sort_and_full_rank(monthly):
    data = monthly.rename({r["prepared_field"]: r["name"] for r in definitions()})
    # Independent portfolio construction for the last 12 months, then direct OLS.
    last13 = sorted(data["month"].unique())[-13:]
    x, y = [], []
    for prev, now in zip(last13[:-1], last13[1:], strict=True):
        before = data.filter(pl.col("month") == prev).sort("product")
        after = data.filter(pl.col("month") == now).sort("product")
        ret = dict(zip(after["product"], after["R"], strict=True))
        factors = [1.0, np.mean(list(ret.values()))]
        for signal in ("basis", "mom12"):
            ranked = before.sort(signal, "product")["product"].to_list()
            factors.append(
                np.mean([ret[k] for k in ranked[-2:]]) - np.mean([ret[k] for k in ranked[:2]])
            )
        x.append(factors)
        y.append(ret["0"])
    x, y = np.array(x), np.array(y)
    expected = np.std(y - x @ np.linalg.lstsq(x, y, rcond=None)[0], ddof=1)
    actual = monthly.filter(pl.col("product") == "0")["zk_ivolaBGR"][-1]
    assert actual == pytest.approx(expected)
    singular = data.with_columns(pl.lit(0.0).alias("R"))
    assert np.isnan(_bgr(singular).to_numpy()).all()


@pytest.fixture
def audit_run(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        "zookeeper_audit", root / "scripts/evaluate_zookeeper.py"
    )
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    shutil.copytree(root / "configs", tmp_path / "configs")
    rows = []
    for year in (2019, 2022, 2024, 2025):
        for k in range(2):
            for t in range(80):
                stamp = datetime(year, 1, 2, 9) + timedelta(minutes=5 * t)
                row = dict(
                    row_id=len(rows),
                    exchange="X",
                    product=str(k),
                    instrument_id=f"{year}-{k}",
                    timestamp=stamp,
                    trading_day=stamp.date(),
                    eligible=True,
                    target_eligible=True,
                    segment_id=1,
                    product_segment_id=1,
                    adj_close=100 + t * 0.01 + np.sin(t / 4),
                    amount=100.0,
                )
                row.update(
                    {
                        r["prepared_field"]: np.cos(t / (i + 2)) * (100 if year >= 2024 else 1)
                        for i, r in enumerate(definitions())
                    }
                )
                rows.append(row)
    data = pl.DataFrame(rows)
    calls = []

    class Market:
        def __init__(self, *_):
            self.monthly = pl.DataFrame({"synthetic": [1]})

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def export_monthly(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            self.monthly.write_parquet(path)

        def snapshot_id(self):
            return "synthetic-zookeeper"

        def provenance(self):
            return {"history_start": "2012-01-01"}

        def load_features(self, *, fields, start, end):
            calls.append(end)
            if end.year == 2025:
                run = next((tmp_path / "artifacts/reference-evaluations").glob("*/run.json"))
                assert json.loads(run.read_text())["status"] == "frozen"
                assert (run.parent / "frozen/library.json").exists()
            return data.filter(pl.col("trading_day").is_between(start, end))

    monkeypatch.setattr(audit, "ZookeeperMarketData", Market)
    monkeypatch.setattr("alpha_atlas.assets.zookeeper.ZookeeperMarketData", Market)
    directory = audit.freeze(tmp_path, "fold2")
    assert calls == [date(2023, 12, 31)]
    return audit, directory, calls, Market


def test_audit_freezes_before_test_and_reuses_train_clipping(tmp_path, audit_run):
    audit, directory, calls, _ = audit_run
    audit.test(tmp_path, directory)
    assert calls == [date(2023, 12, 31), date(2025, 12, 31)]
    records = [json.loads(s) for s in (directory / "results.jsonl").read_text().splitlines()]
    assert len(records) == 26
    assert all(abs(r["clipping"]["value"][1]) <= 1 for r in records)
    assert all(r["test_coverage"] == 1 for r in records)
    frozen = json.loads((directory / "frozen/library.json").read_text())
    oos = json.loads((directory / "oos.json").read_text())
    for item, result in zip(frozen["factors"], oos["results"], strict=True):
        raw = {m["name"]: m["value"] for m in result["metrics"] if m["split"] == "test_raw"}
        for m in result["metrics"]:
            if m["split"] == "test" and m["value"] is not None:
                assert m["value"] == pytest.approx(item["report"]["direction"] * raw[m["name"]])
    frozen["definitions"][0]["formula"] = "tampered"
    (directory / "frozen/library.json").write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="modified"):
        audit.test(tmp_path, directory)


def test_catalogue_models_freeze_fit_before_test_and_validate_cache(
    tmp_path, audit_run, monkeypatch
):
    _, directory, calls, market = audit_run
    original = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    load = market.load_features

    def checked_load(self, **kwargs):
        if kwargs["end"].year == 2025:
            assert (directory / "model/fit.json").exists()
            assert kwargs["start"] == date(2024, 1, 1)
        else:
            assert kwargs["start"] == date(2019, 1, 1)
        return load(self, **kwargs)

    monkeypatch.setattr(market, "load_features", checked_load)
    with pytest.raises(ValueError, match="before"):
        run_model(tmp_path, directory, test=True)
    first = run_model(tmp_path, directory)
    assert first["reports"] == {}
    assert calls == [date(2023, 12, 31)] * 2
    fit_path = directory / "model/fit.json"
    fitted_bytes = fit_path.read_bytes()
    fitted = read_json(fit_path)
    frozen = read_json(directory / "frozen/library.json")
    assert fitted["factor_ids"] == [f["compiled"]["factor_id"] for f in frozen["factors"]]
    assert len(fitted["fitted"]["preprocessing"]["columns"]) == 26
    assert all(abs(v) <= 1 for v in fitted["fitted"]["preprocessing"]["upper"])
    assert fitted["fitted"]["training_rows"] == 2 * 2 * (80 - 12)

    def forbidden(*args, **kwargs):
        pytest.fail("saved model must never refit or rescan current source")

    monkeypatch.setattr("alpha_atlas.model.fit_models", forbidden)
    monkeypatch.setattr("alpha_atlas.runner.source_fingerprint", forbidden)
    assert run_model(tmp_path, directory) == first
    result = run_model(tmp_path, directory, test=True)
    assert set(result["reports"]["test"]) == {"linear", "lightgbm"}
    assert run_model(tmp_path, directory, test=True) == result
    assert calls == [date(2023, 12, 31)] * 2 + [date(2025, 12, 31)]
    assert fit_path.read_bytes() == fitted_bytes
    assert all(p.read_bytes() == contents for p, contents in original.items())
    with monkeypatch.context() as patch:
        patch.setattr(market, "snapshot_id", lambda _: "changed")
        with pytest.raises(ValueError, match="dataset changed"):
            run_model(tmp_path, directory, test=True)
    changed = list(definitions())
    changed[0]["formula"] = "changed"
    monkeypatch.setattr("alpha_atlas.factor_libraries.zookeeper.definitions", lambda: changed)
    with pytest.raises(ValueError, match="definitions changed"):
        run_model(tmp_path, directory, test=True)


def test_portfolio_missingness_has_fixed_coverage_not_zero_returns():
    assert _portfolio_mean(np.array([1.0, 2.0, 3.0, 4.0, np.nan])) == 2.5
    assert np.isnan(_portfolio_mean(np.array([1.0, 2.0, 3.0, np.nan, np.nan])))
    assert np.isnan(_portfolio_mean(np.array([])))
