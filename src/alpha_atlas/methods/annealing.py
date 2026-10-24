"""Three-agent research rounds with simulated annealing over Atlas regions."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass

from alpha_atlas.annealing import acceptance_probability, finite, temperature
from alpha_atlas.contracts import Candidate, Region, SearchContext
from alpha_atlas.methods.mcts_llm import LLMSearchConfig
from alpha_atlas.methods.models import HTTPModels
from alpha_atlas.methods.prompt_examples import render_factor_examples
from alpha_atlas.reporting import TerminalStream, terminal_progress
from alpha_atlas.storage import candidate_from_dict
from alpha_atlas.timeframes import field_reference

READ_ONLY_QUERY_PROMPT = """For shared read-only tools, return only a JSON object:
{"queries": [{"name": "library_search", "arguments": {"query": "mechanism", "source": "all"}}]}.
Available queries: get_context(), library_search(query='', source='all', offset=0, limit=10),
library_get(factor_id), library_list(offset=0, limit=20), library_stats().
get_context returns allowed fields, operators, expression and evaluation rules, and reference settings.
Use query_results as evidence, then return the final JSON required by your role.
At most 8 query rounds, 16 requests per round. Queries do not submit or evaluate candidates.
Reference definitions without measured performance must be evaluated by the platform.
Queries do not change the region, direction, plan, allowed fields or admission rules.
"""


def _one_sentence(value):
    text = " ".join(value.split())
    endings = [text.find(mark) for mark in ("。", "！", "？", "!", "?", ".") if mark in text]
    if endings:
        text = text[: min(endings) + 1].strip()
    return text if len(text) <= 120 else text[:119].rstrip() + "…"


def _distribution(values, *, positive_ratio=False):
    ordered = sorted(values)

    def quantile(probability):
        if not ordered:
            return None
        position = (len(ordered) - 1) * probability
        lower, upper = math.floor(position), math.ceil(position)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    result = {
        "count": len(ordered),
        "median": (ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2]) / 2
        if ordered
        else None,
        "q1": quantile(0.25),
        "q3": quantile(0.75),
    }
    if positive_ratio:
        result["positive_ratio"] = (
            sum(value > 0 for value in ordered) / len(ordered) if ordered else None
        )
    return result


def _metric_value(evaluation, metric_name, split):
    return next(
        (
            item.get("value")
            for item in evaluation.get("metrics", [])
            if item.get("name") == metric_name and item.get("split") == split
        ),
        None,
    )


@dataclass(frozen=True)
class AnnealingLLMConfig(LLMSearchConfig):
    initial_temperature: float = 0.25
    final_temperature: float = 0.02
    candidates_per_plan: int = 5
    analyst_feedback_top_k: int = 5

    def __post_init__(self):
        super().__post_init__()
        if (
            not finite(self.initial_temperature)
            or not finite(self.final_temperature)
            or not 0 < self.final_temperature <= self.initial_temperature
        ):
            raise ValueError("temperatures must be finite and 0 < final <= initial")
        if type(self.candidates_per_plan) is not int or self.candidates_per_plan < 1:
            raise ValueError("candidates_per_plan must be a positive integer")
        if type(self.analyst_feedback_top_k) is not int or self.analyst_feedback_top_k < 1:
            raise ValueError("analyst_feedback_top_k must be a positive integer")


class AnnealingLLMSearch:
    """Explorer proposes regions, Analyst plans, Miner emits candidates; SA picks regions."""

    def __init__(self, seed, config=None, *, models=None):
        self.config = config or AnnealingLLMConfig()
        self.models = models if models is not None else HTTPModels(self.config)
        self.rng = random.Random(seed)
        self._session = None
        self._atlas_context = lambda: {"regions": [], "statistics": {}}
        self._checkpoint = lambda: None
        self.context = None
        self.round_state = None
        self.work = None
        self.pending = None
        self.active_region_id = None
        self.region_scores = {}
        self.proposed_regions = {}
        self.attempts_total = None
        self.completed_attempts = 0
        self.round_index = 0
        self.last_evaluation = None
        self.steps = []
        self.usage = {
            "chat_requests": 0,
            "unknown_usage_requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        if hasattr(self.models, "on_retry"):
            self.models.on_retry = self._start_request

    @property
    def configuration(self):
        return asdict(self.config)

    def set_session(self, session):
        self._session = session

    def set_atlas_context(self, provider):
        self._atlas_context = provider

    def set_checkpoint(self, callback):
        self._checkpoint = callback

    def atlas_regions(self):
        return tuple(
            {**value, "continent_ids": tuple(value["continent_ids"])}
            for value in self.proposed_regions.values()
        )

    def dump_state(self):
        return {
            "version": 1,
            "config": self.configuration,
            "rng": self.rng.getstate(),
            **{
                key: getattr(self, key)
                for key in (
                    "context",
                    "round_state",
                    "work",
                    "pending",
                    "active_region_id",
                    "region_scores",
                    "proposed_regions",
                    "attempts_total",
                    "completed_attempts",
                    "round_index",
                    "last_evaluation",
                    "steps",
                    "usage",
                )
            },
        }

    def load_state(self, state):
        if state.get("version") != 1 or state.get("config") != self.configuration:
            raise ValueError("annealing method configuration changed")
        rng = state["rng"]
        self.rng.setstate((rng[0], tuple(rng[1]), rng[2]))
        for key in self.dump_state().keys() - {"version", "config", "rng"}:
            setattr(self, key, state[key])

    def _start_request(self, kind):
        self.usage[f"{kind}_requests"] += 1
        self.usage["unknown_usage_requests"] += 1
        self._checkpoint()

    def _record_usage(self, usage):
        if isinstance(usage, dict) and all(
            type(usage.get(key)) is int and usage[key] >= 0
            for key in ("prompt_tokens", "completion_tokens")
        ):
            self.usage["unknown_usage_requests"] -= 1
            for key in ("prompt_tokens", "completion_tokens"):
                self.usage[key] += usage[key]

    def _catalog(self):
        snapshot = self._atlas_context()
        result = {item["id"]: item for item in snapshot.get("regions", [])}
        result.update(self.proposed_regions)
        return snapshot, result

    def _seed_region_scores(self, snapshot):
        for region_id, stats in snapshot.get("statistics", {}).items():
            self.region_scores.setdefault(
                region_id,
                {
                    "accepted": int(stats.get("accepted", 0)),
                    "attempts": int(stats.get("attempts", 0)),
                },
            )

    def _region_evidence(self, region_id):
        metric_name = self.context.get("metric")
        rules = self.context.get("evaluation_rules", {})
        min_coverage = rules.get("min_coverage", 0.0)
        steps = [step for step in self.steps if step.get("region_id") == region_id]
        evaluated = [
            step for step in steps if (step.get("evaluation") or {}).get("status") == "success"
        ]
        qualified = []
        for step in evaluated:
            evaluation = step["evaluation"]
            train = _metric_value(evaluation, metric_name, "train")
            valid = _metric_value(evaluation, metric_name, "val")
            coverage = evaluation.get("coverage")
            if finite(train) and finite(valid) and finite(coverage) and coverage >= min_coverage:
                qualified.append(
                    {
                        "step": step,
                        "train": abs(train),
                        "valid": valid,
                        "coverage": coverage,
                    }
                )

        positives = [item for item in qualified if item["valid"] > 0]
        negatives = [item for item in qualified if item["valid"] < 0]
        neutral = [item for item in qualified if item["valid"] == 0]
        if not qualified:
            label = "样本不足"
        elif (positives and negatives) or neutral:
            label = "证据混合"
        elif positives:
            label = "有初步支持"
        else:
            label = "本轮验证较弱"

        rejection_reasons = {}
        for step in steps:
            if step.get("accepted"):
                continue
            evaluation = step.get("evaluation") or {}
            reason = evaluation.get("reason") or evaluation.get("status") or "未入库"
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

        def examples(items, key):
            selected = sorted(items, key=key, reverse=True)[:1]
            return [
                {
                    "ref": f"trial:{item['step'].get('trial_index')}",
                    "trial_index": item["step"].get("trial_index"),
                    "evaluation_id": item["step"].get("evaluation", {}).get("evaluation_id"),
                    "factor_id": item["step"].get("evaluation", {}).get("factor_id"),
                    "train_oriented": item["train"],
                    "valid_oriented": item["valid"],
                    "valid_coverage": item["coverage"],
                    "admitted": bool(item["step"].get("accepted")),
                }
                for item in selected
            ]

        return {
            "attempts": len(steps),
            "successful_evaluations": len(evaluated),
            "valid_coverage_qualified": len(qualified),
            "admitted": sum(bool(step.get("accepted")) for step in steps),
            "rejection_reasons": rejection_reasons,
            "evidence_label": label,
            "train_primary": _distribution([item["train"] for item in qualified]),
            "valid_primary": _distribution(
                [item["valid"] for item in qualified], positive_ratio=True
            ),
            "train_valid_gap": _distribution([item["train"] - item["valid"] for item in qualified]),
            "supporting_trials": examples(positives, lambda item: item["valid"]),
            "opposing_trials": examples(negatives, lambda item: -item["valid"]),
        }

    def _explorer_atlas(self, snapshot):
        regions = []
        region_ids_by_continent = {}
        for region in snapshot.get("regions", []):
            region_id = region["id"]
            continent_ids = list(region.get("continent_ids", []))
            for continent_id in continent_ids:
                region_ids_by_continent.setdefault(continent_id, []).append(region_id)
            regions.append(
                {
                    **region,
                    "statistics": self._region_evidence(region_id),
                }
            )
        return {
            "scope": "当前 run 的全量 Region；AlphaAtlas 每个 run 独立，不合并其他 run",
            "primary_metric": self.context.get("metric"),
            "direction_handling": (
                "Train 主指标按绝对值汇总；报告中的 Valid 主指标已按 Train 方向同向化。"
            ),
            "validation_coverage_threshold": self.context.get("evaluation_rules", {}).get(
                "min_coverage", 0.0
            ),
            "continent_index": [
                {"id": continent_id, "region_ids": sorted(region_ids)}
                for continent_id, region_ids in sorted(region_ids_by_continent.items())
            ],
            "regions": regions,
        }

    def _round_temperature(self, context):
        if self.attempts_total is None:
            self.attempts_total = context.remaining_attempts
        progress = min(1.0, self.completed_attempts / max(1, self.attempts_total - 1))
        return progress, temperature(
            progress, self.config.initial_temperature, self.config.final_temperature
        )

    def _analyst_previous_round(self, round_index=None):
        round_index = self.round_index if round_index is None else round_index
        metric_name = self.context.get("metric") if isinstance(self.context, dict) else None
        prior_steps = [step for step in self.steps if step.get("round_index") == round_index]
        ranked = []
        evaluation_rules = (
            self.context.get("evaluation_rules", {}) if isinstance(self.context, dict) else {}
        )
        minimum_coverage = evaluation_rules.get(
            "min_coverage", self.context.get("min_coverage", 0.0)
        )
        for step in prior_steps:
            evaluation = step.get("evaluation") or {}
            coverage = evaluation.get("coverage")
            if (
                step.get("accepted") is not True
                or evaluation.get("status") != "success"
                or not finite(coverage)
                or coverage < minimum_coverage
            ):
                continue
            metrics = evaluation.get("metrics", [])
            primary_val = next(
                (
                    item.get("value")
                    for item in metrics
                    if item.get("name") == metric_name and item.get("split") == "val"
                ),
                None,
            )
            if not finite(primary_val):
                continue
            ranked.append(
                {
                    "trial_index": step.get("trial_index"),
                    "candidate": step.get("candidate"),
                    "coverage": coverage,
                    "selection_metric": metric_name,
                    "primary_val_ic": primary_val,
                    "absolute_primary_val_ic": abs(primary_val),
                    "train_and_val_metrics": [
                        {"split": item.get("split"), "value": item.get("value")}
                        for item in metrics
                        if item.get("name") == metric_name and item.get("split") in {"train", "val"}
                    ],
                }
            )
        ranked.sort(
            key=lambda item: (
                -item["absolute_primary_val_ic"],
                item["trial_index"] if type(item["trial_index"]) is int else 0,
            )
        )
        return {
            "round_index": round_index or None,
            "attempted_candidates": len(prior_steps),
            "qualified_candidates": len(ranked),
            "selection_metric": metric_name,
            "ranking": "descending absolute direction-adjusted validation primary IC",
            "top_factors": ranked[: self.config.analyst_feedback_top_k],
        }

    def _read_queries(self, raw, records):
        if sum("query_results" in record for record in records) >= 8:
            raise ValueError("metadata query round budget exhausted")
        requests = raw["queries"]
        if (
            not isinstance(requests, list)
            or not 1 <= len(requests) <= 16
            or any(not isinstance(request, dict) for request in requests)
        ):
            raise ValueError("invalid metadata queries")
        results = []
        for request in requests:
            try:
                result = self._session.query(request["name"], request.get("arguments", {}))
            except (KeyError, TypeError, ValueError) as exc:
                result = {"error": str(exc)}
            results.append(result)
        return {"requests": requests, "results": results}

    def _phase(self, stage, system, payload, validator, records):
        if stage == "analyst":
            system += "\n\n" + READ_ONLY_QUERY_PROMPT
        while True:
            if records and not any(
                key in records[-1] for key in ("result", "error", "query_results")
            ):
                try:
                    raw = json.loads(records[-1]["text"])
                    if not isinstance(raw, dict):
                        raise ValueError("model response must be a JSON object")
                    if stage == "analyst" and "queries" in raw:
                        records[-1]["query_results"] = self._read_queries(raw, records)
                    else:
                        records[-1]["result"] = validator(raw)
                except (ValueError, TypeError, KeyError) as exc:
                    records[-1]["error"] = str(exc)
                self._checkpoint()
            if records and "result" in records[-1]:
                return records[-1]["result"]
            limit = (
                self.config.max_formula_repairs + 1
                if stage == "miner"
                else self.config.max_dialogue_rounds
            )
            if len(records) >= limit:
                raise ValueError(f"{stage}: response repair limit exhausted")
            prompt = {
                **payload,
                "previous_response": records[-1].get("text") if records else None,
                "validation_error": records[-1].get("error") if records else None,
                "query_results": [
                    record["query_results"] for record in records if "query_results" in record
                ],
            }
            terminal_progress(
                "退火",
                进度=f"{self.completed_attempts + 1}/{self.attempts_total}",
                Agent={"explorer": "Explorer", "analyst": "Analyst", "miner": "Miner"}[stage],
                轮次=(self.round_state or {}).get("index", self.round_index + 1),
                模式=(self.round_state or {}).get("mode"),
            )
            self._start_request("chat")
            text, usage = self.models.chat(
                system,
                prompt,
                temperature=(
                    self.config.repair_temperature
                    if records and "error" in records[-1]
                    else self.config.temperature
                ),
            )
            self._record_usage(usage)
            records.append({"text": text})
            self._checkpoint()

    def _validate_exploration(self, raw, catalog, available_evidence_refs=()):
        direction = raw.get("direction")
        if not isinstance(direction, str) or not direction.strip():
            raise ValueError("Explorer must provide a nonempty direction")
        evidence_refs = raw.get("evidence_refs", [])
        if (
            not isinstance(evidence_refs, list)
            or any(not isinstance(ref, str) for ref in evidence_refs)
            or len(set(evidence_refs)) != len(evidence_refs)
            or not set(evidence_refs).issubset(available_evidence_refs)
        ):
            raise ValueError(
                "Explorer evidence_refs must cite unique trials shown in the Atlas map"
            )
        region_id = raw.get("region_id")
        new_region = raw.get("new_region")
        if isinstance(region_id, str) and region_id in catalog:
            region = catalog[region_id]
        elif isinstance(new_region, dict):
            hypothesis = new_region.get("hypothesis")
            if not isinstance(hypothesis, str) or not hypothesis.strip():
                raise ValueError("new_region.hypothesis must be nonempty")
            continent_ids = new_region.get("continent_ids")
            known_continents = {
                value for item in catalog.values() for value in item.get("continent_ids", [])
            }
            if not isinstance(continent_ids, list) or not continent_ids:
                raise ValueError("new region must reference an existing continent")
            if any(value not in known_continents for value in continent_ids):
                raise ValueError("new region references an unknown continent")
            existing = next(
                (
                    item
                    for item in catalog.values()
                    if item.get("hypothesis", "").casefold() == hypothesis.strip().casefold()
                ),
                None,
            )
            if existing is not None:
                region = existing
            else:
                region_id = (
                    "explorer-"
                    + hashlib.sha256(hypothesis.strip().encode("utf-8")).hexdigest()[:16]
                )
                region = asdict(
                    Region(
                        region_id,
                        tuple(dict.fromkeys(continent_ids)),
                        hypothesis.strip(),
                        source="alpha_atlas_explorer",
                    )
                )
                self.proposed_regions[region_id] = region
        else:
            raise ValueError("Explorer must choose a known region or propose a new region")
        return {
            "region": region,
            "region_id": region["id"],
            "direction": direction.strip(),
            "evidence_refs": evidence_refs,
        }

    @staticmethod
    def _validate_plan(raw, exploration, context):
        hypothesis = raw.get("hypothesis")
        instruction = raw.get("miner_instruction")
        fields = raw.get("allowed_fields")
        known_fields = set(context.get("fields", []))
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ValueError("Analyst must provide a testable hypothesis")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("Analyst must provide Miner instructions")
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(value, str) or value not in known_fields for value in fields)
        ):
            raise ValueError("Analyst fields must be a nonempty subset of available fields")
        return {
            "region_id": exploration["region_id"],
            "direction": exploration["direction"],
            "hypothesis": hypothesis.strip(),
            "allowed_fields": list(dict.fromkeys(fields)),
            "measurement_plan": str(raw.get("measurement_plan", "")).strip(),
            "miner_instruction": instruction.strip(),
        }

    def _validate_miner(self, raw):
        expression = raw.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("Miner must provide a nonempty Atlas DSL expression")
        checked = self._session.validate_expression(expression)
        if checked["error"] is not None:
            raise ValueError(checked["error"])
        tree = checked["expression"]
        fields = set(self.round_state["plan"]["allowed_fields"])

        def used_fields(node):
            values = {field_reference(node["value"])[0]} if node["op"] == "field" else set()
            for child in node.get("args", []):
                values.update(used_fields(child))
            return values

        outside = used_fields(tree) - fields
        if outside:
            raise ValueError(
                "Miner used fields outside the Analyst plan: " + ", ".join(sorted(outside))
            )
        name = raw.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError("Miner name must be text")
        return {"expression": expression, "name": name}

    def _new_round(self, context, index):
        snapshot, _ = self._catalog()
        self._seed_region_scores(snapshot)
        progress, heat = self._round_temperature(context)
        geometric_midpoint = math.sqrt(
            self.config.initial_temperature * self.config.final_temperature
        )
        return {
            "index": index,
            "temperature": heat,
            "budget_progress": progress,
            "mode": "breadth" if heat >= geometric_midpoint else "depth",
            "atlas_snapshot": snapshot,
            "exploration": None,
            "plan": None,
            "responses": {"explorer": [], "analyst": []},
            "outcomes": [],
        }

    def _start_round(self, context):
        if self.round_state is None:
            self.round_state = self._new_round(context, self.round_index + 1)
            self._checkpoint()
        snapshot, catalog = self._catalog()
        self.round_state["atlas_snapshot"] = snapshot
        self._seed_region_scores(snapshot)
        exploration = self.round_state["exploration"]
        if exploration is None:
            exploration = self._explore_round(snapshot, catalog)
            self.round_state["exploration"] = exploration
            self._checkpoint()
        terminal_progress("Explorer", 方向=_one_sentence(exploration["direction"]))
        plan = self.round_state["plan"]
        if plan is None:
            analyst_payload = {
                "research_context": self.context,
                "exploration": exploration,
                "previous_round_results": self._analyst_previous_round(),
            }
            plan = self._phase(
                "analyst",
                "You are the Research Analyst Agent. Turn the Explorer's fixed direction into a "
                "testable plan for one Factor Miner candidate at a time. Do not change the region "
                "or research direction. Choose only available base field names in allowed_fields; "
                "specify any permitted @interval in the measurement plan and Miner instructions. "
                "Do not decide whether a "
                "candidate is qualified; the platform does that. Return JSON with hypothesis, "
                "allowed_fields, measurement_plan and miner_instruction.",
                analyst_payload,
                lambda raw: self._validate_plan(raw, exploration, self.context),
                self.round_state["responses"]["analyst"],
            )
            terminal_progress("Analyst", 假设=_one_sentence(plan["hypothesis"]))
            self.round_state["plan"] = plan
            self._checkpoint()

    def _explore_round(self, snapshot, catalog):
        explorer_atlas = self._explorer_atlas(snapshot)
        available_evidence_refs = {
            item["ref"]
            for region in explorer_atlas["regions"]
            for field in ("supporting_trials", "opposing_trials")
            for item in region["statistics"][field]
        }
        mode = self.round_state["mode"]
        active = catalog.get(self.active_region_id)
        explorer_payload = {
            "research_context": self.context,
            "atlas": explorer_atlas,
            "mode": mode,
            "active_region": active,
            "active_region_score": self._region_score(self.active_region_id),
            "instruction": (
                "The Atlas field is the complete Region map for this run. For breadth, compare all "
                "regions and investigate an underexplored or genuinely new one. For depth, focus "
                "on the active region and a specific unresolved question. Existing region evidence "
                "summaries include admitted and non-admitted candidates; do not infer causation. "
                "Cite only trial refs shown in the map. Valid metrics are already aligned to the "
                "Train primary-metric direction."
            ),
        }

        def explore(raw):
            result = self._validate_exploration(raw, catalog, available_evidence_refs)
            self.round_state["exploration"] = result
            return result

        exploration = self.round_state["exploration"]
        if exploration is None:
            exploration = self._phase(
                "explorer",
                "You are the Atlas Explorer Agent. Choose what market mechanism or region to "
                "study. Use only the complete current-run Atlas map and research context. The map "
                "summarizes successful, Valid-coverage-qualified Train/Valid primary-metric pairs; "
                "Train is shown by absolute value and Valid is already flipped with the Train sign. "
                "Read the evidence label cautiously, keep insufficient evidence inconclusive, and "
                "do not describe correlation as causation. Cite only listed trial refs; OOS and other "
                "runs are outside this map. You may propose one new Region under an existing "
                "continent. Do not design a factor or write a formula. Return JSON with direction, "
                "evidence_refs (an array, empty when none apply), and either region_id or new_region: "
                '{"region_id":"known id","direction":"research question",'
                '"evidence_refs":["trial:3"]} OR {"new_region":{'
                '"hypothesis":"region mechanism","continent_ids":["known id"]},'
                '"direction":"research question","evidence_refs":[]}.',
                explorer_payload,
                explore,
                self.round_state["responses"]["explorer"],
            )
        return exploration

    def _region_score(self, region_id):
        stats = self.region_scores.get(region_id)
        if not stats or not stats["attempts"]:
            return None
        return stats["accepted"] / stats["attempts"]

    def _miner_candidate(self, plan):
        system = (
            "You are the Factor Miner Agent in one continuing Miner session. Implement one "
            "concrete factor candidate within the Analyst's plan. Use only allowed fields and "
            "the supplied DSL/operator rules. allowed_fields contains base names; append only "
            "suffixes permitted by the context without changing the base field permission. Follow the "
            "plan's frequency; do not substitute rolling native bars for completed coarse bars. "
            "The conversation contains every earlier candidate "
            "you proposed in this plan and the platform evaluation returned for each one. Use "
            "that evidence to propose a materially distinct next candidate; never repeat an "
            "earlier expression. Do not change the region, direction, plan, evaluation, or "
            "admission decision. Return JSON with expression and optional name."
        )
        system += "\n\n" + READ_ONLY_QUERY_PROMPT
        system += "\n\n" + render_factor_examples(
            plan["allowed_fields"], self.context.get("frequency")
        )
        messages = self.round_state.setdefault("miner_messages", [])
        if not messages:
            payload = {
                "research_context": self.context,
                "exploration": self.round_state["exploration"],
                "plan": plan,
                "candidate_attempt": 1,
                "candidate_attempt_limit": self._plan_limit(),
            }
            messages.extend(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ]
            )
            self._checkpoint()
        else:
            messages[0]["content"] = system
        records = self.work["responses"]
        while True:
            if records and not any(
                key in records[-1] for key in ("result", "error", "query_results")
            ):
                record = records[-1]
                try:
                    raw = json.loads(record["text"])
                    if not isinstance(raw, dict):
                        raise ValueError("model response must be a JSON object")
                    if "queries" in raw:
                        record["query_results"] = self._read_queries(raw, records)
                    else:
                        record["result"] = self._validate_miner(raw)
                except (ValueError, TypeError, KeyError) as exc:
                    record["error"] = str(exc)
                if not record.get("assistant_added"):
                    messages.append({"role": "assistant", "content": record["text"]})
                    record["assistant_added"] = True
                if "query_results" in record:
                    messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"query_results": [record["query_results"]]}, ensure_ascii=False
                            ),
                        }
                    )
                if "error" in record and not record.get("error_added"):
                    messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "validation_error": record["error"],
                                    "instruction": "Correct the response and return one valid candidate.",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    record["error_added"] = True
                self._checkpoint()
            if records and "result" in records[-1]:
                return records[-1]["result"]
            limit = self.config.max_formula_repairs + 1
            replies = sum("query_results" not in record for record in records)
            if replies >= limit:
                raise ValueError("miner: response repair limit exhausted")
            terminal_progress(
                "退火",
                进度=f"{self.completed_attempts + 1}/{self.attempts_total}",
                Agent="Miner",
                轮次=self.round_state["index"],
                模式=self.round_state.get("mode"),
                计划候选=(f"{len(self.round_state['outcomes']) + 1}/{self._plan_limit()}"),
                修复=f"{replies + 1}/{limit}",
            )
            self._start_request("chat")
            if hasattr(self.models, "chat_messages"):
                with TerminalStream() as stream:
                    text, usage = self.models.chat_messages(
                        messages,
                        on_delta=stream.delta,
                        temperature=(
                            self.config.repair_temperature
                            if records and "error" in records[-1]
                            else self.config.temperature
                        ),
                    )
            else:
                text, usage = self.models.chat(
                    system,
                    {"conversation": messages[1:]},
                    temperature=(
                        self.config.repair_temperature
                        if records and "error" in records[-1]
                        else self.config.temperature
                    ),
                )
            self._record_usage(usage)
            messages.append({"role": "assistant", "content": text})
            records.append({"text": text, "assistant_added": True})
            self._checkpoint()

    def ask(self, context: SearchContext, count=1):
        if count != 1 or self.pending is not None:
            raise ValueError("annealing requires one ask followed by one tell")
        if context.remaining_attempts <= 0:
            raise ValueError("attempt budget exhausted")
        if self.context is None:
            self.context = self._session.query("get_context", {})
        candidate = self._prepare_candidate(context)
        self.pending = asdict(candidate)
        self._checkpoint()
        return [candidate]

    def _plan_limit(self):
        return self.round_state.get("candidate_limit", self.config.candidates_per_plan)

    def _prepare_candidate(self, context):
        if self.round_state is None or self.round_state.get("plan") is None:
            self._start_round(context)
        if self.work is None:
            self.work = {
                "round_index": self.round_state["index"],
                "region_id": self.round_state["plan"]["region_id"],
                "responses": [],
                "validation_error": None,
            }
            self._checkpoint()
        plan = self.round_state["plan"]
        try:
            miner = self._miner_candidate(plan)
            candidate = Candidate(
                miner["expression"],
                (plan["region_id"],),
                hypothesis=plan["hypothesis"],
                name=miner.get("name"),
            )
        except (ValueError, TypeError, KeyError) as exc:
            self.work["validation_error"] = str(exc)
            candidate = Candidate(
                "",
                (plan["region_id"],),
                hypothesis=f"generation failed: {exc}",
                name="alpha_atlas_generation_error",
            )
        self.work["candidate"] = asdict(candidate)
        self._checkpoint()
        return candidate

    def _finish_round(self):
        outcomes = self.round_state["outcomes"]
        if not outcomes:
            self.round_state = None
            return
        region_id = self.round_state["plan"]["region_id"]
        stats = self.region_scores.setdefault(region_id, {"accepted": 0, "attempts": 0})
        stats["accepted"] += sum(outcomes)
        stats["attempts"] += len(outcomes)
        proposal_score = self._region_score(region_id)
        incumbent = self.active_region_id
        incumbent_score = self._region_score(incumbent)
        probability = draw = None
        if incumbent is None or incumbent == region_id or incumbent_score is None:
            accepted, reason = True, "initialize_or_continue_region"
            probability = 1.0
        else:
            probability = acceptance_probability(
                incumbent_score, proposal_score, self.round_state["temperature"]
            )
            draw = self.rng.random()
            accepted = draw < probability
            reason = "metropolis_accept" if accepted else "metropolis_reject"
        if accepted:
            self.active_region_id = region_id
        self.round_state["annealing_result"] = {
            "proposed_region_id": region_id,
            "incumbent_before": incumbent,
            "proposal_score": proposal_score,
            "incumbent_score": incumbent_score,
            "temperature": self.round_state["temperature"],
            "acceptance_probability": probability,
            "draw": draw,
            "accepted": accepted,
            "reason": reason,
            "active_region_after": self.active_region_id,
        }
        self.steps[-1]["annealing_result"] = self.round_state["annealing_result"]
        self.round_index += 1
        self.round_state = None

    def tell(self, results):
        if len(results) != 1 or self.pending is None:
            raise ValueError("annealing expects one pending feedback")
        feedback = results[0]
        if feedback.candidate != candidate_from_dict(self.pending):
            raise ValueError("feedback does not match pending candidate")
        self.round_state["outcomes"].append(int(feedback.accepted))
        self.last_evaluation = self._session.evaluation_result(feedback)
        self.steps.append(
            {
                "round_index": self.round_state["index"],
                "region_id": self.work["region_id"],
                "direction": self.round_state["exploration"]["direction"],
                "evidence_refs": self.round_state["exploration"].get("evidence_refs", []),
                "plan": self.round_state["plan"],
                "candidate": self.pending,
                "accepted": feedback.accepted,
                "trial_index": feedback.trial_index,
                "evaluation": self.last_evaluation,
            }
        )
        messages = self.round_state.get("miner_messages")
        if messages is not None:
            attempt = len(self.round_state["outcomes"])
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "platform_evaluation": self.last_evaluation,
                            "candidate_attempt": attempt,
                            "candidate_attempt_limit": self._plan_limit(),
                            "plan_complete": attempt >= self._plan_limit(),
                            "instruction": (
                                "Use this evaluation in the ongoing Miner session. If the plan "
                                "continues, propose a materially distinct next candidate without "
                                "repeating any expression already in this conversation."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        self.completed_attempts += 1
        self.pending = None
        self.work = None
        if (
            len(self.round_state["outcomes"]) >= self._plan_limit()
            or self._session.remaining_budget() == 0
        ):
            self._finish_round()
        self._checkpoint()
