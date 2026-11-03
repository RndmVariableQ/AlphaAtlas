"""Shared factor examples adapted from ChaoticTrader's prompt examples."""

from collections.abc import Sequence


def render_factor_examples(fields: Sequence[str], frequency: str | None) -> str:
    """Render valid examples for the current Atlas fields and native frequency."""
    allowed = set(fields)
    rows: list[tuple[str, list[str]]] = []
    is_five_minute = frequency == "5m"
    price = "adj_close" if "adj_close" in allowed else "close"

    if price in allowed:
        if is_five_minute:
            rows.append(
                (
                    f"{price}_zscore_15m_64",
                    [
                        "# Mechanism: standardized close deviation over 64 15m bars, intraday-to-multiday "
                        "mean reversion",
                        f"# Fields: ${price}@15m",
                        f"TS_ZSCORE(${price}@15m, 64)",
                    ],
                )
            )
            rows.append(
                (
                    "price_rank_divergence_60m_1d",
                    [
                        "# Mechanism: relative percentile gap of price between 24 60m bars and 10 trading days",
                        f"# Fields: ${price}@60m, ${price}@1d",
                        f"rank_60m = TS_RANK(${price}@60m, 24)",
                        f"rank_1d = TS_RANK(${price}@1d, 10)",
                        "SUBTRACT(rank_60m, rank_1d)",
                    ],
                )
            )
        else:
            rows.append(
                (
                    f"{price}_zscore_native_64",
                    [
                        "# Mechanism: standardized close deviation over 64 native bars, capturing mean reversion",
                        f"# Fields: ${price}",
                        f"TS_ZSCORE(${price}, 64)",
                    ],
                )
            )

    if price in allowed and ("open_interest" in allowed or "amount" in allowed):
        flow = "open_interest" if "open_interest" in allowed else "amount"
        flow_label = "open interest" if flow == "open_interest" else "turnover"
        if is_five_minute:
            close = f"${price}@30m"
            flow_field = f"${flow}@30m"
            name = (
                "oi_velocity_gated_return"
                if flow == "open_interest"
                else "amount_velocity_gated_return"
            )
            rows.append(
                (
                    name,
                    [
                        f"# Mechanism: 30-minute return continuation when {flow_label} growth expands versus "
                        "its multi-day background",
                        f"# Fields: {close}, {flow_field}",
                        f"ret_30m = RETURN({close}, 1)",
                        f"flow_speed = TS_ZSCORE(RETURN({flow_field}, 1), 240)",
                        "MULTIPLY(ret_30m, MAX(flow_speed, 0))",
                    ],
                )
            )
        else:
            rows.append(
                (
                    f"{flow}_velocity_gated_return",
                    [
                        f"# Mechanism: return continuation when {flow_label} growth expands versus its "
                        "background",
                        f"# Fields: ${price}, ${flow}",
                        f"ret = RETURN(${price}, 1)",
                        f"flow_speed = TS_ZSCORE(RETURN(${flow}, 1), 60)",
                        "MULTIPLY(ret, MAX(flow_speed, 0))",
                    ],
                )
            )

    if price in allowed and "volume" in allowed:
        rows.append(
            (
                "price_volume_corr_5m_96" if is_five_minute else "price_volume_corr_native_96",
                [
                    "# Mechanism: price-volume co-movement strength within native bars, reversal on divergence",
                    f"# Fields: ${price}, $volume",
                    f"TS_CORR(${price}, $volume, 96)",
                ],
            )
        )

    if {"adj_close", "adj_vwap"} <= allowed:
        scope = "@30m" if is_five_minute else ""
        rows.append(
            (
                "adjusted_vwap_deviation_30m"
                if is_five_minute
                else "adjusted_vwap_deviation_native",
                [
                    "# Mechanism: deviation of adjusted close from same-period volume-weighted average price",
                    f"# Fields: $adj_close{scope}, $adj_vwap{scope}",
                    f"$adj_close{scope} / $adj_vwap{scope} - 1",
                ],
            )
        )

    lines = [
        "### Canonical factor examples",
        "DSL examples; no evaluation results.",
    ]
    for name, expression in rows:
        lines.append(f"\n- `{name}`\n```text\n" + "\n".join(expression) + "\n```")
    return "\n".join(lines)


__all__ = ["render_factor_examples"]
