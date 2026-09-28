"""Atomic trial records and bounded, rebuildable Arrow caches."""

from __future__ import annotations

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
        self._trim()

    def __contains__(self, path: Path) -> bool:
        return path in self._sizes

    def _trim(self):
        total = sum(self._sizes.values())
        while total > self.max_bytes:
            path = next(iter(self._sizes))
            path.unlink(missing_ok=True)
            total -= self._sizes.pop(path)

    def read(self, path: Path) -> pl.DataFrame:
        frame = pl.read_ipc(path, memory_map=False)
        if path in self._sizes:
            self._sizes.move_to_end(path)
        return frame

    def write(self, path: Path, frame: pl.DataFrame) -> None:
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
            self._trim()
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


def feedback_from_dict(value: dict) -> TrialFeedback:
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
        path = self.directory / "trials" / f"{attempt:08d}.json"
        if path.exists():
            raise ValueError(f"trial already committed: {attempt}")
        atomic_json(
            path,
            {
                "format_version": FORMAT_VERSION,
                "trial_index": attempt,
                "feedback": asdict(feedback),
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

    def trials(self) -> list[dict]:
        import json

        records = []
        for expected, path in enumerate(sorted((self.directory / "trials").glob("*.json")), 1):
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("format_version") != FORMAT_VERSION:
                raise ValueError("unsupported run format; old artifacts are not migrated")
            if row["trial_index"] != expected or path.stem != f"{expected:08d}":
                raise ValueError("trial sequence is not contiguous")
            records.append(row)
        return records

    def library_view(self) -> LibraryView:
        from alpha_atlas.library import LibraryEntry, LibraryView

        members = []
        identities = set()
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
