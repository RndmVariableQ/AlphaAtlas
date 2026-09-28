"""Shared factor examples adapted from ChaoticTrader's prompt examples."""

from collections.abc import Sequence


def render_factor_examples(fields: Sequence[str], frequency: str | None) -> str:
    """Render valid examples for the current Atlas fields and native frequency."""
    allowed = set(fields)
    rows: list[tuple[str, list[str]]] = []
    is_five_minute = frequency == "5m"

    if "close" in allowed:
        if is_five_minute:
            rows.append(
                (
                    "close_zscore_15m_64",
                    [
                        "# 机制: 收盘价在 64 根 15m bar 内的标准化偏离，日内到多日的均值回归",
                        "# 字段: $close@15m",
                        "TS_ZSCORE($close@15m, 64)",
                    ],
                )
            )
            rows.append(
                (
                    "trend_divergence_60m_1d",
                    [
                        "# 机制: 24 根 60m 与 10 个交易日的单调趋势分歧，趋势内回调",
                        "# 字段: $close@60m, $close@1d",
                        "trend_60m = TS_RANK($close@60m, 24)",
                        "trend_1d = TS_RANK($close@1d, 10)",
                        "SUBTRACT(trend_60m, trend_1d)",
                    ],
                )
            )
        else:
            rows.append(
                (
                    "close_zscore_native_64",
                    [
                        "# 机制: 收盘价在 64 根原生 bar 内的标准化偏离，捕捉均值回归",
                        "# 字段: $close",
                        "TS_ZSCORE($close, 64)",
                    ],
                )
            )

    if "close" in allowed and ("open_interest" in allowed or "amount" in allowed):
        flow = "open_interest" if "open_interest" in allowed else "amount"
        flow_label = "持仓" if flow == "open_interest" else "成交额"
        if is_five_minute:
            close = "$close@30m"
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
                        f"flow_speed = TS_ZSCORE(RETURN(LOG1P({flow_field}), 1), 240)",
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
                        f"# 字段: $close, ${flow}",
                        "ret = RETURN($close, 1)",
                        f"flow_speed = TS_ZSCORE(RETURN(LOG1P(${flow}), 1), 60)",
                        "MULTIPLY(ret, MAX(flow_speed, 0))",
                    ],
                )
            )

    if "close" in allowed and "volume" in allowed:
        rows.append(
            (
                "price_volume_corr_5m_96" if is_five_minute else "price_volume_corr_native_96",
                [
                    "# 机制: 原生 bar 内量价共动强度，背离时反转",
                    "# 字段: $close, $volume",
                    "TS_CORR($close, $volume, 96)",
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
