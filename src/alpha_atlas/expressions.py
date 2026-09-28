"""Text/AST compiler and vectorized executor. Candidate Python never executes here."""

from __future__ import annotations

import ast
import math
import re

import numpy as np
import polars as pl

from alpha_atlas.contracts import CompiledFactor, Expression, fingerprint
from alpha_atlas.operators.registry import MINIMUM_WINDOWS, OperatorRegistry

GROUP = ["exchange", "instrument_id"]
DSL_VERSION = "atlas-dsl-v2"
STRUCTURAL = {
    "row_id",
    "timestamp",
    "trading_day",
    "exchange",
    "instrument_id",
    "product",
    "eligible",
    "valid_bar",
    "segment_id",
    "snapshot_id",
    "bar_interval_minutes",
}
STRUCTURAL.update(
    f"{name}_p{leg}"
    for leg in (1, 2)
    for name in ("exchange", "instrument_id", "segment_id", "valid_bar")
)
_BINARY = {ast.Add: "ADD", ast.Sub: "SUBTRACT", ast.Mult: "MULTIPLY", ast.Div: "DIVIDE"}
_COMPARE = {ast.Lt: "LT", ast.LtE: "LE", ast.Gt: "GT", ast.GtE: "GE", ast.Eq: "EQ", ast.NotEq: "NE"}


def parse(source: str, *, parameters: set[str] | None = None) -> Expression:
    if not isinstance(source, str) or len(source) > 65536:
        raise ValueError("DSL source must be text of at most 65536 characters")
    references = {}

    def reference(match):
        name = f"__field_ref_{len(references)}"
        references[name] = match[1]
        return name

    translated = re.sub(r"\$([A-Za-z][A-Za-z0-9_]*(?:@[A-Za-z0-9_]+)?)", reference, source)
    variables: dict[str, Expression] = {}
    parameters = parameters or set()
    try:
        tree = ast.parse(translated)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError(f"invalid DSL syntax: {exc}") from None
    if sum(1 for _ in ast.walk(tree)) > 4096:
        raise ValueError("DSL source exceeds syntax budget")

    def convert(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return Expression("const", value=node.value)
        if isinstance(node, ast.Name):
            if node.id in references:
                return Expression("field", value=references[node.id])
            if node.id in variables:
                return variables[node.id]
            if node.id in parameters:
                return Expression("param", value=node.id)
            raise ValueError(f"unknown variable: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return Expression(_BINARY[type(node.op)], (convert(node.left), convert(node.right)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = convert(node.operand)
            if value.op == "const":
                return Expression(
                    "const", value=-value.value if isinstance(node.op, ast.USub) else value.value
                )
            return Expression("NEG", (value,)) if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
            return Expression(
                _COMPARE[type(node.ops[0])], (convert(node.left), convert(node.comparators[0]))
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id.startswith("_"):
                raise ValueError("private calls are forbidden")
            return Expression(node.func.id, tuple(convert(a) for a in node.args))
        raise ValueError(f"unsupported DSL syntax: {type(node).__name__}")

    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        raise ValueError("DSL must end with an output expression")
    try:
        for statement in tree.body[:-1]:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                raise ValueError("only single variable assignments precede the output")
            target = statement.targets[0]
            if not isinstance(target, ast.Name) or target.id.startswith("_"):
                raise ValueError("invalid assignment target")
            if target.id in variables or target.id in parameters:
                raise ValueError(f"variable already defined: {target.id}")
            variables[target.id] = convert(statement.value)
        return convert(tree.body[-1].value)
    except RecursionError:
        raise ValueError("DSL nesting is too deep") from None


def substitute(node: Expression, parameters: dict[str, Expression]) -> Expression:
    if node.op == "param":
        if node.value not in parameters:
            raise ValueError(f"unknown parameter {node.value}")
        return parameters[node.value]
    return Expression(node.op, tuple(substitute(a, parameters) for a in node.args), node.value)


def compile_factor(
    expression: Expression | str,
    fields: set[str],
    registry: OperatorRegistry | None = None,
    *,
    max_nodes: int = 100,
    max_depth: int = 20,
    field_types: dict[str, str] | None = None,
) -> CompiledFactor:
    registry = registry or OperatorRegistry()
    expression = parse(expression) if isinstance(expression, str) else expression
    used: set[str] = set()
    dependencies: dict[str, str] = {}
    count = 0
    field_types = field_types or {}

    def visit(node: Expression, expansion: tuple[str, ...] = ()):
        nonlocal count
        count += 1
        if count > max_nodes * 8 + 128 or len(expansion) > 32:
            raise ValueError("expression expansion exceeds budget")
        if not isinstance(node, Expression):
            raise ValueError("invalid AST node")
        if node.op == "field":
            from alpha_atlas.timeframes import field_reference

            name, _interval = field_reference(str(node.value))
            if (
                node.args
                or name not in fields
                or name in STRUCTURAL
                or name.startswith(("label", "target", "__"))
            ):
                raise ValueError(f"invalid feature field: {name}")
            used.add(name)
            return node, field_types.get(name, "series"), 0, 1, 0
        if node.op == "const":
            if node.args or type(node.value) not in {int, float} or not math.isfinite(node.value):
                raise ValueError("constant must be finite numeric")
            return Expression("const", value=float(node.value)), "scalar", 0, 1, 0
        spec = registry.spec(node.op)
        args = node.args
        if node.value is not None:
            if "window" not in spec.args or len(args) != len(spec.args) - 1:
                raise ValueError(f"unexpected value on {spec.name}")
            args = (*args, Expression("const", value=node.value))
        required = len(spec.args) - len(spec.defaults)
        if not required <= len(args) <= len(spec.args):
            raise ValueError(f"{spec.name} requires {required}..{len(spec.args)} arguments")
        if len(args) < len(spec.args):
            args += tuple(
                Expression("const", value=d) for d in spec.defaults[len(args) - required :]
            )
        results = [visit(a, expansion) for a in args]
        normalized = tuple(result[0] for result in results)
        for kind, (arg, actual, *_rest) in zip(spec.args, results, strict=True):
            if kind in {"window", "float"}:
                if arg.op != "const":
                    raise ValueError(f"{spec.name}: {kind} must be static")
                if kind == "window":
                    if arg.value != int(arg.value) or arg.value < 1:
                        raise ValueError("window must be an integer >= 1")
            elif kind == "condition" and actual != "condition":
                raise ValueError(f"{spec.name}: condition input required")
            elif kind == "series" and actual not in {"scalar", "series"}:
                raise ValueError(f"{spec.name}: numeric input required")
        static = [a.value for a, k in zip(normalized, spec.args, strict=True) if k == "float"]
        if spec.name in {"CLIP", "CS_WINSORIZE", "TS_WINSORIZE"}:
            low, high = static
            if low > high or ("WINSORIZE" in spec.name and not 0 <= low < high <= 1):
                raise ValueError(f"invalid {spec.name} bounds")
        if spec.name == "TS_QUANTILE" and not 0 <= static[0] <= 1:
            raise ValueError("quantile must be in [0,1]")
        n = int(normalized[spec.args.index("window")].value) if "window" in spec.args else 0
        minimum = MINIMUM_WINDOWS.get(spec.name, 1)
        if n and n < minimum:
            raise ValueError(f"{spec.name} requires window >= {minimum}")
        definition = registry.definition(spec.name)
        if definition and definition.kind == "composite":
            if spec.name in expansion:
                raise ValueError("operator dependency cycle")
            replacements = dict(zip((p for p, _ in definition.parameters), normalized, strict=True))
            return visit(
                substitute(registry.body(spec.name), replacements), (*expansion, spec.name)
            )
        if spec.name == "TS_WINSORIZE":
            x, w, lo, hi = normalized
            low = Expression("TS_QUANTILE", (x, w, lo))
            high = Expression("TS_QUANTILE", (x, w, hi))
            return visit(Expression("MIN", (Expression("MAX", (x, low)), high)), expansion)
        history = n if spec.history == "lag" else max(n - 1, 0)
        if definition and definition.kind == "group_batch":
            history = definition.history
            if definition.window_arg:
                index = [p for p, _ in definition.parameters].index(definition.window_arg)
                history = int(normalized[index].value) + definition.history_offset
            if history < 0:
                raise ValueError("custom history must be nonnegative")
            if registry.runtime:
                dependencies["operator_runtime"] = registry.runtime.fingerprint
        dependencies[spec.name] = spec.version
        return (
            Expression(spec.name, normalized),
            spec.output,
            max((r[2] for r in results), default=0) + history,
            1 + sum(r[3] for r in results),
            1 + max((r[4] for r in results), default=0),
        )

    try:
        normalized, kind, lookback, nodes, depth = visit(expression)
    except RecursionError:
        raise ValueError("AST exceeds nesting budget") from None
    if kind == "condition":
        raise ValueError("factor output must be numeric")
    if nodes > max_nodes or depth > max_depth:
        raise ValueError(f"expression exceeds budget: nodes={nodes}, depth={depth}")
    names: set[str] = set()
    used.clear()

    def collect(node):
        if node.op == "field":
            used.add(str(node.value).split("@")[0])
        if node.op not in {"field", "const"}:
            names.add(node.op)
        for a in node.args:
            collect(a)

    collect(normalized)
    deps = tuple(
        sorted((k, v) for k, v in dependencies.items() if k in names or k == "operator_runtime")
    )
    identity = fingerprint((DSL_VERSION, normalized.to_dict(), deps))
    return CompiledFactor(identity, normalized, tuple(sorted(used)), deps, lookback, nodes, depth)


def validate(expr: Expression | str, fields: set[str], max_nodes: int = 100) -> None:
    compile_factor(expr, fields, max_nodes=max_nodes)


def execute(
    expr: Expression | str | CompiledFactor,
    frame: pl.DataFrame,
    fields: set[str],
    registry: OperatorRegistry | None = None,
    *,
    _field_legs: dict[str, set[str]] | None = None,
) -> pl.DataFrame:
    from alpha_atlas.operators.builtin import build_expression, window_array

    registry = registry or OperatorRegistry()
    compiled = expr if isinstance(expr, CompiledFactor) else compile_factor(expr, fields, registry)
    if isinstance(expr, CompiledFactor):
        if not set(compiled.fields) <= fields:
            raise ValueError("compiled fields are unavailable in this context")
        for name, version in compiled.dependencies:
            current = (
                registry.runtime.fingerprint
                if name == "operator_runtime" and registry.runtime
                else (registry.spec(name).version if name != "operator_runtime" else None)
            )
            if current != version:
                raise ValueError(f"compiled dependency changed: {name}")
    from alpha_atlas.timeframes import execute_timeframes, frequency_scopes

    scopes = frequency_scopes(compiled.expression)
    if any(scope not in {None, "base"} for scope in scopes.values()):
        return execute_timeframes(compiled, frame, fields, registry, scopes)
    field_legs = _field_legs or {
        field: {f"p{i}" for i in (1, 2) if field.endswith(f"_p{i}")} for field in compiled.fields
    }
    group = GROUP + (["segment_id"] if "segment_id" in frame.columns else [])
    far_segments = {
        f"p{leg}": f"segment_id_p{leg}"
        for leg in (1, 2)
        if any(f"p{leg}" in legs for legs in field_legs.values())
    }
    if missing := set(far_segments.values()) - set(frame.columns):
        raise ValueError(f"far-contract fields require continuity metadata: {sorted(missing)}")
    work = frame.lazy().sort(GROUP + ["timestamp"])
    memo: dict[Expression, str] = {}
    legs: dict[Expression, set[str]] = {}

    def materialize(node):
        nonlocal work
        if node in memo:
            return memo[node]
        spec = registry.spec(node.op) if node.op not in {"field", "const"} else None
        columns, params = [], []
        for i, child in enumerate(node.args):
            if spec.args[i] in {"window", "float"}:
                params.append(child.value)
            else:
                columns.append(materialize(child))
        # Each rolling subtree only resets for the legs it actually reads. Main-contract
        # windows and targets retain their existing boundaries when an auxiliary leg changes.
        used_legs = set().union(*(legs.get(child, set()) for child in node.args))
        if node.op == "field":
            used_legs.update(field_legs.get(str(node.value), set()))
        legs[node] = used_legs
        node_group = group + [far_segments[k] for k in sorted(used_legs)]
        name = f"__atlas_expr_{len(memo)}"
        if node.op == "field":
            value = pl.col(str(node.value)).cast(pl.Float64)
        elif node.op == "const":
            value = pl.lit(node.value, dtype=pl.Float64)
        elif spec.kind == "builtin" and "window" in spec.args and int(params[0]) > frame.height:
            # No group can fill this window. Avoid allocating window-sized temporary arrays.
            value = pl.lit(None, dtype=pl.Boolean if spec.output == "condition" else pl.Float64)
        elif (definition := registry.definition(node.op)) and definition.kind == "group_batch":
            if registry.runtime is None:
                raise ValueError("operator_runtime_unavailable")
            eager = work.collect()
            value = registry.runtime.evaluate(
                definition, eager, columns, params, node_group
            ).rename(name)
            work = eager.lazy()
        elif node.op in {"TS_PROD", "TS_ARGMIN", "TS_ARGMAX", "TS_RANKCORR"}:
            eager = work.collect()
            source = eager.select(*node_group, *dict.fromkeys(columns)).with_row_index("__pos")
            values = np.full(eager.height, np.nan)
            for part in source.partition_by(node_group, maintain_order=True):
                values[part["__pos"].to_numpy()] = window_array(
                    node.op, [part[c].to_numpy() for c in columns], int(params[0])
                )
            value = pl.Series(name, values, dtype=pl.Float64)
            work = eager.lazy()
        else:
            value = build_expression(node.op, [pl.col(c) for c in columns], params, node_group)
        work = work.with_columns(value.alias(name))
        if not spec or spec.output != "condition":
            work = work.with_columns(
                pl.when(pl.col(name).is_finite()).then(pl.col(name).cast(pl.Float64)).alias(name)
            )
        memo[node] = name
        return name

    name = materialize(compiled.expression)
    return work.select("row_id", pl.col(name).alias("value")).collect()
