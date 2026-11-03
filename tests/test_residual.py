import json
import math
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
from alpha_atlas.methods.v2_sync import STATE_VERSION, V2SyncConfig, V2SyncSearch
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


def test_library_residual_rule_is_explicit():
    assert _library_residual({}) is False
    assert _library_residual({"library_residual": "residual_ic_v1"}) is True
    with pytest.raises(ValueError, match="unsupported library_residual"):
        _library_residual({"library_residual": "other"})


def v2_sync(**config):
    return V2SyncSearch(42, V2SyncConfig(chat_model="m", **config), models=AgentModels())


def method_with(records, residuals, **config):
    method = v2_sync(**{"sea_level": 0.005, "probe_attempts": 2, **config})
    method.factor_trial_records = [
        {"factor_trial_index": i, "region_id": region} for i, region in enumerate(records, 1)
    ]
    method._trial_results = lambda selected=None: {
        i: {"residual_ic": value} for i, value in enumerate(residuals, 1)
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
    assert V2SyncSearch.explorer_prompt.startswith(EXPLORER_PROMPT)
    assert "residual_ic" in V2SyncSearch.explorer_prompt
    assert "residual_ic" not in EXPLORER_PROMPT
    for bad in ({"sea_level": 0}, {"sea_level": True}, {"probe_attempts": 0}):
        with pytest.raises(ValueError):
            V2SyncConfig(**bad)


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
    listed = views.run_list(index)
    assert [r["id"] for r in listed] == [directory.name] and listed[0]["residual"] is True
    summary = views.summary(index, directory.name)
    assert summary["completed"] == 2 and summary["initial_members"] == 1
    assert summary["curve"][-1]["information"] == pytest.approx(
        sum(t["feedback"].get("residual_ic") or 0 for t in RunStore(directory).trials())
    )
    mapped = views.map_view(index, directory.name)
    assert mapped["available"] is True
    kinds = sorted(p["kind"] for p in mapped["points"])
    assert kinds.count("initial") == 1
    # The behavior duplicate lies on top of the initial landmark it duplicates.
    initial = next(p for p in mapped["points"] if p["kind"] == "initial")
    duplicate = next(p for p in mapped["points"] if p["trial"] == 2)
    assert duplicate["offshore"] is False
    assert math.hypot(initial["x"] - duplicate["x"], initial["y"] - duplicate["y"]) < 0.02
    assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 for p in mapped["points"])
    assert views.trial(index, directory.name, 2)["reason"] == "behavior_duplicate"
    with pytest.raises(KeyError):
        views.summary(index, "../elsewhere")
    after = {p: p.stat().st_mtime_ns for p in directory.rglob("*") if p.is_file()}
    assert after == before


def test_dashboard_map_is_unavailable_without_residual_and_layout_helpers():
    import math as _math

    from alpha_atlas.dashboard.views import _classical_mds, _place

    distance = np.array([[0, 1, 1], [1, 0, 1], [1, 1, 0]], dtype=float)
    coordinates = _classical_mds(distance)
    pairwise = [
        np.linalg.norm(coordinates[i] - coordinates[j]) for i, j in ((0, 1), (0, 2), (1, 2))
    ]
    assert pairwise == pytest.approx([1, 1, 1], abs=1e-6)
    landmarks = np.array([[0.0, 0.0], [1.0, 0.0]])
    near_first, strongest = _place(landmarks, [0.99, 0.0])
    assert strongest == 0.99 and np.linalg.norm(near_first - landmarks[0]) < 0.2
    assert _place(landmarks, [0.1, None]) is None  # Offshore: no measured position.
    assert _place(landmarks, []) is None
    assert _math.isfinite(float(coordinates.sum()))


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
    assert sum(len(p["trials"]) for p in plans["plans"]) == 3
    assert all(p["explorer"] for p in plans["plans"])


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
