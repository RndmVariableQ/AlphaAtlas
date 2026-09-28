"""AlphaPROBE search adapted to Atlas; paper equations 5--15, not its trading pipeline.

Reference: https://arxiv.org/abs/2602.11917 (2026), Guo et al.
Quality is absolute Atlas train IC. Numerical diversity uses training Pearson with
the asset's aggregation. The graph is method state, separate from library admission.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from functools import cache
from itertools import combinations
from urllib.parse import urlsplit

import numpy as np

from alpha_atlas.contracts import Candidate, Expression
from alpha_atlas.methods.models import HTTPModels
from alpha_atlas.methods.prompt_examples import render_factor_examples
from alpha_atlas.reporting import terminal_progress
from alpha_atlas.storage import candidate_from_dict


@dataclass(frozen=True)
class AlphaProbeConfig:
    pool_capacity: int = 50
    top_k: int = 15
    offspring: int = 5
    depth_penalty: float = 0.05
    retrieval_penalty: float = 0.10
    min_train_quality: float = 0.006
    initial_expressions: tuple[str, ...] = ()
    chat_model: str = ""
    embedding_model: str = ""
    chat_base_url: str = "https://api.openai.com/v1"
    embedding_base_url: str = "https://api.openai.com/v1"
    chat_key_env: str = "ALPHA_ATLAS_LLM_API_KEY"
    embedding_key_env: str = "ALPHA_ATLAS_EMBEDDING_API_KEY"
    temperature: float = 0.5
    max_output_tokens: int = 4096
    timeout_seconds: float = 120.0

    def __post_init__(self):
        for name in ("pool_capacity", "top_k", "offspring", "max_output_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("depth_penalty", "retrieval_penalty"):
            if not 0 <= getattr(self, name) < 1:
                raise ValueError(f"{name} must be in [0, 1)")
        if not 0 <= self.min_train_quality <= 1 or not 0 <= self.temperature <= 2:
            raise ValueError("invalid quality threshold or temperature")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout must be finite and positive")
        for endpoint in (self.chat_base_url, self.embedding_base_url):
            url = urlsplit(endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError("model endpoint must be an HTTP(S) base URL without credentials")
        if any(not isinstance(s, str) or not s.strip() for s in self.initial_expressions):
            raise ValueError("initial expressions must be nonempty DSL strings")

    @classmethod
    def from_mapping(cls, value):
        return cls(**{**value, "initial_expressions": tuple(value.get("initial_expressions", ()))})


@dataclass
class ProbeNode:
    factor_id: str
    expression: Expression
    description: str
    quality: float
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    depth: int = 0
    retrievals: int = 0
    embedding: list[float] | None = None


def expression_text(expr):
    if expr.op in {"field", "const"}:
        return ("$" if expr.op == "field" else "") + str(expr.value)
    return f"{expr.op}({','.join(expression_text(a) for a in expr.args)})"


def syntax_distance(left: Expression, right: Expression) -> float:
    """Normalized AST edit cost: replace a token, insert/delete a wrapper or subtree.

    Uses Atlas's explicit parameter leaves (including windows), unlike the upstream
    distance which ignores most window changes. Commutative operands can be swapped.
    """

    @cache
    def size(node):
        return 1 + sum(size(a) for a in node.args)

    @cache
    def distance(a, b):
        if a == b:
            return 0
        if not a.args and not b.args:
            return 1
        costs = [size(a) + size(b)]
        if len(a.args) == len(b.args):
            base = int((a.op, a.value) != (b.op, b.value))
            costs.append(base + sum(distance(x, y) for x, y in zip(a.args, b.args, strict=True)))
            commutative = {"ADD", "MULTIPLY", "MIN", "MAX", "TS_CORR", "TS_COV"}
            if a.op in commutative and b.op in commutative and len(a.args) >= 2:
                swapped = (b.args[1], b.args[0], *b.args[2:])
                costs.append(
                    base + sum(distance(x, y) for x, y in zip(a.args, swapped, strict=True))
                )
        costs.extend(size(a) - size(child) + distance(child, b) for child in a.args)
        costs.extend(size(b) - size(child) + distance(a, child) for child in b.args)
        return min(costs)

    return distance(left, right) / (size(left) + size(right))


def sigmoid(value):
    return 1 / (1 + math.exp(-max(-700.0, min(700.0, float(value)))))


class AlphaProbeSearch:
    """Resumable synchronous search; model calls occur only in ask, never in tell."""

    def __init__(self, seed, config=None, *, models=None):
        self.config = config or AlphaProbeConfig()
        self.models = models if models is not None else HTTPModels(self.config)
        self.rng = random.Random(seed)
        self.nodes: dict[str, ProbeNode] = {}
        self.correlations: dict[str, dict] = {}
        self.roots = None
        self.root_index = 0
        self.queue = []
        self.parents = []
        self.pending = None
        self.work = None
        self.batches = []
        self.retrievals = []
        self.generation_context = None
        self.last_evaluation = None
        self._session = None
        self.usage = {
            "chat_requests": 0,
            "embedding_requests": 0,
            "unknown_usage_requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "embedding_tokens": 0,
        }
        self._checkpoint = lambda: None
        if hasattr(self.models, "on_retry"):
            self.models.on_retry = self._start_request

    @property
    def configuration(self):
        return json.loads(json.dumps(asdict(self.config)))

    def set_checkpoint(self, callback):
        self._checkpoint = callback

    def set_session(self, session):
        self._session = session

    def _query(self, request):
        try:
            return self._session.query(request["name"], request.get("arguments", {}))
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": str(exc)}

    def dump_state(self):
        return {
            "version": 1,
            "config": self.configuration,
            "rng": self.rng.getstate(),
            "nodes": [asdict(n) for n in self.nodes.values()],
            **{
                key: getattr(self, key)
                for key in (
                    "correlations",
                    "roots",
                    "root_index",
                    "queue",
                    "parents",
                    "pending",
                    "work",
                    "batches",
                    "retrievals",
                    "generation_context",
                    "last_evaluation",
                    "usage",
                )
            },
        }

    def load_state(self, state):
        if state.get("version") != 1 or state["config"] != self.configuration:
            raise ValueError("AlphaPROBE method configuration changed")
        rng = state["rng"]
        self.rng.setstate((rng[0], tuple(rng[1]), rng[2]))
        self.nodes = {
            row["factor_id"]: ProbeNode(
                **{**row, "expression": Expression.from_dict(row["expression"])}
            )
            for row in state["nodes"]
        }
        for key in (
            "correlations",
            "roots",
            "root_index",
            "queue",
            "parents",
            "pending",
            "work",
            "batches",
            "retrievals",
            "generation_context",
            "last_evaluation",
            "usage",
        ):
            setattr(self, key, state[key])

    def active_pool(self):
        return sorted(self.nodes, key=lambda i: (-self.nodes[i].quality, i))[
            : self.config.pool_capacity
        ]

    @staticmethod
    def _pair(left, right):
        return "|".join(sorted((left, right)))

    def correlation_pairs(self):
        if self.queue or self.work is not None or self.parents:
            return ()
        pool = self.active_pool()
        pairs = set(combinations(sorted(pool), 2))
        for identity in pool:
            children = self.nodes[identity].children
            pairs.update(tuple(sorted((identity, child))) for child in children)
            pairs.update(combinations(sorted(children), 2))
        return tuple(sorted(pair for pair in pairs if self._pair(*pair) not in self.correlations))

    def _corr(self, left, right):
        if left == right:
            return 1.0
        return self.correlations.get(self._pair(left, right), {}).get("value")

    def score_pool(self):
        """Paper's unnormalized prior × likelihood; no upstream half-leaf quota."""
        pool = self.active_pool()
        if not pool:
            return {}
        qualities = np.array([self.nodes[i].quality for i in pool])
        mean, std = float(qualities.mean()), float(qualities.std())
        scores = {}
        for identity in pool:
            node = self.nodes[identity]
            prior = sigmoid((node.quality - mean) / max(std, 1e-12))
            prior *= (1 - self.config.depth_penalty) ** node.depth
            prior *= (1 - self.config.retrieval_penalty) ** node.retrievals
            if node.children:
                children = [self.nodes[i] for i in node.children]
                gain = sum(
                    (c.quality - node.quality) / max(node.quality, 1e-12) for c in children
                ) / len(children)
                pc = [self._corr(identity, c.factor_id) for c in children]
                cc = [self._corr(a.factor_id, b.factor_id) for a, b in combinations(children, 2)]
                if any(c is None for c in pc + cc):
                    likelihood = 0.0  # Unknown overlap is not evidence of diversity.
                else:
                    vertical = 1 - sum(pc) / len(pc)
                    horizontal = 1 - sum(cc) / len(cc) if cc else 1.0
                    likelihood = max(0.0, gain) * vertical * horizontal
            else:
                others = [self.nodes[i] for i in pool if i != identity]
                corr = [self._corr(identity, n.factor_id) for n in others]
                if any(c is None for c in corr):
                    likelihood = 0.0
                elif others:
                    value = 1 - abs(sum(corr) / len(corr))
                    if node.embedding is None or any(n.embedding is None for n in others):
                        raise ValueError("semantic embeddings are required for leaf retrieval")
                    cosine = sum(float(np.dot(node.embedding, n.embedding)) for n in others)
                    semantic = sigmoid(1 - cosine / len(others))
                    syntax = sum(syntax_distance(node.expression, n.expression) for n in others)
                    likelihood = value * semantic * syntax / len(others)
                else:
                    likelihood = 1.0  # No reference pool yet.
            scores[identity] = {
                "prior": prior,
                "likelihood": likelihood,
                "score": prior * likelihood,
            }
        return scores

    def _start_request(self, kind):
        terminal_progress(
            "模型请求",
            类型=kind,
            MODEL=getattr(self.config, f"{kind}_model"),
            请求次数=self.usage[f"{kind}_requests"] + 1,
        )
        self.usage[f"{kind}_requests"] += 1
        self.usage["unknown_usage_requests"] += 1
        self._checkpoint()

    def _finish_usage(self, kind, usage):
        terminal_progress("模型响应收到", 类型=kind)
        names = ("prompt_tokens", "completion_tokens") if kind == "chat" else ("total_tokens",)
        if isinstance(usage, dict) and all(
            type(usage.get(k)) is int and usage[k] >= 0 for k in names
        ):
            self.usage["unknown_usage_requests"] -= 1
            if kind == "chat":
                for key in names:
                    self.usage[key] += usage[key]
            else:
                self.usage["embedding_tokens"] += usage["total_tokens"]

    def _embed_pool(self):
        missing = [self.nodes[i] for i in self.active_pool() if self.nodes[i].embedding is None]
        if not missing:
            return
        self._start_request("embedding")
        vectors, usage = self.models.embed([n.description for n in missing])
        self._finish_usage("embedding", usage)
        self._checkpoint()
        try:
            array = np.asarray(vectors, dtype=float)
            if array.ndim != 2 or array.shape[0] != len(missing) or not array.shape[1]:
                raise ValueError("shape")
            lengths = np.linalg.norm(array, axis=1)
            if (
                not np.isfinite(array).all()
                or not np.isfinite(lengths).all()
                or (lengths <= 0).any()
            ):
                raise ValueError("nonfinite or zero")
            dimensions = {len(n.embedding) for n in self.nodes.values() if n.embedding is not None}
            if dimensions and dimensions != {array.shape[1]}:
                raise ValueError("dimensions changed")
        except (TypeError, ValueError):
            raise ValueError(
                "embedding vectors must be finite, nonzero and dimensionally consistent"
            ) from None
        for node, vector in zip(missing, array / lengths[:, None], strict=True):
            node.embedding = vector.tolist()
        self._checkpoint()

    def _trace(self, identity):
        result = []
        while identity is not None:
            node = self.nodes[identity]
            result.append(
                {
                    "factor_id": identity,
                    "expression": expression_text(node.expression),
                    "hypothesis": node.description,
                    "train_quality": node.quality,
                }
            )
            identity = node.parent
        return list(reversed(result))

    def _generate(self, context):
        if self.work is None:
            if not self.parents:
                self._embed_pool()
                scores = self.score_pool()
                # Seeded tie-breaking also permits progress when all scores are zero.
                ties = {i: self.rng.random() for i in scores}
                self.parents = sorted(scores, key=lambda i: (-scores[i]["score"], ties[i]))[
                    : self.config.top_k
                ]
                for identity in self.parents:
                    self.nodes[identity].retrievals += 1
                self.retrievals.append({"scores": scores, "selected": list(self.parents)})
            parent = self.parents.pop(0) if self.parents else None
            self.work = len(self.batches)
            self.batches.append(
                {
                    "parent": parent,
                    "trace": self._trace(parent),
                    "count": min(self.config.offspring, context.remaining_attempts),
                    "responses": {},
                    "outcomes": [],
                }
            )
            self._checkpoint()
        batch = self.batches[self.work]
        payload = {
            "context": self.generation_context,
            "ancestor_trace": batch["trace"],
            "count": batch["count"],
            "evaluation_result": self.last_evaluation,
        }
        instructions = {
            "analyst": 'Return {"strategies": [strings]} with exactly count distinct modification '
            "strategies. Use the full ancestor trace to avoid repeating edits. With no "
            "ancestor, independently propose count root hypotheses covering distinct financial "
            "mechanisms using the shared research context. Reason about financial mechanisms.",
            "execution": 'Return {"candidates": [{"expression": "Atlas DSL", "hypothesis": '
            '"financial explanation"}]} with exactly count entries, one per strategy. '
            "Query available fields/operators and expression rules before using them.",
            "validator": "Check each candidate for syntax, types, causality, available fields, "
            "positive integer windows and complexity limits. Repair errors without changing the intended "
            'hypothesis. Return {"candidates": [{"expression": "Atlas DSL", '
            '"hypothesis": "financial explanation"}]} with exactly count entries '
            "in the same order. Do not invent observed performance.",
        }
        try:
            examples = render_factor_examples(
                self.generation_context.get("fields", ()),
                self.generation_context.get("frequency"),
            )
            for phase in ("analyst", "execution", "validator"):
                while phase not in batch["responses"]:
                    queries = batch.setdefault("queries", {}).setdefault(phase, [])
                    terminal_progress("LLM 生成阶段", 角色=phase)
                    self._start_request("chat")
                    text, usage = self.models.chat(
                        "You research alpha factors. Return only a JSON object. "
                        'For shared read-only tools, return {"queries": [{"name": '
                        '"get_context", "arguments": {}}]}. '
                        "Available queries: get_context(), library_search(query='', source='all', "
                        "offset=0, limit=10), library_get(factor_id), library_list(offset=0, limit=20), "
                        "library_stats(). get_context returns all allowed fields, full operators, "
                        "expression and evaluation rules, and reference-library settings. "
                        "Reference access follows the shared run configuration; no automatic admission. "
                        "Candidates are evaluated by the same platform evaluate operation after proposal. "
                        "At most 8 query rounds, 16 requests per round. " + examples + "\n"
                        "Use query_results to produce the final response required below. "
                        + instructions[phase],
                        {
                            **payload,
                            "previous_stages": dict(batch["responses"]),
                            "query_results": list(queries),
                        },
                    )
                    self._finish_usage("chat", usage)
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        parsed = None  # Persist malformed responses before the usual rejection.
                    if isinstance(parsed, dict) and "queries" in parsed:
                        if len(queries) >= 8:
                            raise ValueError("metadata query round budget exhausted")
                        requests = parsed["queries"]
                        if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
                            raise ValueError("invalid metadata queries")
                        if any(not isinstance(r, dict) for r in requests):
                            raise ValueError("invalid metadata query")
                        queries.append(
                            {"requests": requests, "results": [self._query(r) for r in requests]}
                        )
                        self._checkpoint()
                        continue
                    batch["responses"][phase] = text
                    self._checkpoint()  # Raw response survives parse errors and interrupted next phases.
                response = json.loads(batch["responses"][phase])
                if phase == "analyst":
                    rows = response["strategies"]
                    valid = all(isinstance(s, str) and s.strip() for s in rows)
                else:
                    rows = response["candidates"]
                    valid = all(
                        isinstance(r, dict)
                        and all(
                            isinstance(r.get(k), str) and r[k].strip()
                            for k in ("expression", "hypothesis")
                        )
                        for r in rows
                    )
                if not isinstance(rows, list) or len(rows) != batch["count"] or not valid:
                    raise ValueError("generation schema")
            self.queue = [
                {
                    "candidate": asdict(
                        Candidate(
                            r["expression"],
                            hypothesis=r["hypothesis"],
                            name=f"alphaprobe:{self.work}:{i}",
                        )
                    ),
                    "parent": batch["parent"],
                    "batch": self.work,
                }
                for i, r in enumerate(rows)
            ]
        except (ValueError, TypeError, KeyError):
            batch["error"] = "invalid_generation_response"
            # A malformed response consumes a recorded attempt; never substitute a fake factor.
            self.queue = [
                {
                    "candidate": asdict(
                        Candidate(
                            "",
                            name="alphaprobe_generation_error",
                            hypothesis="Invalid model JSON/schema",
                        )
                    ),
                    "parent": batch["parent"],
                    "batch": self.work,
                }
            ]
        self.work = None
        self._checkpoint()

    def ask(self, context, count=1):
        if count != 1 or self.pending is not None:
            raise ValueError("AlphaPROBE requires one ask followed by one tell")
        if context.remaining_attempts <= 0:
            raise ValueError("attempt budget exhausted")
        for result in self._session.factor_correlations(self.correlation_pairs()):
            if result.split != "train":
                raise ValueError("AlphaPROBE correlations must be training-only")
            self.correlations[self._pair(result.left, result.right)] = asdict(result)
        self.generation_context = self._session.query("get_context", {})
        if self.roots is None:
            self.roots = [
                asdict(Candidate(s, hypothesis=f"Configured initial hypothesis: {s}"))
                for s in self.config.initial_expressions
            ]
        if self.root_index < len(self.roots):
            self.pending = {"candidate": self.roots[self.root_index], "parent": None, "batch": None}
            self.root_index += 1
        else:
            if not self.queue:
                self._generate(context)
            self.pending = self.queue.pop(0)
        return [candidate_from_dict(self.pending["candidate"])]

    def tell(self, results):
        if len(results) != 1 or self.pending is None:
            raise ValueError("AlphaPROBE has no matching pending candidate")
        feedback = results[0]
        if feedback.candidate != candidate_from_dict(self.pending["candidate"]):
            raise ValueError("feedback candidate differs from pending candidate")
        if self._session is not None:
            self.last_evaluation = self._session.evaluation_result(feedback)
        report = feedback.report
        train = (
            next((m.value for m in report.metrics if m.split == "train"), None) if report else None
        )
        quality = abs(train) if train is not None and math.isfinite(train) else None
        if self.pending["batch"] is not None:
            self.batches[self.pending["batch"]]["outcomes"].append(
                {
                    "trial": feedback.trial_index,
                    "factor_id": report.expression_id if report else None,
                    "reason": feedback.reason,
                    "accepted": feedback.accepted,
                    "train_quality": quality,
                }
            )
        if (
            report
            and report.status == "success"
            and report.canonical_expression is not None
            and quality is not None
            and quality > 0
            and (self.pending["parent"] is None or quality >= self.config.min_train_quality)
            and report.expression_id not in self.nodes
        ):
            parent = self.pending["parent"]
            node = ProbeNode(
                report.expression_id,
                report.canonical_expression,
                feedback.candidate.hypothesis or expression_text(report.canonical_expression),
                quality,
                parent=parent,
                depth=self.nodes[parent].depth + 1 if parent else 0,
            )
            self.nodes[node.factor_id] = node
            if parent:
                self.nodes[parent].children.append(node.factor_id)
        self.pending = None
