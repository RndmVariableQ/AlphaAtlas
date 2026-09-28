"""Small declarative catalog; signatures are the single source of DSL validation.

Scope/signature/lookback separation follows AlphaSeeker's OperatorSpec. We do not
inherit its partial-window policy, Numba ABI, or mutable runtime namespace.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from alpha_atlas.contracts import Expression, fingerprint

MINIMUM_WINDOWS = {
    "TS_STD": 2,
    "TS_VAR": 2,
    "TS_ZSCORE": 2,
    "TS_CORR": 2,
    "TS_COV": 2,
    "TS_RANKCORR": 2,
    "TS_SKEW": 3,
    "TS_KURT": 4,
}

BUILTIN_DESCRIPTIONS = {
    "ADD": "Elementwise x + y.",
    "SUBTRACT": "Elementwise x - y.",
    "MULTIPLY": "Elementwise x * y.",
    "DIVIDE": "Elementwise x / y. Null when abs(y) <= 1e-12.",
    "MIN": "Elementwise minimum of x and y. Both inputs must be finite.",
    "MAX": "Elementwise maximum of x and y. Both inputs must be finite.",
    "NEG": "Negate x.",
    "ABS": "Absolute value of x.",
    "SIGN": "Sign of x: -1 for negative, 0 for zero, +1 for positive.",
    "LOG": "Natural logarithm of x. Null for x <= 0.",
    "LOG1P": "Natural logarithm of 1 + x. Null for x <= -1.",
    "SQRT": "Square root of x. Null for x < 0.",
    "EXP": "Exponential exp(x). Nonfinite results become null.",
    "POWER": "Raise x to a fixed exponent p. Invalid/nonfinite results become null.",
    "SIGNED_POWER": "sign(x) * abs(x)**p for fixed p. Zero with p <= 0 yields null.",
    "CLIP": "Clamp x to the fixed lower and upper bounds, inclusive.",
    "LT": "Condition x < y.",
    "LE": "Condition x <= y.",
    "GT": "Condition x > y.",
    "GE": "Condition x >= y.",
    "EQ": "Condition x == y.",
    "NE": "Condition x != y.",
    "AND": "Logical AND of two conditions. Either unknown input yields unknown.",
    "OR": "Logical OR of two conditions. Either unknown input yields unknown.",
    "NOT": "Logical negation. An unknown condition stays unknown.",
    "IF_THEN_ELSE": "Choose x when condition is true, y when false, null when unknown.",
    "IS_FINITE": "True for finite x, false for null or nonfinite x.",
    "FILLNA": "Replace null x with y. Does not change market eligibility or target validity.",
    "DELAY": "Value of x n bars ago. Requires all n+1 observations to be finite.",
    "DELTA": "x minus its value n bars ago. Requires all n+1 observations to be finite.",
    "RETURN": "Simple return x / x[n bars ago] - 1, not log return. Requires n+1 valid bars.",
    "TS_SUM": "Sum of x over the last n bars, including the current bar.",
    "TS_PROD": "Product of x over the last n bars.",
    "TS_MEAN": "Arithmetic mean of x over the last n bars.",
    "TS_MEDIAN": "Median of x over the last n bars.",
    "TS_LINEAR_DECAY": "Weighted mean over n bars, with weights 1 (oldest) through n (newest).",
    "TS_STD": "Sample standard deviation over n bars, ddof=1.",
    "TS_VAR": "Sample variance over n bars, ddof=1.",
    "TS_ZSCORE": "(x - rolling mean) / rolling sample std over n bars. Null if std <= 1e-12.",
    "TS_SKEW": "Bias-corrected skewness over n bars. Constant windows yield null.",
    "TS_KURT": "Bias-corrected excess kurtosis over n bars (normal=0). Null for constant windows.",
    "TS_MIN": "Minimum x over the last n bars.",
    "TS_MAX": "Maximum x over the last n bars.",
    "TS_RANK": "Current x's ascending average rank within n bars, divided by n.",
    "TS_ARGMIN": "Bars since the minimum in the n-bar window. Current=0, nearest tie wins.",
    "TS_ARGMAX": "Bars since the maximum in the n-bar window. Current=0, nearest tie wins.",
    "TS_QUANTILE": "Quantile q of x over n bars, using linear interpolation.",
    "TS_WINSORIZE": "Clamp current x to its rolling lower/upper quantiles over n bars.",
    "TS_CORR": "Rolling Pearson correlation of x and y over n jointly valid bars.",
    "TS_COV": "Rolling sample covariance of x and y over n jointly valid bars, ddof=1.",
    "TS_RANKCORR": "Rolling Spearman correlation, reranking x and y inside each n-bar window.",
    "TS_COUNT": "Number of true conditions over n bars. Any unknown invalidates the window.",
    "TS_RATE": "Fraction of true conditions over n bars. Any unknown invalidates the window.",
    "TS_ANY": "Whether any condition is true over n bars. Requires a fully known window.",
    "TS_ALL": "Whether all conditions are true over n bars. Requires a fully known window.",
    "CS_RANK": "Ascending average rank / count among eligible finite values at the same timestamp.",
    "CS_ZSCORE": (
        "Cross-sectional (x - mean) / sample std among eligible finite values. "
        "Null if std <= 1e-12."
    ),
    "CS_DEMEAN": "Subtract the mean of eligible finite values at the same timestamp.",
    "CS_SCALE": "Divide x by sum(abs(x)) among eligible finite values. Null if total <= 1e-12.",
    "CS_WINSORIZE": "Clamp eligible x to same-timestamp lower/upper quantiles (linear interpolation).",
}


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    args: tuple[str, ...]
    scope: str = "elementwise"
    output: str = "series"
    defaults: tuple[float, ...] = ()
    history: str = "none"
    version: str = "1"
    kind: str = "builtin"
    parameter_names: tuple[str, ...] = ()
    description: str = ""
    history_bars: int = 0
    window_arg: str | None = None
    history_offset: int = -1


def builtin_specs() -> dict[str, OperatorSpec]:
    specs: dict[str, OperatorSpec] = {}

    def add(names, args, scope="elementwise", output="series", defaults=(), history="none"):
        for name in names.split():
            specs[name] = OperatorSpec(
                name, args, scope, output, defaults, history, description=BUILTIN_DESCRIPTIONS[name]
            )

    add("ADD SUBTRACT MULTIPLY DIVIDE MIN MAX", ("series", "series"))
    add("NEG ABS SIGN LOG LOG1P SQRT EXP", ("series",))
    add("POWER SIGNED_POWER", ("series", "float"))
    add("CLIP", ("series", "float", "float"))
    add("LT LE GT GE EQ NE", ("series", "series"), output="condition")
    add("AND OR", ("condition", "condition"), output="condition")
    add("NOT", ("condition",), output="condition")
    add("IF_THEN_ELSE", ("condition", "series", "series"))
    add("IS_FINITE", ("series",), output="condition")
    add("FILLNA", ("series", "series"))
    add("DELAY DELTA RETURN", ("series", "window"), "ts", history="lag")
    add(
        "TS_SUM TS_PROD TS_MEAN TS_MEDIAN TS_LINEAR_DECAY TS_STD TS_VAR TS_ZSCORE "
        "TS_SKEW TS_KURT TS_MIN TS_MAX TS_RANK TS_ARGMIN TS_ARGMAX",
        ("series", "window"),
        "ts",
        history="window",
    )
    add("TS_QUANTILE", ("series", "window", "float"), "ts", history="window")
    add(
        "TS_WINSORIZE",
        ("series", "window", "float", "float"),
        "ts",
        defaults=(0.01, 0.99),
        history="window",
    )
    add("TS_CORR TS_COV TS_RANKCORR", ("series", "series", "window"), "ts", history="window")
    add("TS_COUNT TS_RATE", ("condition", "window"), "ts", history="window")
    add("TS_ANY TS_ALL", ("condition", "window"), "ts", "condition", history="window")
    add("CS_RANK CS_ZSCORE CS_DEMEAN CS_SCALE", ("series",), "cs")
    add("CS_WINSORIZE", ("series", "float", "float"), "cs", defaults=(0.01, 0.99))
    return specs


ALIASES = {
    "SUB": "SUBTRACT",
    "MUL": "MULTIPLY",
    "DIV": "DIVIDE",
    "LAG": "DELAY",
    "MEAN": "TS_MEAN",
    "STD": "TS_STD",
    "ZSCORE": "TS_ZSCORE",
    "CSRANK": "CS_RANK",
    "RANK": "CS_RANK",
    "TS_PCTCHANGE": "RETURN",
    "POW": "POWER",
}


@dataclass(frozen=True)
class OperatorDefinition:
    name: str
    parameters: tuple[tuple[str, str], ...]
    body: str
    kind: Literal["composite", "group_batch"] = "composite"
    scope: str = "ts"
    history: int = 0  # additional past bars; or window_arg + history_offset
    window_arg: str | None = None
    history_offset: int = -1
    golden: str = ""
    examples: tuple[dict, ...] = ()
    description: str = ""

    @property
    def operator_id(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class OperatorFeedback:
    accepted: bool
    operator_id: str | None
    error: str | None
    tests: tuple[str, ...]
    elapsed_seconds: float
    validation: dict = field(default_factory=dict)


class OperatorRegistry:
    def __init__(self, *, runtime=None, record=None):
        self._specs = builtin_specs()
        self._definitions: dict[str, OperatorDefinition] = {}
        self._bodies: dict[str, Expression] = {}
        self.runtime = runtime
        self.record = record

    def resolve(self, name: str) -> str:
        upper = name.upper()
        return ALIASES.get(upper, upper)

    def spec(self, name: str) -> OperatorSpec:
        try:
            return self._specs[self.resolve(name)]
        except KeyError:
            raise ValueError(f"unknown operator: {name}") from None

    def definition(self, name: str) -> OperatorDefinition | None:
        return self._definitions.get(self.resolve(name))

    def body(self, name: str) -> Expression:
        return self._bodies[self.resolve(name)]

    def catalog(self) -> tuple[OperatorSpec, ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def definitions(self) -> tuple[OperatorDefinition, ...]:
        return tuple(self._definitions.values())

    def register(self, definition: OperatorDefinition) -> OperatorFeedback:
        from alpha_atlas.expressions import compile_factor, parse

        started = time.perf_counter()
        checks: tuple[str, ...] = ()
        validation = {}
        body = None
        try:
            # Detach nested example lists from the caller before assigning an immutable identity.
            copied = json.loads(json.dumps(asdict(definition), allow_nan=False))
            copied["parameters"] = tuple(tuple(p) for p in copied["parameters"])
            copied["examples"] = tuple(copied["examples"])
            definition = OperatorDefinition(**copied)
            name = definition.name
            if not name.isidentifier() or name != name.upper() or name.startswith("_"):
                raise ValueError("operator name must be uppercase and public")
            if self.resolve(name) in self._specs:
                raise ValueError("operator already exists; use a new versioned name")
            parameters = dict(definition.parameters)
            if len(parameters) != len(definition.parameters):
                raise ValueError("duplicate parameter")
            if any(not p.isidentifier() or p.startswith("_") for p in parameters):
                raise ValueError("invalid parameter name")
            if any(
                k not in {"series", "condition", "window", "float"} for k in parameters.values()
            ):
                raise ValueError("unsupported parameter type")
            if definition.kind == "composite":
                body = parse(definition.body, parameters=set(parameters))
                # Validate with representative static arguments; every actual call is rechecked.
                replacements = {
                    p: Expression("field", value=p)
                    if kind in {"series", "condition"}
                    else Expression("const", value=5 if kind == "window" else 0.5)
                    for p, kind in parameters.items()
                }
                from alpha_atlas.expressions import substitute

                compiled = compile_factor(
                    substitute(body, replacements),
                    set(parameters),
                    self,
                    max_nodes=1000,
                    max_depth=32,
                    field_types={p: k for p, k in parameters.items() if k == "condition"},
                )
                scope = definition.scope
                checks = ("signature", "dependency_expansion", "numeric_output")
                del compiled
            elif definition.kind == "group_batch":
                if definition.scope not in {"ts", "cs"}:
                    raise ValueError("group_batch scope must be ts or cs")
                if any(k == "condition" for k in parameters.values()):
                    raise ValueError("group_batch v1 inputs must be numeric arrays")
                if not any(k == "series" for k in parameters.values()):
                    raise ValueError("group_batch requires a numeric array")
                if type(definition.history) is not int or definition.history < 0:
                    raise ValueError("history must be a nonnegative integer")
                if definition.window_arg is not None:
                    if parameters.get(definition.window_arg) != "window":
                        raise ValueError("window_arg must reference a window parameter")
                    if type(definition.history_offset) is not int or definition.history_offset < -1:
                        raise ValueError("invalid history offset")
                if definition.scope == "cs" and (definition.history or definition.window_arg):
                    raise ValueError("cross-sectional kernels cannot request history")
                if self.runtime is None:
                    raise ValueError(
                        "operator_runtime_unavailable: Numba runtime is not configured"
                    )
                checks = self.runtime.validate(definition, report=validation)
                scope = definition.scope
            else:
                raise ValueError("unsupported operator kind")
            feedback = OperatorFeedback(
                True,
                definition.operator_id,
                None,
                checks,
                time.perf_counter() - started,
                validation,
            )
            # Persistence must succeed before publishing the operator.
            if self.record is not None:
                self.record(definition, feedback)
            self._specs[name] = OperatorSpec(
                name,
                tuple(parameters.values()),
                scope,
                version=definition.operator_id,
                history="custom" if definition.kind == "group_batch" else "expanded",
                kind=definition.kind,
                parameter_names=tuple(parameters),
                description=definition.description,
                history_bars=definition.history,
                window_arg=definition.window_arg,
                history_offset=definition.history_offset,
            )
            self._definitions[name] = definition
            if body is not None:
                self._bodies[name] = body
            return feedback
        except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
            feedback = OperatorFeedback(
                False,
                None,
                str(exc),
                tuple(k for k, v in validation.items() if v["status"] == "passed") or checks,
                time.perf_counter() - started,
                validation,
            )
            if self.record is not None:
                self.record(definition, feedback)
            return feedback

    def restore(self, definitions: list[dict]) -> None:
        """Revalidate frozen definitions, including code, before publishing them."""
        for item in definitions:
            item = dict(item)
            item["parameters"] = tuple(tuple(p) for p in item["parameters"])
            item["examples"] = tuple(item.get("examples", ()))
            feedback = self.register(OperatorDefinition(**item))
            if not feedback.accepted:
                raise ValueError(f"frozen operator rejected: {feedback.error}")
