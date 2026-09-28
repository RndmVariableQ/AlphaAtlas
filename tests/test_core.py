from datetime import date, datetime, timedelta

import polars as pl
import pytest

from alpha_atlas.atlas import FactorAtlas
from alpha_atlas.contracts import (
    Candidate,
    DateRange,
    Expression,
    Fold,
    Region,
    SearchContext,
    TargetDefinition,
)
from alpha_atlas.evaluation import build_targets, split_panel
from alpha_atlas.expressions import execute, validate
from alpha_atlas.methods import make_method


def panel():
    rows = []
    for instrument, base in [("A", 10), ("B", 1000)]:
        for index in range(8):
            rows.append(
                {
                    "row_id": len(rows),
                    "exchange": "TEST",
                    "instrument_id": instrument,
                    "timestamp": datetime(2020, 1, 1) + timedelta(days=index),
                    "trading_day": date(2020, 1, 1) + timedelta(days=index),
                    "eligible": True,
                    "close": base + index,
                }
            )
    return pl.DataFrame(rows)


def test_contract_boundaries_and_no_future_features():
    frame = panel()
    expr = Expression("mean", (Expression("field", value="close"),), 3)
    values = execute(expr, frame, {"close"})["value"].to_list()
    assert values[:3] == [None, None, 11.0]
    assert values[8:11] == [None, None, 1001.0]
    with pytest.raises(ValueError):
        validate(Expression("field", value="label_return"), {"close"})
    with pytest.raises(ValueError):
        validate(Expression("lag", (Expression("field", value="close"),), -1), {"close"})


def test_target_purge_and_contract_boundary():
    frame = panel()
    targets = build_targets(frame, "close", 2)
    assert targets["target"][[6, 7, 14, 15]].null_count() == 4
    selected = split_panel(
        frame.join(targets, on="row_id"), DateRange(date(2020, 1, 1), date(2020, 1, 5))
    )
    assert selected.height == 6
    assert selected["target_end"].max() == date(2020, 1, 5)


def test_expression_identity_not_semantic_identity():
    expr = Expression("return", (Expression("field", value="close"),), 5)
    assert Expression.from_dict(expr.to_dict()).expression_id == expr.expression_id
    assert (
        Candidate(expr, ("a",)).expression.expression_id
        == Candidate(expr, ("b",)).expression.expression_id
    )


def test_empty_hypothesis_region_and_version():
    atlas = FactorAtlas()
    region = Region("new", ("mechanism",), "untested interaction")
    atlas.register_hypothesis(region)
    assert atlas.statistics["new"]["attempts"] == 0
    with pytest.raises(ValueError):
        atlas.register_hypothesis(Region("new", ("mechanism",), "changed"))


@pytest.mark.parametrize("name", ["random", "gp", "mcts", "atlas"])
def test_methods_are_deterministic_and_use_common_grammar(name):
    from alpha_atlas.contracts import TrialFeedback

    context = SearchContext("futures", "5m", TargetDefinition("close", 12), "ic", 10)
    a, b = make_method(name, 42), make_method(name, 42)
    for _ in range(8):
        ca, cb = a.ask(context)[0], b.ask(context)[0]
        assert ca == cb
        validate(ca.expression, {"close", "volume"})
        feedback = TrialFeedback(ca, None, False, "test")
        a.tell([feedback])
        b.tell([feedback])


def test_invalid_fold():
    with pytest.raises(ValueError):
        Fold(
            "bad",
            DateRange(date(2020, 1, 1), date(2020, 2, 1)),
            DateRange(date(2020, 1, 1), date(2020, 3, 1)),
            DateRange(date(2021, 1, 1), date(2021, 3, 1)),
        )
