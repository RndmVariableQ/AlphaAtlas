"""Run the third Luna B26 V3 search and immediately evaluate its frozen library."""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).resolve().parent
OUTPUT = ROOT / "artifacts/comparisons" / CONFIG.name
LOGS = ROOT / "logs/experiments" / CONFIG.name
SEARCH_SOURCE = (
    ROOT / "artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/source/v3/src"
)


def worker(phase):
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
        CONFIG / "luna.json",
        OUTPUT / "luna",
        search_only=phase == "search",
        test=phase == "test",
    )
    for name in result["runs"]:
        saved = json.loads((ROOT / name / "run.json").read_text())
        if saved["status"] != "frozen":
            raise RuntimeError(f"search did not freeze: {name}")
    print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    for phase in ("search", "fit", "test"):
        with (LOGS / f"luna-{phase}.log").open("a") as log:
            print(f"Starting Luna {phase}", flush=True)
            subprocess.run(
                [sys.executable, "-u", str(Path(__file__).resolve()), phase],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        print(f"Completed Luna {phase}", flush=True)
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
    print("Completed Luna repeat 3 and paired scores.", flush=True)


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
    args = parser.parse_args()
    if args.phase is None:
        main()
    else:
        worker(args.phase)
