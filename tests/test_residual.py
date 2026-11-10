import json
import math
import shutil
from datetime import date

import numpy as np
import polars as pl
import pytest
from test_annealing import AgentModels
from test_evaluation_library import RULES, evaluated, futures_frame
from test_pipeline import project as project
from test_versions import version as version

from alpha_atlas.contracts import Candidate, DateRange, Fold
from alpha_atlas.evaluation import FUTURES_METRICS, EvaluationService, library_projection
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods.v1 import EXPLORER_PROMPT, V1Search
from alpha_atlas.methods.v2_sync import (
    MECHANISM_PROMPT,
    MECHANISM_REGION_PROMPT,
    SCALE_PROMPT,
    STATE_VERSION,
    V2SyncConfig,
    V2SyncSearch,
)
from alpha_atlas.runner import _library_residual
from alpha_atlas.storage import RunStore


def standardized(values):
    return (values - values.mean()) / values.std()


def test_projection_matches_independent_ridge_and_reports_explained_share():
    rng = np.random.default_rng(7)
    members = np.column_stack([standardized(rng.normal(size=400)) for _ in range(3)])
    candidate = standardized(members @ np.array([0.6, -0.3, 0.0]) + rng.normal(size=400))
    residual, explained = library_projection(candidate, members)
    ridge = 1e-3 * 400 * np.eye(3)
    expected = candidate - members @ np.linalg.solve(
        members.T @ members + ridge, members.T @ candidate
    )
    assert residual == pytest.approx(expected)
    assert explained == pytest.approx(1 - (expected @ expected) / (candidate @ candidate))
    assert 0.2 < explained < 0.5
    alone, none = library_projection(candidate, np.zeros((400, 0)))
    assert alone == pytest.approx(candidate) and none == 0.0


def service_and_values():
    frame = futures_frame()
    fold = Fold(
        "f",
        DateRange(date(2020, 1, 1), date(2020, 1, 1)),
        DateRange(date(2020, 1, 2), date(2020, 1, 2)),
        DateRange(date(2020, 1, 3), date(2020, 1, 3)),
    )
    profile = {"price_field": "close", "target_horizon_bars": 12, "metric": FUTURES_METRICS[1]}
    service = EvaluationService(frame, fold, profile, {"x", "close"})
    report = service.evaluate(Candidate("$x"))
    return service, report, service.observation(report.evaluation_id).frame


def test_empty_library_residual_equals_the_directed_valid_ic():
    service, report, values = service_and_values()
    residual, explained, similarity = service.residual_ic(values, [], report.direction)
    assert similarity == []
    val = next(m.value for m in report.metrics if m.split == "val")
    assert explained == 0.0
    assert residual == pytest.approx(val)


def test_duplicate_member_leaves_no_residual_information():
    service, report, values = service_and_values()
    residual, explained, similarity = service.residual_ic(
        values, [("member", values)], report.direction
    )
    assert similarity == [pytest.approx(1.0, abs=1e-3)]
    assert explained == pytest.approx(1.0, abs=1e-5)
    assert abs(residual) < 1e-3
    # A member without variation is skipped instead of failing the projection.
    flat = values.with_columns(pl.lit(1.0).alias("value"))
    alone, none, skipped = service.residual_ic(values, [("flat", flat)], report.direction)
    assert none == 0.0 and skipped == [None]
    assert service.library_correlations([("a", values), ("flat", flat), ("b", values)]) == [
        [pytest.approx(1.0, abs=1e-3), None, pytest.approx(1.0, abs=1e-3)],
        [None, None, None],
        [pytest.approx(1.0, abs=1e-3), None, pytest.approx(1.0, abs=1e-3)],
    ]
    assert alone == pytest.approx(service.residual_ic(values, [], report.direction)[0])


def test_library_records_residual_only_when_enabled(tmp_path):
    calls = []

    def residual(frame, members, direction):
        calls.append(([identity for identity, _ in members], direction))
        return 0.004, 0.25, [0.5] * len(members)

    enabled = FactorLibrary(RULES, residual=residual)
    first = enabled.consider(*evaluated("a", np.arange(300.0)))
    second = enabled.consider(*evaluated("b", np.sin(np.arange(300.0))))
    assert (first.residual_ic, first.library_r2) == (0.004, 0.25)
    assert calls[0][0] == [] and calls[1][0] == [first.report.expression_id]
    assert first.library_similarity == () and second.library_similarity == (0.5,)
    duplicate = enabled.consider(*evaluated("a", np.arange(300.0)))
    assert duplicate.reason == "expression_duplicate" and duplicate.residual_ic is None
    assert len(calls) == 2 and second.residual_ic == 0.004

    plain = FactorLibrary(RULES).consider(*evaluated("a", np.arange(300.0)))
    assert plain.residual_ic is None and plain.library_r2 is None
    store = RunStore(tmp_path)
    store.record(1, plain, 0, 0, 0, 1)
    store.record(2, first, 0, 0, 0, 1)
    rows = [json.loads(p.read_text()) for p in sorted((tmp_path / "factor_trials").glob("*.json"))]
    assert not {"residual_ic", "library_r2", "library_similarity"} & rows[0]["feedback"].keys()
    assert rows[1]["feedback"]["residual_ic"] == 0.004
    assert [t["feedback"].get("residual_ic") for t in store.trials()] == [None, 0.004]
    from alpha_atlas.storage import feedback_from_dict

    assert feedback_from_dict(rows[1]["feedback"]) == first


def test_min_residual_ic_rejects_factors_without_new_valid_information():
    values = iter([0.004, 0.0, None, 0.0001])

    def residual(frame, members, direction):
        return next(values), 0.5, [0.1] * len(members)

    rules = {**RULES, "min_residual_ic": 0.0}
    library = FactorLibrary(rules, residual=residual)
    first = library.consider(*evaluated("a", np.arange(300.0)))
    zero = library.consider(*evaluated("b", np.sin(np.arange(300.0))))
    unmeasured = library.consider(*evaluated("c", np.cos(np.arange(300.0))))
    small = library.consider(*evaluated("d", np.sin(np.arange(300.0) / 7)))
    assert first.accepted and small.accepted and small.library_version == 2
    assert (zero.reason, unmeasured.reason) == ("residual_threshold", "residual_threshold")
    assert zero.residual_ic == 0.0 and not zero.accepted and zero.library_version == 1
    assert len(library.view().members) == 2
    with pytest.raises(ValueError, match="requires library_residual"):
        FactorLibrary(rules)


def test_library_residual_rule_is_explicit():
    assert _library_residual({}) is False
    assert _library_residual({"library_residual": "residual_ic_v1"}) is True
    with pytest.raises(ValueError, match="unsupported library_residual"):
        _library_residual({"library_residual": "other"})


def v2_sync(**config):
    return V2SyncSearch(42, V2SyncConfig(chat_model="m", **config), models=AgentModels())


def method_with(records, residuals, expressions=None, **config):
    method = v2_sync(**{"sea_level": 0.005, "probe_attempts": 2, **config})
    method.context = {"frequency": "5m"}
    method.factor_trial_records = [
        {"factor_trial_index": i, "region_id": region} for i, region in enumerate(records, 1)
    ]
    expressions = expressions or ["$adj_close"] * len(residuals)
    method._trial_results = lambda selected=None: {
        i: {"residual_ic": value, "candidate": {"expression": expression}}
        for i, (value, expression) in enumerate(zip(residuals, expressions, strict=True), 1)
    }
    return method


def test_mechanism_status_and_score_follow_residual_information():
    method = method_with(["m"], [0.002])
    assert method._residual_information("m")["status"] == "probing"
    assert method._residual_information("other") is None
    assert method._region_score("other") is None

    seabed = method_with(["m", "m"], [0.004, None])
    information = seabed._residual_information("m")
    assert information["status"] == "seabed"
    # Unmeasured attempts cost an attempt and add zero information.
    assert information["mean_residual_ic"] == pytest.approx(0.002)
    assert seabed._region_score("m") == pytest.approx(0.4)

    land = method_with(["m", "m", "m"], [-0.001, 0.008, 0.001])
    assert land._residual_information("m")["status"] == "land"
    saturated = method_with(["m"] * 4, [0.009, 0.006, 0.001, None])
    assert saturated._residual_information("m")["status"] == "saturated"
    assert saturated._residual_information("m")["best_residual_ic"] == 0.009


def test_v2_sync_configuration_state_and_prompt_leave_v1_unchanged():
    method = v2_sync()
    assert method.configuration["sea_level"] == 0.005
    assert method.configuration["probe_attempts"] == 5
    assert "selection_policy" not in method.configuration
    state = method.dump_state()
    assert state["version"] == STATE_VERSION
    v2_sync().load_state(state)
    with pytest.raises(ValueError, match="V2 Sync requires its own checkpoint"):
        v2_sync().load_state({**state, "version": "atlas_explorer_v1"})
    assert V1Search.explorer_prompt is EXPLORER_PROMPT
    assert method.explorer_prompt == MECHANISM_PROMPT
    assert MECHANISM_PROMPT.startswith(EXPLORER_PROMPT)
    assert "residual_ic" in MECHANISM_PROMPT
    assert "residual_ic" not in EXPLORER_PROMPT
    for bad in (
        {"sea_level": 0},
        {"sea_level": True},
        {"probe_attempts": 0},
        {"scale_statistics": 1},
    ):
        with pytest.raises(ValueError):
            V2SyncConfig(**bad)


@pytest.mark.parametrize(
    ("expression", "frequency", "minutes"),
    [
        ("$close", "5m", 0),
        ("TS_MEAN($close, 12)", "5m", 55),
        ("RETURN($close, 12)", "5m", 60),
        ("CS_RANK(DELTA($close, 1))", "5m", 5),
        ("$close / $close@1d - 1", "5m", 235),
        ("TS_MEAN($close@30m - $close@60m, 20)", "5m", 150),
        ("$close / TS_MEAN($close@1d, 5) - 1", "5m", 1195),
        ("x = TS_STD($close@60m, 4)\nCS_ZSCORE(x)", "5m", 235),
        ("TS_MEAN($close, 5)", "1d", 960),
    ],
)
def test_feature_span_counts_window_history_in_each_subtree_frequency(
    expression, frequency, minutes
):
    from alpha_atlas.expressions import parse
    from alpha_atlas.timeframes import feature_span_minutes

    assert feature_span_minutes(parse(expression), frequency) == minutes


def test_scale_statistics_split_residual_information_by_feature_span():
    from alpha_atlas.methods.v2_sync import expression_band
    from alpha_atlas.timeframes import scale_band

    assert [scale_band(m) for m in (0, 60, 61, 240, 241)] == [
        "up_to_1h",
        "up_to_1h",
        "1h_to_1d",
        "1h_to_1d",
        "over_1d",
    ]
    assert expression_band("TS_MEAN($close, 12", "5m") is None
    expressions = ["RETURN($close, 12)", "TS_MEAN($close, 100)", "$close + 1", "bad("]
    values = [0.009, 0.001, None, 0.002]

    plain = method_with(["m"] * 4, values, expressions)
    assert "by_scale" not in plain._residual_information("m")
    assert "scale_statistics" not in plain.configuration
    assert plain.explorer_prompt == MECHANISM_PROMPT

    method = method_with(["m"] * 4, values, expressions, scale_statistics=True)
    information = method._residual_information("m")
    bands = information["by_scale"]
    assert list(bands) == ["up_to_1h", "1h_to_1d", "over_1d"]
    assert bands["up_to_1h"]["attempts"] == 2
    assert bands["up_to_1h"]["status"] == "land"
    assert bands["up_to_1h"]["mean_residual_ic"] == pytest.approx(0.0045)
    assert bands["1h_to_1d"] == {"attempts": 0}
    assert bands["over_1d"]["status"] == "probing"
    # Unparsed expressions stay in the Region total but in no band; the score is unchanged.
    assert information["attempts"] == 4
    assert method._region_score("m") == plain._region_score("m")
    assert method.configuration["scale_statistics"] is True
    assert method.explorer_prompt.endswith(SCALE_PROMPT)


def mechanism_proposals(names=("Inventory pressure reversal", "Delayed discovery drift")):
    from test_v1 import proposals

    raw = proposals(len(names))
    for item, name in zip(raw["proposals"], names, strict=True):
        hypothesis = item.pop("region_id") + " mechanism: " + item["hypothesis"]
        item["new_region"] = {
            "name": name,
            "hypothesis": hypothesis,
            "continent_ids": ["price_volume"],
        }
    return raw


def test_mechanism_regions_require_named_mechanisms_and_refuse_grammar_regions():
    from test_v1 import proposals

    catalog = {
        **{
            op: {"id": op, "continent_ids": ["price_volume"], "source": "baseline_grammar"}
            for op in ("mean", "std")
        },
        "explorer-a": {
            "id": "explorer-a",
            "continent_ids": ["price_volume"],
            "hypothesis": "Crowded positions unwind",
            "name": "Crowding unwind",
        },
    }
    plain = v2_sync()
    assert plain._validate_proposals(proposals(2), catalog, set())["proposals"]
    assert "mechanism_regions" not in plain.configuration
    method = v2_sync(mechanism_regions=True)
    assert method.configuration["mechanism_regions"] is True
    assert method.explorer_prompt.endswith(MECHANISM_REGION_PROMPT)
    with pytest.raises(ValueError, match="grammar Region kept as reference"):
        method._validate_proposals(proposals(2), catalog, set())
    unnamed = mechanism_proposals()
    del unnamed["proposals"][0]["new_region"]["name"]
    long = mechanism_proposals(("one two three four five six seven", "Short name"))
    taken = mechanism_proposals(("crowding  UNWIND", "Short name"))
    twice = mechanism_proposals(("Same name", "same name"))
    for raw, message in (
        (unnamed, "short mechanism name"),
        (long, "at most 6 words"),
        (taken, "already names Region explorer-a; use its region_id"),
        (twice, "distinct name"),
    ):
        with pytest.raises(ValueError, match=message):
            method._validate_proposals(raw, catalog, set())
    batch = method._validate_proposals(mechanism_proposals(), catalog, set())
    assert [p["new_region"]["name"] for p in batch["proposals"]] == [
        "Inventory pressure reversal",
        "Delayed discovery drift",
    ]
    reuse = proposals(2)
    reuse["proposals"][0]["region_id"] = reuse["proposals"][1]["region_id"] = "explorer-a"
    reuse["proposals"][1]["hypothesis"] += " in other states"
    assert method._validate_proposals(reuse, catalog, set())["proposals"]
    with pytest.raises(ValueError, match="grammar Region"):
        method._validate_exploration({"region_id": "mean", "direction": "d"}, catalog)
    exploration = method._validate_exploration(
        {"direction": "d", **{"new_region": mechanism_proposals()["proposals"][0]["new_region"]}},
        catalog,
    )
    assert method.proposed_regions[exploration["region_id"]]["name"] == (
        "Inventory pressure reversal"
    )


def test_mechanism_region_run_names_regions_on_the_map(project):
    from test_v1 import V1Models, proposals

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.dashboard import views
    from alpha_atlas.runner import run

    enable_residual(project)
    models = V1Models(
        responses=[proposals(), mechanism_proposals()],
        expressions=["$adj_close", "$volume"],
    )
    method = V2SyncSearch(
        42,
        V2SyncConfig(chat_model="m", candidates_per_plan=2, mechanism_regions=True),
        models=models,
    )
    directory = run(project, "ashare", "fold1", "v2_sync", 42, 2, method_impl=method)
    state = read_json(directory / "checkpoint.json")["method_state"]
    region_ids = {r["region_id"] for r in state["factor_trial_records"]}
    assert region_ids and all(r.startswith("explorer-") for r in region_ids)
    names = {v["name"] for v in state["proposed_regions"].values()}
    assert names <= {"Inventory pressure reversal", "Delayed discovery drift"} and names
    explorer = [c for c in models.calls if "Atlas Explorer Agent" in c[0]]
    regions = {r["id"]: r for r in explorer[0][1]["atlas"]["regions"]}
    assert regions["mean"]["reference_only"] is True
    index = views.RunIndex(project)
    key = directory.name
    mapped = {r["id"]: r for r in views.map_view(index, key)["regions"]}
    assert {mapped[r]["name"] for r in region_ids} <= names
    assert {views.plans(index, key)["regions"][r]["name"] for r in region_ids} <= names


def enable_residual(project):
    path = project / "configs/benchmark.toml"
    path.write_text(path.read_text() + '\nlibrary_residual = "residual_ic_v1"\n')


def test_v2_sync_runner_records_residuals_feeds_the_map_and_resumes(project, monkeypatch):
    from test_v1 import V1Models

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.methods.annealing import AnnealingLLMSearch
    from alpha_atlas.runner import resume, run

    with pytest.raises(ValueError, match="v2_sync requires a version with library_residual"):
        run(project, "ashare", "fold1", "v2_sync", 42, attempts=1, method_impl=v2_sync())
    enable_residual(project)
    expressions = ["$adj_close", "$volume", "$adj_close * $volume"]

    def method(models):
        return V2SyncSearch(
            42, V2SyncConfig(chat_model="m", candidates_per_plan=1, probe_attempts=1), models=models
        )

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
                "v2_sync",
                42,
                3,
                method_impl=method(V1Models(expressions=expressions)),
            )
    directory = next((project / "artifacts/runs").iterdir())
    committed = {p: p.read_bytes() for p in (directory / "factor_trials").glob("*.json")}
    restored = method(V1Models(expressions=expressions[2:]))
    resume(project, directory, method_impl=restored)
    assert all(path.read_bytes() == data for path, data in committed.items())
    assert restored.completed_attempts == 3

    trials = RunStore(directory).trials()
    measured = [t["feedback"] for t in trials if t["feedback"]["report"]["status"] == "success"]
    assert measured and all(isinstance(f["residual_ic"], float) for f in measured)
    assert all(0.0 <= f["library_r2"] <= 1.0 for f in measured)
    # Similarity is recorded against every member that existed when the trial was measured.
    for trial in trials:
        feedback = trial["feedback"]
        if "library_similarity" in feedback:
            assert len(feedback["library_similarity"]) == trial["library_size"] - int(
                feedback["accepted"]
            )
    assert read_json(directory / "checkpoint.json")["method_state"]["version"] == STATE_VERSION
    explorer = [c for c in restored.models.calls if "Atlas Explorer Agent" in c[0]]
    assert explorer and "residual_ic" in explorer[-1][0]
    regions = explorer[-1][1]["atlas"]["regions"]
    assert any(r["statistics"].get("residual_information") for r in regions)

    baseline_models = V1Models(expressions=expressions)
    baseline = run(
        project, "ashare", "fold1", "v2_sync", 42, 3, method_impl=method(baseline_models)
    )
    expected = read_json(baseline / "checkpoint.json")["method_state"]
    assert json.loads(json.dumps(restored.factor_trial_records)) == expected["factor_trial_records"]
    assert [t["feedback"].get("residual_ic") for t in RunStore(baseline).trials()] == [
        t["feedback"].get("residual_ic") for t in trials
    ]


def test_seeded_residual_run_records_initial_landmarks_and_trial_similarity(project, version):
    from test_initial_library import DuplicateSearch, seed_version

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.runner import run

    seed_version(project, version)
    path = project / "configs/versions" / f"{version}.json"
    value = read_json(path)
    value["rules"]["library_residual"] = "residual_ic_v1"
    path.write_text(json.dumps(value))
    directory = run(
        project,
        "ashare",
        "fold1",
        "random",
        42,
        attempts=2,
        version=version,
        method_impl=DuplicateSearch(),
    )
    spec = read_json(directory / "run.json")
    assert spec["initial_library"]["correlations"] == [[pytest.approx(1.0, abs=1e-3)]]
    behavior = RunStore(directory).trials()[1]["feedback"]
    assert behavior["reason"] == "behavior_duplicate"
    # 2 * $close duplicates the initial member: full similarity, no residual information.
    assert behavior["library_similarity"] == [pytest.approx(1.0, abs=1e-3)]
    assert behavior["library_r2"] == pytest.approx(1.0, abs=1e-4)
    assert abs(behavior["residual_ic"]) < 1e-3


def test_dashboard_views_read_a_residual_run_without_writing(project, version, tmp_path):
    from test_initial_library import DuplicateSearch, seed_version

    from alpha_atlas.dashboard import views
    from alpha_atlas.runner import run

    seed_version(project, version)
    path = project / "configs/versions" / f"{version}.json"
    value = json.loads(path.read_text())
    value["rules"]["library_residual"] = "residual_ic_v1"
    path.write_text(json.dumps(value))
    directory = run(
        project,
        "ashare",
        "fold1",
        "random",
        42,
        attempts=2,
        version=version,
        method_impl=DuplicateSearch(),
    )
    before = {p: p.stat().st_mtime_ns for p in directory.rglob("*") if p.is_file()}
    index = views.RunIndex(project)
    assert views.run_list(views.RunIndex(project / "elsewhere")) == []
    listed = views.run_list(index)
    assert [r["id"] for r in listed] == [directory.name] and listed[0]["residual"] is True
    # A comparison workspace created after the index exists is still discovered.
    later = project / "artifacts/comparisons/later/workspace/artifacts/runs/later-run"
    later.mkdir(parents=True)
    (later / "run.json").write_text(json.dumps({"method": "random", "status": "running"}))
    assert "later-run" in {r["id"] for r in views.run_list(index)}
    shutil.rmtree(project / "artifacts/comparisons/later")
    summary = views.summary(index, directory.name)
    assert summary["completed"] == 2 and summary["initial_members"] == 1
    assert summary["curve"][-1]["information"] == pytest.approx(
        sum(
            t["feedback"].get("residual_ic") or 0
            for t in RunStore(directory).trials()
            if t["feedback"]["accepted"]
        )
    )
    mapped = views.map_view(index, directory.name)
    assert mapped["available"] is True
    kinds = sorted(p["kind"] for p in mapped["points"])
    assert kinds.count("initial") == 1
    # The behavior duplicate's group is pulled next to the initial member it duplicates.
    initial = next(p for p in mapped["points"] if p["kind"] == "initial")
    duplicate = next(p for p in mapped["points"] if p["trial"] == 2)
    assert math.hypot(initial["x"] - duplicate["x"], initial["y"] - duplicate["y"]) < 0.2
    assert 0 < mapped["spacing"] <= 0.84 / 14
    assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 for p in mapped["points"])
    assert views.trial(index, directory.name, 2)["reason"] == "behavior_duplicate"
    with pytest.raises(KeyError):
        views.summary(index, "../elsewhere")
    after = {p: p.stat().st_mtime_ns for p in directory.rglob("*") if p.is_file()}
    assert after == before


def test_region_layout_pulls_correlated_regions_together_and_packs_the_rest():
    from alpha_atlas.dashboard.views import _layout_groups, _sunflower

    sizes = np.array([1.0, 1.0, 1.0, 1.0])
    affinity = np.zeros((4, 4))
    affinity[0, 1] = affinity[1, 0] = 0.9
    position = _layout_groups(sizes, affinity)
    distance = np.linalg.norm(position[:, None] - position[None], axis=2)
    assert distance[0, 1] < distance[0, 2] and distance[0, 1] < distance[2, 3]
    unlinked = [distance[i, j] for i in range(4) for j in range(i + 1, 4) if (i, j) != (0, 1)]
    assert min(unlinked) > 2.0  # Unrelated Regions never overlap.
    assert np.array_equal(position, _layout_groups(sizes, affinity))  # Deterministic.
    assert np.array_equal(_layout_groups(np.array([1.0]), np.zeros((1, 1))), [[0.0, 0.0]])
    # A hub correlated with many Regions must not make the iteration diverge.
    many = 60
    hub = np.zeros((many, many))
    hub[0, 1:] = hub[1:, 0] = 0.6
    hub[1:20, 1:20] = 0.3
    np.fill_diagonal(hub, 0)
    spread = _layout_groups(np.full(many, 2.0), hub)
    assert np.isfinite(spread).all() and np.abs(spread).max() < 200
    from alpha_atlas.dashboard.views import _disc_radius

    radii = [_disc_radius(n) for n in (1, 5, 20, 80, 320)]
    assert radii == sorted(radii)
    # Unit spacing up to 20 attempts; afterwards 16x the attempts only doubles the radius.
    assert _disc_radius(20) == pytest.approx(0.62 * math.sqrt(20))
    assert _disc_radius(320) == pytest.approx(2 * _disc_radius(20))
    from alpha_atlas.dashboard.views import STRETCH

    extent = np.linalg.norm(_sunflower(320, 0.7), axis=1).max()
    assert _disc_radius(320) < extent <= _disc_radius(320) * STRETCH * 1.24
    disc = _sunflower(5, 0.3)
    assert np.array_equal(disc[0], [0.0, 0.0])
    assert min(np.linalg.norm(disc[i] - disc[j]) for i in range(5) for j in range(i)) > 0.5


def test_mechanism_regions_get_timelines_from_the_shared_status(project, monkeypatch):
    from test_v1 import V1Models

    from alpha_atlas.dashboard import views
    from alpha_atlas.runner import run

    enable_residual(project)
    run(
        project,
        "ashare",
        "fold1",
        "v2_sync",
        42,
        3,
        method_impl=V2SyncSearch(
            42,
            V2SyncConfig(chat_model="m", candidates_per_plan=1, probe_attempts=2),
            models=V1Models(expressions=["$adj_close", "$volume", "$adj_close * $volume"]),
        ),
    )
    index = views.RunIndex(project)
    key = views.run_list(index)[0]["id"]
    mapped = views.map_view(index, key)
    plans = views.plans(index, key)
    assert mapped["available"] and mapped["regions"]
    for region in mapped["regions"]:
        statuses = [step["status"] for step in region["timeline"]]
        assert statuses[0] == "probing" or len(region["timeline"]) >= 2
    assert all(
        set(step["by_scale"]) == {"up_to_1h", "1h_to_1d", "over_1d"}
        for region in mapped["regions"]
        for step in region["timeline"]
    )
    assert sum(len(p["trials"]) for p in plans["plans"]) == 3
    assert all(p["explorer"] for p in plans["plans"])
    # The page shows proposals only; cited sources stay in the checkpoint.
    assert all("proposals" in p["explorer"] for p in plans["plans"])
    assert not any("sources" in p["explorer"] for p in plans["plans"])


def test_dashboard_http_serves_api_and_static_only(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from alpha_atlas.dashboard import make_handler, views

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(views.RunIndex(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        assert json.loads(opener.open(f"{base}/api/runs").read()) == []
        assert b"AlphaAtlas" in opener.open(f"{base}/").read()
        for path in ("/api/summary?id=missing", "/%2e%2e/__init__.py", "/../views.py"):
            with pytest.raises(urllib.error.HTTPError) as error:
                opener.open(base + path)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_v2_async_runs_concurrent_plans_with_v2_rules_and_resumes(project, monkeypatch):
    from test_v1 import V1Models

    from alpha_atlas.checkpoint import read_json
    from alpha_atlas.methods.annealing import AnnealingLLMSearch
    from alpha_atlas.methods.v2_sync import (
        ASYNC_STATE_VERSION,
        V2AsyncConfig,
        V2AsyncSearch,
    )
    from alpha_atlas.runner import resume, run

    def method(models):
        return V2AsyncSearch(
            42,
            V2AsyncConfig(
                chat_model="m",
                candidates_per_plan=2,
                parallel_plans=2,
                probe_attempts=1,
                scale_statistics=True,
                mechanism_regions=True,
            ),
            models=models,
        )

    probe = method(V1Models())
    assert probe.configuration["parallel_plans"] == 2
    assert probe.explorer_prompt.endswith(MECHANISM_REGION_PROMPT)
    state = probe.dump_state()
    assert state["version"] == ASYNC_STATE_VERSION and "groups" in state
    method(V1Models()).load_state(state)
    for other in ("atlas_explorer_v1_async", STATE_VERSION):
        with pytest.raises(ValueError, match="V2 Async requires its own checkpoint"):
            method(V1Models()).load_state({**state, "version": other})

    enable_residual(project)
    expressions = ["$adj_close", "$volume", "$adj_close * $volume", "$adj_close / $volume"]
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
                "v2_async",
                42,
                4,
                method_impl=method(
                    V1Models(responses=[mechanism_proposals()], expressions=expressions)
                ),
            )
    directory = next((project / "artifacts/runs").iterdir())
    restored = method(V1Models(responses=[mechanism_proposals()], expressions=expressions[2:]))
    resume(project, directory, method_impl=restored)
    assert restored.completed_attempts == 4
    saved = read_json(directory / "checkpoint.json")["method_state"]
    assert saved["version"] == ASYNC_STATE_VERSION
    regions = {r["region_id"] for r in saved["factor_trial_records"]}
    assert len(regions) == 2 and all(r.startswith("explorer-") for r in regions)
    assert {saved["proposed_regions"][r]["name"] for r in regions} == {
        "Inventory pressure reversal",
        "Delayed discovery drift",
    }
    trials = RunStore(directory).trials()
    assert any("residual_ic" in t["feedback"] for t in trials)


def test_adaptive_depth_and_region_budget_share():
    from test_v1 import proposals

    from alpha_atlas.contracts import SearchContext
    from alpha_atlas.methods.v2_sync import FOCUS_PROMPT

    plain = v2_sync()
    assert "adaptive_depth" not in plain.configuration
    assert "region_budget_share" not in plain.configuration
    assert not plain.explorer_prompt.endswith(FOCUS_PROMPT)
    for bad in ({"adaptive_depth": 1}, {"region_budget_share": 0}, {"region_budget_share": 1.5}):
        with pytest.raises(ValueError):
            V2SyncConfig(chat_model="m", **bad)

    # Six attempts in "mean": saturated (best above sea, recent two below) and over 50% of 10.
    method = method_with(
        ["mean"] * 6,
        [0.009, 0.006, 0.001, 0.0, 0.001, 0.002],
        adaptive_depth=True,
        region_budget_share=0.5,
    )
    assert method.configuration["region_budget_share"] == 0.5
    assert method.explorer_prompt.endswith(FOCUS_PROMPT)
    method.attempts_total = 10
    method.completed_attempts = 9  # Late budget: the temperature alone would choose depth.
    method.active_region_id = "mean"
    method._catalog = lambda: ({"regions": []}, {})
    context = SearchContext("ashare", "1d", None, "ic", 1)
    state = method._new_round(context, 7)
    assert state["mode"] == "breadth" and state["mode_reason"] == "active Region saturated"
    assert method._closed("mean") and not method._closed("std")
    catalog = {
        op: {"id": op, "continent_ids": ["price_volume"], "source": "baseline_grammar"}
        for op in ("mean", "std")
    }
    with pytest.raises(ValueError, match="mean is closed"):
        method._validate_proposals(proposals(2), catalog, set())
    with pytest.raises(ValueError, match="mean is closed"):
        method._validate_exploration({"region_id": "mean", "direction": "d"}, catalog)
    open_plan = method._validate_exploration({"region_id": "std", "direction": "d"}, catalog)
    assert open_plan["region_id"] == "std"
    without = method_with(["mean"] * 6, [0.009, 0.006, 0.001, 0.0, 0.001, 0.002])
    without.attempts_total, without.completed_attempts = 10, 9
    without.active_region_id = "mean"
    without._catalog = method._catalog
    assert without._new_round(context, 7)["mode"] == "depth"
