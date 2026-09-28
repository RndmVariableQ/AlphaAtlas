"""Synchronous run-local method interface; no OOS or data-reading capabilities."""

import json
import time
from dataclasses import asdict, replace

from alpha_atlas.contracts import Candidate, EvaluationReport, SearchContext, TargetDefinition
from alpha_atlas.expressions import compile_factor
from alpha_atlas.operators.registry import ALIASES, MINIMUM_WINDOWS
from alpha_atlas.reporting import terminal_candidate, terminal_evaluation, terminal_progress
from alpha_atlas.storage import candidate_from_dict


def _report(report):
    return {
        "factor_id": report.expression_id,
        "status": report.status,
        "metrics": [asdict(m) for m in report.metrics],
        "direction": report.direction,
        "coverage": report.coverage,
        "failure_reason": report.failure_reason,
        "diagnostics": report.diagnostics,
    }


class SearchSession:
    def __init__(self, evaluator, library, store, limit: int, *, elapsed=None, run_spec=None):
        self._evaluator, self._library, self._store = evaluator, library, store
        trials = store.trials()
        self._evaluated = {
            t["feedback"]["report"]["expression_id"]: candidate_from_dict(
                t["feedback"]["candidate"]
            )
            for t in trials
            if t["feedback"]["report"] and t["feedback"]["report"].get("canonical_expression")
        }
        self._min_corr_overlap = (run_spec or {}).get("rules", {}).get("min_corr_overlap", 100)
        if self._min_corr_overlap < 1:
            raise ValueError("correlation overlap must be positive")
        if trials and library.view() != store.library_view():
            raise ValueError("restore committed library before opening a session")
        self._limit = limit
        self._attempts = sum(t["feedback"]["reason"] != "budget_rejected" for t in trials)
        self._trial = len(trials)
        self._started = time.perf_counter()
        self._elapsed = elapsed or (lambda: time.perf_counter() - self._started)
        spec = run_spec or {}
        self._reference_library = spec.get("rules", library._rules).get("reference_library", "")
        if self._reference_library not in {"", "futures_cta"}:
            raise ValueError("reference_library must be empty or futures_cta")
        profile = evaluator.profile
        # Explicit metadata only: never pass run paths, data arrays, registry handles or OOS.
        self._context = SearchContext(
            remaining_attempts=self.remaining_budget(),
            asset=spec.get("asset"),
            frequency=profile.get("frequency"),
            target=TargetDefinition(profile["price_field"], profile["target_horizon_bars"]),
            metric=profile["metric"],
        )
        self._fields = tuple(spec.get("fields", sorted(evaluator.fields)))

    @property
    def attempts_used(self) -> int:
        return self._attempts

    @property
    def trial_count(self) -> int:
        return self._trial

    def remaining_budget(self) -> int:
        return max(0, self._limit - self._attempts)

    def get_library(self):
        return self._library.view()

    def get_context(self) -> SearchContext:
        """Only method-relevant metadata; dates and run identities remain internal."""
        return replace(self._context, remaining_attempts=self.remaining_budget())

    def get_fields(self):
        return self._fields

    def list_operators(self):
        return tuple(
            {"name": op.name, "args": op.args, "scope": op.scope, "kind": op.kind}
            for op in self._evaluator.registry.catalog()
        )

    def get_operator(self, name):
        if not isinstance(name, str):
            raise ValueError("operator name must be text")
        op = self._evaluator.registry.spec(name)
        return {
            **asdict(op),
            "minimum_window": MINIMUM_WINDOWS.get(op.name, 1) if "window" in op.args else None,
            "aliases": tuple(k for k, v in sorted(ALIASES.items()) if v == op.name),
        }

    def get_expression_rules(self):
        from alpha_atlas.timeframes import AGGREGATIONS, INTERVALS

        options = self._evaluator.compile_options
        return {
            "max_nodes": options.get("max_nodes", 100),
            "max_depth": options.get("max_depth", 20),
            "syntax_rules": "An expression may be a single formula or multiline Atlas DSL. "
            "For complex formulas, prefer named intermediate variables for readability; "
            "simple formulas may stay on one line. Define each variable once before use, "
            "using one variable name per assignment and no leading underscore. End with one "
            "numeric output expression, not an assignment or a return statement. Use only "
            "allowed $fields and operators with positional arguments; arithmetic, single "
            "comparisons and # comments are supported. No imports, loops, function definitions, "
            "attribute access or arbitrary Python. In JSON, put the entire DSL in one expression "
            "string, encoding line breaks as \\n; do not put Markdown fences inside it. "
            "Intermediate variables expand into the same AST as the equivalent nested formula; "
            "node and depth limits apply after expansion. The example below illustrates syntax "
            "only: replace $field with an allowed field; it is not a recommended factor or window.",
            "syntax_example": json.dumps(
                {"expression": "base = TS_MEAN($field, 20)\n$field / base - 1"}
            ),
            "timeframes": {
                "suffixes": tuple(f"@{i}" for i in INTERVALS)
                if self._context.frequency == "5m"
                else (),
                "fields": tuple(
                    f
                    for f in self._fields
                    if f.removesuffix("_p1").removesuffix("_p2") in AGGREGATIONS
                ),
                "semantics": "Use $close@15m or $close_p1@1d; field permissions use the base "
                "name. Same-frequency subtrees run on coarse bars, including TS windows; "
                "mixed-frequency subtrees run on native 5m after backward broadcasting. "
                "Constants inherit scope. Intraday buckets publish at natural clock ends; "
                "daily buckets use trading_day, confirmed at the next trading day's first "
                "observed bar. No inferred last-row close or future values. Null buckets "
                "replace older values. Broadcast and TS obey main/dependent-leg segments. "
                "Only observed native bars are aggregated; unknown whole-bar omissions "
                "cannot be distinguished from scheduled breaks without a session calendar.",
            },
            "numeric_rules": "Float64; nonfinite results and division near zero yield null. "
            "Windows are positive integers in the operand frequency with no upper bound; "
            "operator minimum windows still apply. Full finite windows are required; "
            "insufficient history yields null. TS never crosses instruments or continuity "
            "segments. Fields ending _p1/_p2 refer to the first/second later-maturity contract, "
            "matched at the same bar end and trading day. Missing quotes stay null. Each TS "
            "subexpression also resets when any auxiliary leg it reads switches or has a gap; "
            "main-only windows and targets keep their original boundaries. "
            "Conditions are typed; final output must be numeric.",
        }

    def get_evaluation_rules(self):
        """Explicit research rules only, without dates, data or internal run configuration."""
        rules = self._library._rules
        minimum = self._evaluator.profile.get("min_train_val_ic")
        return {
            "primary_metric": self._context.metric,
            "aggregation": (
                "Pool all contracts of each product, then calculate Pearson and Spearman IC. "
                "Report product equal and weighted means; weight=sqrt(sum(amount)) over all "
                "eligible finite nonnegative turnover in each scoring split, independent of "
                "factor/target missingness. Require >=5 finite pairs and nonconstant series."
                if self._context.metric.startswith("time_series_")
                else "Daily cross-sectional Spearman and Pearson IC, equally weighted by day; "
                "require >=5 finite pairs and nonconstant series."
            ),
            "direction": "Train primary IC <0: -1; >=0: +1; undefined: reject. "
            "Apply this direction to all val metrics; val_raw remains unadjusted.",
            "coverage": "Finite factor rows / eligible rows with a valid target in scoring "
            "split; target start and end must both be in the split. Empty denominator=null.",
            "quality": (
                {"train_and_val_ic_gt": minimum}
                if minimum is not None
                else {"val_ic_gte": rules["min_abs_val_ic"]}
            ),
            "min_coverage": rules["min_coverage"],
            "search_diagnostics": {
                "version": self._evaluator.diagnostics or None,
                "split": "train",
                "icir": "Daily primary IC mean / sample std; >=2 valid days, std>1e-12. "
                "Futures first correlate within product/day, then use primary product weights. "
                "Train direction applies. Each correlation needs >=5 finite pairs.",
                "turnover": "Half absolute holdings change. Stocks: daily demeaned ranks, "
                "gross normalized to one, PIT entries/exits included; require complete finite "
                "snapshots with >=5 names. Futures: sign(value) unit holdings per native bar, "
                "same instrument/segment; product means use primary weights. No initial entry, "
                "no bridging null snapshots/positions. Diagnostic only, not realized costs.",
            },
            "deduplication": {
                "metric": "validation_pooled_spearman",
                "absolute_correlation_lt": rules["max_abs_corr"],
                "min_common_finite_rows": rules["min_corr_overlap"],
                "policy": "Compare entire current library, rerank common rows; reject "
                "duplicates, constants, insufficient overlap or undefined correlation. "
                "First admitted factor wins; no manual library writes.",
            },
        }

    def research_context(self):
        return {
            **{k: v for k, v in asdict(self.get_context()).items() if k != "remaining_attempts"},
            "fields": self.get_fields(),
            "operators": [self.get_operator(op["name"]) for op in self.list_operators()],
            "expression_rules": self.get_expression_rules(),
            "evaluation_rules": self.get_evaluation_rules(),
            "reference_library": {
                "enabled": bool(self._reference_library),
                "name": self._reference_library or None,
                "bars_per_day": 1,
                "semantics": "Original daily window numbers count native bars. "
                "Definitions are loaded only when searched; no pre-admission. "
                "Use explicit @15m/@30m/@60m/@1d for other frequency scopes.",
            },
        }

    def library_get(self, factor_id):
        item = self.get_library().get(factor_id)
        return (
            None
            if item is None
            else {
                "candidate": asdict(item.candidate),
                **_report(item.report),
                "version": item.version,
            }
        )

    def library_list(self, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("offset must be >=0 and limit in [1,100]")
        members = self.get_library().list()
        return {
            "total": len(members),
            "members": [
                {
                    "factor_id": m.factor_id,
                    "candidate": asdict(m.candidate),
                    "primary_val_ic": next(v.value for v in m.report.metrics if v.split == "val"),
                }
                for m in members[offset : offset + limit]
            ],
        }

    def query(self, tool, arguments):
        functions = {
            "get_context": self.research_context,
            "library_get": self.library_get,
            "library_list": self.library_list,
            "library_stats": lambda: self.get_library().stats(),
            "library_search": self.library_search,
        }
        if tool not in functions:
            raise ValueError("Unknown tool; only the listed evaluation and read-only tools exist")
        return functions[tool](**arguments)

    def library_search(self, query="", source="all", offset=0, limit=10):
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError("query must be text of at most 1000 characters")
        if not isinstance(source, str) or source not in {"all", "run", "reference"}:
            raise ValueError("source must be all, run or reference")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("offset must be >=0 and limit in [1,20]")
        rows = []
        if source in {"all", "run"}:
            for member in self.get_library().members:
                rows.append(
                    {"source": "run", "admitted": True, **self.library_get(member.factor_id)}
                )
        if source in {"all", "reference"} and self._reference_library:
            from alpha_atlas.factor_libraries import load_library

            library = load_library(
                self._reference_library,
                frequency=self.get_context().frequency,
                bars_per_day=1,
            )
            rows.extend(
                {
                    "source": "reference",
                    "admitted": False,
                    "library": library.name,
                    "version": library.version,
                    "source_url": library.source,
                    "source_commit": library.source_commit,
                    **row,
                }
                for row in library.inspect(self.get_fields())
            )
        terms = query.casefold().split()
        matches = []
        for row in rows:
            # Search descriptions/formulas, not accidental matches in numeric IC strings.
            text = json.dumps(
                {
                    key: row[key]
                    for key in (
                        "name",
                        "source_name",
                        "family",
                        "expression",
                        "fields",
                        "note",
                        "candidate",
                    )
                    if key in row
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).casefold()
            if all(term in text for term in terms):
                matches.append(row)
        return {
            "total": len(matches),
            "offset": offset,
            "limit": limit,
            "reference_enabled": bool(self._reference_library),
            "results": matches[offset : offset + limit],
        }

    def factor_correlations(self, pairs):
        """Only previously evaluated factors in this run can request training statistics."""
        pairs = tuple(dict.fromkeys(tuple(sorted(pair)) for pair in pairs))
        if any(len(pair) != 2 or any(i not in self._evaluated for i in pair) for pair in pairs):
            raise ValueError("correlations require two evaluated factors from this run")
        return self._evaluator.factor_correlations(pairs, self._evaluated, self._min_corr_overlap)

    def validate_expression(self, expression):
        """Shared compile-only check; no data, evaluation, admission or attempt consumed."""
        try:
            compiled = compile_factor(
                expression,
                set(self._fields),
                self._evaluator.registry,
                **self._evaluator.compile_options,
            )
            return {"expression": compiled.expression.to_dict(), "error": None}
        except (TypeError, ValueError) as exc:
            return {"expression": None, "error": str(exc)}

    def evaluation_result(self, feedback):
        """Common model-facing result; live budget belongs to evaluation feedback only."""
        return {
            "candidate": asdict(feedback.candidate),
            **_report(feedback.report),
            **{
                name: getattr(feedback, name)
                for name in (
                    "accepted",
                    "reason",
                    "max_abs_corr",
                    "nearest_factor",
                    "comparison_complete",
                    "library_version",
                    "trial_index",
                )
            },
            "remaining_attempts": self.remaining_budget(),
        }

    def register_operator(self, definition):
        terminal_progress("算子注册验证", 名称=definition.name)
        feedback = self._evaluator.registry.register(definition)
        terminal_progress(
            "算子验证完成", 名称=definition.name, 结果="通过" if feedback.accepted else "拒绝"
        )
        return feedback

    def evaluate(self, candidate: Candidate, *, generation_seconds: float = 0):
        self._trial += 1
        started = time.perf_counter()
        terminal_evaluation(candidate, index=self._trial, limit=self._limit)
        if self.remaining_budget() == 0:
            report = EvaluationReport(
                None, (), None, None, 0.0, "budget_rejected", "attempt budget exhausted"
            )
        else:
            self._attempts += 1
            report = self._evaluator.evaluate(candidate)
        values = None
        if report.observation_ref:
            values = self._evaluator.observation(report.observation_ref)
        compiled = None
        if report.expression_id:
            compiled = compile_factor(
                candidate.expression,
                self._evaluator.fields,
                self._evaluator.registry,
                **self._evaluator.compile_options,
            )
        runtime = self._evaluator.registry.runtime

        def commit(feedback):
            self._store.record(
                self._trial,
                feedback,
                time.perf_counter() - started,
                generation_seconds,
                self._elapsed(),
                feedback.library_version,
                compiled=compiled,
                runtime_cost=dict(runtime.cost) if runtime else None,
            )

        feedback = self._library.consider(
            candidate, report, values, commit=commit, trial_index=self._trial
        )
        if report.canonical_expression is not None:
            self._evaluated[report.expression_id] = candidate
        terminal_candidate(
            feedback,
            index=self._trial,
            limit=self._limit,
            elapsed=time.perf_counter() - started,
            total=self._elapsed(),
        )
        return feedback

    def evaluate_many(self, candidates: list[Candidate]):
        return [self.evaluate(candidate) for candidate in candidates]
