import pytest
from test_annealing import AgentModels
from test_pipeline import project as project
from test_residual import enable_residual, mechanism_proposals

from alpha_atlas.contracts import SearchContext
from alpha_atlas.methods.v1 import V1Search
from alpha_atlas.methods.v3_async import (
    BAND_PROMPT,
    BAND_SCHEME,
    LEGACY_SCHEME,
    STATE_VERSION,
    V3_PROMPT,
    V3AsyncConfig,
    V3AsyncSearch,
    band_status,
    region_status,
)
from alpha_atlas.storage import RunStore

HOUR = "$adj_close"  # one base bar: intraday
DAY = "TS_MEAN($adj_close, 96)"  # 95 x 5m of history = 475 trading minutes: 1d_to_20d
LONG = "TS_MEAN($adj_close@1d, 30)"  # about 30 trading days: over_20d


def single(name):
    """One named mechanism direction whose content differs from every other name."""
    raw = mechanism_proposals((name,))
    for field in ("hypothesis", "prediction", "alternative", "conditions"):
        raw["proposals"][0][field] += f" ({name})"
    return raw


def v3(**config):
    return V3AsyncSearch(
        42, V3AsyncConfig(chat_model="m", **{"probe_attempts": 2, **config}), models=AgentModels()
    )


def method_with(records, rows, **config):
    """rows: (expression, residual_ic, accepted) per attempt."""
    method = v3(**config)
    method.context = {"frequency": "5m"}
    method.factor_trial_records = [
        {"factor_trial_index": i, "region_id": region} for i, region in enumerate(records, 1)
    ]
    method._trial_results = lambda selected=None: {
        i: {"residual_ic": r, "accepted": a, "candidate": {"expression": e}}
        for i, (e, r, a) in enumerate(rows, 1)
    }
    return method


def test_band_status_is_judged_by_admission_not_the_best_residual():
    assert band_status([(0.02, False)], 2)["status"] == "probing"
    # A large rejected residual is noise; only a platform admission makes land.
    assert band_status([(0.03, False), (0.02, False)], 2)["status"] == "seabed"
    assert band_status([(-0.001, True), (0.0, False)], 2)["status"] == "seabed"
    land = band_status([(0.004, True), (None, False)], 2)
    assert land["status"] == "land" and land["admitted"] == 1
    assert land["admitted_residual_per_attempt"] == pytest.approx(0.002)
    saturated = band_status([(0.004, True), (0.0, False), (0.02, False), (None, False)], 2)
    assert saturated["status"] == "saturated"
    assert (
        band_status([(0.004, True), (0.0, False), (0.01, True), (0.0, False)], 2)["status"]
        == "land"
    )


def test_region_state_is_derived_from_its_tested_bands():
    assert region_status([], 2) is None
    # Only tested bands decide: one closed band closes the Region; untested bands are reported.
    one_band = region_status([("intraday", 0.0, False), ("intraday", 0.01, False)], 2, BAND_SCHEME)
    assert one_band["status"] == "seabed"
    assert list(one_band["by_scale"]) == ["intraday", "1d_to_20d", "over_20d"]
    assert one_band["by_scale"]["over_20d"] == {
        "attempts": 0,
        "admitted": 0,
        "admitted_residual_ic": 0,
        "admitted_residual_per_attempt": 0.0,
        "status": "probing",
    }
    # A tested band that is not yet probed keeps the Region open.
    assert (
        region_status([*[("intraday", 0.0, False)] * 2, ("over_20d", 0.0, False)], 2, BAND_SCHEME)[
            "status"
        ]
        == "probing"
    )
    closed = [(band, 0.0, False) for band in ("intraday", "1d_to_20d") for _ in "ab"]
    assert region_status(closed, 2, BAND_SCHEME)["status"] == "seabed"
    land = [*closed, ("over_20d", 0.003, True), ("over_20d", 0.0, False)]
    assert region_status(land, 2, BAND_SCHEME)["status"] == "land"
    saturated = [*closed[:2], ("1d_to_20d", 0.003, True), *[("1d_to_20d", 0.0, False)] * 3]
    assert region_status(saturated, 2, BAND_SCHEME)["status"] == "saturated"


def test_legacy_scheme_reproduces_runs_before_the_band_change():
    # Runs without a recorded scheme used the V2 bands and closed only when every band closed.
    rows = [("up_to_1h", 0.0, False), ("up_to_1h", 0.01, False)]
    legacy = region_status(rows, 2, LEGACY_SCHEME)
    assert legacy["status"] == "probing"
    assert list(legacy["by_scale"]) == ["up_to_1h", "1h_to_1d", "over_1d"]
    assert legacy["by_scale"]["up_to_1h"]["status"] == "seabed"


def test_region_score_counts_only_admitted_residual_per_attempt():
    method = method_with(
        ["m"] * 4,
        [(HOUR, 0.004, True), (HOUR, 0.03, False), (HOUR, -0.02, False), (DAY, None, False)],
    )
    information = method._residual_information("m")
    assert information["attempts"] == 4 and information["admitted"] == 1
    assert "by_scale" not in information
    # 0.004 / 4 attempts / sea_level 0.005; rejected residuals of either sign add zero.
    assert method._region_score("m") == pytest.approx(0.2)
    assert method._region_score("other") is None


def test_depth_continues_while_the_active_region_has_an_open_tested_band():
    def round_mode(rows):
        method = method_with(["m"] * len(rows), rows, scale_bands=BAND_SCHEME)
        method.attempts_total, method.completed_attempts = 10, 9  # late budget: depth
        method.active_region_id = "m"
        method._catalog = lambda: ({"regions": []}, {})
        return method._new_round(SearchContext("ashare", "1d", None, "ic", 1), 3)

    hour_closed = [(HOUR, 0.0, False), (HOUR, 0.02, False)]
    assert round_mode([*hour_closed, (LONG, 0.0, False)])["mode"] == "depth"
    # Untested bands do not hold the Region open once its tested bands are closed.
    state = round_mode(hour_closed)
    assert state["mode"] == "breadth" and state["mode_reason"] == "active Region seabed"
    all_closed = [(e, 0.0, False) for e in (HOUR, DAY, LONG) for _ in "ab"]
    assert round_mode(all_closed)["mode"] == "breadth"


def test_v3_config_is_a_fixed_protocol():
    config = V3AsyncConfig(chat_model="m")
    assert config.probe_attempts == 20 and config.region_budget_share is None
    assert config.scale_statistics and config.mechanism_regions and config.adaptive_depth
    for bad in (
        {"scale_statistics": False},
        {"mechanism_regions": False},
        {"adaptive_depth": False},
        {"region_budget_share": 0.15},
        {"scale_bands": LEGACY_SCHEME},
    ):
        with pytest.raises(ValueError):
            V3AsyncConfig(chat_model="m", **bad)
    configuration = v3().configuration
    assert configuration["probe_attempts"] == 2 and "region_budget_share" not in configuration
    assert configuration["scale_bands"] == "none"
    assert v3(scale_bands=BAND_SCHEME).configuration["scale_bands"] == "intraday_1d_20d"


def test_v3_prompt_allows_one_direction_and_states_bars_per_day():
    from test_v1 import proposals

    assert "Submit one to four directions." in V3_PROMPT
    assert "two to four" not in V3_PROMPT
    method = v3()
    assert method.explorer_prompt == V3_PROMPT
    method.context = {"frequency": "5m"}
    assert "one hour is 12 bars and one trading day (240 trading minutes) is 48 bars" in (
        method.explorer_prompt
    )
    catalog = {"explorer-a": {"id": "explorer-a", "continent_ids": ["price_volume"]}}
    raw = proposals(1)
    raw["proposals"][0]["region_id"] = "explorer-a"
    method.research_plan_state = {"active_proposals": []}
    assert len(method._validate_proposals(raw, catalog, set())["proposals"]) == 1
    # V1 keeps its two-proposal minimum.
    with pytest.raises(ValueError, match="two to four distinct proposals"):
        V1Search(42, models=AgentModels())._validate_proposals(raw, catalog, set())


def test_v3_async_runs_single_proposal_batches_feeds_the_map_and_resumes(project, monkeypatch):
    from test_v1 import V1Models

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.dashboard import views
    from alpha_atlas.methods.annealing import AnnealingLLMSearch
    from alpha_atlas.runner import resume, run

    def method(models):
        return V3AsyncSearch(
            42,
            V3AsyncConfig(chat_model="m", candidates_per_plan=2, parallel_plans=2),
            models=models,
        )

    probe = method(V1Models())
    state = probe.dump_state()
    assert state["version"] == STATE_VERSION
    with pytest.raises(ValueError, match="V3 Async requires its own checkpoint"):
        method(V1Models()).load_state({**state, "version": "atlas_mechanism_v2_async"})

    with pytest.raises(ValueError, match="v3_async requires a version with library_residual"):
        run(project, "ashare", "fold1", "v3_async", 42, 1, method_impl=probe)
    enable_residual(project)
    expressions = ["$adj_close", "$volume", "$adj_close * $volume", "$adj_close / $volume"]
    names = ("Inventory pressure reversal", "Delayed discovery drift", "Hedger flow absorption")
    batches = [single(name) for name in names]
    original = AnnealingLLMSearch.tell

    def interrupt(self, feedback):
        original(self, feedback)
        if feedback[0].factor_trial_index == 2:
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(AnnealingLLMSearch, "tell", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(
                project,
                "ashare",
                "fold1",
                "v3_async",
                42,
                4,
                method_impl=method(V1Models(responses=batches, expressions=expressions)),
            )
    directory = next((project / "artifacts/runs").iterdir())
    restored = method(
        V1Models(
            responses=[
                single(name)
                for name in ("Settlement liquidity drain", "Roll yield carry", "Margin call spiral")
            ],
            expressions=expressions[2:],
        )
    )
    resume(project, directory, method_impl=restored)
    assert restored.completed_attempts == 4
    from alpha_atlas.methods import make_method

    assert isinstance(make_method("v3_async", 42, {"chat_model": "m"}), V3AsyncSearch)
    saved = read_json(directory / "checkpoint.json")["method_state"]
    assert saved["version"] == STATE_VERSION
    assert any("residual_ic" in t["feedback"] for t in RunStore(directory).trials())
    index = views.RunIndex(project)
    mapped = views.map_view(index, views.run_list(index)[0]["id"])
    assert mapped["available"] and mapped["probe_attempts"] == 20
    step = mapped["regions"][0]["timeline"][-1]
    assert mapped["rule"] == "v3_region"
    assert "admitted_residual_per_attempt" in step and "by_scale" not in step


def test_reasoning_effort_is_sent_and_recorded_only_when_set(monkeypatch):
    from alpha_atlas.methods.models import HTTPModels

    assert "reasoning_effort" not in v3().configuration
    assert v3(reasoning_effort="low").configuration["reasoning_effort"] == "low"
    with pytest.raises(ValueError, match="reasoning_effort"):
        V3AsyncConfig(chat_model="m", reasoning_effort="extreme")
    bodies = []

    def post(kind, route, body, on_delta=None):
        bodies.append(body)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    for effort in (None, "low"):
        models = HTTPModels(V3AsyncConfig(chat_model="m", reasoning_effort=effort))
        models._chat_response([{"role": "user", "content": "x"}], post=post)
    assert "reasoning_effort" not in bodies[0] and bodies[1]["reasoning_effort"] == "low"


def test_unresolved_is_refused_and_the_explorer_searches_a_new_direction(project):
    from test_v1 import V1Models

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.methods.v3_async import NO_UNRESOLVED
    from alpha_atlas.runner import run

    enable_residual(project)
    blocked = {"summary": "every Region is busy", "unresolved": "map exhausted"}
    models = V1Models(
        responses=[blocked, single("Inventory pressure reversal"), single("Roll yield carry")]
    )
    method = V3AsyncSearch(42, V3AsyncConfig(chat_model="m", candidates_per_plan=1), models=models)
    assert "unresolved" not in method.explorer_reminder
    assert '"unresolved":' not in method.explorer_prompt
    assert "search\nfor a new direction" in method.explorer_prompt
    directory = run(project, "ashare", "fold1", "v3_async", 42, 2, method_impl=method)
    assert read_json(directory / "run.json")["status"] == "frozen"
    retried = [
        payload["dialogue"]
        for system, payload, _ in models.calls
        if "Atlas Explorer Agent" in system and payload["dialogue"]
    ]
    assert retried[0][0]["error"] == NO_UNRESOLVED and "unresolved" not in retried[0][0]


def test_regions_are_judged_on_all_their_attempts_without_bands():
    rows = [(None, 0.0, False), (None, 0.01, False)]
    assert region_status(rows, 2) == {
        "attempts": 2,
        "admitted": 0,
        "admitted_residual_ic": 0.0,
        "admitted_residual_per_attempt": 0.0,
        "status": "seabed",
        "probe_attempts": 2,
    }
    # Spans no longer split the budget: a long-lookback attempt counts toward the same Region.
    method = method_with(["m"] * 3, [(HOUR, 0.0, False), (LONG, 0.0, False), (DAY, 0.004, True)])
    information = method._residual_information("m")
    assert information["status"] == "land" and information["attempts"] == 3


def test_no_band_prompt_drops_the_band_paragraphs_and_band_runs_keep_theirs():
    for text in ("by_scale", "band", "span", "over_20d"):
        assert text not in V3_PROMPT
    assert "Each Region's residual_information has attempts, admitted factors" in V3_PROMPT
    assert "test the active Region while it is probing or land" in V3_PROMPT
    # Runs recorded with the intraday_1d_20d bands resume on the prompt they started with.
    assert v3(scale_bands=BAND_SCHEME).explorer_prompt == BAND_PROMPT
    assert "residual_information.by_scale splits a Region's attempts by feature span" in BAND_PROMPT
    assert "testing a mechanism in an untested band is a distinct direction" in BAND_PROMPT
