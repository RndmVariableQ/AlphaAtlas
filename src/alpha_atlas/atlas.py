"""Static semantic regions with evidence accumulation, separate from factor identity."""

from __future__ import annotations

from dataclasses import asdict

from alpha_atlas.contracts import Region, TrialFeedback


class FactorAtlas:
    def __init__(self):
        self._regions: dict[str, Region] = {}
        self.statistics: dict[str, dict] = {}

    def regions(self) -> tuple[Region, ...]:
        return tuple(self._regions.values())

    def register_hypothesis(self, region: Region) -> None:
        if region.id in self._regions and self._regions[region.id] != region:
            raise ValueError("region already exists; use a new version/id")
        self._regions[region.id] = region
        self.statistics.setdefault(region.id, {"attempts": 0, "accepted": 0, "seconds": 0.0})

    def observe(self, feedback: TrialFeedback) -> None:
        for region in feedback.candidate.region_ids:
            if region not in self._regions:
                raise ValueError(f"unknown region {region}")
            row = self.statistics[region]
            row["attempts"] += 1
            row["accepted"] += int(feedback.accepted)
            row["seconds"] += feedback.report.elapsed_seconds if feedback.report else 0

    def to_dict(self) -> dict:
        return {"regions": [asdict(r) for r in self.regions()], "statistics": self.statistics}
