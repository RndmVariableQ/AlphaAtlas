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
