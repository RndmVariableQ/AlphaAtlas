"""One platform availability rule drives the V3 map, proposals, depth and the annealing slot."""

import pytest
from test_pipeline import project as project
from test_residual import enable_residual
from test_v3_async import HOUR, LONG, method_with, single, v3

from alpha_atlas.contracts import SearchContext
from alpha_atlas.methods.v3_async import (
    AVAILABILITY_PROMPT,
    BREADTH_RULE,
    REGION_DEPTH_RULE,
    V3_PROMPT,
    V3AsyncConfig,
    V3AsyncSearch,
)

LIMITS = {"region_admission_limit": 2, "region_plan_limit": 1}
FULL = [(HOUR, 0.004, True), (LONG, 0.003, True)]  # two admissions: full at limit 2


def running(*regions):
    return {
        str(i): {"research_plan_state": {"exploration": {"region_id": r}}, "work": None}
        for i, r in enumerate(regions, 7)
    }


def test_limits_are_validated_recorded_only_when_set_and_explained_in_the_prompt():
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="region_admission_limit"):
            V3AsyncConfig(chat_model="m", region_admission_limit=bad)
    plain = v3()
    assert "region_plan_limit" not in plain.configuration
    assert AVAILABILITY_PROMPT not in plain.explorer_prompt
    limited = v3(**LIMITS)
    assert limited.configuration["region_admission_limit"] == 2
    assert limited.configuration["region_plan_limit"] == 1
    assert AVAILABILITY_PROMPT in limited.explorer_prompt


def test_a_full_or_busy_region_is_unavailable_and_the_map_says_why(monkeypatch):
    from alpha_atlas.methods.v2_sync import V2SyncSearch

    method = method_with(["full"] * 2 + ["open"], [*FULL, (HOUR, 0.004, True)], **LIMITS)
    method.groups = running("busy")
    assert method._unavailable("full").startswith("full: 2 admitted factors, limit 2")
    assert method._unavailable("busy") == "busy: plan #7 running"
    assert method._unavailable("open") is None and method._unavailable("new") is None
    # Without limits nothing is gated: earlier runs keep their behaviour.
    unlimited = method_with(["full"] * 2, FULL)
    unlimited.groups = running("full")
    assert unlimited._unavailable("full") is None

    regions = [{"id": r, "statistics": {}} for r in ("full", "busy", "open")]
    regions.append({"id": "mean", "reference_only": True})
    monkeypatch.setattr(V2SyncSearch, "_explorer_atlas", lambda self, s, t=None: {"regions": s})
    shown = {r["id"]: r for r in method._explorer_atlas(regions)["regions"]}
    assert shown["full"]["available"] is False and shown["full"]["closed_reason"].startswith("full")
    assert shown["busy"]["closed_reason"].startswith("busy")
    assert shown["open"] == {"id": "open", "statistics": {}, "available": True}
    assert "available" not in shown["mean"]


def test_proposals_into_unavailable_regions_or_twice_into_one_region_are_refused():
    from test_v1 import proposals

    method = method_with(["full"] * 2, FULL, **LIMITS)
    method.groups = running("busy")
    method.research_plan_state = {"active_proposals": []}
    catalog = {r: {"id": r, "continent_ids": ["price_volume"]} for r in ("full", "busy", "a", "b")}

    def batch(*regions):
        raw = proposals(len(regions))
        for item, region in zip(raw["proposals"], regions, strict=True):
            item["region_id"] = region
        return raw

    for region in ("full", "busy"):
        with pytest.raises(ValueError, match=f"{region} is unavailable"):
            method._validate_proposals(batch(region), catalog, set())
    with pytest.raises(ValueError, match="different Regions"):
        method._validate_proposals(batch("a", "a"), catalog, set())
    assert len(method._validate_proposals(batch("a", "b"), catalog, set())["proposals"]) == 2


def test_depth_needs_an_available_unclaimed_active_region():
    def mode(records, rows, *, groups=(), explorers=None, **config):
        method = method_with(records, rows, **config)
        method.attempts_total, method.completed_attempts = 10, 9  # late budget: depth
        method.active_region_id = "m"
        method.groups = running(*groups)
        method.explorers = explorers or {}
        method._catalog = lambda: ({"regions": []}, {})
        state = method._new_round(SearchContext("ashare", "1d", None, "ic", 1), 3)
        return state["mode"], state.get("mode_reason")

    one = (["m"], [(HOUR, 0.004, True)])
    assert mode(*one, **LIMITS) == ("depth", None)
    full = "active Region full: 2 admitted factors, limit 2"
    assert mode(["m"] * 2, FULL, **LIMITS) == ("breadth", full)
    assert mode(*one, groups=["m"], **LIMITS) == ("breadth", "active Region busy: plan #7 running")
    claimed = {"explorer": {"research_plan_state": {"mode": "depth"}}}
    assert mode(*one, explorers=claimed, **LIMITS) == (
        "breadth",
        "active Region claimed by another Explorer",
    )
    # The same states keep depth when no limit is configured.
    assert mode(["m"] * 2, FULL, groups=["m"])[0] == "depth"


def test_a_full_incumbent_releases_the_annealing_slot_to_the_finished_plan():
    def finish(**config):
        method = method_with(["m"] * 2 + ["n"], [*FULL, (HOUR, 0.0, False)], **config)
        method.active_region_id = "m"
        method.region_scores = {"m": {"accepted": 2, "attempts": 2}}
        method.factor_trial_records[-1]["research_plan_index"] = 4
        method.research_plan_state = {
            "research_plan_index": 4,
            "outcomes": [0],
            "plan": {"region_id": "n"},
            "temperature": 0.02,
            "explorer_proposals": {},
        }
        method._finish_round()
        return method.active_region_id, method.factor_trial_records[-1]["annealing_result"]

    region, result = finish(**LIMITS)
    assert region == "n" and result["reason"] == "initialize_or_continue_region"
    # Without limits the full incumbent's stale score keeps it (n scored zero).
    assert finish()[0] == "m"


def test_limited_run_completes_and_records_its_rules(project):
    from test_v1 import V1Models

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.runner import run

    enable_residual(project)
    names = ("Inventory pressure reversal", "Delayed discovery drift", "Hedger flow absorption")
    models = V1Models(
        responses=[single(name) for name in names],
        expressions=["$adj_close", "$volume", "$adj_close * $volume", "$adj_close / $volume"],
    )
    method = V3AsyncSearch(
        42,
        V3AsyncConfig(chat_model="m", candidates_per_plan=2, parallel_plans=2, **LIMITS),
        models=models,
    )
    directory = run(project, "ashare", "fold1", "v3_async", 42, 4, method_impl=method)
    spec = read_json(directory / "run.json")
    assert spec["status"] == "frozen" and spec["method_config"]["region_plan_limit"] == 1


def test_breadth_only_runs_drop_every_depth_rule_from_the_prompt():
    method = method_with(["m"], [(HOUR, 0.004, True)], selection_policy="greedy", **LIMITS)
    method.attempts_total, method.completed_attempts = 10, 9  # late budget: depth if annealing
    method.active_region_id = "m"
    method.groups, method.explorers = {}, {}
    method._catalog = lambda: ({"regions": []}, {})
    assert method._new_round(SearchContext("ashare", "1d", None, "ic", 1), 3)["mode"] == "breadth"
    prompt = method.explorer_prompt
    assert BREADTH_RULE in prompt and "Depth mode" not in prompt
    assert REGION_DEPTH_RULE not in prompt
    assert AVAILABILITY_PROMPT in prompt
    # Region choice is stated once; the inherited V1 revisit and breadth lines are gone.
    depth = method_with(["m"], [(HOUR, 0.004, True)], **LIMITS).explorer_prompt
    for text in (prompt, depth):
        assert text.count("compare the whole map") == 1 and "(V1)" not in text
        assert "revisit promising directions" not in text and "In breadth mode compare" not in text
    assert "Depth mode is assigned only when the active Region is available" in depth
    assert method.configuration["selection_policy"] == "greedy"
    # Annealing runs keep the depth rule, byte for byte.
    assert v3().explorer_prompt == V3_PROMPT and REGION_DEPTH_RULE in V3_PROMPT
