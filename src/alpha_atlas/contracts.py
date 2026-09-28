"""Asset-independent objects. Price/target observations never enter method state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import polars as pl


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must not exceed end")


@dataclass(frozen=True)
class AssetProfile:
    """Common asset capabilities; provider-specific options remain in adapter configuration."""

    asset_id: str
    frequency: str
    data_dir: str
    default_universe: str
    universes: tuple[str, ...]
    timezone: str
    price_field: str
    target_horizon_bars: int
    metric: str

    @classmethod
    def from_mapping(cls, value: dict) -> AssetProfile:
        names = cls.__dataclass_fields__
        fields = {name: value[name] for name in names}
        fields["universes"] = tuple(fields["universes"])
        result = cls(**fields)
        if result.default_universe not in result.universes or result.target_horizon_bars < 1:
            raise ValueError("invalid universe or target horizon in asset profile")
        return result


@dataclass(frozen=True)
class Fold:
    id: str
    train: DateRange
    val: DateRange
    test: DateRange

    def __post_init__(self) -> None:
        if not self.train.end < self.val.start <= self.val.end < self.test.start:
            raise ValueError("fold splits must be chronological and disjoint")


@dataclass(frozen=True)
class Continent:
    id: str
    mechanism: str
    taxonomy_version: str = "1"


@dataclass(frozen=True)
class Region:
    id: str
    continent_ids: tuple[str, ...]
    hypothesis: str
    source: str = "researcher"
    constraints: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Expression:
    """Typed call tree; field leaves and numeric constants have explicit kinds."""

    op: str
    args: tuple[Expression, ...] = ()
    value: str | float | int | None = None

    def to_dict(self) -> dict:
        return {"op": self.op, "args": [a.to_dict() for a in self.args], "value": self.value}

    @classmethod
    def from_dict(cls, value: dict) -> Expression:
        return cls(
            value["op"], tuple(cls.from_dict(a) for a in value.get("args", [])), value.get("value")
        )

    @property
    def expression_id(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(("atlas-dsl-v1:" + payload).encode()).hexdigest()


def fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class CompiledFactor:
    factor_id: str
    expression: Expression
    fields: tuple[str, ...]
    dependencies: tuple[tuple[str, str], ...]
    lookback: int
    nodes: int
    depth: int


@dataclass(frozen=True)
class Candidate:
    expression: Expression | str
    region_ids: tuple[str, ...] = ()
    hypothesis: str = ""
    name: str | None = None


@dataclass(frozen=True)
class FactorValues:
    expression_id: str
    snapshot_id: str
    frame: pl.DataFrame  # row_id + value; row_id is scoped by snapshot_id
    context_id: str = ""


@dataclass(frozen=True)
class Metric:
    name: str
    split: str
    value: float | None
    n_obs: int
    aggregation: str
    n_groups: int = 0


@dataclass(frozen=True)
class EvaluationReport:
    """Metrics are grouped by name, primary first; val uses the primary train direction."""

    expression_id: str | None
    metrics: tuple[Metric, ...]
    direction: int | None
    coverage: float | None
    elapsed_seconds: float
    status: str = "success"
    failure_reason: str | None = None
    observation_ref: str | None = None
    cache_hit: bool = False
    evaluation_id: str | None = None
    canonical_expression: Expression | None = None
    diagnostics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FactorCorrelation:
    left: str
    right: str
    value: float | None
    n_obs: int
    aggregation: str
    split: str = "train"


@dataclass(frozen=True)
class TrialFeedback:
    candidate: Candidate
    report: EvaluationReport | None
    accepted: bool
    reason: str
    max_abs_corr: float | None = None
    nearest_factor: str | None = None
    comparison_complete: bool = True
    library_version: int = 0
    correlation_scope: str = "validation_pooled_spearman"
    trial_index: int = 0


@dataclass(frozen=True)
class TargetDefinition:
    price_field: str
    horizon_bars: int
    return_type: str = "log_return"
    boundary: str = "same_instrument_and_continuity_segment"


@dataclass(frozen=True)
class SearchContext:
    asset: str | None
    frequency: str | None
    target: TargetDefinition
    metric: str
    remaining_attempts: int


class SearchMethod(Protocol):
    def ask(self, context: SearchContext, count: int = 1) -> list[Candidate]: ...
    def tell(self, results: list[TrialFeedback]) -> None: ...


class ResumableSearchMethod(SearchMethod, Protocol):
    """State includes RNG and pending ask/tell work; tell must have no external side effects."""

    def dump_state(self) -> dict: ...
    def load_state(self, state: dict) -> None: ...


class MarketData(Protocol):
    def load_features(
        self, *, fields: list[str], end: date, start: date | None = None
    ) -> pl.DataFrame: ...
    def snapshot_id(self) -> str: ...


class Evaluator(Protocol):
    def evaluate(self, candidate: Candidate) -> EvaluationReport: ...


class AtlasView(Protocol):
    def regions(self) -> tuple[Region, ...]: ...
    def register_hypothesis(self, region: Region) -> None: ...
    def observe(self, feedback: TrialFeedback) -> None: ...
