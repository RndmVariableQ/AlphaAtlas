"""Disposable local validation process; fault containment, not a security sandbox."""

import json
import sys

from alpha_atlas.operators.registry import OperatorDefinition
from alpha_atlas.operators.runtime import NumbaRuntime, RuntimeLimits
from alpha_atlas.operators.validation import OperatorValidation


def main():
    report = {}
    try:
        payload = json.load(sys.stdin)
        definition = OperatorDefinition(**payload["definition"])
        runtime = NumbaRuntime(RuntimeLimits(**payload["limits"]))
        validation = OperatorValidation(runtime, definition, report)
        validation.progress = lambda: print(
            json.dumps({"validation": report}, allow_nan=False), flush=True
        )
        validation.run()
        print(json.dumps({"ok": True, "validation": report}, allow_nan=False), flush=True)
    except Exception as exc:
        print(
            json.dumps({"ok": False, "validation": report, "error": str(exc)}, allow_nan=False),
            flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
