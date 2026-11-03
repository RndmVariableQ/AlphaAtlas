"""V1 Explorer adapted from ChaoticTrader_Future; shared three-agent annealing execution.

Source modules, provenance and intentional differences are documented in docs/methods/v1.md.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from alpha_atlas.methods.annealing import AnnealingLLMConfig, AnnealingLLMSearch
from alpha_atlas.methods.models import parse_json_object
from alpha_atlas.session import READ_ONLY_QUERY_HELP, READ_ONLY_TOOLS
from alpha_atlas.terminal import terminal_progress

REVIEW = {
    "importance": (
        30,
        "importance",
        "Research value: 1 local detail; 3 affects one research line; 5 key unknown",
    ),
    "decision": (
        30,
        "information_gain",
        "Discriminating value: 1 repeated observation; 3 separates two explanations; "
        "5 changes research decisions",
    ),
    "difference": (
        20,
        "difference",
        "Research difference: 1 parameter or condition variation; "
        "3 new prediction within a known mechanism; 5 untested mechanism or information source",
    ),
    "readiness": (
        20,
        "readiness",
        "Readiness: 1 missing data; 3 needs added computing capability; "
        "5 existing capability suffices",
    ),
}
CONTENT_FIELDS = ("objective", "hypothesis", "conditions", "prediction", "alternative")

EXPLORER_PROMPT = (
    """You are the Atlas Explorer Agent (V1).
Identify unknowns worth resolving. Propose falsifiable questions, mechanism hypotheses,
competing explanations and observable predictions.

Read research_context, the full Region map, previous_directions and attached plan results.
previous_directions indexes completed research; active_proposals identifies ongoing plans.
Prioritize untested mechanisms and information sources while coverage is sparse.
As coverage grows, revisit promising directions to resolve remaining questions.
State the evidence gap and the next test that distinguishes competing explanations.
Read atlas.blocked_plans for missing capabilities when choosing the next test.
An unresolved question in one Region does not exhaust the map. In breadth mode compare
other Regions and reference definitions using existing fields and tools. Do not ask the
Analyst or Miner to add unavailable data, tools or permissions.
Proposals must differ from each other and from ongoing plans in their question and prediction.
Changes to names, windows, signs or timeframes alone do not make a new direction.
Ask what happens without the condition, in the opposite direction, or in different states.
Propose questions answerable with the fields, operators and evaluation feedback in
research_context. The Analyst chooses the measurement route, fields, feature timeframes,
windows, controls and thresholds. The Miner implements factor expressions; the platform
evaluates them. In review.readiness, explain how the available measurements test the prediction.

Ground claims in the supplied formulas, measurements and query results. Distinguish missing
evidence, failed measurements and untested questions. Read main measurements and controls
together; a control's IC does not establish the main hypothesis. completed means execution
ended, not that every planned test ran.
Region Train summaries use absolute values; full trial Train values retain their original sign.
Valid is already aligned to the Train sign. Consider coverage, distributions and counterexamples.
atlas.plan_results contains original plans and all submitted trials, including rejected and
failed candidates. Their candidate.expression and evaluation are full, citable evidence.
An indexed trial ref can be included in a draft to request its plan. Read the complete plan
supplied in dialogue.sources, then confirm or revise the proposals in another reply.

Use the public library to resolve gaps in the attached evidence. library_search supports
space-separated AND keywords, offset/limit paging and source run/reference/all.
library_list and library_stats are indexes; library_search and library_get return full entries.
library_get takes an actual factor_id, not trial:N, and returns admitted members only.
A null result means no admitted member, not a missing trial definition.
Query results supply citable refs in sources. Reference definitions have no measured performance.

Submit two to four directions. Use an existing Region where it applies.
Give four integer scores from 1 to 5, each with a reason.

Return one JSON object in one of three formats:
1. {"tool":"library_search","arguments":{"query":"keywords","source":"all","limit":3}}
"""
    + READ_ONLY_QUERY_HELP
    + """
2. {"summary":"conclusion from the evidence","proposals":[two to four directions]}
   Each direction contains objective, hypothesis, conditions, prediction, alternative,
   references, review, and either region_id or new_region (hypothesis, continent_ids).
   references is an array of ref values copied from supplied trials or query sources;
   use [] when none are available.
   review: {"scores":{"importance":3,"decision":3,"difference":3,"readiness":3},
   "importance":"reason","information_gain":"reason","difference":"reason","readiness":"reason"}
3. {"summary":"conclusion from the evidence","unresolved":"specific evidence gap"}
   Use unresolved when neither a new question nor a revisit supports an executable direction.
"""
)

EXPLORER_REMINDER = """Submit two to four directions with scores and references, or return
unresolved with the evidence gap.
"""

EXPLORER_BREADTH_REDIRECT = """The current Region cannot support the proposed depth tests.
Switch to breadth across the supplied map and reference definitions. Choose another executable
question using only existing fields, operators and tools; do not request new capabilities.
Keep this Region's evidence gap in the dialogue. Return unresolved only if this broader review
also cannot justify an executable direction.
"""


@dataclass(frozen=True)
class V1Config(AnnealingLLMConfig):
    max_dialogue_rounds: int = 40
    selection_policy: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.selection_policy not in (None, "greedy", "random", "boltzmann"):
            raise ValueError("selection_policy must be greedy, random or boltzmann")


class ExplorerUnresolved(ValueError):
    """The bounded Explorer dialogue cannot supply a complete research direction."""


class ExplorerDialogueLimitError(ValueError):
    """The Explorer used its dialogue budget without a validated proposal batch."""


def _unresolved(records, limit):
    return bool(
        records
        and "unresolved" in records[-1]
        and not records[-1].get("redirected_to_breadth")
        and (
            len(records) >= limit
            or any(
                "unresolved" in record and not record.get("redirected_to_breadth")
                for record in records[:-1]
            )
        )
    )


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _normalize(value):
    return re.sub(r"[\W_]+", "", value.casefold())


def _source_refs(source):
    return {source["ref"]} | {row["ref"] for row in source["value"].get("trials", [])}


def _review(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("scores"), dict):
        raise ValueError("review.scores must contain four ratings")
    scores = raw["scores"]
    if set(scores) != set(REVIEW):
        raise ValueError("review.scores requires importance, decision, difference, readiness")
    reasons = {}
    for name, (_, reason, _) in REVIEW.items():
        if type(scores[name]) is not int or not 1 <= scores[name] <= 5:
            raise ValueError(f"review.scores.{name} must be an integer from 1 to 5")
        reasons[reason] = _text(raw.get(reason), f"review.{reason}")
    return {"scores": dict(scores), **reasons}


class V1Search(AnnealingLLMSearch):
    """Explorer proposals share one selector; absent policy retains legacy Region annealing."""

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or V1Config(), models=models)

    @property
    def configuration(self):
        config = super().configuration
        if config.get("selection_policy") is None:
            config.pop("selection_policy", None)
        return config

    def _new_round(self, context, index):
        state = super()._new_round(context, index)
        if getattr(self.config, "selection_policy", None) is not None:
            state["mode"] = "breadth"
        return state

    def _select_proposals(self, proposals, count, temperature):
        policy = getattr(self.config, "selection_policy", None)
        remaining = list(range(len(proposals)))
        count = min(count, len(remaining))
        if policy in (None, "greedy"):
            return sorted(remaining, key=lambda i: -proposals[i]["score"])[:count]
        if policy == "random":
            return self.rng.sample(remaining, count)
        selected = []
        for _ in range(count):
            maximum = max(proposals[i]["score"] for i in remaining)
            weights = [
                math.exp((proposals[i]["score"] - maximum) / 100 / temperature) for i in remaining
            ]
            index = self.rng.choices(remaining, weights=weights, k=1)[0]
            selected.append(index)
            remaining.remove(index)
        return selected

    def dump_state(self):
        return {**super().dump_state(), "version": "atlas_explorer_v1"}

    def load_state(self, state):
        if state.get("version") != "atlas_explorer_v1":
            raise ValueError("V1 requires its own Explorer checkpoint")
        super().load_state({**state, "version": 1})

    def _plan_results(self, rounds=None, trials=None):
        records = [
            record
            for record in self.factor_trial_records
            if rounds is None or record["research_plan_index"] in rounds
        ]
        trials = self._trial_results(records) if trials is None else trials
        plans = {}
        for record in records:
            index = record["research_plan_index"]
            plan = plans.setdefault(
                index,
                {
                    "research_plan_index": index,
                    "region_id": record["region_id"],
                    "direction": record["direction"],
                    "plan": record["plan"],
                    "completed": False,
                    "trials": [],
                },
            )
            plan["completed"] |= "explorer_proposals" in record or "annealing_result" in record
            evaluation = trials[record["factor_trial_index"]]
            plan["trials"].append(
                {
                    "ref": f"trial:{record['factor_trial_index']}",
                    "factor_id": evaluation.get("factor_id"),
                    "candidate": evaluation["candidate"],
                    "admitted": bool(evaluation["accepted"]),
                    "evaluation": {
                        **{
                            key: evaluation.get(key)
                            for key in (
                                "status",
                                "direction",
                                "coverage",
                                "reason",
                                "failure_reason",
                            )
                        },
                        "metrics": [
                            metric
                            for metric in evaluation.get("metrics", [])
                            if metric.get("split") in {"train", "val", "val_raw"}
                        ],
                    },
                }
            )
        for item in self.blocked_plans:
            if item["research_plan_index"] in plans:
                plan = plans[item["research_plan_index"]]
                plan["completed"] = True
                plan["blocked"] = item["plan"]["blocked"]
        return list(plans.values())

    def _explorer_atlas(self, snapshot, trials=None):
        trials = self._trial_results() if trials is None else trials
        atlas = super()._explorer_atlas(snapshot, trials)
        trial_rounds = {
            f"trial:{record['factor_trial_index']}": record["research_plan_index"]
            for record in self.factor_trial_records
        }
        rounds = {
            trial_rounds[example["ref"]]
            for region in atlas["regions"]
            for name in ("supporting_trials", "opposing_trials")
            for example in region["statistics"][name]
        }
        recent_rounds = set(
            list(
                dict.fromkeys(
                    record["research_plan_index"]
                    for record in reversed(self.factor_trial_records)
                    if "explorer_proposals" in record or "annealing_result" in record
                )
            )[: getattr(self.config, "parallel_plans", 1)]
        )
        atlas["plan_results"] = self._plan_results(rounds | recent_rounds, trials)
        return self._compact_explorer_atlas(atlas)

    def _compact_explorer_atlas(self, atlas, references=()):
        plans = atlas.get("plan_results", [])
        recent = sorted(
            (plan for plan in plans if plan["completed"]),
            key=lambda plan: max(
                (int(row["ref"].split(":")[1]) for row in plan["trials"]), default=0
            ),
        )[-getattr(self.config, "parallel_plans", 1) :]
        recent_indices = {
            plan.get("research_plan_index", plan.get("round_index")) for plan in recent
        }
        compact = dict(atlas)
        if "plan_results" in atlas:
            compact["plan_results"] = [
                plan
                for plan in plans
                if not plan["completed"]
                or plan.get("research_plan_index", plan.get("round_index")) in recent_indices
                or any(row["ref"] in references for row in plan["trials"])
            ]
        if "blocked_plans" in atlas:
            compact["blocked_plans"] = [
                {
                    **item,
                    "plan": {
                        key: value
                        for key, value in item["plan"].items()
                        if key in {"region_id", "direction", "blocked"}
                    },
                }
                for item in atlas.get("blocked_plans", [])
            ]
        return compact

    def _query(self, raw, records):
        name, arguments = raw.get("tool"), raw.get("arguments", {})
        if not isinstance(name, str) or name not in READ_ONLY_TOOLS:
            raise ValueError(
                "Explorer may only use the five shared read-only tools. " + READ_ONLY_QUERY_HELP
            )
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        if any(
            record.get("query") == {"tool": name, "arguments": arguments} for record in records[:-1]
        ):
            raise ValueError("query already answered; use its saved result")
        result = self._session.query(name, arguments)
        # Search/Get return complete definitions. List/Stats are indexes, not evidence.
        rows = (
            result["results"]
            if name == "library_search"
            else [result]
            if name == "library_get" and result is not None
            else []
        )
        sources = [
            {
                "ref": f"library:{self.research_plan_state['research_plan_index']}:{len(records)}:{index}",
                "value": row,
            }
            for index, row in enumerate(rows)
        ]
        return {
            "query": {"tool": name, "arguments": arguments},
            "result": result,
            "sources": sources,
        }

    def _validate_proposals(self, raw, catalog, available_refs):
        summary = _text(raw.get("summary"), "summary")
        proposals = raw.get("proposals")
        if not isinstance(proposals, list) or not 2 <= len(proposals) <= 4:
            raise ValueError("Explorer must submit two to four distinct proposals")
        validated = []
        known_continents = {c for region in catalog.values() for c in region["continent_ids"]}
        for proposal in proposals:
            if not isinstance(proposal, dict):
                raise ValueError("each proposal must be an object")
            item = {key: _text(proposal.get(key), key) for key in CONTENT_FIELDS}
            references = proposal.get("references")
            if (
                not isinstance(references, list)
                or any(not isinstance(ref, str) for ref in references)
                or len(set(references)) != len(references)
                or not set(references) <= available_refs
            ):
                raise ValueError("references must be unique refs to full evidence actually shown")
            region_id, new_region = proposal.get("region_id"), proposal.get("new_region")
            if (region_id is not None) == (new_region is not None):
                raise ValueError("choose exactly one of region_id and new_region")
            if new_region is not None:
                if not isinstance(new_region, dict):
                    raise ValueError("new_region must be an object")
                hypothesis = _text(new_region.get("hypothesis"), "new_region.hypothesis")
                continents = new_region.get("continent_ids")
                if (
                    not isinstance(continents, list)
                    or not continents
                    or any(not isinstance(c, str) or c not in known_continents for c in continents)
                ):
                    raise ValueError("new_region must reference existing continents")
                item["new_region"] = {"hypothesis": hypothesis, "continent_ids": continents}
            elif not isinstance(region_id, str) or region_id not in catalog:
                raise ValueError("region_id must reference the supplied map")
            else:
                item["region_id"] = region_id
            # Renaming the question alone does not make an otherwise identical direction new.
            if any(
                all(_normalize(item[k]) == _normalize(other[k]) for k in CONTENT_FIELDS[1:])
                for other in validated
            ):
                raise ValueError("duplicate proposals; changing only the title is insufficient")
            item["review"] = _review(proposal.get("review"))
            item["references"] = references
            item["score"] = (
                sum(item["review"]["scores"][key] * rule[0] for key, rule in REVIEW.items()) / 5
            )
            validated.append(item)
        return {"summary": summary, "proposals": validated}

    def _explore_round(self, snapshot, catalog, *, select=True):
        state = self.research_plan_state
        records = state["responses"]["explorer"]
        if "explorer_input" not in state:
            trials = self._trial_results()
            atlas = self._explorer_atlas(snapshot, trials)
            state["explorer_input"] = {
                "research_context": self.context,
                "atlas": atlas,
                "mode": state["mode"],
                "active_region": catalog.get(self.active_region_id),
                "active_region_score": self._region_score(self.active_region_id),
                "previous_directions": [
                    {
                        **{
                            key: plan[key]
                            for key in ("research_plan_index", "region_id", "direction")
                        },
                        "factor_trial_refs": [row["ref"] for row in plan["trials"]],
                        "successful_evaluations": sum(
                            row["evaluation"]["status"] == "success" for row in plan["trials"]
                        ),
                        "rejection_reasons": dict(
                            Counter(
                                row["evaluation"]["reason"] or row["evaluation"]["status"]
                                for row in plan["trials"]
                                if not row["admitted"]
                            )
                        ),
                        "admitted": sum(row["admitted"] for row in plan["trials"]),
                    }
                    for plan in self._plan_results(trials=trials)
                    if plan["completed"]
                ],
                "review_rubric": {key: rule[2] for key, rule in REVIEW.items()},
            }
            if getattr(self.config, "selection_policy", None) is not None:
                for key in ("mode", "active_region", "active_region_score"):
                    state["explorer_input"].pop(key, None)
            self._checkpoint()
        atlas = self._compact_explorer_atlas(
            state["explorer_input"]["atlas"],
            {
                ref
                for record in records
                for proposal in record.get("result", {}).get("proposals", [])
                for ref in proposal["references"]
            },
        )
        if atlas != state["explorer_input"]["atlas"]:
            state["explorer_input"] = {**state["explorer_input"], "atlas": atlas}
            self._checkpoint()
        evidence = {
            example["ref"]: example
            for example in [
                *(
                    example
                    for region in atlas["regions"]
                    for name in ("supporting_trials", "opposing_trials")
                    for example in region["statistics"][name]
                ),
                *atlas.get("recent_trials", []),
            ]
            if "candidate" in example and "evaluation" in example
        }
        evidence.update(
            (row["ref"], plan) for plan in atlas.get("plan_results", []) for row in plan["trials"]
        )
        history = {
            ref: item.get("research_plan_index", item.get("round_index"))
            for item in state["explorer_input"].get("previous_directions", [])
            for ref in item.get("factor_trial_refs", item.get("trial_refs", []))
        }
        available_refs = set(evidence)
        while True:
            available_refs.update(
                ref
                for record in records
                for source in record.get("sources", [])
                for ref in _source_refs(source)
            )
            if records and not records[-1].get("processed"):
                record = records[-1]
                try:
                    raw = parse_json_object(record["text"])
                    if sum(key in raw for key in ("tool", "proposals", "unresolved")) != 1:
                        raise ValueError("choose one query, proposal batch or unresolved report")
                    if "tool" in raw:
                        record.update(self._query(raw, records))
                    elif "unresolved" in raw:
                        record["unresolved"] = _text(raw["unresolved"], "unresolved")
                        record["summary"] = _text(raw.get("summary"), "summary")
                    else:
                        batch = self._validate_proposals(
                            raw, catalog, available_refs | history.keys()
                        )
                        missing = {
                            ref
                            for item in batch["proposals"]
                            for ref in item["references"]
                            if ref not in available_refs
                        }
                        if missing:
                            record["sources"] = [
                                {
                                    "ref": next(
                                        row["ref"]
                                        for row in plan["trials"]
                                        if row["ref"] in missing
                                    ),
                                    "value": plan,
                                }
                                for plan in self._plan_results({history[ref] for ref in missing})
                            ]
                            raise ValueError(
                                "Read the complete plans in sources, including measurements and "
                                "controls, then confirm or revise the proposals in another reply."
                            )
                        record["result"] = batch
                except (ValueError, TypeError, KeyError) as exc:
                    record["error"] = str(exc)
                record["processed"] = True
                self._checkpoint()
            if records and "result" in records[-1] and "query" not in records[-1]:
                batch = records[-1]["result"]
                values = dict(evidence)
                for record in records:
                    for source in record.get("sources", []):
                        values.update((ref, source["value"]) for ref in _source_refs(source))
                batch["sources"] = []
                covered = set()
                for item in batch["proposals"]:
                    for ref in item["references"]:
                        if ref not in covered:
                            source = {"ref": ref, "value": values[ref]}
                            batch["sources"].append(source)
                            covered.update(_source_refs(source))
                state["explorer_proposals"] = batch
                if not select:
                    return None
                if "selected" not in batch:
                    batch["selected"] = self._select_proposals(
                        batch["proposals"], 1, state["temperature"]
                    )[0]
                proposal = batch["proposals"][batch["selected"]]
                return self._proposal_exploration(
                    proposal, catalog, available_refs, batch["sources"]
                )
            if records and "unresolved" in records[-1]:
                if state["mode"] == "depth" and len(records) < self.config.max_dialogue_rounds:
                    # This is a local scope change, not another request or a budget reset.
                    for record in records:
                        if "unresolved" in record:
                            record["redirected_to_breadth"] = True
                    state["mode"] = "breadth"
                    state["explorer_input"]["mode"] = "breadth"
                    records[-1]["error"] = EXPLORER_BREADTH_REDIRECT
                    terminal_progress(
                        f"[计划 #{state['research_plan_index']} · Explorer] 深挖受阻，转向广度探索",
                        原因=records[-1]["unresolved"],
                    )
                    self._checkpoint()
                elif _unresolved(records, self.config.max_dialogue_rounds):
                    raise ExplorerUnresolved("V1 Explorer unresolved: " + records[-1]["unresolved"])
                if "error" not in records[-1]:
                    records[-1]["error"] = (
                        "Reassess the closest completed plans for a missing control, condition or "
                        "interaction. Submit two to four directions with a discriminating prediction, "
                        "scores and references, or return summary and unresolved with the evidence gap."
                    )
                    self._checkpoint()
            if len(records) >= self.config.max_dialogue_rounds:
                raise ExplorerDialogueLimitError(
                    f"V1 Explorer dialogue limit exhausted ({len(records)} replies)"
                )
            reminder = EXPLORER_REMINDER if len(records) >= 20 else ""
            self._request_progress(
                "Explorer",
                计划=state["research_plan_index"],
                对话=f"{len(records) + 1}/{self.config.max_dialogue_rounds}",
                模式=state["mode"],
                reminder="优先收敛并提交方向" if len(records) == 20 else None,
            )
            self._start_request("chat")
            payload = {**state["explorer_input"], "dialogue": records}
            # Keep each indexed plan; only older title-only snapshots need text deduplication.
            payload["previous_directions"] = list(
                {
                    item.get(
                        "research_plan_index",
                        item.get("round_index", (item["region_id"], item["direction"])),
                    ): item
                    for item in payload["previous_directions"]
                }.values()
            )
            system = EXPLORER_PROMPT
            if getattr(self.config, "selection_policy", None) is None:
                system += (
                    "\nbreadth compares unresolved questions across the map; "
                    "depth investigates active_region.\n"
                )
            text, usage = self.models.chat(
                system + reminder,
                payload,
                temperature=(
                    self.config.repair_temperature
                    if records and "error" in records[-1]
                    else self.config.temperature
                ),
            )
            self._record_usage(usage)
            records.append({"text": text})
            self._checkpoint()

    def _proposal_exploration(self, proposal, catalog, available_refs, sources):
        region = {key: proposal[key] for key in ("region_id", "new_region") if key in proposal}
        exploration = self._validate_exploration(
            {
                **region,
                "direction": proposal["objective"],
                "evidence_refs": proposal["references"],
            },
            catalog,
            available_refs,
        )
        return {
            **exploration,
            "proposal": proposal,
            "sources": [
                {
                    "ref": next(
                        ref for ref in proposal["references"] if ref in _source_refs(source)
                    ),
                    "value": source["value"],
                }
                for source in sources
                if _source_refs(source) & set(proposal["references"])
            ],
        }

    def _finish_round(self, *, update_region=True):
        if self.research_plan_state["outcomes"]:
            index = self.research_plan_state["research_plan_index"]
            record = next(
                item
                for item in reversed(self.factor_trial_records)
                if item["research_plan_index"] == index
            )
            record["explorer_proposals"] = self.research_plan_state["explorer_proposals"]
        super()._finish_round(
            update_region=update_region and getattr(self.config, "selection_policy", None) is None
        )


__all__ = ["AnnealingLLMConfig", "V1Config", "V1Search"]
