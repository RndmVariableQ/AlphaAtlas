from unittest.mock import Mock

import pytest
from test_pipeline import project as project
from test_residual import enable_residual
from test_v1 import V1Models
from test_v3_async import single

from alpha_atlas.methods import make_method
from alpha_atlas.methods.v3_async import V3AsyncConfig
from alpha_atlas.methods.v4_async import V4AsyncSearch
from alpha_atlas.runner import run
from alpha_atlas.storage import RunStore


def test_v4_evidence_operations_and_checkpoint():
    method = make_method("v4_async", 42, {"chat_model": "mock"})
    v3 = make_method("v3_async", 42, {"chat_model": "mock"})
    assert method.explorer_prompt.startswith(v3.explorer_prompt)
    catalog = {
        "mean": {"id": "mean", "source": "baseline_grammar", "continent_ids": ["price_volume"]}
    }
    method.research_plan_state = {
        "active_proposals": [],
        "responses": {
            "explorer": [{"sources": [{"ref": "library:1:1:0", "value": {"source": "reference"}}]}]
        },
    }
    method._session = Mock()
    method._session.get_trial.return_value = {"status": "success", "metrics": [{"value": 0.01}]}
    batch = single("Inventory pressure reversal")
    proposal = batch["proposals"][0]
    proposal["references"] = []
    for operation in ("expansion", "revision", "refinement"):
        proposal["review"]["information_gain"] = operation + ": Compare signed inventory changes."
        if operation != "expansion":
            with pytest.raises(ValueError, match="cite measured evidence"):
                method._validate_proposals(batch, catalog, set())
            proposal["references"] = ["library:1:1:0"]
            with pytest.raises(ValueError, match="cite measured evidence"):
                method._validate_proposals(batch, catalog, {"library:1:1:0"})
            proposal["references"] = ["trial:1"]
            with pytest.raises(ValueError, match="references"):
                method._validate_proposals(batch, catalog, set())
            method._session.get_trial.return_value = {"status": "error", "metrics": []}
            with pytest.raises(ValueError, match="cite measured evidence"):
                method._validate_proposals(batch, catalog, {"trial:1"})
            method._session.get_trial.return_value = {
                "status": "success",
                "metrics": [{"value": 0.01}],
            }
        method._validate_proposals(batch, catalog, {"trial:1"})
        proposal["references"] = []
    proposal["review"]["information_gain"] = "A free-form reason"
    with pytest.raises(ValueError, match="operation prefix"):
        method._validate_proposals(batch, catalog, set())
    method.research_plan_state = None
    state = method.dump_state()
    method.load_state(state)
    with pytest.raises(ValueError, match="own checkpoint"):
        v3.load_state(state)
    with pytest.raises(ValueError, match="own checkpoint"):
        method.load_state(v3.dump_state())


def test_v4_runs_cold_start_and_evidence_based_followup(project):
    enable_residual(project)
    first = single("Inventory pressure reversal")
    followup = single("Volume conditioned reversal")
    first["proposals"][0]["review"]["information_gain"] = "expansion: test inventory pressure."
    followup["proposals"][0]["review"]["information_gain"] = "refinement: vary the volume proxy."
    followup["proposals"][0]["references"] = ["trial:1"]
    models = V1Models(responses=[first, followup], expressions=["$adj_close", "$volume"])
    method = V4AsyncSearch(
        42, V3AsyncConfig(chat_model="mock", parallel_plans=1, candidates_per_plan=1), models=models
    )
    directory = run(project, "ashare", "fold1", "v4_async", 42, 2, method_impl=method)
    assert len(list(RunStore(directory).trials())) == 2
    assert method.factor_trial_records[-1]["explorer_proposals"]["proposals"][0]["references"] == [
        "trial:1"
    ]
