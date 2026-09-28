"""Restricted numeric language for trusted kernels; not a security sandbox."""

import ast

NUMPY_NAMES = frozenset(
    "abs absolute add all any arange argmax argmin argsort array asarray clip concatenate "
    "corrcoef cos cumsum diff dot empty empty_like exp expm1 float64 full full_like inf "
    "isfinite isnan linspace log log1p max maximum mean median min minimum nan nanmean "
    "nanmedian nanstd nansum ndarray ones ones_like percentile power prod quantile sign "
    "sin sort sqrt square std subtract sum var where zeros zeros_like".split()
)
ARRAY_NAMES = frozenset(
    "astype copy sum mean std var min max prod sort argsort reshape size shape".split()
)
BUILTINS = frozenset("abs all any bool enumerate float int len max min range round zip".split())
_ALLOWED = (
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Assign,
    ast.AugAssign,
    ast.If,
    ast.IfExp,
    ast.For,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.Expr,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Call,
    ast.keyword,
    ast.Attribute,
    ast.Subscript,
    ast.Slice,
    ast.Tuple,
    ast.List,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.operator,
    ast.unaryop,
    ast.boolop,
    ast.cmpop,
)


def validate_source(source: str, function: str, parameters: list[str]) -> ast.Module:
    if not isinstance(source, str) or len(source) > 32768:
        raise ValueError("code source exceeds 32768 characters")
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError(f"invalid kernel syntax: {exc}") from None
    functions = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if (
                len(node.names) != 1
                or node.names[0].name != "numpy"
                or node.names[0].asname != "np"
            ):
                raise ValueError("only import numpy as np is allowed")
        elif isinstance(node, ast.FunctionDef):
            functions.append(node)
        else:
            raise ValueError("top level only permits the numeric function and numpy import")
    if len(functions) != 1 or functions[0].name != function:
        raise ValueError(f"exactly one function named {function} is required")
    root = functions[0]
    args = root.args
    if (
        [a.arg for a in args.args] != parameters
        or args.defaults
        or args.kw_defaults
        or args.kwonlyargs
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or root.decorator_list
        or root.returns
        or any(a.annotation for a in args.args)
    ):
        raise ValueError(
            "kernel signature must exactly match declared parameters, without annotations"
        )
    if any(p in BUILTINS or p == "np" or p.startswith("_") for p in parameters):
        raise ValueError("reserved parameter name")
    nodes = list(ast.walk(root))
    if len(nodes) > 4096:
        raise ValueError("kernel syntax exceeds node budget")
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"forbidden syntax: {type(node).__name__}")
        if isinstance(node, ast.FunctionDef) and node is not root:
            raise ValueError("nested functions are forbidden")
        if isinstance(node, ast.Constant) and not isinstance(
            node.value, (int, float, bool, type(None))
        ):
            raise ValueError("only numeric literals are allowed")
        if isinstance(node, ast.Name):
            if node.id.startswith("_"):
                raise ValueError("private names are forbidden")
            if isinstance(node.ctx, ast.Store) and (node.id == "np" or node.id in BUILTINS):
                raise ValueError("cannot overwrite the numeric namespace")
        if isinstance(node, ast.Attribute):
            allowed = (
                NUMPY_NAMES
                if isinstance(node.value, ast.Name) and node.value.id == "np"
                else ARRAY_NAMES
            )
            if node.attr not in allowed or isinstance(node.ctx, ast.Store):
                raise ValueError(f"forbidden attribute: {node.attr}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id not in BUILTINS:
                raise ValueError(f"forbidden call: {node.func.id}")
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ValueError("indirect calls are forbidden")
            if any(k.arg is None or k.arg.startswith("_") for k in node.keywords):
                raise ValueError("invalid keyword argument")
    return ast.fix_missing_locations(ast.Module(body=[root], type_ignores=[]))
