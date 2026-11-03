"""Evaluate a predeclared batch against the frozen Zookeeper 26-factor baseline."""

import argparse
import json
import logging
from pathlib import Path

from alpha_atlas.incremental import evaluate_incremental


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    logger = logging.getLogger("alpha_atlas.progress")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = [logging.StreamHandler()]
    result = evaluate_incremental(args.root, args.scope, args.runs, args.output, test=args.test)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
