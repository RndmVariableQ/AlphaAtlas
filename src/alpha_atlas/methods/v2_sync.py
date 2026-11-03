"""AlphaAtlas V2 Sync: mechanism Regions rated by residual information per attempt.

Serial V1 research (Explorer, Analyst, Miner, Metropolis) with two changes: the Region score
is the mean library residual IC per attempt instead of the admission rate, and the Explorer
reads each Region's residual information and derived lifecycle status. Requires a version
with library_residual = residual_ic_v1; admission itself is unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from alpha_atlas.methods.v1 import EXPLORER_PROMPT, V1Config, V1Search

STATE_VERSION = "atlas_mechanism_v2_sync"

MECHANISM_PROMPT = (
    EXPLORER_PROMPT
    + """
V2 map semantics. Treat each Region as a market mechanism: who acts or is constrained, why
that creates predictability, and which footprint it leaves in the available fields. The
initial grammar Regions name measurement families, not mechanisms; prefer a Region that states
a mechanism.
Each evaluated trial reports residual_ic, the Valid IC left after removing what the current
library explains, and library_r2, the share of the factor the library explains. High
library_r2 with low residual_ic is a rediscovery of known information.
Each Region's residual_information gives attempts, mean residual_ic per attempt, best and
recent values, and a status: probing (fewer than probe_attempts), land (some factor rose above
sea_level), saturated (land whose recent attempts stay below sea_level) or seabed (probed with
no factor above sea_level). Seabed and saturation hold only for the tested implementations.
In prediction, name the admitted factors or Regions the new factors should be weakly related
to, and the result after a few attempts that would refute the mechanism.
New mechanisms may come from lenses such as belief updating and adaptation speed, liquidity
provision and microstructure, hedging pressure and risk premia, multiscale self-similarity
and its breaks, market state spaces and transitions, or redundancy and synergy among price,
volume and open interest. Prefer mechanisms and lenses that have added little residual
information so far. A lens or label earns nothing; only measured residual information does.
"""
)


@dataclass(frozen=True)
class V2SyncConfig(V1Config):
    sea_level: float = 0.005
    probe_attempts: int = 5

    def __post_init__(self):
        super().__post_init__()
        if (
            isinstance(self.sea_level, bool)
            or not isinstance(self.sea_level, int | float)
            or not math.isfinite(self.sea_level)
            or self.sea_level <= 0
        ):
            raise ValueError("sea_level must be a positive finite number")
        if type(self.probe_attempts) is not int or self.probe_attempts < 1:
            raise ValueError("probe_attempts must be a positive integer")


class V2SyncSearch(V1Search):
    explorer_prompt = MECHANISM_PROMPT

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or V2SyncConfig(), models=models)

    def dump_state(self):
        return {**super().dump_state(), "version": STATE_VERSION}

    def load_state(self, state):
        if state.get("version") != STATE_VERSION:
            raise ValueError("V2 Sync requires its own checkpoint")
        super().load_state({**state, "version": "atlas_explorer_v1"})

    def _residual_information(self, region_id, trials=None):
        """Derived from atomic trials each time; failed or unmeasured attempts add zero."""
        records = [r for r in self.factor_trial_records if r.get("region_id") == region_id]
        if not records:
            return None
        trials = self._trial_results(records) if trials is None else trials
        values = [trials[r["factor_trial_index"]].get("residual_ic") for r in records]
        measured = [value for value in values if value is not None]
        probe, sea = self.config.probe_attempts, self.config.sea_level
        best = max(measured, default=None)
        recent = [value or 0.0 for value in values[-probe:]]
        if len(values) < probe:
            status = "probing"
        elif best is None or best < sea:
            status = "seabed"
        elif len(values) >= 2 * probe and max(recent) < sea:
            status = "saturated"
        else:
            status = "land"
        return {
            "attempts": len(values),
            "measured": len(measured),
            "mean_residual_ic": sum(measured) / len(values),
            "best_residual_ic": best,
            "recent_mean_residual_ic": sum(recent) / len(recent),
            "status": status,
            "sea_level": sea,
            "probe_attempts": probe,
        }

    def _region_score(self, region_id):
        # Metropolis compares mean residual information per attempt in sea-level units.
        information = self._residual_information(region_id)
        if information is None:
            return None
        return information["mean_residual_ic"] / self.config.sea_level

    def _region_evidence(self, region_id, trials=None):
        evidence = super()._region_evidence(region_id, trials)
        evidence["residual_information"] = self._residual_information(region_id, trials)
        return evidence
