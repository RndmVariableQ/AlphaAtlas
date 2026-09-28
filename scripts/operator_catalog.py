"""Generate/check the public catalog directly from registered builtin signatures."""

import argparse
from pathlib import Path

from alpha_atlas.operators import OperatorRegistry


def catalog() -> str:
    specs = OperatorRegistry().catalog()
    lines = [
        "# 基础算子目录",
        "",
        f"共 {len(specs)} 个规范名称；别名不重复计数。",
        "由 `uv run python scripts/operator_catalog.py` 生成。数值与扩展协议见 [DSL](dsl.md)。",
        "",
        "| 名称 | 参数类型 | 输出 | 作用域 | 历史规则 | 含义 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for spec in specs:
        arguments = list(spec.args)
        for index, default in enumerate(spec.defaults, len(arguments) - len(spec.defaults)):
            arguments[index] += f"={default}"
        lines.append(
            f"| `{spec.name}` | {', '.join(arguments)} | {spec.output} | "
            f"{spec.scope} | {spec.history} | {spec.description} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "docs/operators.md"
    text = catalog()
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            raise SystemExit("operator catalog is out of date")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
