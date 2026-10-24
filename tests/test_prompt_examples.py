import re

import pytest

from alpha_atlas.expressions import compile_factor
from alpha_atlas.methods.prompt_examples import render_factor_examples


def test_futures_examples_share_coarse_timeframe_variants():
    prompt = render_factor_examples(("close", "volume", "amount"), "5m")

    assert "close_zscore_15m_64" in prompt
    assert "$close@15m" in prompt
    assert "$close@30m" in prompt
    assert "$close@60m" in prompt
    assert "$close@1d" in prompt
    assert "amount_velocity_gated_return" in prompt
    assert "@1h" not in prompt
    assert "$open_interest" not in prompt


def test_non_intraday_examples_do_not_advertise_unavailable_scopes():
    prompt = render_factor_examples(("close", "volume"), "1d")

    assert "$close@" not in prompt
    assert "close_zscore_native_64" in prompt
    assert "price_volume_corr_native_96" in prompt


@pytest.mark.parametrize("frequency", ["5m", "1d"])
@pytest.mark.parametrize(
    "fields",
    [
        ("adj_close", "adj_vwap", "volume", "amount"),
        ("adj_close", "close", "volume", "open_interest"),
        ("adj_close",),
        ("adj_close", "adj_vwap"),
    ],
)
def test_adjusted_examples_compile_using_only_available_inputs(frequency, fields):
    prompt = render_factor_examples(fields, frequency)
    assert "$close" not in prompt
    assert "adj_close_zscore" in prompt
    if frequency == "5m" and "adj_vwap" in fields:
        assert "$adj_close@30m / $adj_vwap@30m - 1" in prompt
    if frequency == "1d":
        assert "@" not in prompt
    formulas = re.findall(r"```text\n(.*?)\n```", prompt, re.DOTALL)
    assert formulas
    for formula in formulas:
        compiled = compile_factor(formula, set(fields))
        assert set(compiled.fields) <= set(fields)
