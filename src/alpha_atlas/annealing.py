"""Numerical helpers for budget-cooled Metropolis search."""

from __future__ import annotations

import math


def finite(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def temperature(progress: float, initial: float, final: float) -> float:
    if not finite(progress) or not 0 <= progress <= 1:
        raise ValueError("budget progress must be between zero and one")
    if not all(finite(value) for value in (initial, final)) or not 0 < final <= initial:
        raise ValueError("temperatures must be finite and 0 < final <= initial")
    value = math.exp((1 - progress) * math.log(initial) + progress * math.log(final))
    return min(initial, max(final, value))


def acceptance_probability(current: float, candidate: float, heat: float) -> float:
    if not all(finite(value) for value in (current, candidate, heat)) or heat <= 0:
        raise ValueError("scores and temperature must be finite; temperature must be positive")
    return math.exp(min(0.0, (candidate - current) / heat))
