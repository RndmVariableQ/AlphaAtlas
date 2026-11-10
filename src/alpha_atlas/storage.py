"""Atomic trial records and bounded, rebuildable Arrow caches."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.contracts import Candidate, EvaluationReport, Expression, Metric, TrialFeedback

if TYPE_CHECKING:
    from alpha_atlas.library import LibraryView

FORMAT_VERSION = 2


class ArrowCache:
    """One disk budget for explicit run-local directories; index file metadata once."""

    def __init__(self, directories: tuple[Path, ...], max_bytes: int = 512 * 1024 * 1024):
        self.max_bytes = max(0, max_bytes)
        files = []
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            for path in directory.glob("*.arrow"):
                stat = path.stat()
                files.append((stat.st_mtime_ns, path, stat.st_size))
        self._sizes = OrderedDict((path, size) for _, path, size in sorted(files))
        # Concurrent evaluations and admissions share one budget index.
        self._lock = threading.RLock()
        self._trim()

    def __contains__(self, path: Path) -> bool:
        with self._lock:
            return path in self._sizes

    def _trim(self):
        with self._lock:
            self._trim_locked()

    def _trim_locked(self):
        total = sum(self._sizes.values())
        while total > self.max_bytes:
            path = next(iter(self._sizes))
            path.unlink(missing_ok=True)
            total -= self._sizes.pop(path)

    def read(self, path: Path) -> pl.DataFrame:
        frame = pl.read_ipc(path, memory_map=False)
        with self._lock:
            if path in self._sizes:
                self._sizes.move_to_end(path)
        return frame

    def write(self, path: Path, frame: pl.DataFrame) -> None:
        with self._lock:
            self._write(path, frame)

    def _write(self, path: Path, frame: pl.DataFrame) -> None:
        temporary = path.with_suffix(".tmp")
        try:
            frame.write_ipc(temporary, compression="zstd")
            size = temporary.stat().st_size
            if size > self.max_bytes:
                # Oversized results remain usable in the caller, but are not retained on disk.
                path.unlink(missing_ok=True)
                self._sizes.pop(path, None)
                return
            temporary.replace(path)
            self._sizes[path] = size
            self._sizes.move_to_end(path)
            self._trim_locked()
        finally:
            temporary.unlink(missing_ok=True)


def candidate_from_dict(value: dict) -> Candidate:
    data = dict(value)
    if isinstance(data["expression"], dict):
        data["expression"] = Expression.from_dict(data["expression"])
    data["region_ids"] = tuple(data.get("region_ids", ()))
    return Candidate(**data)


def report_from_dict(value: dict) -> EvaluationReport:
    data = dict(value)
    data["metrics"] = tuple(Metric(**m) for m in data["metrics"])
    if data.get("canonical_expression"):
        data["canonical_expression"] = Expression.from_dict(data["canonical_expression"])
    return EvaluationReport(**data)


def _feedback_record(feedback: TrialFeedback) -> dict:
    """Omit unused residual fields so runs without library_residual keep their trial format."""
    value = asdict(feedback)
    for key in ("residual_ic", "library_r2", "library_similarity"):
        if value[key] is None:
            del value[key]
    return value


def feedback_from_dict(value: dict) -> TrialFeedback:
    value = dict(value)
    if "trial_index" in value:
        value["factor_trial_index"] = value.pop("trial_index")
    if "nearest_factor" in value:
        value["nearest_factor_id"] = value.pop("nearest_factor")
    if value.get("library_similarity") is not None:
        value["library_similarity"] = tuple(value["library_similarity"])
    return TrialFeedback(
        **{
            **value,
            "candidate": candidate_from_dict(value["candidate"]),
            "report": report_from_dict(value["report"]) if value["report"] else None,
        }
    )


class RunStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.factor_trials_directory = directory / "factor_trials"
        legacy = directory / "trials"
        if legacy.is_dir():
            if self.factor_trials_directory.is_dir():
                raise ValueError("run contains both factor_trials and legacy trials directories")
            self.factor_trials_directory = legacy
        self._operator_index = len(list((directory / "operators").glob("*.json")))

    def record(
        self,
        attempt,
        feedback,
        elapsed,
        generation,
        cumulative,
        library_size,
        *,
        compiled=None,
        runtime_cost=None,
    ):
        path = self.factor_trials_directory / f"{attempt:08d}.json"
        if path.exists():
            raise ValueError(f"trial already committed: {attempt}")
        atomic_json(
            path,
            {
                "format_version": FORMAT_VERSION,
                "factor_trial_index": attempt,
                "feedback": _feedback_record(feedback),
                "compiled": asdict(compiled) if compiled else None,
                "elapsed_seconds": elapsed,
                "generation_seconds": generation,
                "cumulative_seconds": cumulative,
                "library_size": library_size,
                "runtime_cost": runtime_cost,
            },
        )

    def record_operator(self, definition, feedback):
        index = self._operator_index + 1
        atomic_json(
            self.directory / "operators" / f"{index:08d}.json",
            {
                "definition": asdict(definition),
                "feedback": asdict(feedback),
            },
        )
        self._operator_index = index

    def trial(self, factor_trial_index: int) -> dict:
        if type(factor_trial_index) is not int or factor_trial_index < 1:
            raise ValueError("factor_trial_index must be a positive integer")
        path = self.factor_trials_directory / f"{factor_trial_index:08d}.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("format_version") != FORMAT_VERSION:
            raise ValueError("unsupported run format; old artifacts are not migrated")
        if "trial_index" in row:
            row["factor_trial_index"] = row.pop("trial_index")
        feedback = row["feedback"]
        if "trial_index" in feedback:
            feedback["factor_trial_index"] = feedback.pop("trial_index")
        if "nearest_factor" in feedback:
            feedback["nearest_factor_id"] = feedback.pop("nearest_factor")
        if row["factor_trial_index"] != factor_trial_index:
            raise ValueError("committed trial index mismatch")
        return row

    def trials(self) -> list[dict]:
        records = []
        for expected, path in enumerate(sorted(self.factor_trials_directory.glob("*.json")), 1):
            if path.stem != f"{expected:08d}":
                raise ValueError("trial sequence is not contiguous")
            records.append(self.trial(expected))
        return records

    def library_view(self) -> LibraryView:
        from alpha_atlas.library import LibraryEntry, LibraryView

        members = []
        identities = set()
        spec_path = self.directory / "run.json"
        spec = json.loads(spec_path.read_text()) if spec_path.exists() else {}
        for item in spec.get("initial_library", {}).get("members", []):
            report = report_from_dict(item["report"])
            if report.expression_id in identities:
                raise ValueError("duplicate initial library member")
            identities.add(report.expression_id)
            members.append(
                LibraryEntry(
                    report.expression_id,
                    candidate_from_dict(item["candidate"]),
                    report,
                    len(members) + 1,
                )
            )
        for trial in self.trials():
            feedback = trial["feedback"]
            if feedback["accepted"]:
                report = report_from_dict(feedback["report"])
                if report.expression_id in identities:
                    raise ValueError("duplicate committed library member")
                identities.add(report.expression_id)
                members.append(
                    LibraryEntry(
                        report.expression_id,
                        candidate_from_dict(feedback["candidate"]),
                        report,
                        len(members) + 1,
                    )
                )
            if feedback["library_version"] != len(members):
                raise ValueError("committed library version mismatch")
        return LibraryView(tuple(members), len(members))
