"""Repeat the Luna and Haiku B26 V3 searches, then evaluate both frozen runs."""

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
SEARCH_SOURCE = (
    ROOT / "artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/source/v3/src"
)
GROUPS = ("haiku", "luna")


def worker(phase, group):
    source = SEARCH_SOURCE if phase == "search" else OUTPUT / "source/src"
    if not (source / "alpha_atlas").is_dir():
        raise FileNotFoundError(f"saved experiment source missing: {source}")
    sys.path.insert(0, str(source))
    from dotenv import load_dotenv

    from alpha_atlas.incremental import run_scope

    load_dotenv(ROOT / ".env", override=False)
    logger = logging.getLogger("alpha_atlas.progress")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = [logging.StreamHandler()]
    result = run_scope(
        ROOT,
        CONFIG / f"{group}.json",
        OUTPUT / group,
        search_only=phase == "search",
        test=phase == "test",
    )
    for name in result["runs"]:
        saved = json.loads((ROOT / name / "run.json").read_text())
        if saved["status"] != "frozen":
            raise RuntimeError(f"search did not freeze: {name}")
    print(json.dumps(result, ensure_ascii=False), flush=True)


def command(phase, group):
    return [sys.executable, "-u", str(Path(__file__).resolve()), phase, group]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    processes = {}
    with ExitStack() as stack:
        try:
            for group in GROUPS:
                log = stack.enter_context((LOGS / f"{group}-search.log").open("a"))
                processes[group] = subprocess.Popen(
                    command("search", group), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
                )
                print(f"Started {group}: PID {processes[group].pid}", flush=True)
            codes = {group: process.wait() for group, process in processes.items()}
        except KeyboardInterrupt:
            for process in processes.values():
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
            for process in processes.values():
                process.wait()
            raise
    if any(codes.values()):
        raise RuntimeError(f"search failed or interrupted; no Test opened: {codes}")
    print("Both searches frozen; fitting both scopes before Test.", flush=True)
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
    env = dict(os.environ, PYTHONPATH=str(OUTPUT / "source/src"))
    with (LOGS / "score.log").open("a") as log:
        subprocess.run(
            [sys.executable, "-u", str(CONFIG / "score.py")],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    print("Completed both repeats and paired scores.", flush=True)


if __name__ == "__main__":
    for name, value in {
        "POLARS_MAX_THREADS": "8",
        "NUMBA_NUM_THREADS": "8",
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "4",
    }.items():
        os.environ.setdefault(name, value)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=("search", "fit", "test"))
    parser.add_argument("group", nargs="?", choices=GROUPS)
    args = parser.parse_args()
    if args.phase is None and args.group is None:
        main()
    elif args.phase is not None and args.group is not None:
        worker(args.phase, args.group)
    else:
        parser.error("phase and group must be supplied together")
