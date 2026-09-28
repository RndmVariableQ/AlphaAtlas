"""Append-only run-local admission; exact common-row Spearman and immutable views."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from alpha_atlas.contracts import Candidate, EvaluationReport, FactorValues, TrialFeedback
from alpha_atlas.storage import ArrowCache


@dataclass(frozen=True)
class LibraryEntry:
    factor_id: str
    candidate: Candidate
    report: EvaluationReport
    version: int


@dataclass(frozen=True)
class LibraryView:
    members: tuple[LibraryEntry, ...]
    version: int

    def get(self, factor_id: str) -> LibraryEntry | None:
        return next((m for m in self.members if m.factor_id == factor_id), None)

    def list(self, *, min_val_ic: float | None = None) -> tuple[LibraryEntry, ...]:
        return tuple(
            m
            for m in self.members
            if min_val_ic is None
            or (value := next((v.value for v in m.report.metrics if v.split == "val"), None))
            is not None
            and value >= min_val_ic
        )

    def stats(self) -> dict:
        return {"members": len(self.members), "version": self.version}


class FactorLibrary:
    def __init__(
        self,
        rules: dict,
        *,
        min_train_val_ic: float | None = None,
        snapshot_id: str | None = None,
        context_id: str | None = None,
        directory: Path | None = None,
        disk_cache: ArrowCache | None = None,
        rebuild=None,
    ):
        self._rules = dict(rules)
        self._min_train_val_ic = min_train_val_ic
        self._snapshot_id, self._context_id = snapshot_id, context_id
        self._directory = directory
        if disk_cache is not None and (directory is None or rebuild is None):
            raise ValueError("bounded member cache requires a directory and rebuild callback")
        self._disk_cache, self._rebuild = disk_cache, rebuild
        if directory:
            directory.mkdir(parents=True, exist_ok=True)
        self._members: dict[str, LibraryEntry] = {}
        self._values: dict[str, pl.DataFrame | Path] = {}

    def view(self) -> LibraryView:
        return LibraryView(tuple(self._members.values()), len(self._members))

    def _load(self, factor_id: str) -> pl.DataFrame:
        reference = self._values[factor_id]
        if not isinstance(reference, Path):
            return reference
        try:
            return (
                self._disk_cache.read(reference)
                if self._disk_cache is not None
                else pl.read_ipc(reference, memory_map=False)
            )
        except (FileNotFoundError, pl.exceptions.PolarsError):
            if self._rebuild is None:
                raise
        values = self._rebuild(self._members[factor_id])
        self._save_values(factor_id, values.frame)
        return values.frame

    def _save_values(self, identity: str, frame: pl.DataFrame) -> pl.DataFrame | Path:
        if self._directory:
            path = self._directory / f"{identity}.arrow"
            if self._disk_cache is not None:
                self._disk_cache.write(path, frame)
            else:
                temp = path.with_suffix(".tmp")
                frame.write_ipc(temp, compression="zstd")
                temp.replace(path)
            return path
        return frame.clone()

    def restore(self, view: LibraryView, observation=None) -> None:
        """Restore committed membership without readmission or trial/budget side effects."""
        if self._members:
            raise ValueError("restore requires an empty library")
        for entry in view.members:
            if self._disk_cache is not None:
                # Committed metadata is sufficient until a comparison needs this member's values.
                self._values[entry.factor_id] = self._directory / f"{entry.factor_id}.arrow"
                self._members[entry.factor_id] = entry
                continue
            values = observation(entry)
            if (
                values.expression_id != entry.factor_id
                or values.snapshot_id != self._snapshot_id
                or values.context_id != self._context_id
                or values.frame["row_id"].n_unique() != values.frame.height
            ):
                raise ValueError("restored observation identity mismatch")
            self._values[entry.factor_id] = self._save_values(entry.factor_id, values.frame)
            self._members[entry.factor_id] = entry

    def consider(
        self,
        candidate: Candidate,
        report: EvaluationReport,
        values: FactorValues | None,
        *,
        commit=None,
        trial_index: int = 0,
    ) -> TrialFeedback:
        identity = report.expression_id
        reason, highest, nearest = "accepted", None, None
        complete = True
        val = next((m.value for m in report.metrics if m.split == "val"), None)
        train = next((m.value for m in report.metrics if m.split == "train"), None)
        if report.status != "success":
            reason = report.status
        elif identity in self._members:
            reason = "expression_duplicate"
        elif train is None or report.direction not in {-1, 1}:
            reason = "undefined_train_metric"
        elif report.coverage is None or report.coverage < self._rules["min_coverage"]:
            reason = "low_coverage"
        elif (
            val is None
            or not math.isfinite(val)
            or (
                self._min_train_val_ic is not None
                and (
                    not math.isfinite(train)
                    or train * report.direction <= self._min_train_val_ic
                    or val <= self._min_train_val_ic
                )
            )
            or self._min_train_val_ic is None
            and val < self._rules["min_abs_val_ic"]
        ):
            reason = "quality_threshold"
        elif values is None:
            reason = "missing_observation"
        elif (
            values.expression_id != identity
            or self._snapshot_id is not None
            and values.snapshot_id != self._snapshot_id
            or self._context_id is not None
            and values.context_id != self._context_id
        ):
            reason = "observation_context_mismatch"
        elif values.frame["row_id"].n_unique() != values.frame.height:
            reason = "duplicate_observation_row"
        else:
            finite = values.frame.filter(pl.col("value").is_finite().fill_null(False))
            if finite["value"].n_unique() < 2:
                reason = "constant_factor"
            else:
                failures = set()
                for factor_id in self._members:
                    reference = self._load(factor_id).rename({"value": "reference"})
                    overlap = finite.join(reference, on="row_id", validate="1:1").filter(
                        pl.col("reference").is_finite().fill_null(False)
                    )
                    if overlap.height < self._rules["min_corr_overlap"]:
                        failures.add("insufficient_corr_overlap")
                        continue
                    corr = overlap.select(pl.corr("value", "reference", method="spearman")).item()
                    if corr is None or not math.isfinite(corr):
                        failures.add("undefined_correlation")
                        continue
                    absolute = min(1.0, abs(corr))
                    if highest is None or absolute > highest:
                        highest, nearest = absolute, factor_id
                    if absolute >= self._rules["max_abs_corr"]:
                        failures.add("behavior_duplicate")
                if failures:
                    reason = next(
                        r
                        for r in (
                            "insufficient_corr_overlap",
                            "undefined_correlation",
                            "behavior_duplicate",
                        )
                        if r in failures
                    )
                    complete = not (
                        failures & {"insufficient_corr_overlap", "undefined_correlation"}
                    )
        accepted = reason == "accepted"
        version = len(self._members) + int(accepted)
        feedback = TrialFeedback(
            candidate,
            report,
            accepted,
            reason,
            highest,
            nearest,
            complete,
            version,
            trial_index=trial_index,
        )
        stored = None
        if accepted:
            if (
                identity is None
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise ValueError("invalid factor identity")
            stored = self._save_values(identity, values.frame)
        # The trial file is the commit marker; caches alone never imply membership.
        if commit:
            commit(feedback)
        if accepted:
            self._snapshot_id, self._context_id = values.snapshot_id, values.context_id
            self._members[identity] = LibraryEntry(identity, candidate, report, version)
            self._values[identity] = stored
        return feedback
