"""Run the five declared Haiku B26 searches concurrently, then paired evaluation."""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).resolve().parent
OUTPUT = ROOT / "artifacts/comparisons" / CONFIG.name
LOGS = ROOT / "logs/experiments" / CONFIG.name
GROUPS = ("methods", "v3")
METHODS = ("alphaprobe", "alphaschema", "mcts_llm", "react", "v3_async")


def worker(phase, name):
    group = "v3" if name == "v3_async" else "methods" if phase == "search" else name
    source = "react" if phase == "search" and name == "react" else group
    source_path = OUTPUT / "source" / source / "src"
    if not (source_path / "alpha_atlas").is_dir():
        raise FileNotFoundError(f"saved experiment source missing: {source_path}")
    sys.path.insert(0, str(source_path))
    from dotenv import load_dotenv

    from alpha_atlas.incremental import _scope_runs, run_scope

    load_dotenv(ROOT / ".env", override=False)
    logger = logging.getLogger("alpha_atlas.progress")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = [logging.StreamHandler()]
    scope_path = CONFIG / f"{group}.json"
    if phase == "search":
        scope = json.loads(scope_path.read_text())
        scope["methods"] = [name]
        directories = _scope_runs(ROOT, scope)
        for directory in directories:
            spec = json.loads((directory / "run.json").read_text())
            if spec["status"] != "frozen":
                raise RuntimeError(f"search did not freeze: {directory.name}")
        result = {"runs": [str(path) for path in directories]}
    else:
        result = run_scope(ROOT, scope_path, OUTPUT / group, test=phase == "test")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def command(phase, name):
    return [sys.executable, "-u", str(Path(__file__).resolve()), phase, name]


def run_all():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    processes = {}
    with ExitStack() as stack:
        try:
            for method in METHODS:
                log = stack.enter_context((LOGS / f"{method}.log").open("a"))
                processes[method] = subprocess.Popen(
                    command("search", method), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
                )
                print(f"Started {method}: PID {processes[method].pid}", flush=True)
            codes = {method: process.wait() for method, process in processes.items()}
        except KeyboardInterrupt:
            for process in processes.values():
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
            for process in processes.values():
                process.wait()
            raise
    if any(codes.values()):
        raise RuntimeError(f"search failed or interrupted; paired evaluation not started: {codes}")
    print("All five searches frozen; fitting both scopes before opening Test.", flush=True)
    for phase in ("fit", "test"):
        for group in GROUPS:
            with (LOGS / f"{group}-{phase}.log").open("a") as log:
                subprocess.run(
                    command(phase, group),
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            print(f"Completed {group} {phase}", flush=True)
    print("Completed all searches and paired B26 evaluations.", flush=True)


if __name__ == "__main__":
    for variable, value in {
        "POLARS_MAX_THREADS": "8",
        "NUMBA_NUM_THREADS": "8",
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
    }.items():
        os.environ.setdefault(variable, value)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=("search", "fit", "test"))
    parser.add_argument("name", nargs="?", choices=(*METHODS, *GROUPS))
    args = parser.parse_args()
    if args.phase is None and args.name is None:
        run_all()
    elif args.phase == "search" and args.name in METHODS:
        worker(args.phase, args.name)
    elif args.phase in {"fit", "test"} and args.name in GROUPS:
        worker(args.phase, args.name)
    else:
        parser.error("search requires a method; fit/test requires a scope group")
