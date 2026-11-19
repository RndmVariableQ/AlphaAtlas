"""A long-horizon Analyst directing a fresh Miner session for each plan."""

import json
from collections import Counter
from dataclasses import dataclass

from alpha_atlas.methods.annealing import AnnealingLLMSearch
from alpha_atlas.methods.models import LLMSearchConfig


@dataclass(frozen=True)
class AnalystMinerConfig(LLMSearchConfig):
    candidates_per_plan: int = 5

    def __post_init__(self):
        super().__post_init__()
        if type(self.candidates_per_plan) is not int or self.candidates_per_plan < 1:
            raise ValueError("candidates_per_plan must be a positive integer")


class AnalystMinerSearch(AnnealingLLMSearch):
    """Choose directions from run history, without Atlas or region selection."""

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or AnalystMinerConfig(), models=models)
        self.completed_plans = []

    def dump_state(self):
        state = super().dump_state()
        state["version"] = "analyst_miner_v1"
        state["completed_plans"] = self.completed_plans
        return state

    def load_state(self, state):
        if state.get("version") != "analyst_miner_v1":
            raise ValueError("invalid analyst_miner checkpoint")
        super().load_state({**state, "version": 1})

    def _candidate_regions(self, plan):
        return ()

    def _history(self):
        return {
            "plans": self.completed_plans,
            "trials": [
                {
                    "research_plan_index": record["research_plan_index"],
                    **self._session.get_trial(record["factor_trial_index"]),
                }
                for record in self.factor_trial_records
            ],
        }

    def _compressed_history(self, history):
        """Bound the model-facing history while retaining the original trial records."""
        plans, trials = history["plans"], history["trials"]

        def shortened(value, limit):
            if not isinstance(value, str) or len(value) <= limit:
                return value
            return value[:limit] + " [truncated]"

        compact_plans = []
        for record in plans[-40:]:
            plan = record.get("plan") or {}
            compact_plans.append(
                {
                    "research_plan_index": record["research_plan_index"],
                    "direction": shortened(plan.get("direction"), 240),
                    "hypothesis": shortened(plan.get("hypothesis"), 240),
                    "end_reason": shortened(record.get("end_reason"), 240),
                }
            )

        compact_trials = []
        metric = self.context.get("metric")
        for record in trials[-40:]:
            candidate = record.get("candidate") or {}
            compact_trials.append(
                {
                    "research_plan_index": record["research_plan_index"],
                    "factor_trial_index": record["factor_trial_index"],
                    "factor_id": record.get("factor_id"),
                    "name": shortened(candidate.get("name"), 120),
                    "expression": shortened(candidate.get("expression"), 400),
                    "status": record.get("status"),
                    "accepted": record.get("accepted"),
                    "reason": record.get("reason"),
                    "coverage": record.get("coverage"),
                    "direction": record.get("direction"),
                    "train_ic": self._history_metric(record, metric, "train"),
                    "val_ic": self._history_metric(record, metric, "val"),
                    "residual_ic": record.get("residual_ic"),
                    "max_abs_corr": record.get("max_abs_corr"),
                    "nearest_factor_id": record.get("nearest_factor_id"),
                }
            )

        older_directions = [
            [
                record["research_plan_index"],
                shortened((record.get("plan") or {}).get("direction"), 100),
            ]
            for record in plans[:-40]
        ]
        result = {
            "compression": (
                "recent structured summaries and earlier [plan index, direction] pairs; "
                "details omitted"
            ),
            "plan_count": len(plans),
            "trial_count": len(trials),
            "accepted_count": sum(record.get("accepted") is True for record in trials),
            "outcome_counts": dict(
                Counter(str(record.get("reason") or record.get("status")) for record in trials)
            ),
            "plans": compact_plans,
            "trials": compact_trials,
            "earlier_directions": older_directions,
            "omitted_plan_count": len(plans) - len(compact_plans),
            "omitted_trial_count": len(trials) - len(compact_trials),
            "omitted_direction_count": len(plans) - len(compact_plans) - len(older_directions),
        }
        # Preserve recent plans and trials preferentially even if future fields grow.
        if len(json.dumps(result, ensure_ascii=False)) > 120000:
            lower, upper = 0, len(older_directions)
            while lower < upper:
                middle = (lower + upper) // 2
                result["earlier_directions"] = older_directions[middle:]
                result["omitted_direction_count"] = middle
                if len(json.dumps(result, ensure_ascii=False)) <= 120000:
                    upper = middle
                else:
                    lower = middle + 1
            result["earlier_directions"] = older_directions[lower:]
            result["omitted_direction_count"] = lower
        while len(json.dumps(result, ensure_ascii=False)) > 120000:
            if len(compact_plans) >= len(compact_trials) and compact_plans:
                compact_plans.pop(0)
            elif compact_trials:
                compact_trials.pop(0)
            else:
                raise ValueError("compressed analyst history exceeds its size limit")
            result["omitted_plan_count"] = len(plans) - len(compact_plans)
            result["omitted_trial_count"] = len(trials) - len(compact_trials)
            result["omitted_direction_count"] = (
                len(plans) - len(compact_plans) - len(result["earlier_directions"])
            )
        return result

    @staticmethod
    def _history_metric(record, metric, split):
        return next(
            (
                item.get("value")
                for item in record.get("metrics", [])
                if item.get("name") == metric and item.get("split") == split
            ),
            None,
        )

    def _start_round(self, context):
        if self.research_plan_state is None:
            if self.attempts_total is None:
                self.attempts_total = context.remaining_attempts
            self.research_plan_state = {
                "research_plan_index": self.completed_research_plan_count + 1,
                "exploration": None,
                "plan": None,
                "responses": {"analyst": []},
                "outcomes": [],
                "history": self._history(),
            }
            self._checkpoint()
        state = self.research_plan_state
        if state["plan"] is not None:
            return

        def validate(raw):
            if "blocked" in raw:
                reason = self._blocked_reason(raw)
                return {"region_id": None, "direction": "", "blocked": reason}
            direction = raw.get("direction")
            if not isinstance(direction, str) or not direction.strip():
                raise ValueError("Analyst must provide a nonempty research direction")
            exploration = {"region_id": None, "direction": direction.strip()}
            return self._validate_plan(raw, exploration, self.context)

        plan = self._phase(
            "analyst",
            "You are the Research Analyst Agent. Choose a research direction using the "
            "compressed run history and available library evidence, then design a testable "
            "plan for a "
            "Factor Miner. Return one JSON object with nonempty direction, hypothesis, "
            "allowed_fields, measurement_plan and miner_instruction. allowed_fields is a "
            "nonempty array of available base field names. Use only available DSL and platform "
            "feedback. If the plan requires unavailable capabilities, return only "
            '{"blocked":"specific missing capability"}.',
            {
                "research_context": self.context,
                "history": self._compressed_history(state["history"]),
            },
            validate,
            state["responses"]["analyst"],
        )
        state["plan"] = plan
        state["exploration"] = {"direction": plan["direction"], "region_id": None}
        self._checkpoint()

    def _finish_round(self, *, update_region=True):
        state = self.research_plan_state
        if state is None:
            return
        if state["outcomes"]:
            self.completed_research_plan_count += 1
        self.completed_plans.append(
            {
                "research_plan_index": state["research_plan_index"],
                "plan": state["plan"],
                "end_reason": state.get("end_reason", "candidate_limit_or_budget"),
            }
        )
        self.research_plan_state = None

    def _finish_done_plan(self, reason):
        self.research_plan_state["end_reason"] = reason
        super()._finish_done_plan(reason)

    def _finish_blocked_plan(self, reason):
        self.research_plan_state["end_reason"] = reason
        super()._finish_blocked_plan(reason)
