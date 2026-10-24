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
                        "# 机制: 收盘价在 64 根 15m bar 内的标准化偏离，日内到多日的均值回归",
                        f"# 字段: ${price}@15m",
                        f"TS_ZSCORE(${price}@15m, 64)",
                    ],
                )
            )
            rows.append(
                (
                    "price_rank_divergence_60m_1d",
                    [
                        "# 机制: 价格在 24 根 60m 与 10 个交易日窗口内的相对分位差异",
                        f"# 字段: ${price}@60m, ${price}@1d",
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
                        "# 机制: 收盘价在 64 根原生 bar 内的标准化偏离，捕捉均值回归",
                        f"# 字段: ${price}",
                        f"TS_ZSCORE(${price}, 64)",
                    ],
                )
            )

    if price in allowed and ("open_interest" in allowed or "amount" in allowed):
        flow = "open_interest" if "open_interest" in allowed else "amount"
        flow_label = "持仓" if flow == "open_interest" else "成交额"
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
                        f"# 机制: {flow_label}增速相对多日背景放大时的 30 分钟收益延续",
                        f"# 字段: {close}, {flow_field}",
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
                        f"# 机制: {flow_label}增速相对背景放大时的收益延续",
                        f"# 字段: ${price}, ${flow}",
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
                    "# 机制: 原生 bar 内量价共动强度，背离时反转",
                    f"# 字段: ${price}, $volume",
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
                    "# 机制: 同周期复权收盘价相对成交量加权均价的偏离",
                    f"# 字段: $adj_close{scope}, $adj_vwap{scope}",
                    f"$adj_close{scope} / $adj_vwap{scope} - 1",
                ],
            )
        )

    lines = [
        "### Canonical factor examples",
        "These are examples adapted from the shared ChaoticTrader prompt library, not measured results or fixed templates.",
        "Preserve the mechanism when adapting them, but use only fields, operators and timeframes allowed in the current context.",
    ]
    for name, expression in rows:
        lines.append(f"\n- `{name}`\n```text\n" + "\n".join(expression) + "\n```")
    return "\n".join(lines)


__all__ = ["render_factor_examples"]
