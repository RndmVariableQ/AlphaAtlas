from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from alpha_atlas.contracts import Expression
from alpha_atlas.expressions import compile_factor, execute, parse
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.operators.builtin import window_array


def frame(values=(2.0, 1.0, 3.0, 5.0, 4.0)):
    n = len(values)
    return pl.DataFrame(
        {
            "row_id": range(n),
            "exchange": ["X"] * n,
            "instrument_id": ["A"] * n,
            "timestamp": [datetime(2020, 1, 1) + timedelta(days=i) for i in range(n)],
            "eligible": [True] * n,
            "x": values,
            "y": list(reversed(values)),
        }
    )


def values(text, data=None, registry=None):
    return execute(text, frame() if data is None else data, {"x", "y"}, registry)[
        "value"
    ].to_numpy()


def test_text_ast_identity_and_composite():
    registry = OperatorRegistry()
    definition = OperatorDefinition(
        "DEV", (("x", "series"), ("n", "window")), "x / TS_MEAN(x,n) - 1"
    )
    assert registry.register(definition).accepted
    a = compile_factor("v = $x / TS_MEAN($x,5)\nv - 1", {"x"}, registry)
    b = compile_factor("DEV($x,5)", {"x"}, registry)
    assert a.factor_id == b.factor_id
    assert a.lookback == 4
    assert not registry.register(definition).accepted
    legacy = Expression("mean", (Expression("field", value="x"),), 5)
    assert (
        compile_factor(legacy, {"x"}).factor_id == compile_factor("TS_MEAN($x,5)", {"x"}).factor_id
    )
    assert (
        compile_factor("TS_WINSORIZE($x,5)", {"x"}).factor_id
        == compile_factor(
            "MIN(MAX($x,TS_QUANTILE($x,5,0.01)),TS_QUANTILE($x,5,0.99))", {"x"}
        ).factor_id
    )
    assert (
        not OperatorRegistry()
        .register(OperatorDefinition("LOOP", (("x", "series"),), "LOOP(x)"))
        .accepted
    )


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "$x[0]",
        "$x.mean()",
        "[x for x in $x]",
        "x=1",
        "x=1\nx=2\nx",
        "TS_MEAN($x,-1)",
        "TS_MEAN($x,2.5)",
        "TS_MEAN($x,$y)",
        "LOG($target)",
        "TS_STD($x,1)",
        "TS_QUANTILE($x,3,2)",
        "CS_WINSORIZE($x,0.9,0.1)",
        "AND($x,$y)",
        "GT($x,1)",
        "True",
        "ADD($x, 1, 2)",
    ],
)
def test_reject_invalid(text):
    with pytest.raises(ValueError):
        compile_factor(text, {"x", "y", "target"})


def test_complexity_and_history_after_expansion():
    registry = OperatorRegistry()
    assert registry.register(
        OperatorDefinition("LAGGED", (("x", "series"),), "DELAY(TS_MEAN(x,5),3)")
    ).accepted
    compiled = compile_factor("LAGGED($x)", {"x"}, registry)
    assert compiled.lookback == 7
    with pytest.raises(ValueError, match="budget"):
        compile_factor("LAGGED(LAGGED($x))", {"x"}, registry, max_depth=3)


def test_default_complexity_boundaries():
    deep = "NEG(" * 20 + "$x" + ")" * 20
    assert compile_factor(deep, {"x"}).depth == 20
    np.testing.assert_allclose(values(deep), frame()["x"].to_numpy())
    with pytest.raises(ValueError, match="budget"):
        compile_factor(f"NEG({deep})", {"x"})

    def balanced(leaves):
        if leaves == 1:
            return "$x"
        return f"ADD({balanced(leaves // 2)}, {balanced(leaves - leaves // 2)})"

    # 50 leaves + 49 ADD nodes + one NEG = exactly 100 expanded nodes.
    wide = f"NEG({balanced(50)})"
    assert compile_factor(wide, {"x"}).nodes == 100
    np.testing.assert_allclose(values(wide), -50 * frame()["x"].to_numpy())
    with pytest.raises(ValueError, match="budget"):
        compile_factor(f"NEG({wide})", {"x"})


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("ADD($x,2)", 6.0),
        ("SUBTRACT($x,2)", 2.0),
        ("MULTIPLY($x,2)", 8.0),
        ("DIVIDE($x,2)", 2.0),
        ("NEG($x)", -4.0),
        ("ABS(NEG($x))", 4.0),
        ("SIGN($x)", 1.0),
        ("LOG($x)", np.log(4.0)),
        ("LOG1P($x)", np.log(5.0)),
        ("SQRT($x)", 2.0),
        ("EXP($x)", np.exp(4.0)),
        ("POWER($x,2)", 16.0),
        ("SIGNED_POWER(NEG($x),0.5)", -2.0),
        ("MIN($x,3)", 3.0),
        ("MAX($x,3)", 4.0),
        ("CLIP($x,1,3)", 3.0),
        ("IF_THEN_ELSE(LT($x,5),$x,0)", 4.0),
        ("IF_THEN_ELSE(LE($x,4),1,0)", 1.0),
        ("IF_THEN_ELSE(GT($x,3),1,0)", 1.0),
        ("IF_THEN_ELSE(GE($x,4),1,0)", 1.0),
        ("IF_THEN_ELSE(EQ($x,4),1,0)", 1.0),
        ("IF_THEN_ELSE(NE($x,4),1,0)", 0.0),
        ("IF_THEN_ELSE(AND(GT($x,0),LT($x,5)),1,0)", 1.0),
        ("IF_THEN_ELSE(OR(LT($x,0),GT($x,3)),1,0)", 1.0),
        ("IF_THEN_ELSE(NOT(GT($x,0)),1,0)", 0.0),
        ("IF_THEN_ELSE(IS_FINITE($x),1,0)", 1.0),
        ("FILLNA(DIVIDE($x,0),7)", 7.0),
        ("DELAY($x,2)", 3.0),
        ("DELTA($x,2)", 1.0),
        ("RETURN($x,2)", 1 / 3),
        ("TS_SUM($x,3)", 12.0),
        ("TS_PROD($x,3)", 60.0),
        ("TS_MEAN($x,3)", 4.0),
        ("TS_MEDIAN($x,3)", 4.0),
        ("TS_LINEAR_DECAY($x,3)", 25 / 6),
        ("TS_STD($x,3)", 1.0),
        ("TS_VAR($x,3)", 1.0),
        ("TS_ZSCORE($x,3)", 0.0),
        ("TS_SKEW($x,3)", 0.0),
        ("TS_KURT($x,5)", -1.2),
        ("TS_QUANTILE($x,3,0.25)", 3.5),
        ("TS_WINSORIZE($x,3,0.6,0.9)", 4.2),
        ("TS_MIN($x,3)", 3.0),
        ("TS_MAX($x,3)", 5.0),
        ("TS_RANK($x,3)", 2 / 3),
        ("TS_ARGMIN($x,3)", 2.0),
        ("TS_ARGMAX($x,3)", 1.0),
        ("TS_CORR($x,$y,3)", -1.0),
        ("TS_COV($x,$y,3)", -1.0),
        ("TS_RANKCORR($x,$y,3)", -1.0),
        ("TS_COUNT(GT($x,3),3)", 2.0),
        ("TS_RATE(GT($x,3),3)", 2 / 3),
        ("IF_THEN_ELSE(TS_ANY(GT($x,3),3),1,0)", 1.0),
        ("IF_THEN_ELSE(TS_ALL(GT($x,3),3),1,0)", 0.0),
    ],
)
def test_numeric_golden(formula, expected):
    assert values(formula)[-1] == pytest.approx(expected)


def test_cross_section_and_membership():
    data = frame((1.0, 2.0, 2.0, 100.0)).with_columns(
        pl.lit(datetime(2020, 1, 1)).alias("timestamp"),
        pl.Series("instrument_id", ["A", "B", "C", "D"]),
        pl.Series("eligible", [True, True, True, False]),
    )
    np.testing.assert_allclose(values("CS_RANK($x)", data), [1 / 3, 2.5 / 3, 2.5 / 3, np.nan])
    np.testing.assert_allclose(values("CS_DEMEAN($x)", data), [-2 / 3, 1 / 3, 1 / 3, np.nan])
    np.testing.assert_allclose(
        values("CS_ZSCORE($x)", data),
        np.array([-2 / 3, 1 / 3, 1 / 3, np.nan]) / np.std([1, 2, 2], ddof=1),
    )
    np.testing.assert_allclose(values("CS_SCALE($x)", data), [0.2, 0.4, 0.4, np.nan])
    np.testing.assert_allclose(values("CS_WINSORIZE($x,0.25,0.75)", data), [1.5, 2, 2, np.nan])


def test_nulls_ties_and_future_invariance():
    data = frame((1.0, 2.0, np.inf, 4.0, 5.0))
    assert np.isnan(values("TS_MEAN($x,3)", data)[-1])
    assert np.isnan(values("TS_RANKCORR($x,$y,3)", data)[-1])
    assert np.isnan(values("IF_THEN_ELSE(GT($x,0),1,0)", data)[2])
    assert values("FILLNA($x,9)", data)[2] == 9
    assert np.isnan(values("LOG(NEG($x))")[-1])
    assert np.isnan(values("TS_CORR($x,$x,3)", frame((1.0,) * 5))[-1])
    assert values("TS_ARGMAX($x,3)", frame((1.0, 2.0, 5.0, 1.0, 5.0)))[-1] == 0
    for op in [s for s in OperatorRegistry().catalog() if s.scope == "ts"]:
        args = [
            "GT($x,0)"
            if k == "condition"
            else "$x"
            if k == "series"
            else "5"
            if k == "window"
            else "0.5"
            for k in op.args
        ]
        if op.name == "TS_WINSORIZE":
            args[-2:] = ["0.1", "0.9"]
        formula = f"{op.name}({','.join(args)})"
        if op.output == "condition":
            formula = f"IF_THEN_ELSE({formula},1,0)"
        old = values(formula)
        new = values(formula, frame((2.0, 1.0, 3.0, 5.0, 4.0, 999.0, -100.0)))
        np.testing.assert_allclose(old, new[:5], equal_nan=True)


def test_order_statistics_against_independent_reference():
    rng = np.random.default_rng(17)
    x, y = rng.integers(-3, 4, size=(2, 301)).astype(float)
    x[40] = np.nan
    actual = window_array("TS_RANKCORR", [x, y], 7)
    for i in range(6, len(x)):
        a, b = x[i - 6 : i + 1], y[i - 6 : i + 1]
        if not np.isfinite(a).all():
            assert np.isnan(actual[i])
            continue
        # Pairwise comparison ranks are independent of the production sorting algorithm.
        ra = np.array([1 + (a < v).sum() + ((a == v).sum() - 1) / 2 for v in a])
        rb = np.array([1 + (b < v).sum() + ((b == v).sum() - 1) / 2 for v in b])
        assert actual[i] == pytest.approx(np.corrcoef(ra, rb)[0, 1])


def test_catalog_is_exactly_sixty():
    assert len(OperatorRegistry().catalog()) == 60
    assert parse("# comment\n$x").op == "field"


def test_compiled_plan_dependency_is_checked():
    from dataclasses import replace

    plan = compile_factor("TS_MEAN($x,3)", {"x"})
    with pytest.raises(ValueError, match="dependency changed"):
        execute(replace(plan, dependencies=(("TS_MEAN", "obsolete"),)), frame(), {"x"})


@pytest.mark.parametrize("window", [1, 3, 12, 100])
@pytest.mark.parametrize(
    "source",
    [
        [None, 1.0, 2.0, 3.0, None, 4.0, 5.0, 6.0, 7.0, float("inf"), 8.0, 9.0, 10.0]
        + [float(i) for i in range(20)],
        [None] * 33,
        [5.0] * 33,
    ],
)
def test_linear_decay_missing_windows_match_independent_reference(window, source):
    x = np.array(source, dtype=float)
    expected = np.full(len(x), np.nan)
    for end in range(window, len(x) + 1):
        part = x[end - window : end]
        if np.isfinite(part).all():
            expected[end - 1] = sum((i + 1) * v for i, v in enumerate(part)) / sum(
                range(1, window + 1)
            )
    result = execute(f"TS_LINEAR_DECAY($x,{window})", frame(source), {"x"})["value"]
    np.testing.assert_allclose(result.to_numpy(), expected, equal_nan=True)
    assert result.null_count() == int(np.isnan(expected).sum())
    assert not result.is_nan().any()


def test_linear_decay_nested_windows_isolation_and_no_lookahead():
    data = pl.concat(
        [
            frame([None, 1.0, 3.0, 2.0, 5.0, 4.0, 6.0, 8.0, 9.0, 7.0]).with_columns(
                pl.lit(exchange).alias("exchange"),
                pl.lit(instrument).alias("instrument_id"),
                pl.lit(segment).alias("segment_id"),
                pl.col("x") * scale,
            )
            for exchange, instrument, segment, scale in [
                ("X", "A", 0, 1),
                ("Y", "A", 0, 3),
                ("X", "B", 0, 10),
                ("X", "A", 1, 100),
            ]
        ]
    ).with_columns(pl.int_range(pl.len()).alias("row_id"))
    # Distinct timestamps for re-entry into the same contract.
    data = data.with_columns(pl.col("timestamp") + pl.duration(days=pl.col("segment_id") * 10))
    formula = "TS_LINEAR_DECAY(TS_MEAN($x,3),3)"
    expected = []
    for part in data.partition_by(["exchange", "instrument_id", "segment_id"], maintain_order=True):
        x = part["x"].to_numpy()
        mean = [np.nan] * 2 + [np.mean(x[i - 2 : i + 1]) for i in range(2, len(x))]
        expected.extend(
            [np.nan] * 2
            + [(mean[i - 2] + 2 * mean[i - 1] + 3 * mean[i]) / 6 for i in range(2, len(x))]
        )
    actual = execute(formula, data.sample(fraction=1, shuffle=True, seed=42), {"x"}).sort("row_id")
    np.testing.assert_allclose(actual["value"].to_numpy(), expected, equal_nan=True)
    assert execute("TS_LINEAR_DECAY($x,12)", data, {"x"})["value"].null_count() == data.height
    for part in data.partition_by(["exchange", "instrument_id", "segment_id"], maintain_order=True):
        prefix = part.head(8)
        earlier = execute(formula, prefix, {"x"}).sort("row_id")["value"].to_numpy()
        changed = part.with_columns(
            pl.when(pl.col("row_id") > prefix["row_id"].max())
            .then(-999.0)
            .otherwise(pl.col("x"))
            .alias("x")
        )
        extended = execute(formula, changed, {"x"}).sort("row_id")["value"].head(8).to_numpy()
        np.testing.assert_allclose(earlier, extended, equal_nan=True)
