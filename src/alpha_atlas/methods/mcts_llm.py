"""Formula-node MCTS, adapted from arXiv:2505.11122v3 to shared Atlas feedback.

Implementation decisions and third-party source comparison: docs/mcts_llm.md.
Original implementation here; no upstream data, Qlib execution or admission code.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from alpha_atlas.contracts import Candidate, Expression
from alpha_atlas.methods.models import HTTPModels
from alpha_atlas.methods.prompt_examples import render_factor_examples
from alpha_atlas.reporting import terminal_progress
from alpha_atlas.storage import candidate_from_dict

DIMENSIONS = ("effectiveness", "stability", "turnover", "diversity", "overfitting")


@dataclass(frozen=True)
class MCTSLLMConfig:
    chat_model: str = ""
    chat_base_url: str = "http://127.0.0.1:8001/v1"
    chat_key_env: str = "ALPHA_ATLAS_LLM_API_KEY"
    temperature: float = 1.0
    repair_temperature: float = 0.8
    risk_temperature: float = 0.1
    timeout_seconds: float = 120.0
    exploration: float = 1.0
    dimension_temperature: float = 1.0
    tree_budget: int = 3
    budget_increment: int = 1
    fsa_top_k: int = 3
    max_formula_repairs: int = 3
    max_dialogue_rounds: int = 8
    tree_selection: bool = True

    def __post_init__(self):
        for key in ("tree_budget", "max_dialogue_rounds"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("budget_increment", "fsa_top_k", "max_formula_repairs"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError(f"{key} must be a nonnegative integer")
        for key in (
            "timeout_seconds",
            "dimension_temperature",
            "exploration",
            "temperature",
            "repair_temperature",
            "risk_temperature",
        ):
            value = getattr(self, key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{key} must be finite")
        if self.timeout_seconds <= 0 or self.dimension_temperature <= 0:
            raise ValueError("timeout and dimension temperature must be positive")
        if self.exploration < 0 or any(
            not 0 <= t <= 2
            for t in (self.temperature, self.repair_temperature, self.risk_temperature)
        ):
            raise ValueError("invalid exploration or model temperature")
        if type(self.tree_selection) is not bool:
            raise ValueError("tree_selection must be boolean")
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

    @classmethod
    def from_mapping(cls, value):
        return cls(**value)


def relative_score(value, reference, *, higher=True):
    """Eq.6--7, including ties. An empty measured reference has an explicit 0.5 prior."""
    if value is None or not math.isfinite(value):
        return None
    known = [v for v in reference if v is not None and math.isfinite(v)]
    if not known:
        return 0.5
    better = sum(v > value if higher else v < value for v in known)
    return 1 - better / len(known)


def dimension_probabilities(scores, temperature):
    # Shift before dividing: tiny positive T must not produce inf - inf.
    minimum = min(scores.values())
    weights = [math.exp(-(scores[d] - minimum) / temperature) for d in DIMENSIONS]
    return [w / sum(weights) for w in weights]


def abstract_tree(expression):
    return [
        expression.op,
        "t" if expression.op == "const" else expression.value,
        [abstract_tree(a) for a in expression.args],
    ]


def contains_tree(tree, pattern):
    return tree == pattern or any(contains_tree(child, pattern) for child in tree[2])


def root_genes(expression):
    """Parameterized operator subtrees with at least one field; per-formula set support."""
    patterns = set()

    def walk(tree):
        children_have_fields = [walk(child) for child in tree[2]]
        has_field = tree[0] == "field" or any(children_have_fields)
        if tree[2] and has_field:
            patterns.add(json.dumps(tree, separators=(",", ":")))
        return has_field

    walk(abstract_tree(expression))
    return patterns


def frequent_patterns(counts, top_k):
    trees = {key: json.loads(key) for key in counts}
    closed = [
        key
        for key, tree in trees.items()
        if not any(
            key != other and counts[key] == counts[other] and contains_tree(parent, tree)
            for other, parent in trees.items()
        )
    ]
    return [trees[key] for key in sorted(closed, key=lambda key: (-counts[key], key))[:top_k]]


def pattern_text(tree):
    if tree[0] in {"field", "const"}:
        return ("$" if tree[0] == "field" else "") + str(tree[1])
    return f"{tree[0]}({','.join(pattern_text(child) for child in tree[2])})"


SYSTEM = (
    "Research formulaic alpha factors using only the shared Atlas research context. "
    "Return a JSON object matching the current phase instruction. Do not invent observed "
    "performance. Rules, fields, full operator semantics and reference access come from context. "
    'To query shared read-only tools, return {"queries": [{"name": "get_context", '
    "\"arguments\": {}}]}. Tools: get_context(), library_search(query='', source='all', "
    "offset=0, limit=10), library_get(factor_id), library_list(offset=0, limit=20), library_stats(). "
    "At most 16 requests per reply. Reference definitions have no measured performance. "
    "The runner submits each proposed candidate to the common evaluate; you cannot admit it. "
    "Use parent/children/sibling refinement history to avoid repeated edits. "
    "Forbidden patterns abstract numerical constants to t; avoid those exact AST motifs."
)


class MCTSLLMSearch:
    def __init__(self, seed, config=None, *, models=None):
        self.config = config or MCTSLLMConfig()
        self.models = models if models is not None else HTTPModels(self.config)
        self.rng = random.Random(seed)
        self.nodes = {}
        self.members = {}
        self.pattern_counts = {}
        self.root = None
        self.tree_remaining = 0
        self.trees_started = 0
        self.work = None
        self.pending = None
        self.steps = []
        self.last_evaluation = None
        self.context = None
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

    def _start_request(self, kind):
        self.usage[f"{kind}_requests"] += 1
        self.usage["unknown_usage_requests"] += 1
        self._checkpoint()

    @property
    def configuration(self):
        return asdict(self.config)

    def set_session(self, session):
        self._session = session

    def set_checkpoint(self, callback):
        self._checkpoint = callback

    def dump_state(self):
        return {
            "version": 1,
            "config": self.configuration,
            "rng": self.rng.getstate(),
            **{
                key: getattr(self, key)
                for key in (
                    "nodes",
                    "members",
                    "pattern_counts",
                    "root",
                    "tree_remaining",
                    "trees_started",
                    "work",
                    "pending",
                    "steps",
                    "last_evaluation",
                    "context",
                    "usage",
                )
            },
        }

    def load_state(self, state):
        if state.get("version") != 1 or state["config"] != self.configuration:
            raise ValueError("MCTS-LLM method configuration changed")
        rng = state["rng"]
        self.rng.setstate((rng[0], tuple(rng[1]), rng[2]))
        for key in self.dump_state().keys() - {"version", "config", "rng"}:
            setattr(self, key, state[key])

    def select_node(self):
        current = self.root
        if not self.config.tree_selection:
            choices = []
            todo = [current]
            while todo:
                identity = todo.pop()
                choices.append(identity)
                todo.extend(self.nodes[identity]["children"])
            return self.rng.choice(choices)
        while self.nodes[current]["children"]:
            node = self.nodes[current]

            def uct(q, visits, parent_visits=node["visits"]):
                return q + self.config.exploration * math.sqrt(math.log(parent_visits) / visits)

            options = [(current, uct(node["q"], 1 + len(node["children"])))]
            options += [
                (child, uct(self.nodes[child]["q"], self.nodes[child]["visits"]))
                for child in node["children"]
            ]
            selected = max(options, key=lambda item: item[1])[0]
            if selected == current:
                break
            current = selected
        return current

    def _portrait(self, identity):
        node = self.nodes[identity]
        return {
            "candidate": node["candidate"],
            "scores": node["scores"],
            "raw_metrics": node["raw"],
            "refinement": node["refinement"],
        }

    def _history(self, parent):
        if parent is None:
            return {}
        node = self.nodes[parent]
        ancestor = node["parent"]
        ancestors = []
        current = ancestor
        while current is not None:
            ancestors.append(self._portrait(current))
            current = self.nodes[current]["parent"]
        return {
            "selected": self._portrait(parent),
            "parent": self._portrait(ancestor) if ancestor else None,
            "ancestors": list(reversed(ancestors)),
            "children": [self._portrait(i) for i in node["children"]],
            "siblings": [self._portrait(i) for i in self.nodes[ancestor]["children"] if i != parent]
            if ancestor
            else [],
        }

    def _examples(self, parent, dimension):
        # Appendix D: one exemplar; quality after lower-half correlation filtering,
        # least correlated for diversity, zero-shot for turnover and overfitting.
        if parent is None or dimension in {"turnover", "overfitting"}:
            return []
        identities = [identity for identity in self.members if identity != parent]
        pairs = [(parent, identity) for identity in identities]
        correlations = self._session.factor_correlations(pairs)
        ordered = sorted(
            (
                (identity, abs(row.value))
                for identity, row in zip(identities, correlations, strict=True)
                if row.value is not None
            ),
            key=lambda item: (item[1], item[0]),
        )
        if not ordered:
            return []
        if dimension == "diversity":
            selected = ordered[0][0]
        else:
            shortlist = [i for i, _ in ordered[: max(1, math.ceil(len(ordered) / 2))]]
            valid = [i for i in shortlist if self.members[i]["raw"][dimension] is not None]
            if not valid:
                return []
            selected = max(valid, key=lambda i: self.members[i]["raw"][dimension])
        return [self._session.library_get(selected)]

    def _phase(self, phase, instruction, validate):
        records = self.work["responses"].setdefault(phase, [])
        while True:
            if records and not ({"result", "error", "query_results"} & records[-1].keys()):
                self._parse_response(records[-1], validate)
                self._checkpoint()
            if records and "result" in records[-1]:
                return records[-1]["result"]
            if len(records) >= self.config.max_dialogue_rounds:
                raise ValueError(f"{phase}: response/query limit exhausted")
            self._start_request("chat")
            terminal_progress("MCTS-LLM 模型阶段", 阶段=phase)
            text, usage = self.models.chat(
                SYSTEM
                + "\n\n"
                + render_factor_examples(
                    self.context.get("fields", ()), self.context.get("frequency")
                )
                + "\n"
                + instruction,
                {
                    "phase": phase,
                    "context": self.context,
                    "evaluation_result": self.last_evaluation,
                    "dimension": self.work["dimension"],
                    "candidate": self.work.get("candidate"),
                    "history": self.work["history"],
                    "examples": self.work["examples"],
                    "forbidden_patterns": [pattern_text(p) for p in self.work["forbidden"]],
                    "previous_stages": self.work["responses"],
                    "validation": self.work["validation"],
                },
                temperature=(
                    self.config.risk_temperature
                    if phase == "risk"
                    else self.config.repair_temperature
                    if phase.startswith("formula_") and phase != "formula_0"
                    else self.config.temperature
                ),
            )
            if isinstance(usage, dict) and all(
                type(usage.get(k)) is int and usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                self.usage["unknown_usage_requests"] -= 1
                for key in ("prompt_tokens", "completion_tokens"):
                    self.usage[key] += usage[key]
            record = {"text": text}
            records.append(record)
            self._checkpoint()  # Save responses before parsing or executing read-only queries.
            self._parse_response(record, validate)
            self._checkpoint()

    def _parse_response(self, record, validate):
        try:
            response = json.loads(record["text"])
            if not isinstance(response, dict):
                raise ValueError("response must be an object")
            if "queries" in response:
                requests = response["queries"]
                if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
                    raise ValueError("expected 1..16 read-only queries")
                results = []
                for request in requests:
                    try:
                        results.append(
                            self._session.query(request["name"], request.get("arguments", {}))
                        )
                    except (TypeError, KeyError, ValueError) as exc:
                        results.append({"error": str(exc)})
                record["query_results"] = results
            else:
                validate(response)
                record["result"] = response
        except (ValueError, TypeError, KeyError) as exc:
            record["error"] = str(exc)

    def _generate(self):
        def text_schema(key):
            def validate(response):
                if not isinstance(response.get(key), str) or not response[key].strip():
                    raise ValueError(f"expected nonempty {key}")

            return validate

        self._phase(
            "suggestion",
            'Return {"suggestion": "financial hypothesis and targeted '
            'refinement"}. With no selected node, propose a new root hypothesis.',
            text_schema("suggestion"),
        )
        candidate = None
        for repair in range(self.config.max_formula_repairs + 1):
            response = self._phase(
                f"formula_{repair}",
                "Translate the suggestion into one concrete formula; repair "
                'any validation errors. Return {"expression": "Atlas DSL", "hypothesis": "reason"}. '
                "Use concrete positive integer windows, not symbolic parameters.",
                text_schema("expression"),
            )
            candidate = Candidate(response["expression"], hypothesis=response.get("hypothesis", ""))
            if not isinstance(candidate.hypothesis, str):
                raise ValueError("hypothesis must be text")
            checked = self._session.validate_expression(candidate.expression)
            if checked["error"] is None:
                tree = abstract_tree(Expression.from_dict(checked["expression"]))
                if any(contains_tree(tree, p) for p in self.work["forbidden"]):
                    checked["error"] = "formula contains a forbidden FSA pattern"
            self.work["validation"][str(repair)] = checked
            self._checkpoint()
            if checked["error"] is None:
                break
        if checked["error"] is not None:
            self.work["error"] = checked["error"]
            if checked["expression"] is not None:
                return Candidate(
                    "", hypothesis="FSA correction exhausted", name="mcts_llm_generation_error"
                )
            return candidate  # Preserve the actual invalid DSL as a charged compile failure.

        self.work["candidate"] = asdict(candidate)

        def risk_schema(response):
            score = response.get("score")
            if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("risk score must be finite in [0,1], high means low risk")
            text_schema("reason")(response)

        risk = self._phase(
            "risk",
            "Assess the selected candidate formula and its refinement history "
            "for excessive complexity, parameter fitting and repeated tuning. Return "
            '{"score": 0.0, "reason": "explanation"}; high score means LOW overfitting '
            "risk. This is a qualitative judgment, not measured OOS performance.",
            risk_schema,
        )
        self.work["risk"] = risk
        return candidate

    def ask(self, context, count=1):
        if count != 1 or self.pending is not None:
            raise ValueError("MCTS-LLM requires one ask followed by one tell")
        if context.remaining_attempts <= 0:
            raise ValueError("attempt budget exhausted")
        if self.context is None:
            self.context = self._session.query("get_context", {})
        if self.context["evaluation_rules"]["search_diagnostics"]["version"] != "icir_turnover_v1":
            raise ValueError("MCTS-LLM requires shared icir_turnover_v1 diagnostics")
        if self.work is None:
            if self.tree_remaining <= 0:
                self.root = None
            parent = self.select_node() if self.root else None
            dimension = (
                self.rng.choices(
                    DIMENSIONS,
                    dimension_probabilities(
                        self.nodes[parent]["scores"], self.config.dimension_temperature
                    ),
                )[0]
                if parent
                else None
            )
            self.work = {
                "parent": parent,
                "dimension": dimension,
                "history": self._history(parent),
                "examples": self._examples(parent, dimension),
                "library_before": list(self.members),
                "library_version": self._session.get_library().version,
                "forbidden": frequent_patterns(self.pattern_counts, self.config.fsa_top_k),
                "responses": {},
                "validation": {},
                "risk": None,
            }
            self._checkpoint()
        try:
            candidate = self._generate()
        except (ValueError, TypeError, KeyError) as exc:
            self.work["error"] = str(exc)
            candidate = Candidate("", hypothesis=str(exc), name="mcts_llm_generation_error")
        self.pending = asdict(candidate)
        return [candidate]

    def tell(self, results):
        if len(results) != 1 or self.pending is None:
            raise ValueError("MCTS-LLM expects one pending feedback")
        feedback = results[0]
        if feedback.candidate != candidate_from_dict(self.pending):
            raise ValueError("feedback does not match pending candidate")
        report = feedback.report
        references = self.work["library_before"]
        raw = {d: None for d in DIMENSIONS}
        identity = report.expression_id if report else None
        if report and report.direction is not None:
            train = next((m.value for m in report.metrics if m.split == "train"), None)
            raw.update(
                effectiveness=report.direction * train if train is not None else None,
                stability=report.diagnostics.get("icir"),
                turnover=report.diagnostics.get("turnover"),
            )
            peers = [i for i in references if i != identity]
            correlations = self._session.factor_correlations([(identity, other) for other in peers])
            known = [abs(row.value) for row in correlations if row.value is not None]
            raw["diversity"] = (
                1 - max(known) if known and len(known) == len(peers) else 1.0 if not peers else None
            )
            raw["overfitting"] = self.work["risk"]["score"] if self.work["risk"] else None
        scores = {
            d: relative_score(
                raw[d],
                [self.members[i]["raw"][d] for i in references if i != identity],
                higher=d != "turnover",
            )
            for d in DIMENSIONS[:-1]
        }
        scores["overfitting"] = raw["overfitting"]
        parent = self.work["parent"]
        if parent is not None:
            self.tree_remaining -= 1
        reason = "scored"
        reward = None
        if any(value is None for value in scores.values()):
            reason = "incomplete_search_feedback"
        elif identity in self.nodes:
            reason = "duplicate_search_node"
        else:
            reward = sum(scores.values()) / len(DIMENSIONS)
            self.nodes[identity] = {
                "candidate": self.pending,
                "parent": parent,
                "children": [],
                "visits": 1,
                "q": reward,
                "score": reward,
                "scores": scores,
                "raw": raw,
                "refinement": self.work["responses"]["suggestion"][-1]["result"]["suggestion"],
            }
            if parent is None:
                self.root = identity
                self.tree_remaining = self.config.tree_budget
                self.trees_started += 1
            else:
                if reward > self.nodes[self.root]["q"]:
                    self.tree_remaining += self.config.budget_increment
                self.nodes[parent]["children"].append(identity)
                current = parent
                while current is not None:
                    self.nodes[current]["visits"] += 1
                    self.nodes[current]["q"] = max(self.nodes[current]["q"], reward)
                    current = self.nodes[current]["parent"]
        if feedback.accepted:
            self.members[identity] = {"raw": raw}
            for pattern in root_genes(report.canonical_expression):
                self.pattern_counts[pattern] = self.pattern_counts.get(pattern, 0) + 1
        self.last_evaluation = self._session.evaluation_result(feedback)
        self.steps.append(
            {
                **self.work,
                "trial_index": feedback.trial_index,
                "candidate": self.pending,
                "raw": raw,
                "scores": scores,
                "reward": reward,
                "reason": reason,
                "accepted": feedback.accepted,
            }
        )
        self.pending = None
        self.work = None
