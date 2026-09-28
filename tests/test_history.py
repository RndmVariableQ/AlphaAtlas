import json
from dataclasses import replace
from datetime import date

import pytest
from test_evaluation_library import market_frame
from test_operator_validation import SimulatedRuntime, identity
from test_pipeline import project as project

from alpha_atlas.contracts import Candidate
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.history import one_year_before, warmup_period
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry
from alpha_atlas.runner import run
from alpha_atlas.runner import test_frozen as evaluate_frozen


def test_positive_windows_without_upper_or_cumulative_history_cap():
    assert compile_factor("TS_MEAN($x,1000000000)", {"x"}).lookback == 999999999
    assert compile_factor("DELAY(TS_MEAN($x,12001),9000)", {"x"}).lookback == 21000
    for value in (0, -1, 2.5):
        with pytest.raises(ValueError, match="window must be an integer"):
            compile_factor(f"TS_MEAN($x,{value})", {"x"})
    with pytest.raises(ValueError, match="window >= 2"):
        compile_factor("TS_STD($x,1)", {"x"})
    registry = OperatorRegistry()
    assert registry.register(
        OperatorDefinition("SMOOTH", (("x", "series"),), "TS_MEAN(x,12001)")
    ).accepted
    assert compile_factor("SMOOTH(SMOOTH($x))", {"x"}, registry).lookback == 24000


@pytest.mark.parametrize(
    "expression",
    [
        "TS_LINEAR_DECAY($x,1000000000)",
        "TS_PROD($x,1000000000)",
        "TS_MEAN($x,1000000000)",
        "IF_THEN_ELSE(TS_ANY($x>0,1000000000),$x,0)",
        "DELAY($x,1000000000)",
    ],
)
def test_windows_larger_than_available_data_return_null(expression):
    frame = market_frame()
    result = execute(expression, frame, {"x"})
    assert result.height == frame.height and result["value"].null_count() == frame.height


def test_group_batch_windows_and_declared_history_have_no_old_cap():
    runtime = SimulatedRuntime()
    registry = OperatorRegistry(runtime=runtime)
    candidate = replace(
        identity(),
        parameters=(("x", "series"), ("window", "window")),
        window_arg="window",
        body="def kernel(x, window):\n    return x.copy()",
        golden="def golden(x, window):\n    return x.copy()",
        examples=({"inputs": [[1.0, 2.0]], "params": [2], "expected": [1.0, 2.0]},),
    )
    assert registry.register(candidate).accepted
    assert compile_factor("DELAY(IDENTITY_TEST($x,12001),9000)", {"x"}, registry).lookback == 21000
    result = execute("IDENTITY_TEST($x,12001)", market_frame(), {"x"}, registry)
    assert result["value"].null_count() == result.height


def test_one_year_warmup_dates_are_not_a_window_budget():
    assert one_year_before(date(2020, 2, 29)) == date(2019, 2, 28)
    assert warmup_period(date(2019, 1, 1)) == {"start": "2018-01-01", "end": "2018-12-31"}


def test_run_keeps_warmup_dates_but_accepts_long_and_nondefault_windows(project):
    class Method:
        def run(self, session):
            for formula in ("TS_MEAN($volume,7)", "TS_MEAN(TS_MEAN($volume,20),20)"):
                result = session.evaluate(Candidate(formula))
                assert result.report.status == "success", result.report.failure_reason
            result = session.evaluate(Candidate("TS_MEAN($volume,1000000000)"))
            assert result.report.status == "undefined_train_metric"

    path = run(project, "ashare", "fold2", "custom_window", 42, attempts=3, method_impl=Method())
    spec = json.loads((path / "run.json").read_text(encoding="utf-8"))
    assert spec["warmup"] == {"start": "2018-01-01", "end": "2018-12-31"}
    assert "max_history_bars" not in spec["runtime"]["limits"]
    assert evaluate_frozen(project, path)["run_id"] == spec["run_id"]
