"""AlphaAtlas V2 Sync: mechanism Regions rated by residual information per attempt.

Serial V1 research (Explorer, Analyst, Miner, Metropolis) with two changes: the Region score
is the mean library residual IC per attempt instead of the admission rate, and the Explorer
reads each Region's residual information and derived lifecycle status. With
scale_statistics, that information is also split by feature-span band. Requires a version
with library_residual = residual_ic_v1; admission itself is unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from alpha_atlas.expressions import parse
from alpha_atlas.methods.v1 import EXPLORER_PROMPT, V1Config, V1Search
from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch
from alpha_atlas.timeframes import SCALE_BANDS, feature_span_minutes, scale_band

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

SCALE_PROMPT = """
residual_information.by_scale splits a Region's attempts by feature span, the approximate
trading time between the oldest and newest bar a factor reads (a trading day counts as 240
minutes): up_to_1h, 1h_to_1d and over_1d, each with the same statistics and status rules.
A mechanism can differ across spans: saturation or seabed in one band says nothing about an
untested band. Changing only the timeframe is still not a new direction, but testing a
promising mechanism in an unprobed or unresolved band is a distinct test within its Region.
"""

MECHANISM_REGION_PROMPT = """
Regions with source baseline_grammar (return, delta, mean, std, zscore) are measurement
families kept only as reference; new plans cannot use them. Choose an existing mechanism
Region or propose new_region with name, hypothesis and continent_ids. name is a short
mechanism name of at most six words, such as "Liquidation reversal after crowding"; it names
who acts and why, not an operator or a field. hypothesis states the full mechanism. Reuse an
existing Region's region_id instead of proposing the same mechanism under a new name.
"""
FOCUS_PROMPT = """
Depth mode ends early when the active Region is saturated or seabed: the next round is breadth.
A Region marked closed has used its share of the attempt budget; new plans cannot use it, so
propose a different mechanism instead of restating it.
"""
MAX_NAME_WORDS = 6
GRAMMAR_SOURCE = "baseline_grammar"


def mechanism_status(values, sea_level, probe_attempts):
    """One Region's residual information from its attempts in order (None = unmeasured).

    The single definition of map states, shared by the method and the read-only map view.
    """
    if not values:
        return None
    measured = [value for value in values if value is not None]
    best = max(measured, default=None)
    recent = [value or 0.0 for value in values[-probe_attempts:]]
    if len(values) < probe_attempts:
        status = "probing"
    elif best is None or best < sea_level:
        status = "seabed"
    elif len(values) >= 2 * probe_attempts and max(recent) < sea_level:
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
        "sea_level": sea_level,
        "probe_attempts": probe_attempts,
    }


@lru_cache(maxsize=4096)
def expression_band(expression: str, frequency: str, bands=SCALE_BANDS) -> str | None:
    """Feature-span band of a candidate's DSL text; None when it does not parse."""
    try:
        return scale_band(feature_span_minutes(parse(expression), frequency), bands)
    except (ValueError, TypeError, KeyError):
        return None


def scale_status(rows, sea_level, probe_attempts):
    """Per-band mechanism_status from (band, residual) pairs in attempt order."""
    return {
        name: mechanism_status(
            [value for band, value in rows if band == name], sea_level, probe_attempts
        )
        or {"attempts": 0}
        for name, _ in SCALE_BANDS
    }


@dataclass(frozen=True)
class V2SyncConfig(V1Config):
    sea_level: float = 0.005
    probe_attempts: int = 5
    scale_statistics: bool = False
    mechanism_regions: bool = False
    adaptive_depth: bool = False
    region_budget_share: float | None = None

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
        if type(self.scale_statistics) is not bool:
            raise ValueError("scale_statistics must be a boolean")
        if type(self.mechanism_regions) is not bool:
            raise ValueError("mechanism_regions must be a boolean")
        if type(self.adaptive_depth) is not bool:
            raise ValueError("adaptive_depth must be a boolean")
        share = self.region_budget_share
        if share is not None and (
            isinstance(share, bool)
            or not isinstance(share, int | float)
            or not math.isfinite(share)
            or not 0 < share <= 1
        ):
            raise ValueError("region_budget_share must be in (0, 1]")


class V2SyncSearch(V1Search):
    @property
    def explorer_prompt(self):
        return (
            MECHANISM_PROMPT
            + (SCALE_PROMPT if self.config.scale_statistics else "")
            + (MECHANISM_REGION_PROMPT if self.config.mechanism_regions else "")
            + (FOCUS_PROMPT if self._focus_rules else "")
        )

    @property
    def _focus_rules(self):
        return self.config.adaptive_depth or self.config.region_budget_share is not None

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or V2SyncConfig(), models=models)

    @property
    def configuration(self):
        config = super().configuration
        for name in (
            "scale_statistics",
            "mechanism_regions",
            "adaptive_depth",
            "region_budget_share",
        ):
            if not config.get(name):
                config.pop(name, None)  # Runs without the option keep their recorded config.
        return config

    def _mechanism_name(self, raw, catalog):
        name = raw.get("name") if isinstance(raw, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("new_region.name must be a short mechanism name")
        name = " ".join(name.split())
        if len(name.split()) > MAX_NAME_WORDS or len(name) > 60:
            raise ValueError(f"new_region.name must have at most {MAX_NAME_WORDS} words")
        taken = next(
            (r["id"] for r in catalog.values() if r.get("name", "").casefold() == name.casefold()),
            None,
        )
        if taken is not None:
            raise ValueError(f"name {name!r} already names Region {taken}; use its region_id")
        return name

    def _closed(self, region_id):
        """A Region that used region_budget_share of the attempt budget takes no new plans."""
        share = self.config.region_budget_share
        if share is None or not self.attempts_total or region_id is None:
            return False
        used = sum(r.get("region_id") == region_id for r in self.factor_trial_records)
        return used >= math.ceil(share * self.attempts_total)

    def _mechanism_region(self, region_id, catalog):
        if (
            self.config.mechanism_regions
            and catalog.get(region_id, {}).get("source") == GRAMMAR_SOURCE
        ):
            raise ValueError(
                f"{region_id} is a grammar Region kept as reference; choose a mechanism Region "
                "or propose new_region with a name"
            )
        if self._closed(region_id):
            raise ValueError(
                f"{region_id} is closed: it used its share of the attempt budget; propose a "
                "different mechanism"
            )

    def _new_round(self, context, index):
        state = super()._new_round(context, index)
        if self.config.adaptive_depth and state["mode"] == "depth":
            active = self.active_region_id
            information = self._residual_information(active) if active else None
            status = information and information["status"]
            if status in {"saturated", "seabed"} or self._closed(active):
                # Digging on in an exhausted Region spends budget that new mechanisms need.
                state["mode"] = "breadth"
                state["mode_reason"] = f"active Region {status or 'closed'}"
        return state

    def _validate_proposals(self, raw, catalog, available_refs):
        if not (self.config.mechanism_regions or self._focus_rules):
            return super()._validate_proposals(raw, catalog, available_refs)
        names = []
        for proposal in raw.get("proposals") or []:
            if not isinstance(proposal, dict):
                break  # The shared validation reports malformed proposals.
            self._mechanism_region(proposal.get("region_id"), catalog)
            new_region = proposal.get("new_region")
            names.append(
                self._mechanism_name(new_region, catalog)
                if new_region is not None and self.config.mechanism_regions
                else None
            )
        if len({n.casefold() for n in names if n}) != sum(1 for n in names if n):
            raise ValueError("each new mechanism Region needs a distinct name")
        batch = super()._validate_proposals(raw, catalog, available_refs)
        for item, name in zip(batch["proposals"], names, strict=True):
            if name is not None:
                item["new_region"]["name"] = name
        return batch

    def _validate_exploration(self, raw, catalog, available_evidence_refs=()):
        if not (self.config.mechanism_regions or self._focus_rules):
            return super()._validate_exploration(raw, catalog, available_evidence_refs)
        new_region = raw.get("new_region")
        self._mechanism_region(raw.get("region_id"), catalog)
        name = None
        if (
            self.config.mechanism_regions
            and raw.get("region_id") not in catalog
            and isinstance(new_region, dict)
        ):
            name = self._mechanism_name(new_region, catalog)
        known = set(self.proposed_regions)
        exploration = super()._validate_exploration(raw, catalog, available_evidence_refs)
        region_id = exploration["region_id"]
        # Restating a grammar or closed Region's hypothesis is no escape.
        self._mechanism_region(region_id, catalog)
        if name is not None and region_id not in known:
            self.proposed_regions[region_id]["name"] = name
        return exploration

    def _explorer_atlas(self, snapshot, trials=None):
        atlas = super()._explorer_atlas(snapshot, trials)
        if self.config.mechanism_regions:
            for region in atlas["regions"]:
                if region.get("source") == GRAMMAR_SOURCE:
                    region["reference_only"] = True
                elif name := self.proposed_regions.get(region["id"], {}).get("name"):
                    region["name"] = name
        for region in atlas["regions"]:
            if self._closed(region["id"]):
                region["closed"] = True
        return atlas

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
        rows = [trials[r["factor_trial_index"]] for r in records]
        values = [row.get("residual_ic") for row in rows]
        information = mechanism_status(values, self.config.sea_level, self.config.probe_attempts)
        if self.config.scale_statistics:
            frequency = self.context.get("frequency")
            bands = [expression_band(row["candidate"]["expression"], frequency) for row in rows]
            information["by_scale"] = scale_status(
                list(zip(bands, values, strict=True)),
                self.config.sea_level,
                self.config.probe_attempts,
            )
        return information

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


ASYNC_STATE_VERSION = "atlas_mechanism_v2_async"


@dataclass(frozen=True)
class V2AsyncConfig(V2SyncConfig, V1AsyncConfig):
    """V2 Sync settings plus V1 Async's parallel_plans."""


class V2AsyncSearch(V2SyncSearch, V1AsyncSearch):
    """V2 mechanism rewards and maps on V1 Async's concurrent plans.

    V2 Sync supplies the prompt, Region score and evidence, scale and mechanism rules; V1 Async
    supplies concurrent Explorer slots, plan groups and model I/O. Only the checkpoint version
    is its own.
    """

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or V2AsyncConfig(), models=models)

    def dump_state(self):
        return {**V1AsyncSearch.dump_state(self), "version": ASYNC_STATE_VERSION}

    def load_state(self, state):
        if state.get("version") != ASYNC_STATE_VERSION:
            raise ValueError("V2 Async requires its own checkpoint")
        V1AsyncSearch.load_state(self, {**state, "version": "atlas_explorer_v1_async"})
