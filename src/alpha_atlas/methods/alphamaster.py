"""AlphaMaster online dual-chain search. Source and metric mapping: docs/methods/alphamaster.md."""

from dataclasses import asdict

from alpha_atlas.contracts import Candidate
from alpha_atlas.methods.models import HTTPModels, LLMSearchConfig, parse_json_object
from alpha_atlas.storage import candidate_from_dict


class AlphaMasterSearch:
    """Generate from round-fixed effective/discarded examples, refine each failure once."""

    def __init__(self, seed, config=None, *, models=None):
        self.config = config or LLMSearchConfig()
        self.models = models if models is not None else HTTPModels(self.config)
        self.session = None
        self.checkpoint = lambda: None
        self.references = None
        self.generated = 0
        self.discarded = []
        self.parent = None
        self.pending = None
        self.responses = []
        self.usage = dict(
            chat_requests=0, prompt_tokens=0, completion_tokens=0, unknown_usage_requests=0
        )
        if hasattr(self.models, "on_retry"):
            self.models.on_retry = self._request

    @property
    def configuration(self):
        return asdict(self.config)

    def set_session(self, session):
        self.session = session

    def set_checkpoint(self, callback):
        self.checkpoint = callback

    def _request(self, kind):
        self.usage["chat_requests"] += 1
        self.usage["unknown_usage_requests"] += 1
        self.checkpoint()

    def dump_state(self):
        return {
            "version": 1,
            "config": self.configuration,
            **{
                key: getattr(self, key)
                for key in (
                    "references",
                    "generated",
                    "discarded",
                    "parent",
                    "pending",
                    "responses",
                    "usage",
                )
            },
        }

    def load_state(self, state):
        if state["version"] != 1 or state["config"] != self.configuration:
            raise ValueError("AlphaMaster checkpoint configuration mismatch")
        for key in self.dump_state().keys() - {"version", "config"}:
            setattr(self, key, state[key])

    def _round_references(self):
        members = []
        while True:
            page = self.session.query("library_list", {"offset": len(members), "limit": 100})
            members.extend(page["members"])
            if len(members) >= page["total"]:
                break
        best = sorted(members, key=lambda item: item["primary_val_ic"], reverse=True)[:3]
        return {
            "effective": [
                {"expression": item["candidate"]["expression"], "val_ic": item["primary_val_ic"]}
                for item in best
            ],
            "discarded": list(self.discarded),
        }

    def ask(self, context, count=1):
        if count != 1 or self.pending is not None or context.remaining_attempts < 1:
            raise ValueError("AlphaMaster requires one budgeted ask per feedback")
        if self.references is None:
            self.references = self._round_references()
        system = (
            "Design a predictive financial factor. Return one JSON object with nonempty string "
            "fields expression (Atlas DSL) and hypothesis (economic explanation). "
        )
        if self.parent is None:
            system += (
                "Learn from effective examples and failure reasons. Propose a distinct formula "
                "with a financial mechanism, avoiding repetition of the reference factors."
            )
        else:
            system += (
                "Optimize the supplied rejected factor once. Diagnose its measured weakness, "
                "then change the window, inputs, smoothing or functional form while retaining "
                "the original idea. Explain how the change addresses the rejection."
            )
        payload = {
            "context": self.session.query("get_context", {}),
            "references": self.references,
            "optimize": self.parent,
        }
        error = None
        for attempt in range(self.config.max_formula_repairs + 1):
            if attempt == len(self.responses):
                self._request("chat")
                text, usage = self.models.chat(system, payload)
                if usage and all(
                    type(usage.get(k)) is int and usage[k] >= 0
                    for k in ("prompt_tokens", "completion_tokens")
                ):
                    self.usage["unknown_usage_requests"] -= 1
                    for k in ("prompt_tokens", "completion_tokens"):
                        self.usage[k] += usage[k]
                self.responses.append(text)
                self.checkpoint()
            try:
                answer = parse_json_object(self.responses[attempt])
                if not all(
                    isinstance(answer.get(k), str) and answer[k].strip()
                    for k in ("expression", "hypothesis")
                ):
                    raise ValueError("expression and hypothesis must be nonempty strings")
                candidate = Candidate(answer["expression"], hypothesis=answer["hypothesis"])
                break
            except ValueError as exc:
                error = str(exc)
                payload["format_repair"] = {"response": self.responses[attempt], "error": error}
        else:
            candidate = Candidate("", hypothesis=error, name="alphamaster_generation_error")
        self.pending = asdict(candidate)
        return [candidate]

    def tell(self, results):
        if len(results) != 1 or self.pending is None:
            raise ValueError("AlphaMaster requires one pending candidate")
        item = results[0]
        if candidate_from_dict(self.pending) != item.candidate or item.reason == "budget_rejected":
            raise ValueError("AlphaMaster feedback does not match candidate")
        report = item.report
        record = {
            "expression": item.candidate.expression,
            "hypothesis": item.candidate.hypothesis,
            "reason": item.reason,
            "trial": item.factor_trial_index,
            "val_ic": next((m.value for m in report.metrics if m.split == "val"), None)
            if report
            else None,
            "train_ic": next((m.value for m in report.metrics if m.split == "train"), None)
            if report
            else None,
            "coverage": report.coverage if report else None,
            "max_abs_corr": item.max_abs_corr,
        }
        if self.parent is None:
            self.generated += 1
            self.parent = None if item.accepted else record
        else:
            if not item.accepted:
                self.discarded.append(record)
                self.discarded.sort(
                    key=lambda f: (
                        f["val_ic"] is None,
                        f["val_ic"] if f["val_ic"] is not None else 0,
                    )
                )
                self.discarded = self.discarded[:3]
            self.parent = None
        if self.generated == 5 and self.parent is None:
            self.references = None
            self.generated = 0
        self.pending = None
        self.responses = []
