"""Knowledge-guided direction selection on the V3 async research pipeline."""

from alpha_atlas.methods.v3_async import V3AsyncSearch

EVIDENCE_RULE = """
Within the assigned mode and available Regions, start from the closest prior question.
Read its full evidence before citing it. For each proposed direction choose one operation:
- expansion: an observable mechanism is untested, or two mechanisms suggest a new joint
  prediction. Explain the market mapping or why their interaction should matter.
- revision: an observation departs from a prediction, or comparable observations conflict.
  Compare like targets, samples and sign conventions. Give a competing explanation and a
  prediction that distinguishes the two. Invalid formulas and poor coverage do not refute
  a mechanism.
- refinement: a tested signal has an unknown boundary or overlaps a library member. Name
  the condition or proxy to vary and what should change relative to the original signal.
  Correlation measures overlap; it does not establish the explanation.

Begin review.information_gain with 'expansion:', 'revision:' or 'refinement:', followed by
the observed fact, the unresolved question, and why this operation addresses it. Cite the
full trial or library evidence in references. Revision and refinement need measured evidence.
Reference definitions support mechanism provenance only. With no measured evidence, use
expansion and identify the untested field-based prediction; references may then be empty.
Use review.difference to distinguish this prediction from the closest prior direction and
review.readiness to identify available fields. Keep the existing proposal scores and format.
"""


class V4AsyncSearch(V3AsyncSearch):
    checkpoint_version = "atlas_mechanism_v4_async"

    @property
    def explorer_prompt(self):
        return super().explorer_prompt + EVIDENCE_RULE

    def _validate_proposals(self, raw, catalog, available_refs):
        batch = super()._validate_proposals(raw, catalog, available_refs)
        measured_refs = {
            source["ref"]
            for record in self.research_plan_state["responses"]["explorer"]
            for source in record.get("sources", [])
            if source["value"].get("status") == "success" and source["value"].get("metrics")
        }
        for proposal in batch["proposals"]:
            reason = proposal["review"]["information_gain"]
            operation, _, explanation = reason.partition(":")
            if operation not in {"expansion", "revision", "refinement"} or not explanation.strip():
                raise ValueError("review.information_gain needs an operation prefix and its reason")
            if operation == "expansion":
                continue
            for ref in proposal["references"]:
                if ref in measured_refs:
                    break
                if ref.startswith("trial:"):
                    trial = self._session.get_trial(int(ref.removeprefix("trial:")))
                    if trial["status"] == "success" and trial["metrics"]:
                        measured_refs.add(ref)
                        break
            else:
                raise ValueError("revision and refinement must cite measured evidence")
        return batch
