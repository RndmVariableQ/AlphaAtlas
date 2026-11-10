"""Independent runs, file evidence, frozen definitions, and isolated OOS evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import polars as pl

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.atlas import FactorAtlas
from alpha_atlas.checkpoint import (
    config_fingerprint,
    read_json,
    run_lock,
    tracked_run,
)
from alpha_atlas.config import asset_config, load_fold, load_toml, one_year_before, warmup_period
from alpha_atlas.contracts import Expression, Metric, Region, fingerprint
from alpha_atlas.evaluation import (
    FUTURES_METRICS,
    EvaluationService,
    apply_clipping,
    build_targets,
    metrics,
    product_weights,
    split_panel,
)
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods import make_method
from alpha_atlas.methods.baselines import default_regions
from alpha_atlas.methods.v1 import ExplorerUnresolved
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime, RuntimeLimits
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import (
    FORMAT_VERSION,
    ArrowCache,
    RunStore,
    candidate_from_dict,
    feedback_from_dict,
)
from alpha_atlas.terminal import metric_text, terminal_progress


def source_fingerprint() -> str:
    source = Path(__file__).parent
    files = sorted(source.rglob("*.py"))
    lock = source.parents[1] / "uv.lock"
    if lock.exists():
        files.append(lock)
    return fingerprint(
        [
            (
                p.name,
                str(p.relative_to(source)) if p.is_relative_to(source) else "uv.lock",
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in files
        ]
    )


def _runtime(root: Path, frozen: dict | None = None):
    if frozen is not None:
        if frozen.get("environment", {}).get("engine") != "numba-window-v1":
            raise ValueError("unsupported frozen operator runtime; legacy artifacts are preserved")
        runtime = NumbaRuntime(RuntimeLimits(**frozen["limits"]))
        if runtime.environment != frozen["environment"]:
            raise ValueError("frozen operator runtime environment changed")
        return runtime
    path = root / "configs/operators.toml"
    return NumbaRuntime(RuntimeLimits(**load_toml(path))) if path.exists() else NumbaRuntime()


def _method_type(method) -> str:
    cls = type(method)
    return f"{cls.__module__}.{cls.__qualname__}"


def _data_fields(fields, profile):
    return (
        list(dict.fromkeys([*fields, "amount"])) if profile["metric"] in FUTURES_METRICS else fields
    )


def _market(root, spec):
    if "experiment_version" in spec:
        from alpha_atlas.assets.versioned import open_market

        return open_market(root, spec["experiment_version"])
    return ParqMarketData(
        root / spec["profile"]["data_dir"],
        spec["universe"],
        allow_incomplete=spec["allow_incomplete"],
    )


def _registry(spec, runtime):
    return OperatorRegistry(
        runtime=runtime, versions=spec.get("experiment_version", {}).get("operator_versions")
    )


def run(
    root: Path,
    asset: str,
    fold_id: str,
    method_name: str,
    seed: int,
    attempts: int | None = None,
    universe: str | None = None,
    allow_incomplete: bool = False,
    field_names: list[str] | None = None,
    *,
    method_impl=None,
    runtime=None,
    version: str | None = None,
    run_key: str | None = None,
) -> Path:
    experiment_version = None
    if version is not None:
        from alpha_atlas.versions import fold_from_version, load_version

        experiment_version = load_version(root, version)
        if asset != experiment_version["asset"] or fold_id != experiment_version["fold"]["id"]:
            raise ValueError("asset/fold overrides conflict with experiment version")
        if universe is not None and universe != experiment_version["universe"]:
            raise ValueError("universe override conflicts with experiment version")
        if field_names is not None and field_names != experiment_version["fields"]:
            raise ValueError("field override conflicts with experiment version")
        if allow_incomplete:
            raise ValueError("version data completeness is fixed per source")
        profile, fold, rules = (
            experiment_version["profile"],
            fold_from_version(experiment_version),
            experiment_version["rules"],
        )
        universe, field_names = experiment_version["universe"], experiment_version["fields"]
    else:
        profile = asset_config(root, asset)
        fold = load_fold(root, fold_id)
        rules = load_toml(root / "configs/benchmark.toml")
    limit = attempts if attempts is not None else rules["max_attempts"]
    if limit <= 0:
        raise ValueError("attempt budget must be positive")
    universe = universe or profile["default_universe"]
    if universe not in profile["universes"]:
        raise ValueError(f"unsupported universe {universe}")
    fields = (
        list(field_names)
        if experiment_version is not None
        else list(dict.fromkeys([profile["price_field"], *(field_names or [])]))
    )
    options = (
        load_toml(root / f"configs/{method_name}.toml")
        if experiment_version is None
        and method_name
        in {"alphaprobe", "alphaschema", "react", "mcts_llm", "annealing", "v1", "v1_async"}
        else None
    )
    if experiment_version is not None:
        if method_name not in experiment_version["method_configs"]:
            raise ValueError(f"method is not configured in this version: {method_name}")
        options = experiment_version["method_configs"][method_name]
    method = method_impl if method_impl is not None else make_method(method_name, seed, options)
    if method_name in {"v2_sync", "v2_async", "v3_async"} and not _library_residual(rules):
        raise ValueError(f"{method_name} requires a version with library_residual = residual_ic_v1")
    if method_name in {
        "mcts_llm",
        "annealing",
        "v1",
        "v1_async",
        "v2_sync",
        "v2_async",
        "v3_async",
    } and rules.get("search_diagnostics") not in {"icir_turnover_v1", "icir_turnover_lag1_v2"}:
        raise ValueError(f"{method_name} requires shared icir_turnover_v1 diagnostics")
    if method_name == "alphaschema" and rules.get("search_diagnostics") != "icir_turnover_lag1_v2":
        raise ValueError("alphaschema requires shared icir_turnover_lag1_v2 diagnostics")
    resumable = not hasattr(method, "run") and all(
        callable(getattr(method, name, None)) for name in ("dump_state", "load_state")
    )
    # A caller-supplied key makes the run directory addressable before the search finishes.
    key = run_key or uuid.uuid4().hex[:10]
    run_id = f"{asset}-{universe}-{fold_id}-{method_name}-{seed}-{key}"
    directory = root / "artifacts/runs" / run_id
    directory.mkdir(parents=True)
    spec = {
        "format_version": FORMAT_VERSION,
        "run_id": run_id,
        "asset": asset,
        "universe": universe,
        "fold": asdict(fold),
        "method": method_name,
        "seed": seed,
        "attempts": limit,
        "rules": rules,
        "profile": profile,
        "fields": fields,
        "allow_incomplete": allow_incomplete,
        "status": "running",
        "implementation": (
            "session_plugin"
            if method_impl is not None
            else "single_agent_react_v1"
            if method_name == "react"
            else "alphaprobe_atlas_v1"
            if method_name == "alphaprobe"
            else "alphaschema_atlas_v1"
            if method_name == "alphaschema"
            else "mcts_llm_atlas_v1"
            if method_name == "mcts_llm"
            else "atlas_mechanism_v2_sync"
            if method_name == "v2_sync"
            else "atlas_mechanism_v2_async"
            if method_name == "v2_async"
            else "atlas_mechanism_v3_async"
            if method_name == "v3_async"
            else "atlas_explorer_v1_async"
            if method_name == "v1_async"
            else "atlas_explorer_v1"
            if method_name == "v1"
            else "annealing_llm_atlas_v1"
            if method_name == "annealing"
            else "finite_grammar_baseline_v2"
        ),
        "source_fingerprint": source_fingerprint(),
        "resumable": resumable,
        "method_type": _method_type(method) if resumable else None,
        "continuity": "targets reset at actual contract/gap boundaries; factor windows follow "
        "the configured panel; unknown intraday missing bars are not detected",
    }
    if hasattr(method, "configuration"):
        spec["method_config"] = method.configuration
    if experiment_version is not None:
        spec["experiment_version"] = experiment_version
    with run_lock(directory), tracked_run(directory, spec) as progress:
        progress.status("running")
        progress.stage = "load_data"
        terminal_progress(
            "开始搜索 / 加载行情",
            运行=run_id,
            范围=f"{asset} / {universe} / {fold_id}",
            方法=f"{method_name} / seed={seed} / 预算={limit}",
            数据=f"{one_year_before(fold.train.start)} → {fold.val.end}",
        )
        with _market(root, spec) as market:
            if field_names is None:
                fields.extend(field for field in market.get_fields() if field not in fields)
            snapshot_id = market.snapshot_id()
            features = market.load_features(
                fields=_data_fields(fields, profile),
                start=one_year_before(fold.train.start),
                end=fold.val.end,
            )
        warmup = warmup_period(fold.train.start)
        terminal_progress("行情就绪", 行数=f"{features.height:,}")
        if experiment_version is not None:
            limits = RuntimeLimits(**experiment_version["runtime_limits"])
            if runtime is not None and runtime.limits != limits:
                raise ValueError("runtime limits conflict with experiment version")
            runtime = NumbaRuntime(limits)
        else:
            runtime = NumbaRuntime(runtime.limits) if runtime else _runtime(root)
        spec.update(
            snapshot_id=snapshot_id,
            warmup=warmup,
            runtime={"environment": runtime.environment, "limits": asdict(runtime.limits)},
        )
        spec["config_fingerprint"] = config_fingerprint(spec)
        progress.status("running")
        _search(directory, spec, method, progress, features, fold, runtime)
    return directory


def _search(directory, spec, method, progress, features, fold, runtime, checkpoint=None):
    fields, profile, rules = spec["fields"], spec["profile"], spec["rules"]
    snapshot_id = spec["snapshot_id"]
    progress.stage = "setup"
    store = RunStore(directory)
    registry = _registry(spec, runtime)
    # Revalidate successful definitions without adding a second registration record.
    for path in sorted((directory / "operators").glob("*.json")):
        row = read_json(path)
        if row["feedback"]["accepted"]:
            if fingerprint(row["definition"]) != row["feedback"]["operator_id"]:
                raise ValueError("registered operator identity mismatch")
            registry.restore([row["definition"]])
    registry.record = store.record_operator
    disk_cache = ArrowCache((directory / "cache/evaluations", directory / "cache/members"))
    evaluator = EvaluationService(
        features,
        fold,
        profile,
        set(fields),
        snapshot_id=snapshot_id,
        context={"asset": spec["asset"], "universe": spec["universe"], "run_id": spec["run_id"]},
        registry=registry,
        cache_dir=directory / "cache/evaluations",
        disk_cache=disk_cache,
        compile_options={
            "max_nodes": rules["max_expression_nodes"],
            "max_depth": rules["max_expression_depth"],
        },
        retain_training=hasattr(method, "correlation_pairs")
        or spec["method"]
        in {"mcts_llm", "annealing", "v1", "v1_async", "v2_sync", "v2_async", "v3_async"},
        diagnostics=rules.get("search_diagnostics", ""),
        clip_quantiles=rules.get("clip_quantiles"),
    )
    library = FactorLibrary(
        rules,
        min_train_val_ic=profile.get("min_train_val_ic"),
        snapshot_id=snapshot_id,
        context_id=evaluator.context_id,
        directory=directory / "cache/members",
        disk_cache=disk_cache,
        rebuild=lambda entry: _rebuild_member(evaluator, entry),
        residual=evaluator.residual_ic if _library_residual(rules) else None,
    )
    trials = store.trials()
    initial = spec.get("experiment_version", {}).get("initial_library")
    if initial and "initial_library" not in spec:
        if trials or checkpoint is not None:
            raise ValueError("initial library missing from an existing search")
        started = time.perf_counter()
        entries, observations = [], {}
        for index, item in enumerate(initial["factors"], 1):
            terminal_progress("初始化因子库 / Train+Val", 进度=f"{index}/{len(initial['factors'])}")
            candidate = candidate_from_dict(item["candidate"])
            report = evaluator.evaluate(candidate)
            if (
                report.status != "success"
                or report.direction not in {-1, 1}
                or report.expression_id != item["factor_id"]
            ):
                raise ValueError(f"initial factor cannot be measured: {candidate.name}")
            entries.append({"candidate": asdict(candidate), "report": asdict(report)})
            observations[report.expression_id] = evaluator.observation(report.observation_ref)
        # The configured baseline is pre-existing membership, not new search admissions.
        # Persist once in the existing run record, before the first method checkpoint/trial.
        spec["initial_library"] = {
            "members": entries,
            "elapsed_seconds": time.perf_counter() - started,
        }
        if _library_residual(rules):
            # Map landmarks: pairwise Valid correlations of the initial members.
            spec["initial_library"]["correlations"] = evaluator.library_correlations(
                [
                    (e["report"]["expression_id"], observations[e["report"]["expression_id"]].frame)
                    for e in entries
                ]
            )
        spec["config_fingerprint"] = config_fingerprint(spec)
        progress.status("running")
        library.restore(store.library_view(), lambda entry: observations[entry.factor_id])
    elif checkpoint is not None or "initial_library" in spec:
        library.restore(store.library_view())
    session = SearchSession(
        evaluator, library, store, spec["attempts"], elapsed=progress.elapsed, run_spec=spec
    )
    if hasattr(method, "set_session"):
        method.set_session(session)
    atlas = FactorAtlas()
    for region in default_regions():
        atlas.register_hypothesis(region)
    if hasattr(method, "set_atlas_context"):
        method.set_atlas_context(atlas.to_dict)
    pending = None
    if checkpoint is not None:
        method.load_state(checkpoint["method_state"])
        _sync_method_regions(method, atlas)
        pending = checkpoint["pending"]
        for trial in trials[: checkpoint["completed"]]:
            atlas.observe(feedback_from_dict(trial["feedback"]))
    elif spec["resumable"]:
        progress.save(method, 0, 0, library.view().version)

    def save(pending=None):
        if spec["resumable"]:
            progress.save(
                method,
                session.factor_trial_count,
                session.attempts_used,
                library.view().version,
                pending,
            )

    if hasattr(method, "set_checkpoint"):
        method.set_checkpoint(save)

    search_stop_reason = None
    try:
        if hasattr(method, "run"):
            progress.stage = "method_run"
            method.run(session)
        elif getattr(method, "evaluation_slots", 1) > 1:
            search_stop_reason = _search_concurrent(
                method, session, atlas, spec, progress, trials, pending, save
            )
        else:
            while pending is not None or session.remaining_budget():
                if pending is None:
                    progress.stage = "ask"
                    terminal_progress(
                        "生成候选",
                        transient=True,
                        进度=f"{session.factor_trial_count + 1}/{spec['attempts']}",
                    )
                    step = time.perf_counter()
                    context = session.get_context()
                    try:
                        candidate = method.ask(context, count=1)[0]
                    except ExplorerUnresolved as exc:
                        search_stop_reason = str(exc)
                        _sync_method_regions(method, atlas)
                        save()
                        terminal_progress(
                            "研究提前结束，保存已有结果",
                            已评估=session.attempts_used,
                            预算=spec["attempts"],
                            原因=search_stop_reason,
                        )
                        break
                    _sync_method_regions(method, atlas)
                    pending = {
                        "factor_trial_index": session.factor_trial_count + 1,
                        "candidate": asdict(candidate),
                        "generation_seconds": time.perf_counter() - step,
                    }
                    progress.stage = "checkpoint"
                    save(pending)
                progress.stage = "evaluate"
                index = pending["factor_trial_index"]
                if index <= len(trials):
                    feedback = feedback_from_dict(trials[index - 1]["feedback"])
                    terminal_progress("恢复已提交反馈", 候选=index, 结果=feedback.reason)
                else:
                    feedback = session.evaluate(
                        candidate_from_dict(pending["candidate"]),
                        generation_seconds=pending["generation_seconds"],
                        research_plan_index=getattr(method, "pending_research_plan_index", None),
                    )
                progress.stage = "tell"
                method.tell([feedback])
                atlas.observe(feedback)
                progress.stage = "checkpoint"
                save()
                pending = None
    finally:
        if hasattr(method, "close"):
            method.close()
    progress.stage = "freeze"
    terminal_progress("冻结因子库", 成员=len(library.view().members))
    view = store.library_view()
    if view != library.view():
        raise ValueError("committed library differs from memory")
    archive = []
    initial_count = len(spec.get("initial_library", {}).get("members", []))
    for entry in view.members:
        compiled = compile_factor(
            entry.candidate.expression, set(fields), registry, **evaluator.compile_options
        )
        archive.append(
            {
                "candidate": asdict(entry.candidate),
                "report": asdict(entry.report),
                "compiled": asdict(compiled),
                **({"initial": entry.version <= initial_count} if initial_count else {}),
            }
        )
    frozen = {
        "format_version": FORMAT_VERSION,
        "factors": archive,
        "operators": [asdict(d) for d in registry.definitions()],
        "runtime": spec["runtime"],
    }
    atomic_json(directory / "frozen/library.json", frozen)
    progress.status(
        "frozen",
        library_size=len(archive),
        library_sha256=hashlib.sha256((directory / "frozen/library.json").read_bytes()).hexdigest(),
        **({"search_stop_reason": search_stop_reason} if search_stop_reason else {}),
    )


def _search_concurrent(method, session, atlas, spec, progress, trials, pending, save):
    """Compute up to evaluation_slots candidates at once; admit and tell strictly in trial order.

    Each in-flight candidate is checkpointed with its trial index before computation, so a
    resumed run recomputes uncommitted ones and reuses committed trials. Returns a stop reason.
    """
    slots = method.evaluation_slots
    executor = ThreadPoolExecutor(max_workers=slots, thread_name_prefix="evaluate")
    inflight, stop = [], None

    def records():
        return [{k: v for k, v in e.items() if k != "future"} for e in inflight] or None

    def submit(entry):
        committed = entry["factor_trial_index"] <= len(trials)
        candidate = candidate_from_dict(entry["candidate"])
        entry["future"] = None if committed else executor.submit(session.measure, candidate)
        inflight.append(entry)

    method.set_checkpoint(lambda: save(records()))
    for entry in pending if isinstance(pending, list) else [pending] if pending else []:
        submit(dict(entry))
    try:
        while inflight or stop is None and session.remaining_budget():
            free = min(slots - len(inflight), session.remaining_budget() - len(inflight))
            if stop is None and free > 0:
                progress.stage = "ask"
                step = time.perf_counter()
                try:
                    # Block for a candidate only when nothing is being computed.
                    candidates = method.ask(session.get_context(), count=free, block=not inflight)
                except ExplorerUnresolved as exc:
                    stop, candidates = str(exc), []
                    terminal_progress(
                        "研究提前结束，完成在算候选后保存",
                        在算=len(inflight),
                        原因=stop,
                    )
                _sync_method_regions(method, atlas)
                for candidate in candidates:
                    submit(
                        {
                            "factor_trial_index": session.factor_trial_count + len(inflight) + 1,
                            "candidate": asdict(candidate),
                            "generation_seconds": time.perf_counter() - step,
                        }
                    )
                progress.stage = "checkpoint"
                save(records())
                if candidates:
                    continue
            if not inflight:
                break
            head = inflight[0]
            if head["future"] is not None and not head["future"].done():
                # Poll for model replies while slots are free; otherwise wait for the result.
                wait([head["future"]], timeout=0.5 if stop is None and free > 0 else None)
                continue
            progress.stage = "evaluate"
            if head["future"] is None:
                feedback = feedback_from_dict(trials[head["factor_trial_index"] - 1]["feedback"])
                terminal_progress(
                    "恢复已提交反馈", 候选=head["factor_trial_index"], 结果=feedback.reason
                )
            else:
                feedback = session.evaluate(
                    candidate_from_dict(head["candidate"]),
                    generation_seconds=head["generation_seconds"],
                    research_plan_index=getattr(method, "pending_research_plan_index", None),
                    measured=head["future"].result(),
                )
            # Leave the in-flight list before tell: the method checkpoints the told state.
            inflight.pop(0)
            progress.stage = "tell"
            method.tell([feedback])
            atlas.observe(feedback)
            progress.stage = "checkpoint"
            save(records())
    finally:
        # Running computations cannot be interrupted; their results are never committed.
        executor.shutdown(wait=False, cancel_futures=True)
    return stop


def _rebuild_member(evaluator, entry):
    report = evaluator.evaluate(entry.candidate)
    # Parallel floating-point reductions can differ in their final bits on recomputation.
    # Only metric values allow roundoff; metric identities and sample counts stay exact.
    metrics_match = len(report.metrics) == len(entry.report.metrics) and all(
        replace(actual, value=expected.value) == expected
        and (
            actual.value is None
            and expected.value is None
            or actual.value is not None
            and expected.value is not None
            and math.isfinite(actual.value)
            and math.isfinite(expected.value)
            and math.isclose(actual.value, expected.value, rel_tol=1e-12, abs_tol=1e-15)
        )
        for actual, expected in zip(report.metrics, entry.report.metrics, strict=True)
    )
    if (
        report.expression_id != entry.factor_id
        or not metrics_match
        or report.direction != entry.report.direction
        or report.coverage != entry.report.coverage
        or report.status != entry.report.status
        or report.clipping != entry.report.clipping
    ):
        raise ValueError("rebuilt member differs from committed evaluation")
    return evaluator.observation(report.observation_ref)


def _library_residual(rules) -> bool:
    value = rules.get("library_residual")
    if value not in {None, "residual_ic_v1"}:
        raise ValueError(f"unsupported library_residual: {value}")
    return value is not None


def _sync_method_regions(method, atlas):
    if not callable(getattr(method, "atlas_regions", None)):
        return
    for value in method.atlas_regions():
        atlas.register_hypothesis(
            Region(
                id=value["id"],
                continent_ids=tuple(value["continent_ids"]),
                hypothesis=value["hypothesis"],
                source=value.get("source", "researcher"),
                constraints=value.get("constraints", {}),
            )
        )


def run_model(root: Path, directory: Path, *, test: bool = False) -> dict:
    """A separate downstream experiment importing a frozen library, never resuming search."""
    from importlib.metadata import version

    from alpha_atlas.contracts import DateRange, Fold
    from alpha_atlas.model import evaluate_models, factor_panel, fit_models, validate_config
    from alpha_atlas.reporting import write_model_report

    with run_lock(directory):
        spec = read_json(directory / "run.json")
        if spec.get("format_version") != FORMAT_VERSION or spec.get("status") != "frozen":
            raise ValueError(
                "model evaluation requires a frozen run in the current artifact format"
            )
        if config_fingerprint(spec) != spec["config_fingerprint"]:
            raise ValueError("run configuration was modified")
        frozen_path = directory / "frozen/library.json"
        if hashlib.sha256(frozen_path.read_bytes()).hexdigest() != spec["library_sha256"]:
            raise ValueError("frozen library was modified")
        frozen = read_json(frozen_path)
        if not frozen["factors"]:
            raise ValueError("model evaluation requires a nonempty frozen factor library")
        # Import recorded research settings, not today's search configuration. This is explicitly
        # a new experiment under current source. Source fingerprints are provenance only.
        fold = Fold(
            spec["fold"]["id"],
            *(
                DateRange(*(date.fromisoformat(spec["fold"][s][k]) for k in ("start", "end")))
                for s in ("train", "val", "test")
            ),
        )
        profile = spec["profile"]
        if "experiment_version" in spec:
            check_config(root, spec)
            config = spec["experiment_version"]["model"]
        else:
            config = load_toml(root / "configs/model.toml")
        validate_config(config)
        output = directory / "model"
        fit_path, test_path = output / "fit.json", output / "test.json"
        fitted = read_json(fit_path) if fit_path.exists() else None
        # Reusing a saved model retains its fitting-source record, without checking current code.
        current_source = fitted["spec"]["source_fingerprint"] if fitted else source_fingerprint()
        model_spec = {
            "implementation": "factor_models_v1",
            "search_run_id": spec["run_id"],
            "search_config_fingerprint": spec["config_fingerprint"],
            "library_sha256": spec["library_sha256"],
            "snapshot_id": spec["snapshot_id"],
            "search_source_fingerprint": spec["source_fingerprint"],
            "source_fingerprint": current_source,
            "source_matches_search": current_source == spec["source_fingerprint"],
            "config": config,
            "fold": spec["fold"],
            "primary_metric": profile["metric"],
            "versions": {name: version(name) for name in ("numpy", "polars", "lightgbm")},
        }
        if fitted is not None and fitted["spec"] != model_spec:
            raise ValueError("saved model configuration, dependencies or input changed")
        if test and fitted is None:
            raise ValueError("run atlas model <run_dir> before atlas model <run_dir> --test")
        if fitted is None and test_path.exists():
            raise ValueError("saved model test has no fitted model")
        if spec.get("implementation") == "zookeeper_fixed_audit_v1":
            from alpha_atlas.assets.zookeeper import ZookeeperMarketData
            from alpha_atlas.factor_libraries.zookeeper import definitions

            if frozen.get("definitions") != list(definitions()):
                raise ValueError("Zookeeper factor definitions changed after freeze")
            if spec["warmup"].get("policy") != "all_available_history_for_fixed_calendar_factors":
                raise ValueError("recorded Zookeeper warmup differs from model evaluation policy")
            registry = OperatorRegistry()
            market = ZookeeperMarketData(root)
            start = fold.test.start if test else fold.train.start
        else:
            registry = _registry(spec, _runtime(root, frozen["runtime"]))
            registry.restore(frozen["operators"])
            if warmup_period(fold.train.start) != spec["warmup"]:
                raise ValueError("recorded warmup differs from model evaluation policy")
            market = _market(root, spec)
            start = date.fromisoformat(spec["warmup"]["start"])
        with market:
            if market.snapshot_id() != spec["snapshot_id"]:
                raise ValueError("dataset changed after search")
            cached_path = test_path if test else fit_path
            if cached_path.exists():
                cached = read_json(cached_path)
                if cached["spec"] != model_spec:
                    raise ValueError("saved model evaluation specification changed")
                terminal_progress("模型 / 缓存命中", 文件=cached_path)
                return {"output": str(cached_path), "reports": cached["reports"]}
            terminal_progress("模型 / 加载行情", 阶段="test" if test else "train/val")
            features = market.load_features(
                fields=_data_fields(spec["fields"], profile),
                start=start,
                end=fold.test.end if test else fold.val.end,
            )
        panel, identities = factor_panel(features, frozen, spec, registry)
        if test:
            if identities != fitted["factor_ids"]:
                raise ValueError("model factor column order changed")
            reports = {
                "test": evaluate_models(
                    panel, fitted["fitted"], profile, features, fold.test, "test", config
                )
            }
            result = {"spec": model_spec, "reports": reports}
            atomic_json(test_path, result)
        else:
            columns = [f"factor_{i}" for i in range(len(identities))]
            training = pl.concat(
                [split_panel(panel, fold.train), split_panel(panel, fold.val)],
                how="vertical",
            )
            trained = fit_models(training, columns, config)
            reports = {}
            fitted = {
                "spec": model_spec,
                "factor_ids": identities,
                "fitted": trained,
                "reports": reports,
            }
            atomic_json(fit_path, fitted)
        write_model_report(output, fitted, result if test else None)
        terminal_progress("模型 / 完成", 报告=output / "report.md")
        return {"output": str(test_path if test else fit_path), "reports": reports}


def resume(root: Path, directory: Path, *, method_impl=None) -> Path:
    """Continue the same synchronous ask/tell run; never reinterpret a run(session) stack."""
    with run_lock(directory):
        spec = read_json(directory / "run.json")
        if spec.get("status") == "frozen":
            raise ValueError("run is already frozen")
        has_checkpoint = (directory / "checkpoint.json").exists()
        initial_setup = bool(spec.get("experiment_version", {}).get("initial_library"))
        if not spec.get("resumable") or not has_checkpoint and not initial_setup:
            raise ValueError("run has no resumable method checkpoint")
        with tracked_run(directory, spec) as progress:
            progress.stage = "resume_checks"
            terminal_progress("恢复检查 / 加载行情", 运行=spec["run_id"])
            fold, profile = check_config(root, spec)
            method = method_impl
            if method is None:
                if spec["implementation"] == "session_plugin":
                    raise ValueError("resume requires the original method_impl")
                method = make_method(spec["method"], spec["seed"], spec.get("method_config"))
            if _method_type(method) != spec["method_type"]:
                raise ValueError("method type changed")
            if spec.get("method_config") != getattr(method, "configuration", None):
                raise ValueError("method configuration changed")
            checkpoint = progress.load() if has_checkpoint else None
            store = RunStore(directory)
            trials = store.trials()
            if checkpoint is None:
                if trials:
                    raise ValueError("committed search trials require a method checkpoint")
            else:
                _check_checkpoint(
                    checkpoint,
                    trials,
                    spec["attempts"],
                    initial_count=len(spec.get("initial_library", {}).get("members", [])),
                )
            progress.previous_seconds = max(
                progress.previous_seconds, max((t["cumulative_seconds"] for t in trials), default=0)
            )
            if spec["status"] == "running":
                progress.failure(
                    RuntimeError(
                        "previous process ended without a final status; "
                        "unrecorded active time is unknown"
                    )
                )
            runtime = _runtime(root, spec["runtime"])
            with _market(root, spec) as market:
                if market.snapshot_id() != spec["snapshot_id"]:
                    raise ValueError("dataset changed after search")
                features = market.load_features(
                    fields=_data_fields(spec["fields"], profile),
                    start=date.fromisoformat(spec["warmup"]["start"]),
                    end=fold.val.end,
                )
            if warmup_period(fold.train.start) != spec["warmup"]:
                raise ValueError("warmup budget changed")
            terminal_progress("恢复数据就绪", 行数=f"{features.height:,}")
            progress.status("running")
            _search(directory, spec, method, progress, features, fold, runtime, checkpoint)
    return directory


def _check_checkpoint(checkpoint, trials, limit, *, initial_count=0):
    completed, pending = checkpoint["completed"], checkpoint["pending"]
    # Serial runs save one pending trial; concurrent evaluation saves the in-flight list.
    entries = pending if isinstance(pending, list) else [] if pending is None else [pending]
    if type(completed) is not int or not 0 <= completed <= len(trials):
        raise ValueError("checkpoint trial cursor mismatch")
    if len(trials) > completed + len(entries):
        raise ValueError("unexpected trials beyond checkpoint")
    attempts = sum(t["feedback"]["reason"] != "budget_rejected" for t in trials[:completed])
    version = trials[completed - 1]["feedback"]["library_version"] if completed else initial_count
    if checkpoint["attempts"] != attempts or checkpoint["library_version"] != version:
        raise ValueError("checkpoint budget or library version mismatch")
    if len(trials) > limit or completed + len(entries) > limit:
        raise ValueError("checkpoint exceeds attempt budget")
    for offset, entry in enumerate(entries):
        if entry["factor_trial_index"] != completed + offset + 1:
            raise ValueError("pending trial index mismatch")
        if len(trials) > completed + offset and candidate_from_dict(
            entry["candidate"]
        ) != candidate_from_dict(trials[completed + offset]["feedback"]["candidate"]):
            raise ValueError("pending candidate differs from committed trial")


def check_config(root, spec):
    if spec.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported run format; old artifacts are not migrated")
    if config_fingerprint(spec) != spec["config_fingerprint"]:
        raise ValueError("run configuration was modified")
    if "experiment_version" in spec:
        from alpha_atlas.versions import fold_from_version, load_version

        version = load_version(root, spec["experiment_version"]["id"])
        if version != spec["experiment_version"]:
            raise ValueError("experiment version changed after search")
        for key in ("asset", "universe", "fields", "profile", "rules", "fold"):
            if spec[key] != version[key]:
                raise ValueError(f"run differs from experiment version: {key}")
        return fold_from_version(version), version["profile"]
    fold = load_fold(root, spec["fold"]["id"])
    if json.loads(json.dumps(asdict(fold), default=str)) != spec["fold"]:
        raise ValueError("fold configuration changed after search")
    profile = asset_config(root, spec["asset"])
    if json.loads(json.dumps(profile, default=str)) != spec["profile"]:
        raise ValueError("asset configuration changed after search")
    if load_toml(root / "configs/benchmark.toml") != spec["rules"]:
        raise ValueError("evaluation configuration changed after search")
    if spec["implementation"] == "alphaprobe_atlas_v1":
        from alpha_atlas.methods.alphaprobe import AlphaProbeConfig

        current = AlphaProbeConfig.from_mapping(load_toml(root / "configs/alphaprobe.toml"))
        if json.loads(json.dumps(asdict(current))) != spec["method_config"]:
            raise ValueError("AlphaPROBE configuration changed")
    if spec["implementation"] == "alphaschema_atlas_v1":
        from alpha_atlas.methods.alphaschema import AlphaSchemaConfig

        current = AlphaSchemaConfig.from_mapping(load_toml(root / "configs/alphaschema.toml"))
        if json.loads(json.dumps(asdict(current))) != spec["method_config"]:
            raise ValueError("AlphaSchema configuration changed")
    if spec["implementation"] in {"single_agent_react_v1", "agentscope_react_v1"}:
        from alpha_atlas.methods.react import ReactConfig

        current = ReactConfig.from_mapping(load_toml(root / "configs/react.toml"))
        if asdict(current) != spec["method_config"]:
            raise ValueError("ReAct configuration changed")
    if spec["implementation"] == "mcts_llm_atlas_v1":
        from alpha_atlas.methods.mcts_llm import MCTSLLMConfig

        current = MCTSLLMConfig.from_mapping(load_toml(root / "configs/mcts_llm.toml"))
        if asdict(current) != spec["method_config"]:
            raise ValueError("MCTS-LLM configuration changed")
    if spec["implementation"] == "atlas_explorer_v1_async":
        from alpha_atlas.methods.v1_async import V1AsyncConfig

        current = V1AsyncConfig.from_mapping(load_toml(root / "configs/v1_async.toml"))
        if asdict(current) != {"selection_policy": None, **spec["method_config"]}:
            raise ValueError("v1_async configuration changed")
    if spec["implementation"] in {"annealing_llm_atlas_v1", "atlas_explorer_v1"}:
        from alpha_atlas.methods.annealing import AnnealingLLMConfig
        from alpha_atlas.methods.v1 import V1Config

        name = "v1" if spec["implementation"] == "atlas_explorer_v1" else "annealing"
        config_type = V1Config if name == "v1" else AnnealingLLMConfig
        current = config_type.from_mapping(load_toml(root / f"configs/{name}.toml"))
        expected = dict(spec["method_config"])
        if name == "v1":
            expected.setdefault("selection_policy", None)
        if asdict(current) != expected:
            raise ValueError(f"{name} configuration changed")
    return fold, profile


def _frozen_scores(spec, item, panel):
    clipping = item["report"].get("clipping", {})
    if bool(clipping) != (spec["rules"].get("clip_quantiles") is not None):
        raise ValueError("frozen clipping policy differs from search configuration")
    return apply_clipping(panel, clipping).with_columns(
        pl.col("value") * item["report"]["direction"]
    )


def _frozen_factor_result(spec, item, panel, weights):
    coverage = panel["value"].is_finite().fill_null(False).mean() if panel.height else None
    measured = metrics(
        _frozen_scores(spec, item, panel), spec["profile"]["metric"], "test", weights=weights
    )
    return {
        "expression_id": item["compiled"]["factor_id"],
        "status": "success",
        "metrics": [asdict(m) for m in measured],
        "coverage": coverage,
    }


def test_frozen(root: Path, directory: Path) -> dict:
    from alpha_atlas.reporting import refresh_report

    with run_lock(directory):
        try:
            terminal_progress("OOS 检查 / 加载行情", 运行=directory.name)
            return _test_frozen(root, directory)
        except BaseException as exc:
            terminal_progress("OOS 中断或失败", 类型=type(exc).__name__)
            raise
        finally:
            refresh_report(directory)


def _test_frozen(root: Path, directory: Path) -> dict:
    spec = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    if spec.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported run format; old artifacts are not migrated")
    if spec["status"] != "frozen":
        raise ValueError("OOS requires a frozen run")
    fold, profile = check_config(root, spec)
    freeze_path = directory / "frozen/library.json"
    if hashlib.sha256(freeze_path.read_bytes()).hexdigest() != spec["library_sha256"]:
        raise ValueError("frozen library was modified")
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    registry = _registry(spec, _runtime(root, frozen["runtime"]))
    with _market(root, spec) as market:
        if market.snapshot_id() != spec["snapshot_id"]:
            raise ValueError("dataset changed after search")
        # Only the independent post-freeze entrypoint requests OOS features.
        features = market.load_features(
            fields=_data_fields(spec["fields"], profile),
            start=date.fromisoformat(spec["warmup"]["start"]),
            end=fold.test.end,
        )
    if warmup_period(fold.train.start) != spec["warmup"]:
        raise ValueError("frozen warmup budget changed")
    terminal_progress("OOS 数据就绪", 行数=f"{features.height:,}", 成员=len(frozen["factors"]))
    registry.restore(frozen["operators"])
    report_id = fingerprint(
        (spec["library_sha256"], spec["config_fingerprint"], spec["source_fingerprint"])
    )
    destination = directory / "oos.json"
    if destination.exists():
        cached = json.loads(destination.read_text(encoding="utf-8"))
        if cached.get("report_id") != report_id:
            raise ValueError("cached OOS report fingerprint mismatch")
        terminal_progress("OOS 缓存命中", 成员=len(cached["results"]), 报告=directory / "report.md")
        return cached
    targets = build_targets(features, profile["price_field"], profile["target_horizon_bars"])
    weights = product_weights(features, fold.test) if profile["metric"] in FUTURES_METRICS else None
    results = []
    for index, item in enumerate(frozen["factors"], 1):
        started = time.perf_counter()
        terminal_progress("OOS 计算", transient=True, 进度=f"{index}/{len(frozen['factors'])}")
        try:
            expr = Expression.from_dict(item["compiled"]["expression"])
            compiled = compile_factor(
                expr,
                set(spec["fields"]),
                registry,
                max_nodes=spec["rules"]["max_expression_nodes"],
                max_depth=spec["rules"]["max_expression_depth"],
            )
            if compiled.factor_id != item["compiled"]["factor_id"]:
                raise ValueError("frozen operator dependency identity mismatch")
            values = execute(
                compiled,
                features,
                set(spec["fields"]),
                registry,
                panel=profile.get("panel", "contract"),
            )
            panel = features.join(targets, on="row_id", validate="1:1").join(
                values, on="row_id", validate="1:1"
            )
            panel = split_panel(panel, fold.test)
            result = _frozen_factor_result(spec, item, panel, weights)
        except (ValueError, RuntimeError, pl.exceptions.PolarsError) as exc:
            result = {
                "expression_id": item["compiled"]["factor_id"],
                "status": "compute_error",
                "error": str(exc),
            }
        results.append(result)
        coverage = result.get("coverage")
        terminal_progress(
            "OOS 因子完成",
            进度=f"{index}/{len(frozen['factors'])} / {result['status']}",
            IC=metric_text(Metric(**m) for m in result["metrics"])
            if result["status"] == "success"
            else None,
            覆盖率=f"{coverage:.2%}" if coverage is not None else None,
            耗时=f"{time.perf_counter() - started:.2f} s",
        )
    report = {
        "run_id": spec["run_id"],
        "report_id": report_id,
        "results": results,
        "note": "Frozen library IC audit, not executable trading returns.",
    }
    atomic_json(destination, report)
    terminal_progress("OOS 完成", 成员=len(results), 报告=directory / "report.md")
    return report
