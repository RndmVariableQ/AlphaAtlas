"""Evaluate a committed search prefix with the fixed prediction models, without publishing."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.checkpoint import (
    MUTABLE_RUN_FIELDS,
    config_fingerprint,
    read_json,
    run_lock,
)
from alpha_atlas.reporting import refresh_report
from alpha_atlas.runner import check_config, run_model
from alpha_atlas.storage import FORMAT_VERSION, RunStore

ROOT = Path(__file__).resolve().parents[1]


def resolve_run(root: Path, selector: str) -> Path:
    path = Path(selector).expanduser()
    if path.is_dir() and (path / "run.json").is_file():
        return path.resolve()
    matches = []
    paths = list((root / "artifacts/runs").glob("*/run.json"))
    paths.extend((root / "artifacts/comparisons").glob("*/workspace/artifacts/runs/*/run.json"))
    for path in paths:
        spec = read_json(path)
        if spec.get("source_run_id"):
            continue
        names = {
            path.parent.name,
            path.parent.name.rsplit("-", 1)[-1],
            spec.get("experiment_version", {}).get("id"),
        }
        if path.parent.parents[2].name == "workspace":
            names.add(path.parent.parents[3].name)
        if selector in names:
            matches.append(path.parent)
    if len(matches) != 1:
        raise ValueError(f"expected one source run for {selector!r}, found {matches}")
    return matches[0].resolve()


def freeze_prefix(source: Path, *, attempts: int | None = None, factors: int | None = None):
    if attempts is not None and factors is not None:
        raise ValueError("choose attempts or factors, not both")
    if any(value is not None and value <= 0 for value in (attempts, factors)):
        raise ValueError("attempts and factors must be positive")
    workspace = source.parents[2]
    spec = read_json(source / "run.json")
    check_config(workspace, spec)
    if spec["status"] == "frozen" and attempts is None and factors is None:
        return source
    if spec.get("initial_library") or list((source / "operators").glob("*.json")):
        raise ValueError("prefix helper supports empty initial libraries and builtin operators")
    checkpoint_path = source / "checkpoint.json"
    if checkpoint_path.exists():
        checkpoint = read_json(checkpoint_path)
        if checkpoint["config_fingerprint"] != spec["config_fingerprint"]:
            raise ValueError("checkpoint configuration mismatch")
        completed = checkpoint["completed"]
    elif spec["status"] == "frozen":
        completed = spec["attempts"]
    else:
        raise ValueError("no committed checkpoint is available")
    if attempts is not None and attempts > completed:
        raise ValueError(f"only {completed} attempts have been committed")
    captured_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    payloads, members = [], []
    store = RunStore(source)
    for index in range(1, (attempts or completed) + 1):
        payload = (store.factor_trials_directory / f"{index:08d}.json").read_bytes()
        trial = json.loads(payload)
        feedback = trial["feedback"]
        if trial["factor_trial_index"] != index:
            raise ValueError("trial index mismatch")
        if factors is not None and feedback["accepted"] and len(members) == factors:
            break
        if feedback["accepted"]:
            members.append(
                {
                    "candidate": feedback["candidate"],
                    "report": feedback["report"],
                    "compiled": trial["compiled"],
                }
            )
        if len(members) != feedback["library_version"] or len(members) != trial["library_size"]:
            raise ValueError("trial library size mismatch")
        payloads.append(payload)
    if not members or (factors is not None and len(members) != factors):
        raise ValueError(f"requested factor count is unavailable; found {len(members)}")
    if len({member["compiled"]["factor_id"] for member in members}) != len(members):
        raise ValueError("duplicate admitted factor identity")
    cutoff = len(payloads)
    if checkpoint_path.exists() and cutoff == completed:
        if len(members) != checkpoint["library_version"]:
            raise ValueError("checkpoint library size mismatch")
    destination = source.with_name(f"{source.name}-prefix{cutoff}")
    frozen = {
        "format_version": FORMAT_VERSION,
        "factors": members,
        "operators": [],
        "runtime": spec["runtime"],
    }
    if destination.exists():
        saved = read_json(destination / "run.json")
        check_config(workspace, saved)
        ignored = MUTABLE_RUN_FIELDS | {"run_id", "attempts", "resumable", "config_fingerprint"}
        if any(saved.get(key) != value for key, value in spec.items() if key not in ignored):
            raise ValueError("existing prefix differs from its source configuration")
        if saved.get("source_run_id") != source.name or saved["attempts"] != cutoff:
            raise ValueError("existing prefix has a different source or cutoff")
        if read_json(destination / "frozen/library.json") != frozen:
            raise ValueError("existing prefix has different frozen members")
        return destination
    destination.mkdir(parents=True)
    with run_lock(destination):
        trials = RunStore(destination).factor_trials_directory
        trials.mkdir()
        for index, payload in enumerate(payloads, 1):
            (trials / f"{index:08d}.json").write_bytes(payload)
        atomic_json(destination / "frozen/library.json", frozen)
        snapshot = {
            **spec,
            "run_id": destination.name,
            "status": "frozen",
            "resumable": False,
            "attempts": cutoff,
            "library_size": len(members),
            "total_seconds": json.loads(payloads[-1])["cumulative_seconds"],
            "library_sha256": hashlib.sha256(
                (destination / "frozen/library.json").read_bytes()
            ).hexdigest(),
            "source_run_id": source.name,
            "source_attempt_budget": spec["attempts"],
            "snapshot_trial_count": cutoff,
            "source_status_at_capture": spec["status"],
            "snapshot_note": f"User-requested committed prefix captured at {captured_at}. "
            "All admitted definitions, order, training directions and clipping preserved. "
            "Prediction models only; no Test feedback, checkpoint or live model usage copied.",
        }
        snapshot["config_fingerprint"] = config_fingerprint(snapshot)
        atomic_json(destination / "run.json", snapshot)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run", help="run directory, run ID, short ID, version, or comparison folder"
    )
    cutoff = parser.add_mutually_exclusive_group()
    cutoff.add_argument("--attempts", type=int, help="exact committed attempt count")
    cutoff.add_argument("--factors", type=int, help="latest prefix with exactly this many members")
    args = parser.parse_args()
    directory = freeze_prefix(
        resolve_run(ROOT, args.run), attempts=args.attempts, factors=args.factors
    )
    spec = read_json(directory / "run.json")
    print(
        json.dumps(
            {
                "directory": str(directory),
                "attempts": spec["attempts"],
                "factors": spec["library_size"],
            }
        ),
        flush=True,
    )
    workspace = directory.parents[2]
    run_model(workspace, directory)
    run_model(workspace, directory, test=True)
    refresh_report(directory)
    fit = read_json(directory / "model/fit.json")
    test = read_json(directory / "model/test.json")
    print(
        json.dumps(
            {
                "report": str(directory / "model/report.md"),
                "training_rows": fit["fitted"]["training_rows"],
                "factors": len(fit["factor_ids"]),
                "dropped": fit["fitted"]["preprocessing"]["dropped"],
                "test": test["reports"]["test"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    logger = logging.getLogger("alpha_atlas.progress")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = [logging.StreamHandler()]
    main()
