"""AlphaSchema semantic search adapted to Atlas's DSL and train-only evaluation.

Algorithm reference: Jingyang Yi et al., arXiv:2607.26642v1 and JingyangYi/AlphaSchema
commit 1206a094abfaad7cc53e6dff39f8fae43e851acb (schema_space, selector, reward_model,
pipeline, workflow, backend). Bundled schema records are from that commit.
Source attribution, unlicensed upstream status and deviations: docs/methods/alphaschema.md.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import lightgbm as lgb
import numpy as np

from alpha_atlas.contracts import Candidate
from alpha_atlas.methods.models import HTTPModels, parse_json_object
from alpha_atlas.methods.prompt_examples import render_factor_examples
from alpha_atlas.reporting import terminal_progress
from alpha_atlas.session import READ_ONLY_QUERY_HELP
from alpha_atlas.storage import candidate_from_dict

CATEGORIES = ("event", "context", "quality", "direction", "output")
DIAGNOSTICS = "icir_turnover_lag1_v2"
MUTATIONS = {
    "replace_quality": 0.22,
    "replace_context": 0.16,
    "replace_output": 0.16,
    "add_quality": 0.14,
    "drop_quality": 0.12,
    "replace_direction": 0.12,
    "replace_event": 0.08,
}
SCHEDULE = (
    {"min_observations": 0, "max_observations": 150, "explore": 16, "exploit": 0, "mutate": 0},
    {"min_observations": 150, "max_observations": 300, "explore": 8, "exploit": 6, "mutate": 2},
    {"min_observations": 300, "max_observations": 700, "explore": 5, "exploit": 8, "mutate": 3},
    {"min_observations": 700, "max_observations": None, "explore": 3, "exploit": 8, "mutate": 5},
)


@dataclass(frozen=True)
class AlphaSchemaConfig:
    chat_model: str = ""
    chat_base_url: str = "http://127.0.0.1:8001/v1"
    chat_key_env: str = "ALPHA_ATLAS_LLM_API_KEY"
    temperature: float = 1.0
    max_output_tokens: int = 65536
    timeout_seconds: float = 1800.0
    candidate_pool_size: int = 10000
    batch_size: int = 16
    periods: tuple[int, ...] = (20, 80)
    max_repairs: int = 1
    max_dialogue_rounds: int = 8
    ensemble_size: int = 8
    bootstrap_fraction: float = 0.7
    reward_alpha: float = 10.0
    reward_beta: float = 1.0
    lag1_penalty: float = 2.0
    quality_count_weights: dict = field(
        default_factory=lambda: {"0": 0.15, "1": 0.35, "2": 0.35, "3": 0.15}
    )
    mutation_weights: dict = field(default_factory=lambda: dict(MUTATIONS))
    schedule: tuple[dict, ...] = field(default_factory=lambda: tuple(dict(s) for s in SCHEDULE))

    def __post_init__(self):
        for key in (
            "candidate_pool_size",
            "batch_size",
            "max_output_tokens",
            "max_dialogue_rounds",
            "ensemble_size",
        ):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f"{key} must be a positive integer")
        if type(self.max_repairs) is not int or self.max_repairs < 0:
            raise ValueError("max_repairs must be a nonnegative integer")
        if self.candidate_pool_size < self.batch_size:
            raise ValueError("candidate_pool_size must cover batch_size")
        if (
            not self.periods
            or any(type(p) is not int or p < 1 for p in self.periods)
            or len(set(self.periods)) != len(self.periods)
        ):
            raise ValueError("periods must be distinct positive integers")
        for key in (
            "temperature",
            "timeout_seconds",
            "bootstrap_fraction",
            "reward_alpha",
            "reward_beta",
            "lag1_penalty",
        ):
            value = getattr(self, key)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if not 0 < self.bootstrap_fraction <= 1 or not 0 <= self.temperature <= 2:
            raise ValueError("invalid bootstrap_fraction or temperature")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        url = urlsplit(self.chat_base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("model endpoint must be HTTP(S) without credentials")
        for weights, allowed in (
            (self.quality_count_weights, {"0", "1", "2", "3"}),
            (self.mutation_weights, set(MUTATIONS)),
        ):
            if (
                not weights
                or not set(weights) <= allowed
                or any(
                    type(w) not in {int, float} or not math.isfinite(w) or w < 0
                    for w in weights.values()
                )
                or sum(weights.values()) <= 0
            ):
                raise ValueError("invalid sampling weights")
        lower = 0
        for stage in self.schedule:
            if set(stage) != {
                "min_observations",
                "max_observations",
                "explore",
                "exploit",
                "mutate",
            }:
                raise ValueError("invalid schedule fields")
            start, upper = stage["min_observations"], stage["max_observations"]
            if (
                type(start) is not int
                or lower is None
                or start != lower
                or (upper is not None and (type(upper) is not int or upper <= lower))
            ):
                raise ValueError("schedule must be contiguous from zero")
            quota = [stage[k] for k in ("explore", "exploit", "mutate")]
            if any(type(n) is not int or n < 0 for n in quota) or sum(quota) != self.batch_size:
                raise ValueError("schedule quotas must sum to batch_size")
            if start == 0 and (stage["exploit"] or stage["mutate"]):
                raise ValueError("initial schedule must explore without unmeasured rewards")
            lower = upper
        if not self.schedule or lower is not None:
            raise ValueError("schedule must cover all observation counts")

    @classmethod
    def from_mapping(cls, value):
        value = dict(value)
        for key in ("periods", "schedule"):
            if key in value:
                value[key] = tuple(value[key])
        if "schedule" in value:
            value["schedule"] = tuple({"max_observations": None, **s} for s in value["schedule"])
        return cls(**value)


@dataclass(frozen=True)
class Plan:
    event: str
    context: str
    qualities: tuple[str, ...]
    direction: str
    output: str

    @property
    def key(self):
        # Preserve the author's ordered quality key, despite set-valued model features.
        return "|".join((self.event, self.context, *self.qualities, self.direction, self.output))

    def to_dict(self):
        return {**asdict(self), "qualities": list(self.qualities)}

    @classmethod
    def from_dict(cls, row):
        return cls(
            row["event"], row["context"], tuple(row["qualities"]), row["direction"], row["output"]
        )


class SchemaSpace:
    def __init__(self):
        self.ids, self.records = {}, {}
        for category in CATEGORIES:
            path = Path(__file__).with_name("alphaschema_schemas") / f"{category}.json"
            rows = json.loads(path.read_text(encoding="utf-8"))["schemas"]
            enabled = [
                r
                for r in rows
                if not r.get("disabled", False) and r.get("enabled", True) is not False
            ]
            self.ids[category] = [r["schema_id"] for r in enabled]
            self.records.update((r["schema_id"], r) for r in enabled)

    def expand(self, plan):
        return {
            "event": self.records[plan.event],
            "context": self.records[plan.context],
            "qualities": [self.records[q] for q in plan.qualities],
            "direction": self.records[plan.direction],
            "output": self.records[plan.output],
        }

    def sample(self, count, seed, weights, excluded):
        rng = random.Random(seed)
        blocked, plans = set(excluded), []
        for _ in range(max(1000, count * 50)):
            n = int(rng.choices(list(weights), weights=list(weights.values()))[0])
            plan = Plan(
                rng.choice(self.ids["event"]),
                rng.choice(self.ids["context"]),
                tuple(rng.sample(self.ids["quality"], n)),
                rng.choice(self.ids["direction"]),
                rng.choice(self.ids["output"]),
            )
            if plan.key not in blocked:
                plans.append(plan)
                blocked.add(plan.key)
                if len(plans) == count:
                    return plans
        raise RuntimeError("AlphaSchema candidate space exhausted")

    def mutate(self, parent, seed, weights):
        rng = random.Random(seed)
        for _ in range(100):
            operation = rng.choices(list(weights), weights=list(weights.values()))[0]
            row = parent.to_dict()
            qualities = row["qualities"]
            if operation == "replace_quality" and qualities:
                index = rng.randrange(len(qualities))
                qualities[index] = rng.choice(
                    [q for q in self.ids["quality"] if q not in qualities]
                )
            elif operation == "add_quality" and len(qualities) < 3:
                qualities.append(rng.choice([q for q in self.ids["quality"] if q not in qualities]))
            elif operation == "drop_quality" and qualities:
                qualities.pop(rng.randrange(len(qualities)))
            elif operation in {
                "replace_event",
                "replace_context",
                "replace_direction",
                "replace_output",
            }:
                key = operation.removeprefix("replace_")
                row[key] = rng.choice([value for value in self.ids[key] if value != row[key]])
            else:
                continue
            return Plan.from_dict(row), operation
        raise ValueError("mutation weights cannot produce a valid child for this parent")


def plan_features(plan):
    values = {
        f"{key}={getattr(plan, key)}": 1.0 for key in ("event", "context", "direction", "output")
    }
    values.update(
        {
            f"event_{key}={plan.event}|{getattr(plan, key)}": 1.0
            for key in ("context", "output", "direction")
        }
    )
    values["quality_count"] = float(len(plan.qualities))
    for quality in plan.qualities:
        values[f"quality={quality}"] = 1.0
        values[f"event_quality={plan.event}|{quality}"] = 1.0
        values[f"context_quality={plan.context}|{quality}"] = 1.0
    return values


class RewardEnsemble:
    """Author's subsampled LightGBM ensemble, using the already installed native API."""

    def __init__(self, config, seed):
        self.config, self.seed = config, seed
        self.columns, self.models = {}, []

    def _matrix(self, plans):
        matrix = np.zeros((len(plans), len(self.columns)), dtype=np.float64)
        for index, plan in enumerate(plans):
            for key, value in plan_features(plan).items():
                if key in self.columns:
                    matrix[index, self.columns[key]] = value
        return matrix

    def fit(self, observations):
        if not observations or any(not math.isfinite(y) for _, y in observations):
            raise ValueError("reward ensemble requires finite observations")
        plans, y = zip(*observations, strict=True)
        self.columns = {
            key: i
            for i, key in enumerate(sorted({key for plan in plans for key in plan_features(plan)}))
        }
        x, y = self._matrix(plans), np.asarray(y)
        rng = np.random.default_rng(self.seed)
        size = max(1, math.ceil(len(plans) * self.config.bootstrap_fraction))
        self.models = []
        for index in range(self.config.ensemble_size):
            sample = (
                np.arange(len(plans))
                if size >= len(plans)
                else rng.choice(len(plans), size, replace=False)
            )
            params = {
                "objective": "regression",
                "learning_rate": 0.035,
                "num_leaves": (11, 15, 19, 15, 13, 17, 11, 21)[index % 8],
                "min_data_in_leaf": max(3, min(20, len(plans) // 10)),
                "bagging_fraction": 0.9,
                "bagging_freq": 1,
                "feature_fraction": 0.9,
                "lambda_l2": 1.4,
                "seed": self.seed + index * 1009,
                "num_threads": 1,
                "verbosity": -1,
            }
            self.models.append(
                lgb.train(params, lgb.Dataset(x[sample], label=y[sample]), num_boost_round=450)
            )
        return self

    def predict(self, plans):
        if not self.models:
            raise ValueError("reward ensemble is not fitted")
        result = []
        # Avoid a dense 10,000-by-vocabulary pool allocation; feature definitions are unchanged.
        for start in range(0, len(plans), 256):
            x = self._matrix(plans[start : start + 256])
            values = np.asarray([m.predict(x, num_threads=1) for m in self.models])
            result.extend(
                (float(mean), float(std))
                for mean, std in zip(values.mean(0), values.std(0), strict=True)
            )
        return result


def quota_for(count, schedule):
    return next(
        s
        for s in schedule
        if count >= s["min_observations"]
        and (s["max_observations"] is None or count < s["max_observations"])
    )


def select_batch(candidates, history, observations, quota, model, space, seed, mutation_weights):
    events = Counter(p.event for p in history)
    contexts = Counter(p.context for p in history)
    pairs = Counter((p.event, p.context) for p in history)

    def novelty(p):
        return (
            0.60 / math.sqrt(1 + pairs[p.event, p.context])
            + 0.25 / math.sqrt(1 + events[p.event])
            + 0.15 / math.sqrt(1 + contexts[p.context])
        )

    blocked = {p.key for p in history}
    available = [p for p in candidates if p.key not in blocked]
    chosen = []

    def add(plan, reason, **extra):
        chosen.append(
            {"plan": plan.to_dict(), "selection_reason": reason, "novelty": novelty(plan), **extra}
        )
        blocked.add(plan.key)

    for plan in sorted(available, key=novelty, reverse=True)[: quota["explore"]]:
        add(plan, "explore")
    if quota["exploit"]:
        if model is None:
            raise ValueError("exploitation requires a fitted reward model")
        pool = [p for p in available if p.key not in blocked]
        ranked = sorted(
            zip(pool, model.predict(pool), strict=True), key=lambda row: row[1][0], reverse=True
        )
        for plan, _ in ranked[: quota["exploit"]]:
            add(plan, "exploit")
    parents = sorted(observations, key=lambda row: row[1], reverse=True)[:100]
    expected = sum(quota[k] for k in ("explore", "exploit", "mutate"))
    for attempt in range(1000):
        if len(chosen) >= expected or not parents:
            break
        parent, reward = parents[attempt % len(parents)]
        child, operation = space.mutate(parent, seed + attempt, mutation_weights)
        if child.key not in blocked:
            before, after = parent.to_dict(), child.to_dict()
            add(
                child,
                "mutation",
                parent_plan_key=parent.key,
                parent_reward=reward,
                mutation_operation=operation,
                changed_components={
                    key: {"from": before[key], "to": after[key]}
                    for key in before
                    if before[key] != after[key]
                },
            )
    for plan in available:
        if len(chosen) >= expected:
            break
        if plan.key not in blocked:
            add(plan, "fallback")
    if len(chosen) != expected:
        raise RuntimeError("AlphaSchema could not fill the selection quota")
    if model is not None:
        predictions = model.predict([Plan.from_dict(row["plan"]) for row in chosen])
        order = sorted(range(len(chosen)), key=lambda i: predictions[i][0], reverse=True)
        for rank, index in enumerate(order, 1):
            chosen[index].update(
                reward_pred_mean=predictions[index][0],
                reward_pred_std=predictions[index][1],
                prediction_rank=rank,
            )
    return chosen


def realization_reward(report, config):
    if report is None or not report.expression_id:
        return None
    raw = report.diagnostics
    if raw.get("version") != DIAGNOSTICS or raw.get("split") != "train":
        return None
    if any(
        type(raw.get(k)) not in {int, float} or not math.isfinite(raw[k])
        for k in ("rank_ic", "rank_icir", "lag1_rank_ic")
    ):
        return None
    terms = {
        "rank_ic_term": config.reward_alpha * raw["rank_ic"],
        "rank_icir_term": config.reward_beta * raw["rank_icir"],
        "lag1_penalty": -config.lag1_penalty * max(0, raw["rank_ic"] - raw["lag1_rank_ic"]),
    }
    reward = sum(terms.values())
    return {"reward": reward, **terms} if math.isfinite(reward) else None


SYSTEM = (
    "Implement the supplied AlphaSchema semantic plan using only the shared Atlas DSL. "
    "Preserve Event, Context, every Quality, Direction and Output. Do not replace the plan "
    "or invent fields, operators, observations or performance. Use the given input frequency; "
    "periods count native input bars, not calendar days. Prefer available adjusted OHLC/VWAP. "
    "Return one JSON object with name, hypothesis and realizations: an array containing "
    '{"period": 20, "expression": "concrete Atlas DSL"} for EACH requested period. '
    "Use the same economic logic and expression structure, scaling windows and thresholds "
    "from each primary period. Windows must be literal positive integers satisfying operator "
    "minima, with no Python, variables or placeholders. The schema describes market semantics, "
    "not additional data permissions. If a required concept cannot be implemented with the "
    'available fields/operators, return {"error": "specific missing capability"}; do not '
    "silently change its meaning. Platform compilation, evaluation and admission are authoritative. "
    'For read-only research queries return {"queries": [{"name": "get_context", "arguments": {}}]}. '
    + READ_ONLY_QUERY_HELP
)


class AlphaSchemaSearch:
    def __init__(self, seed, config=None, *, models=None):
        self.config = config or AlphaSchemaConfig()
        self.models = models if models is not None else HTTPModels(self.config)
        self.space, self.seed = SchemaSpace(), seed
        self.round_index = 0
        self.history, self.batch = [], []
        self.work = self.pending = self.context = None
        self.usage = {
            "chat_requests": 0,
            "unknown_usage_requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        self._session = None
        self._checkpoint = lambda: None
        if hasattr(self.models, "on_retry"):
            self.models.on_retry = self._start_request

    @property
    def configuration(self):
        return json.loads(json.dumps(asdict(self.config)))

    def set_session(self, session):
        self._session = session

    def set_checkpoint(self, callback):
        self._checkpoint = callback

    def dump_state(self):
        return {
            "version": 1,
            "config": self.configuration,
            **{
                key: getattr(self, key)
                for key in (
                    "seed",
                    "round_index",
                    "history",
                    "batch",
                    "work",
                    "pending",
                    "context",
                    "usage",
                )
            },
        }

    def load_state(self, state):
        if state.get("version") != 1 or state["config"] != self.configuration:
            raise ValueError("AlphaSchema method configuration changed")
        for key in self.dump_state().keys() - {"version", "config"}:
            setattr(self, key, state[key])

    def _start_request(self, kind):
        self.usage[f"{kind}_requests"] += 1
        self.usage["unknown_usage_requests"] += 1
        self._checkpoint()

    def _select(self, remaining):
        history = [Plan.from_dict(row["plan"]) for row in self.history]
        observations = [
            (plan, row["reward"])
            for plan, row in zip(history, self.history, strict=True)
            if row["reward"] is not None
        ]
        quota = quota_for(len(observations), self.config.schedule)
        candidates = self.space.sample(
            self.config.candidate_pool_size,
            self.seed + self.round_index * 1009,
            self.config.quality_count_weights,
            {p.key for p in history},
        )
        model = None
        if quota["exploit"]:
            model = RewardEnsemble(self.config, self.seed + self.round_index).fit(observations)
        selected = select_batch(
            candidates,
            history,
            observations,
            quota,
            model,
            self.space,
            self.seed + self.round_index * 7919,
            self.config.mutation_weights,
        )
        self.batch = [
            dict(row, round_index=self.round_index)
            for row in selected[: math.ceil(remaining / len(self.config.periods))]
        ]
        self.round_index += 1
        terminal_progress(
            "AlphaSchema 计划选择",
            轮次=self.round_index,
            有效奖励=len(observations),
            计划数=len(self.batch),
            探索=quota["explore"],
            利用=quota["exploit"],
            变异=quota["mutate"],
        )
        self._checkpoint()

    def _parse_response(self, record):
        try:
            response = parse_json_object(record["text"])
            if "queries" in response and (response["queries"] != [] or len(response) == 1):
                queries = response["queries"]
                if not isinstance(queries, list) or not 1 <= len(queries) <= 16:
                    raise ValueError("expected 1..16 read-only queries")
                results = []
                for query in queries:
                    try:
                        if not isinstance(query, dict):
                            raise ValueError("query must be an object")
                        results.append(
                            self._session.query(query["name"], query.get("arguments", {}))
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        results.append({"error": str(exc)})
                record["query_results"] = results
                return
            if "error" in response:
                raise ValueError(str(response["error"]))
            rows = response.get("realizations")
            if (
                not isinstance(rows, list)
                or len(rows) != len(self.config.periods)
                or any(
                    not isinstance(row, dict)
                    or type(row.get("period")) is not int
                    or not isinstance(row.get("expression"), str)
                    or not row["expression"].strip()
                    for row in rows
                )
                or sorted(row["period"] for row in rows) != sorted(self.config.periods)
            ):
                raise ValueError(
                    "provide exactly one concrete expression for each requested period"
                )
            if (
                not isinstance(response.get("hypothesis"), str)
                or not response["hypothesis"].strip()
                or not isinstance(response.get("name"), str)
                or not response["name"].strip()
            ):
                raise ValueError("name and hypothesis must be nonempty text")
            record["result"] = response
        except (ValueError, TypeError, KeyError) as exc:
            record["error"] = str(exc)

    def _phase(self, repair):
        records = self.work["responses"].setdefault(str(repair), [])
        while True:
            if records and not ({"result", "error", "query_results"} & records[-1].keys()):
                self._parse_response(records[-1])
                self._checkpoint()
            if records and "result" in records[-1]:
                return records[-1]["result"]
            if records and "error" in records[-1]:
                raise ValueError(records[-1]["error"])
            if len(records) >= self.config.max_dialogue_rounds:
                raise ValueError("AlphaSchema query limit exhausted")
            self._start_request("chat")
            terminal_progress(
                "AlphaSchema 计划实现" if repair == 0 else "AlphaSchema 公式修复",
                计划=Plan.from_dict(self.work["plan"]).key,
                对话=f"{len(records) + 1}/{self.config.max_dialogue_rounds}",
            )
            text, usage = self.models.chat(
                SYSTEM
                + "\n"
                + render_factor_examples(
                    self.context.get("fields", ()), self.context.get("frequency")
                ),
                {
                    "context": self.context,
                    "plan": self.space.expand(Plan.from_dict(self.work["plan"])),
                    "periods": list(self.config.periods),
                    "repair": repair,
                    "previous_responses": self.work["responses"],
                    "validation": self.work["validation"],
                },
            )
            if isinstance(usage, dict) and all(
                type(usage.get(k)) is int and usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                self.usage["unknown_usage_requests"] -= 1
                for key in ("prompt_tokens", "completion_tokens"):
                    self.usage[key] += usage[key]
            records.append({"text": text})
            self._checkpoint()  # Persist raw response before parsing or read-only queries.

    def _realize(self):
        for repair in range(self.config.max_repairs + 1):
            try:
                result = self._phase(repair)
                rows = {r["period"]: r for r in result["realizations"]}
                validation = {
                    str(p): self._session.validate_expression(rows[p]["expression"])
                    for p in self.config.periods
                }
                self.work["validation"][str(repair)] = validation
                self._checkpoint()
                errors = [v["error"] for v in validation.values() if v["error"] is not None]
                if errors and repair < self.config.max_repairs:
                    continue
                self.work["candidates"] = [
                    {
                        "period": p,
                        "candidate": asdict(
                            Candidate(
                                rows[p]["expression"],
                                hypothesis=result["hypothesis"],
                                name=f"{result['name']}_p{p}",
                            )
                        ),
                    }
                    for p in self.config.periods
                ]
                return
            except ValueError as exc:
                self.work["validation"][str(repair)] = {"error": str(exc)}
                self._checkpoint()
        self.work["candidates"] = [
            {
                "period": None,
                "candidate": asdict(
                    Candidate(
                        "",
                        name="alphaschema_generation_error",
                        hypothesis="Semantic plan realization failed",
                    )
                ),
            }
        ]

    def ask(self, context, count=1):
        if count != 1 or self.pending is not None:
            raise ValueError("AlphaSchema requires one ask followed by one tell")
        if self._session is None or context.remaining_attempts <= 0:
            raise ValueError("AlphaSchema requires a session with remaining budget")
        if self.context is None:
            self.context = self._session.query("get_context", {})
        if self.context["evaluation_rules"]["search_diagnostics"]["version"] != DIAGNOSTICS:
            raise ValueError(f"AlphaSchema requires shared {DIAGNOSTICS} diagnostics")
        if self.work is None:
            if not self.batch:
                self._select(context.remaining_attempts)
            self.work = {**self.batch.pop(0), "responses": {}, "validation": {}, "results": []}
            self._checkpoint()
        if "candidates" not in self.work:
            self._realize()
            self._checkpoint()
        self.pending = self.work["candidates"][len(self.work["results"])]["candidate"]
        return [candidate_from_dict(self.pending)]

    def tell(self, feedback):
        if len(feedback) != 1 or self.pending is None:
            raise ValueError("AlphaSchema requires exactly one pending feedback")
        item = feedback[0]
        if candidate_from_dict(self.pending) != item.candidate or item.reason == "budget_rejected":
            raise ValueError("AlphaSchema feedback does not match its submitted candidate")
        realized = self.work["candidates"][len(self.work["results"])]
        score = realization_reward(item.report, self.config)
        self.work["results"].append(
            {
                "period": realized["period"],
                "factor_trial_index": item.factor_trial_index,
                "expression_id": item.report.expression_id if item.report else None,
                "accepted": item.accepted,
                "reason": item.reason,
                "diagnostics": item.report.diagnostics if item.report else {},
                "score": score,
            }
        )
        complete = len(self.work["results"]) == len(self.work["candidates"])
        if complete or self._session.remaining_budget() == 0:
            measured = [r for r in self.work["results"] if r["score"] is not None]
            best = (
                max(measured, key=lambda r: r["score"]["reward"]) if measured and complete else None
            )
            self.work.update(
                reward=best["score"]["reward"] if best else None,
                best_period=best["period"] if best else None,
                status="measured" if best else "failed" if complete else "budget_partial",
            )
            self.history.append(self.work)
            self.work = None
        self.pending = None
