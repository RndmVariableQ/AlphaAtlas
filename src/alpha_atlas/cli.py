from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha Atlas reproducible factor research")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("config")
    status = commands.add_parser("data-status")
    status.add_argument("--asset")
    audit = commands.add_parser("audit-data")
    audit.add_argument("--asset", required=True)
    run = commands.add_parser("run")
    run.add_argument("--asset", required=True)
    run.add_argument("--fold", choices=["fold1", "fold2"], required=True)
    run.add_argument(
        "--method",
        choices=["random", "gp", "mcts", "atlas", "alphaprobe", "react", "mcts_llm"],
        default="random",
    )
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--attempts", type=int)
    run.add_argument("--universe")
    run.add_argument("--fields", help="Comma-separated explicit input field allowlist")
    run.add_argument("--allow-incomplete", action="store_true")
    oos = commands.add_parser("test")
    oos.add_argument("run_dir", type=Path)
    oos.add_argument("--json", action="store_true", help="Print JSON instead of the result table")
    resume = commands.add_parser("resume", help="Continue a checkpointed ask/tell run")
    resume.add_argument("run_dir", type=Path)
    model = commands.add_parser(
        "model", help="Train linear and LightGBM on a frozen factor library"
    )
    model.add_argument("run_dir", type=Path)
    model.add_argument(
        "--test", action="store_true", help="Evaluate saved models on test; no refit"
    )
    for command in (run, resume, oos, model):
        command.add_argument("--quiet", action="store_true", help="Hide progress and model streams")
    report = commands.add_parser("report", help="Rebuild the Markdown run report from records")
    report.add_argument("run_dir", type=Path)
    report.add_argument(
        "--oos", action="store_true", help="Display saved OOS results without computing or writing"
    )
    commands.add_parser("compare")
    args = parser.parse_args()
    if args.command in {"run", "resume", "test", "model"}:
        logger = logging.getLogger("alpha_atlas.progress")
        logger.setLevel(logging.CRITICAL + 1 if args.quiet else logging.INFO)
        logger.propagate = False
        logger.handlers = [logging.StreamHandler()]
    root = args.root.resolve()
    if args.command == "config":
        from dataclasses import asdict

        from alpha_atlas.config import load_fold

        result = {name: asdict(load_fold(root, name)) for name in ["fold1", "fold2"]}
    elif args.command == "data-status":
        result = {}
        configured = sorted(path.stem for path in (root / "configs/assets").glob("*.toml"))
        for asset in [args.asset] if args.asset else configured:
            path = root / "data" / asset / "manifest.json"
            if path.exists():
                m = json.loads(path.read_text(encoding="utf-8"))
                result[asset] = {
                    k: m.get(k)
                    for k in [
                        "status",
                        "rows",
                        "actual_start",
                        "actual_end",
                        "frequency",
                        "snapshot_id",
                    ]
                }
            else:
                result[asset] = {"status": "not_acquired"}
    elif args.command == "audit-data":
        from alpha_atlas.assets.audit import audit

        result = audit(root / "data" / args.asset)
    elif args.command == "run":
        from alpha_atlas.runner import run

        path = run(
            root,
            args.asset,
            args.fold,
            args.method,
            args.seed,
            args.attempts,
            args.universe,
            args.allow_incomplete,
            args.fields.split(",") if args.fields else None,
        )
        result = {"run_dir": str(path)}
    elif args.command == "resume":
        from alpha_atlas.runner import resume

        result = {"run_dir": str(resume(root, args.run_dir.resolve()))}
    elif args.command == "model":
        from alpha_atlas.runner import run_model

        result = run_model(root, args.run_dir.resolve(), test=args.test)
    elif args.command == "report":
        from alpha_atlas.checkpoint import run_lock
        from alpha_atlas.reporting import write_report

        if args.oos:
            from alpha_atlas.checkpoint import read_json
            from alpha_atlas.reporting import terminal_oos

            directory = args.run_dir.resolve()
            if not (directory / "oos.json").exists():
                parser.error("no saved oos.json; run atlas test <run_dir> to compute OOS first")
            result = read_json(directory / "oos.json")
            print("已保存的 OOS 报告（只读展示，未按当前代码重新验证或计算）")
            print(terminal_oos(result, directory))
            return
        with run_lock(args.run_dir.resolve()):
            result = {"report": str(write_report(args.run_dir.resolve()))}
    elif args.command == "test":
        from alpha_atlas.runner import test_frozen

        result = test_frozen(root, args.run_dir.resolve())
    else:
        from alpha_atlas.reporting import compare

        report = compare(root)
        result = {"runs": len(report["runs"]), "output": str(root / "artifacts/comparison.json")}
    if args.command == "test" and not args.json:
        from alpha_atlas.reporting import terminal_oos

        print(terminal_oos(result, args.run_dir.resolve()))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
