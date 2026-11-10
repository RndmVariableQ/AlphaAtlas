"""AlphaAtlas V3 Async: V2 Async with admission-based map states per mechanism Region.

Changes from V2 Async, each answering a measured V2 failure:
- Region score is the summed residual_ic of admitted factors per attempt; rejected attempts
  add zero instead of signed Valid noise.
- Map states are judged per Region by platform admission, not by the best single residual.
- No feature-span bands (scale_bands "none"). With three bands, each with its own probe budget
  and an untested band counted as a new direction, V3 Luna spent 16-29% of attempts on spans
  over 20 days (V1 Luna 3%); across fold2 V1/V3 runs, factors looking back over 5 days were
  43% of attempts with Test IC 0.0039 against 0.0059 under one day. Runs recorded with the
  intraday_1d_20d bands resume on them with their prompt.
- Depth continues while the active Region is open; the Explorer may submit a single
  direction instead of reporting unresolved for want of a second.
- Fixed protocol: named mechanism Regions and status-driven depth are always on, with no
  Region budget cap.
- Optional Region availability (region_admission_limit, region_plan_limit): one platform rule
  says whether a Region may take a new plan. In V3 runs one Region took 53-66% of admissions:
  late depth rounds kept digging the active Region while each admission kept it open, and
  concurrent plans dug it together. Availability is shown on the map, checked on proposals,
  gates depth and releases the annealing incumbent, so these parts cannot disagree.
- The Explorer cannot report unresolved: with available Regions and new mechanisms the map is
  never exhausted. Haiku runs stopped at 437-912 of 1000 attempts on unresolved replies that
  cited busy Regions or admitted unscreened ones; such a reply is refused in the dialogue.
- The Explorer prompt states Region choice once (breadth preference, depth rule, availability);
  the inherited V1 lines on revisiting and breadth are removed so the parts cannot conflict.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from alpha_atlas.methods.v1 import EXPLORER_PROMPT
from alpha_atlas.methods.v1_async import V1AsyncSearch
from alpha_atlas.methods.v2_sync import (
    MECHANISM_REGION_PROMPT,
    V2AsyncConfig,
    V2AsyncSearch,
    expression_band,
)
from alpha_atlas.timeframes import (
    SCALE_BANDS,
    TRADING_DAY_MINUTES,
    V3_SCALE_BANDS,
    _bar_minutes,
)

STATE_VERSION = "atlas_mechanism_v3_async"
CLOSED = {"seabed", "saturated"}
REGION_SCHEME = "none"
BAND_SCHEME = "intraday_1d_20d"
# Runs started before the scheme was recorded used the V2 bands, and a Region closed only when
# every band was closed; the map view reproduces them from their recorded configuration.
LEGACY_SCHEME = "legacy_1h_1d"
SCHEMES = {REGION_SCHEME: (), BAND_SCHEME: V3_SCALE_BANDS, LEGACY_SCHEME: SCALE_BANDS}


def _swap(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _one_to_four(text, old):
    return _swap(text, old, old.replace("two to four", "one to four"))


# V3 states Region choice once, below, so the V1 lines on revisiting and breadth are removed.
_BASE = _swap(
    _swap(
        _swap(
            _swap(
                _swap(
                    _one_to_four(
                        _one_to_four(EXPLORER_PROMPT, "Submit two to four directions."),
                        "[two to four directions]",
                    ),
                    "Agent (V1).",
                    "Agent.",
                ),
                "in one of three formats:",
                "in one of two formats:",
            ),
            '3. {"summary":"conclusion from the evidence","unresolved":"specific evidence gap"}\n'
            "   Use unresolved when neither a new question nor a revisit supports an executable "
            "direction.\n",
            "",
        ),
        "Prioritize untested mechanisms and information sources while coverage is sparse.\n"
        "As coverage grows, revisit promising directions to resolve remaining questions.\n",
        "",
    ),
    " In breadth mode compare\nother Regions and reference definitions using existing fields "
    "and tools.",
    "",
)
_BREADTH = """compare the whole map. Prefer untested mechanisms, Regions with few admitted
factors and mechanisms weakly related to the admitted library; revisit a Region only when its
evidence leaves a distinct, untested question.
"""
DEPTH_RULE = (
    "In breadth mode, "
    + _BREADTH
    + """In depth mode, test the active Region in one of its probing or land bands; the platform
chooses the active Region by score. When all of its tested bands are closed, the next round is
breadth.
"""
)
# With selection_policy set every round is breadth: no mode, active Region or annealing switch.
BREADTH_RULE = "Every round is breadth; " + _BREADTH

_V3_TEMPLATE = (
    _BASE
    + """
V3 map semantics. Treat each Region as a market mechanism: who acts or is constrained, why
that creates predictability, and which footprint it leaves in the available fields.
Each evaluated trial reports residual_ic, the Valid IC left after removing what the current
library explains, and library_r2, the share of the factor the library explains. High
library_r2 with low residual_ic is a rediscovery of known information. A single residual_ic
is noisy; only admission is decided by the platform.
residual_information.by_scale splits a Region's attempts by feature span, the approximate
trading time between the oldest and newest bar a factor reads: intraday (up to one trading
day), 1d_to_20d and over_20d. Each band has attempts, admitted factors, admitted residual_ic
and a status, decided in this order: probing (fewer than probe_attempts attempts, even with
admissions), seabed (no admitted factor with positive residual_ic), saturated (at least twice
probe_attempts attempts and none such in the last probe_attempts) or land. Only tested bands
decide a Region: it is land when any tested band is land, seabed or saturated when every tested
band is closed, and probing otherwise. Its score is the admitted residual_ic per attempt, with
rejected attempts adding zero; use it to compare Regions.
A mechanism can differ across spans: a closed band says nothing about an untested band, and
testing a mechanism in an untested band is a distinct direction when the mechanism predicts an
effect at that span.
{depth}In prediction, name the admitted factors or Regions the new factors should be weakly related
to, and the result after a few attempts that would refute the mechanism.
Always submit at least one direction: the map is never exhausted. When your current direction is
blocked (its Regions are busy, full or closed, or it needs data or tools that are missing), search
for a new direction: choose another available Region or propose a new mechanism as new_region.
"""
    + MECHANISM_REGION_PROMPT
)
BAND_PROMPT = _V3_TEMPLATE.replace("{depth}", DEPTH_RULE)

# Without bands a Region is judged on all its attempts; the band paragraphs are removed.
_REGION_TEMPLATE = _swap(
    _swap(
        _swap(
            _V3_TEMPLATE,
            """residual_information.by_scale splits a Region's attempts by feature span, the approximate
trading time between the oldest and newest bar a factor reads: intraday (up to one trading
day), 1d_to_20d and over_20d. Each band has attempts,""",
            "Each Region's residual_information has attempts,",
        ),
        """ Only tested bands
decide a Region: it is land when any tested band is land, seabed or saturated when every tested
band is closed, and probing otherwise.""",
        "",
    ),
    """A mechanism can differ across spans: a closed band says nothing about an untested band, and
testing a mechanism in an untested band is a distinct direction when the mechanism predicts an
effect at that span.
""",
    "",
)
REGION_DEPTH_RULE = _swap(
    DEPTH_RULE,
    """test the active Region in one of its probing or land bands; the platform
chooses the active Region by score. When all of its tested bands are closed, the next round is
breadth.""",
    """test the active Region while it is probing or land; the platform chooses
the active Region by score. When it is closed, the next round is breadth.""",
)
V3_PROMPT = _REGION_TEMPLATE.replace("{depth}", REGION_DEPTH_RULE)
TEMPLATES = {
    REGION_SCHEME: (_REGION_TEMPLATE, REGION_DEPTH_RULE),
    BAND_SCHEME: (_V3_TEMPLATE, DEPTH_RULE),
}

V3_REMINDER = "Submit one to four directions with scores and references.\n"
NO_UNRESOLVED = (
    "unresolved is not accepted: the map is never exhausted. Your current direction is blocked, "
    "so search for a new one: choose another available Region or propose a new mechanism as "
    "new_region, and submit it."
)
AVAILABILITY_PROMPT = """
Region availability is decided by the platform and shown on every mechanism Region as
available. available false means no new plan may use that Region now, and closed_reason says
why: full (it holds the per-Region limit of admitted factors) or busy (the per-Region limit of
plans is already running in it). Do not submit an unavailable region_id or restate its
mechanism as new_region. Directions in one batch use different Regions.
"""
_AVAILABILITY_DEPTH = """Depth mode is assigned only when the active Region is available;
otherwise the round is breadth.
"""


def band_status(rows, probe_attempts):
    """One band's state from (residual_ic, accepted) attempts in order (None = unmeasured).

    The single definition of V3 map states, shared by the method and the read-only map view.
    """
    adds = [bool(accepted) and (residual or 0.0) > 0 for residual, accepted in rows]
    admitted = sum(bool(accepted) for _, accepted in rows)
    admitted_residual = sum(residual or 0.0 for residual, accepted in rows if accepted)
    if len(rows) < probe_attempts:
        status = "probing"
    elif not any(adds):
        status = "seabed"
    elif len(rows) >= 2 * probe_attempts and not any(adds[-probe_attempts:]):
        status = "saturated"
    else:
        status = "land"
    return {
        "attempts": len(rows),
        "admitted": admitted,
        "admitted_residual_ic": admitted_residual,
        "admitted_residual_per_attempt": admitted_residual / len(rows) if rows else 0.0,
        "status": status,
    }


def region_status(rows, probe_attempts, scheme=REGION_SCHEME):
    """Region information from (band, residual_ic, accepted) attempts; None when untried.

    Without bands the Region's own attempts decide. Under band schemes untested bands are
    reported but do not keep a Region open, except under the legacy scheme.
    """
    if not rows:
        return None
    total = band_status([(r, a) for _, r, a in rows], probe_attempts)
    if not SCHEMES[scheme]:
        return {**total, "probe_attempts": probe_attempts}
    by_scale = {
        name: band_status([(r, a) for band, r, a in rows if band == name], probe_attempts)
        for name, _ in SCHEMES[scheme]
    }
    statuses = [
        band["status"] for band in by_scale.values() if band["attempts"] or scheme == LEGACY_SCHEME
    ]
    if "land" in statuses:
        status = "land"
    elif all(s in CLOSED for s in statuses):
        status = "saturated" if "saturated" in statuses else "seabed"
    else:
        status = "probing"
    return {
        **{k: total[k] for k in ("attempts", "admitted", "admitted_residual_ic")},
        "admitted_residual_per_attempt": total["admitted_residual_per_attempt"],
        "status": status,
        "probe_attempts": probe_attempts,
        "by_scale": by_scale,
    }


@dataclass(frozen=True)
class V3AsyncConfig(V2AsyncConfig):
    # 20 per Region (per band under band schemes): in V3 Haiku (600bd5bbd1) volume and
    # open-interest bands closed after 10 rejected attempts, while V1 needed far more attempts
    # before admitting such factors.
    probe_attempts: int = 20
    scale_statistics: bool = True
    mechanism_regions: bool = True
    adaptive_depth: bool = True
    # Sent as the chat request's reasoning_effort only when set; None keeps the service default.
    reasoning_effort: str | None = None
    scale_bands: str = REGION_SCHEME
    # None keeps runs started before Region availability on their recorded rules.
    region_admission_limit: int | None = None
    region_plan_limit: int | None = None

    def __post_init__(self):
        super().__post_init__()
        for name in ("region_admission_limit", "region_plan_limit"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer")
        if self.reasoning_effort not in (None, "low", "medium", "high", "max"):
            raise ValueError("reasoning_effort must be low, medium, high or max")
        if not (self.scale_statistics and self.mechanism_regions and self.adaptive_depth):
            raise ValueError("v3_async fixes scale_statistics, mechanism_regions, adaptive_depth")
        if self.region_budget_share is not None:
            raise ValueError("v3_async has no region_budget_share")
        if self.scale_bands not in TEMPLATES:
            raise ValueError(f"v3_async uses scale_bands {REGION_SCHEME} or {BAND_SCHEME}")


class V3AsyncSearch(V2AsyncSearch):
    explorer_reminder = V3_REMINDER
    unresolved_refusal = NO_UNRESOLVED
    min_proposals = 1

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or V3AsyncConfig(), models=models)

    @property
    def configuration(self):
        config = super().configuration
        if config.get("reasoning_effort") is None:
            config.pop("reasoning_effort", None)  # Runs at the service default keep their record.
        for name in ("region_admission_limit", "region_plan_limit"):
            if config.get(name) is None:
                config.pop(name, None)
        return config

    @property
    def _availability_rules(self):
        return (
            self.config.region_admission_limit is not None
            or self.config.region_plan_limit is not None
        )

    @property
    def explorer_prompt(self):
        breadth_only = self.config.selection_policy is not None
        template, depth = TEMPLATES[self.config.scale_bands]
        prompt = template.replace("{depth}", BREADTH_RULE if breadth_only else depth)
        if self._availability_rules:
            prompt += AVAILABILITY_PROMPT + ("" if breadth_only else _AVAILABILITY_DEPTH)
        frequency = (self.context or {}).get("frequency")
        if not frequency:
            return prompt
        minutes = _bar_minutes(frequency)
        return prompt + (
            f"\nBars count trading time, not clock time: at the base frequency {frequency}, one "
            f"hour is {60 / minutes:g} bars and one trading day ({TRADING_DAY_MINUTES} trading "
            f"minutes) is {TRADING_DAY_MINUTES / minutes:g} bars.\n"
        )

    def _running_plans(self, region_id):
        return [
            key
            for key, group in self.groups.items()
            if ((group["research_plan_state"] or {}).get("exploration") or {}).get("region_id")
            == region_id
        ]

    def _unavailable(self, region_id, information=None):
        """Why no new plan may use this Region now, or None: the single availability rule."""
        limit = self.config.region_admission_limit
        if limit is not None:
            information = information or self._residual_information(region_id)
            if information and information["admitted"] >= limit:
                return f"full: {information['admitted']} admitted factors, limit {limit}"
        limit = self.config.region_plan_limit
        if limit is not None:
            running = self._running_plans(region_id)
            if len(running) >= limit:
                return "busy: plan " + ", ".join(f"#{key}" for key in running) + " running"
        return None

    def _explorer_atlas(self, snapshot, trials=None):
        atlas = super()._explorer_atlas(snapshot, trials)
        if self._availability_rules:
            for region in atlas["regions"]:
                if region.get("reference_only"):
                    continue
                information = region.get("statistics", {}).get("residual_information")
                reason = self._unavailable(region["id"], information)
                region["available"] = reason is None
                if reason:
                    region["closed_reason"] = reason
        return atlas

    def _validate_proposals(self, raw, catalog, available_refs):
        batch = super()._validate_proposals(raw, catalog, available_refs)
        if self._availability_rules:
            chosen = Counter(
                item["region_id"] for item in batch["proposals"] if "region_id" in item
            )
            for region_id, count in chosen.items():
                if reason := self._unavailable(region_id):
                    raise ValueError(
                        f"{region_id} is unavailable ({reason}); choose an available Region or "
                        "propose a new mechanism"
                    )
                if count > 1:
                    raise ValueError("directions in one batch must use different Regions")
        return batch

    def _new_round(self, context, index):
        state = super()._new_round(context, index)
        if self._availability_rules and state["mode"] == "depth":
            active = self.active_region_id
            reason = "missing" if active is None else self._unavailable(active)
            if not reason and self.config.region_plan_limit is not None:
                # Depth targets only the active Region; an Explorer already in depth claims it.
                if any(
                    e["research_plan_state"]["mode"] == "depth" for e in self.explorers.values()
                ):
                    reason = "claimed by another Explorer"
            if reason:
                state["mode"] = "breadth"
                state["mode_reason"] = f"active Region {reason}"
        return state

    def _finish_round(self, *, update_region=True):
        active = self.active_region_id
        annealing = self.config.selection_policy is None
        if update_region and annealing and self._availability_rules and active is not None:
            information = self._residual_information(active)
            limit = self.config.region_admission_limit
            if information and (
                information["status"] in CLOSED
                or (limit is not None and information["admitted"] >= limit)
            ):
                # A closed or full incumbent no longer holds depth; the finished plan's Region
                # takes the slot instead of losing a Metropolis comparison to a stale score.
                self.active_region_id = None
        super()._finish_round(update_region=update_region)

    def dump_state(self):
        return {**V1AsyncSearch.dump_state(self), "version": STATE_VERSION}

    def load_state(self, state):
        if state.get("version") != STATE_VERSION:
            raise ValueError("V3 Async requires its own checkpoint")
        V1AsyncSearch.load_state(self, {**state, "version": "atlas_explorer_v1_async"})

    def _residual_information(self, region_id, trials=None):
        """Derived from atomic trials each time; nothing is stored beside them."""
        records = [r for r in self.factor_trial_records if r.get("region_id") == region_id]
        if not records:
            return None
        trials = self._trial_results(records) if trials is None else trials
        frequency = self.context.get("frequency")
        bands = SCHEMES[self.config.scale_bands]
        rows = []
        for record in records:
            trial = trials[record["factor_trial_index"]]
            expression = trial["candidate"]["expression"]
            band = expression_band(expression, frequency, bands) if bands else None
            rows.append((band, trial.get("residual_ic"), trial.get("accepted")))
        return region_status(rows, self.config.probe_attempts, self.config.scale_bands)

    def _region_score(self, region_id):
        # Metropolis compares admitted residual information per attempt in sea-level units.
        information = self._residual_information(region_id)
        if information is None:
            return None
        return information["admitted_residual_per_attempt"] / self.config.sea_level
