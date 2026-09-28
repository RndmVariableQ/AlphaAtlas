"""All four methods share an identical finite grammar and receive identical feedback."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from alpha_atlas.contracts import Candidate, Expression, Region, SearchContext, TrialFeedback

OPS = ("return", "delta", "mean", "std", "zscore")
COMBINES = ("single", "add", "sub", "mul", "div")


def default_regions() -> tuple[Region, ...]:
    return tuple(
        Region(
            op, ("price_volume",), f"Explore {op} and field interactions", source="baseline_grammar"
        )
        for op in OPS
    )


def dimensions(context: SearchContext, fields) -> tuple[tuple, ...]:
    # A finite baseline search strategy, not a platform window allowlist.
    windows = (3, 6, 12, 24, 48, 96) if context.frequency == "5m" else (5, 10, 20, 60, 120)
    return (OPS, fields, fields, windows, COMBINES)


def decode(genome: tuple) -> Candidate:
    op, field_a, field_b, window, combine = genome
    a = Expression("field", value=field_a)
    base = (
        a if combine == "single" else Expression(combine, (a, Expression("field", value=field_b)))
    )
    return Candidate(
        Expression(op, (base,), window), (op,), f"{op}({field_a}, {field_b}, {window}, {combine})"
    )


def reward(feedback: TrialFeedback) -> float:
    if feedback.report is None:
        return 0.0
    valid = next((m.value for m in feedback.report.metrics if m.split == "val"), None)
    return float(feedback.accepted) + max(0.0, valid or 0.0)


class RandomSearch:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.fields = ("close", "volume")

    def set_session(self, session):
        self._session = session
        self.fields = session.query("get_context", {})["fields"]

    def ask(self, context: SearchContext, count: int = 1) -> list[Candidate]:
        dims = dimensions(context, self.fields)
        return [decode(tuple(self.rng.choice(d) for d in dims)) for _ in range(count)]

    def tell(self, results: list[TrialFeedback]) -> None:
        pass

    def dump_state(self) -> dict:
        return {"rng": self.rng.getstate()}

    def load_state(self, state: dict) -> None:
        version, internal, gaussian = state["rng"]
        self.rng.setstate((version, tuple(internal), gaussian))


class GeneticSearch(RandomSearch):
    """Steady-state typed grammar GP with subtree-equivalent field/operator crossover."""

    def __init__(self, seed: int, population_size: int = 32):
        super().__init__(seed)
        self.size = population_size
        self.population: list[tuple[float, tuple]] = []
        self.pending: dict[str, tuple] = {}

    def dump_state(self) -> dict:
        return {
            **super().dump_state(),
            "size": self.size,
            "population": self.population,
            "pending": self.pending,
        }

    def load_state(self, state: dict) -> None:
        super().load_state(state)
        self.size = state["size"]
        self.population = [(score, tuple(genome)) for score, genome in state["population"]]
        self.pending = {key: tuple(genome) for key, genome in state["pending"].items()}

    def ask(self, context: SearchContext, count: int = 1) -> list[Candidate]:
        result = []
        dims = dimensions(context, self.fields)
        for _ in range(count):
            if len(self.population) < 4:
                genome = tuple(self.rng.choice(d) for d in dims)
            else:

                def tournament():
                    return max(self.rng.sample(self.population, 3), key=lambda item: item[0])[1]

                left, right = tournament(), tournament()
                genes = [self.rng.choice((a, b)) for a, b in zip(left, right, strict=True)]
                position = self.rng.randrange(len(dims))
                genes[position] = self.rng.choice(dims[position])
                genome = tuple(genes)
            candidate = decode(genome)
            self.pending[candidate.expression.expression_id] = genome
            result.append(candidate)
        return result

    def tell(self, results: list[TrialFeedback]) -> None:
        for feedback in results:
            genome = self.pending.pop(feedback.candidate.expression.expression_id)
            self.population.append((reward(feedback), genome))
        self.population.sort(key=lambda item: item[0], reverse=True)
        self.population = self.population[: self.size]


@dataclass
class Node:
    visits: int = 0
    total: float = 0.0
    children: dict[object, Node] = field(default_factory=dict)


class MCTSSearch(RandomSearch):
    """UCT selection, one-node expansion, random rollout and reward backpropagation."""

    def __init__(self, seed: int):
        super().__init__(seed)
        self.root = Node()
        self.pending: dict[str, list[Node]] = {}

    def dump_state(self) -> dict:
        nodes, indexes = [], {}

        def visit(node):
            index = len(nodes)
            indexes[id(node)] = index
            row = {"visits": node.visits, "total": node.total, "children": []}
            nodes.append(row)
            row["children"] = [(key, visit(child)) for key, child in node.children.items()]
            return index

        visit(self.root)
        return {
            **super().dump_state(),
            "nodes": nodes,
            "pending": {key: [indexes[id(n)] for n in path] for key, path in self.pending.items()},
        }

    def load_state(self, state: dict) -> None:
        super().load_state(state)
        nodes = [Node(row["visits"], row["total"]) for row in state["nodes"]]
        for node, row in zip(nodes, state["nodes"], strict=True):
            node.children = {key: nodes[index] for key, index in row["children"]}
        self.root = nodes[0]
        self.pending = {key: [nodes[i] for i in path] for key, path in state["pending"].items()}

    def ask(self, context: SearchContext, count: int = 1) -> list[Candidate]:
        if count != 1:
            raise ValueError("this sequential UCT baseline asks one candidate at a time")
        dims = dimensions(context, self.fields)
        node, path, genes = self.root, [self.root], []
        for depth, options in enumerate(dims):
            unseen = [value for value in options if value not in node.children]
            if unseen:
                selected = self.rng.choice(unseen)
                child = Node()
                node.children[selected] = child
                genes.append(selected)
                path.append(child)
                genes.extend(self.rng.choice(d) for d in dims[depth + 1 :])
                break
            selected = max(options, key=lambda v: self._ucb(node.children[v], node.visits))
            genes.append(selected)
            node = node.children[selected]
            path.append(node)
        candidate = decode(tuple(genes))
        self.pending[candidate.expression.expression_id] = path
        return [candidate]

    @staticmethod
    def _ucb(node: Node, parent_visits: int) -> float:
        if node.visits == 0:
            return math.inf
        return node.total / node.visits + math.sqrt(
            2 * math.log(max(1, parent_visits)) / node.visits
        )

    def tell(self, results: list[TrialFeedback]) -> None:
        for feedback in results:
            for node in self.pending.pop(feedback.candidate.expression.expression_id):
                node.visits += 1
                node.total += reward(feedback)


class AtlasSearch(RandomSearch):
    """Minimal region evidence navigation; future LLM hypothesis generators plug in here."""

    def __init__(self, seed: int):
        super().__init__(seed)
        self.stats = {op: [0, 0.0] for op in OPS}

    def dump_state(self) -> dict:
        return {**super().dump_state(), "stats": self.stats}

    def load_state(self, state: dict) -> None:
        super().load_state(state)
        self.stats = {key: list(value) for key, value in state["stats"].items()}

    def ask(self, context: SearchContext, count: int = 1) -> list[Candidate]:
        dims = dimensions(context, self.fields)
        total = sum(v[0] for v in self.stats.values())

        def score(op):
            visits, gain = self.stats[op]
            return (
                math.inf
                if not visits
                else gain / visits + math.sqrt(2 * math.log(max(1, total)) / visits)
            )

        best = max(score(op) for op in OPS)
        region = self.rng.choice([op for op in OPS if score(op) == best])
        return [
            decode((region,) + tuple(self.rng.choice(d) for d in dims[1:])) for _ in range(count)
        ]

    def tell(self, results: list[TrialFeedback]) -> None:
        for feedback in results:
            for op in feedback.candidate.region_ids:
                self.stats[op][0] += 1
                self.stats[op][1] += reward(feedback)


def make_method(name: str, seed: int):
    methods = {
        "random": RandomSearch,
        "gp": GeneticSearch,
        "mcts": MCTSSearch,
        "atlas": AtlasSearch,
    }
    if name not in methods:
        raise ValueError(f"unknown method {name}")
    return methods[name](seed)
