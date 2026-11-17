import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.operators import OperatorRegistry


def panel(x, y, eligible=None, days=None):
    size = len(x)
    return pl.DataFrame(
        {
            "row_id": range(size),
            "exchange": ["X" if i % 2 else "Y" for i in range(size)],
            "instrument_id": [f"A{i // 2}" for i in range(size)],
            "timestamp": [
                datetime(2020, 1, 1) + timedelta(days=d)
                for d in ([0] * size if days is None else days)
            ],
            "eligible": [True] * size if eligible is None else eligible,
            "x": x,
            "y": y,
        }
    )


def residuals(data, expression="CS_RESID($y,$x)"):
    return execute(expression, data, {"x", "y"}).sort("row_id")["value"]


def test_cs_resid_hand_calculation_and_intercept():
    data = panel([0.0, 1.0, 2.0, 3.0], [1.0, 4.0, 2.0, 5.0])
    # alpha=1.5, beta=1; this also detects omission of the intercept.
    np.testing.assert_allclose(residuals(data), [-0.5, 1.5, -1.5, 0.5], atol=1e-14)


def test_cs_resid_matches_independent_ols_and_isolates_timestamps():
    rng = np.random.default_rng(842)
    x = rng.normal(size=120)
    y = 7.0 + 2.3 * x + rng.normal(size=120)
    eligible = [i % 9 != 0 for i in range(120)]
    eligible[7] = None
    days = np.repeat([0, 1, 2], 40).tolist()
    x[[3, 43, 83]] = np.nan
    y[[4, 44, 84]] = np.inf
    # Unpaired extreme values must affect neither predictor nor response means.
    y[3], x[4] = 1e15, -1e15
    data = panel(x, y, eligible, days)
    expected = np.full(data.height, np.nan)
    for part in data.partition_by("timestamp"):
        px, py = part["x"].to_numpy(), part["y"].to_numpy()
        valid = part["eligible"].fill_null(False).to_numpy() & np.isfinite(px) & np.isfinite(py)
        design = np.column_stack([np.ones(valid.sum()), px[valid]])
        fitted = design @ np.linalg.lstsq(design, py[valid], rcond=None)[0]
        expected[part["row_id"].to_numpy()[valid]] = py[valid] - fitted

    actual = residuals(data.sample(fraction=1, shuffle=True, seed=14)).to_numpy()
    np.testing.assert_allclose(actual, expected, equal_nan=True, atol=1e-12)
    for day in (0, 1, 2):
        valid = (np.array(days) == day) & np.isfinite(actual)
        assert abs(actual[valid].sum()) < 1e-11
        assert abs(np.dot(actual[valid], x[valid])) < 1e-11

    changed = data.with_columns(
        pl.when(~pl.col("eligible").fill_null(False)).then(1e30).otherwise(pl.col("x")).alias("x"),
        pl.when(~pl.col("eligible").fill_null(False)).then(-1e30).otherwise(pl.col("y")).alias("y"),
    )
    np.testing.assert_allclose(residuals(changed), actual, equal_nan=True, atol=1e-12)

    cutoff = datetime(2020, 1, 2)
    prefix = data.filter(pl.col("timestamp") <= cutoff)
    perturbed = data.with_columns(
        pl.when(pl.col("timestamp") > cutoff).then(-999.0).otherwise(pl.col("y")).alias("y")
    )
    np.testing.assert_allclose(
        residuals(prefix),
        residuals(perturbed).head(prefix.height),
        equal_nan=True,
        atol=1e-12,
    )


@pytest.mark.parametrize(
    ("x", "y", "eligible", "expected"),
    [
        ([1.0, 2.0], [5.0, -2.0], None, [0.0, 0.0]),
        ([1.0, 1.0, 1.0], [2.0, 3.0, 7.0], None, [None] * 3),
        ([1.0], [2.0], None, [None]),
        ([1.0, 2.0, 3.0], [7.0] * 3, None, [0.0] * 3),
        ([1.0, 2.0], [3.0, 4.0], [True, False], [None, None]),
        ([1.0, 2.0], [3.0, 4.0], [False, None], [None, None]),
        ([None, None], [3.0, 4.0], None, [None, None]),
    ],
)
def test_cs_resid_degenerate_groups(x, y, eligible, expected):
    actual = residuals(panel(x, y, eligible)).to_numpy()
    np.testing.assert_allclose(actual, np.array(expected, dtype=float), equal_nan=True, atol=1e-12)


@pytest.mark.parametrize("scale", [1e-20, 1.0, 1e20])
def test_cs_resid_is_invariant_to_predictor_scale(scale):
    x = np.array([0.0, 1.0, 2.0, 3.0])
    data = panel(x * scale, [1.0, 4.0, 2.0, 5.0])
    np.testing.assert_allclose(residuals(data), [-0.5, 1.5, -1.5, 0.5], atol=1e-12)


def test_cs_resid_large_common_offsets():
    x = 1e12 + np.arange(4)
    y = 1e12 + np.array([1.0, 4.0, 2.0, 5.0])
    np.testing.assert_allclose(residuals(panel(x, y)), [-0.5, 1.5, -1.5, 0.5], atol=1e-12)


def test_cs_resid_scalar_inputs():
    data = panel([1.0, 2.0, 3.0], [3.0, 5.0, 2.0])
    np.testing.assert_allclose(residuals(data, "CS_RESID(7,$x)"), [0.0] * 3, atol=1e-12)
    assert residuals(data, "CS_RESID($y,1)").to_list() == [None] * 3


def test_cs_resid_log_book_price_and_roe_with_config_registry():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "configs/versions/us_equity_v3asyc_deepseek.json").read_text())
    registry = OperatorRegistry(versions=config["operator_versions"])
    roe = np.array([0.05, 0.1, 0.2, 0.4])
    book_price = np.array([0.5, 0.9, 0.6, 1.2])
    book = np.array([10.0, 20.0, 30.0, 40.0])
    data = (
        panel([0.0] * 6, [0.0] * 6)
        .drop("x", "y")
        .with_columns(
            pl.Series("be", [*book, 20.0, -20.0]),
            pl.Series("market_cap", [*(book / book_price), 20.0, 20.0]),
            pl.Series("ib", [*(roe * book), -2.0, 2.0]),
        )
    )
    expression = "CS_RESID(LOG($be / $market_cap),LOG($ib / $be))"
    compiled = compile_factor(expression, set(config["fields"]), registry)
    assert compiled.lookback == 0
    assert dict(compiled.dependencies)["CS_RESID"] == "1"
    result = execute(compiled, data, set(config["fields"]), registry).sort("row_id")["value"]

    design = np.column_stack([np.ones(4), np.log(roe)])
    y = np.log(book_price)
    expected = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
    np.testing.assert_allclose(result.head(4), expected, atol=1e-12)
    assert result.tail(2).to_list() == [None, None]

    disabled = OperatorRegistry(versions={"LOG": "1", "DIVIDE": "1"})
    with pytest.raises(ValueError, match="unknown operator"):
        compile_factor(expression, set(config["fields"]), disabled)


@pytest.mark.parametrize(
    "expression",
    [
        "CS_RESID($y)",
        "CS_RESID($y,$x,3)",
        "CS_RESID($y,$x>0)",
        "CS_RESID($y>0,$x)",
    ],
)
def test_cs_resid_rejects_invalid_signatures(expression):
    with pytest.raises(ValueError):
        compile_factor(expression, {"x", "y"})
