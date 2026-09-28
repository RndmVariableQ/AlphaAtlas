# SPDX-License-Identifier: GPL-3.0-only
# Adapted from QuantSkills skill-futures-cta-alpha (2026 contributors).
# See README.md and LICENSE for attribution and adaptation boundaries.
"""Optional CTA reference formulas expressed entirely in the existing Atlas DSL."""

from __future__ import annotations

from dataclasses import dataclass

from alpha_atlas.contracts import Candidate
from alpha_atlas.expressions import compile_factor

SOURCE = "https://github.com/quantskills/skill-futures-cta-alpha"
SOURCE_COMMIT = "a9c1feddaec41984d58e8a15ae5678743c769c2a"
VERSION = "futures_cta_dsl_v2"


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    source_name: str
    family: str
    expression: str
    fields: tuple[str, ...]
    note: str = ""

    def candidate(self) -> Candidate:
        return Candidate(
            self.expression,
            name=self.name,
            hypothesis=f"CTA reference / {self.family}. {self.note}".strip(),
        )


@dataclass(frozen=True)
class ReferenceLibrary:
    frequency: str
    bars_per_day: int
    factors: tuple[FactorDefinition, ...]
    name: str = "futures_cta"
    version: str = VERSION
    source: str = SOURCE
    source_commit: str = SOURCE_COMMIT

    def inspect(self, fields) -> tuple[dict, ...]:
        """Definitions and availability only: no values, labels, dates, or run identity."""
        allowed = set(fields)
        return tuple(
            {
                "name": f.name,
                "source_name": f.source_name,
                "family": f.family,
                "expression": f.expression,
                "fields": f.fields,
                "missing_fields": tuple(sorted(set(f.fields) - allowed)),
                "status": "missing_fields" if set(f.fields) - allowed else "available",
                "note": f.note,
            }
            for f in self.factors
        )

    def candidates(self, fields) -> tuple[Candidate, ...]:
        """Eligible definitions, not admitted members; inspect() explains excluded inputs."""
        allowed = set(fields)
        return tuple(f.candidate() for f in self.factors if set(f.fields) <= allowed)


def load(*, frequency: str, bars_per_day: int | None = None) -> ReferenceLibrary:
    """Expand nominal day windows to native bars; 5m requires an explicit experiment scale.

    This is a formula adaptation, not daily resampling or exact session/calendar alignment.
    All fields, operators, factor IDs and execution rules remain the existing platform's.
    """
    if frequency == "1d":
        if bars_per_day is not None and (type(bars_per_day) is not int or bars_per_day != 1):
            raise ValueError("1d requires bars_per_day=1")
        scale = 1
    elif frequency == "5m":
        if type(bars_per_day) is not int or bars_per_day < 1:
            raise ValueError("5m requires an explicit positive integer bars_per_day")
        scale = bars_per_day
    else:
        raise ValueError(f"unsupported CTA frequency: {frequency}")

    def w(days):
        return days * scale

    # Native one-bar log returns, with a nominal annualization for the chosen bar scale.
    hv = f"TS_STD(LOG($close / DELAY($close, 1)), {w(20)}) * SQRT({252 * scale})"
    carry = "($close / $close_p1 - 1) * 365 / ($days_to_maturity_p1 - $days_to_maturity)"
    mom_skip = f"DELAY($close, {w(21)}) / DELAY($close, {w(252)}) - 1"
    mom63 = f"RETURN($close, {w(63)})"
    definitions = (
        ("tsmom_252", "momentum", f"RETURN($close, {w(252)})", ""),
        ("tsmom_252_21", "momentum", mom_skip, ""),
        ("tsmom_63", "momentum", mom63, ""),
        (
            "breakout_55",
            "momentum",
            f"hh = TS_MAX($high, {w(55)})\nll = TS_MIN($low, {w(55)})\n"
            "CLIP(($close - (hh + ll) / 2) / ((hh - ll) / 2), -1, 1)",
            "",
        ),
        (
            "sma_xover_20_100",
            "momentum",
            f"hv = {hv}\n(TS_MEAN($close, {w(20)}) - TS_MEAN($close, {w(100)})) / (hv * $close)",
            "SMA adaptation of upstream EMA crossover; renamed, not an EMA reproduction.",
        ),
        (
            "xsmom_252_21",
            "cross_sectional_momentum",
            f"CS_ZSCORE({mom_skip})",
            "Upstream code uses z-score, not rank; Atlas uses the eligible timestamp cross-section.",
        ),
        (
            "xsmom_63",
            "cross_sectional_momentum",
            f"CS_ZSCORE({mom63})",
            "Upstream code uses z-score, not rank; Atlas uses the eligible timestamp cross-section.",
        ),
        ("carry_ann", "carry", carry, "Maturity difference remains in calendar days."),
        (
            "roll_return_63",
            "carry",
            f"TS_SUM($roll_return, {w(63)})",
            "Requires an adapter-supplied per-native-bar roll-return feature; never inferred here.",
        ),
        ("basis_mom_20", "carry", f"DELTA($basis, {w(20)})", ""),
        (
            "vol_scaled_carry",
            "carry",
            f"carry = {carry}\nhv = {hv}\ncarry / hv",
            "Nominal annualized native-bar volatility; maturity difference stays in calendar days.",
        ),
        (
            "ts_slope",
            "term_structure",
            "LOG($close_p1 / $close) / ($days_to_maturity_p1 - $days_to_maturity)",
            "Maturity difference remains in calendar days.",
        ),
        ("ts_curvature", "term_structure", "$close_p2 - 2 * $close_p1 + $close", ""),
        (
            "oi_price_confirm_20",
            "positioning",
            f"SIGN(DELTA($close, {w(20)})) * LOG($open_interest / DELAY($open_interest, {w(20)}))",
            "Upstream oi maps explicitly to Atlas/RiceQuant open_interest.",
        ),
        (
            "broker_net_chg_5",
            "positioning",
            f"DELTA($broker_net, {w(5)}) / $open_interest",
            "Upstream oi maps explicitly to Atlas/RiceQuant open_interest.",
        ),
        ("ls_ratio_z", "positioning", f"TS_ZSCORE($ls_ratio, {w(60)})", ""),
        ("virtual_ratio_chg", "positioning", f"DELTA($virtual_ratio, {w(20)})", ""),
        (
            "inventory_mom_20",
            "inventory_spot",
            f"LOG($inventory / DELAY($inventory, {w(20)}))",
            "",
        ),
        (
            "receipt_mom_20",
            "inventory_spot",
            f"LOG($warehouse_receipt / DELAY($warehouse_receipt, {w(20)}))",
            "",
        ),
        ("spot_profit_z", "inventory_spot", f"TS_ZSCORE($spot_profit, {w(60)})", ""),
        ("lowvol", "volatility_reversal", f"hv = {hv}\n-hv", ""),
        ("st_reversal_5", "volatility_reversal", f"-RETURN($close, {w(5)})", ""),
    )
    # Derive required fields using the existing compiler, without introducing a second parser.
    fields = {
        "close",
        "high",
        "low",
        "open_interest",
        "close_p1",
        "close_p2",
        "days_to_maturity",
        "days_to_maturity_p1",
        "roll_return",
        "basis",
        "broker_net",
        "ls_ratio",
        "virtual_ratio",
        "inventory",
        "warehouse_receipt",
        "spot_profit",
    }
    factors = tuple(
        FactorDefinition(
            name=name,
            source_name="ema_xover_20_100" if name == "sma_xover_20_100" else name,
            family=family,
            expression=expression,
            fields=compile_factor(expression, fields).fields,
            note=note,
        )
        for name, family, expression, note in definitions
    )
    return ReferenceLibrary(frequency, scale, factors)
