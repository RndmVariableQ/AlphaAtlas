"""Independent runs, file evidence, frozen definitions, and isolated OOS evaluation."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

import polars as pl

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.futures import BAR_FIELDS
from alpha_atlas.assets.market import ParqMarketData
from alpha_atlas.atlas import FactorAtlas
from alpha_atlas.checkpoint import (
    config_fingerprint,
    read_json,
    run_lock,
    tracked_run,
)
from alpha_atlas.config import asset_config, load_fold, load_toml
from alpha_atlas.contracts import Expression, fingerprint
from alpha_atlas.evaluation import (
    FUTURES_METRICS,
    EvaluationService,
    build_targets,
    metrics,
    product_weights,
    split_panel,
)
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.history import one_year_before, warmup_period
from alpha_atlas.library import FactorLibrary
from alpha_atlas.methods import make_method
from alpha_atlas.methods.baselines import default_regions
from alpha_atlas.operators import OperatorRegistry
from alpha_atlas.operators.runtime import NumbaRuntime, RuntimeLimits
from alpha_atlas.reporting import metric_text, terminal_progress
from alpha_atlas.session import SearchSession
from alpha_atlas.storage import (
    FORMAT_VERSION,
    ArrowCache,
    RunStore,
    candidate_from_dict,
    feedback_from_dict,
)


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
) -> Path:
    profile = asset_config(root, asset)
    fold = load_fold(root, fold_id)
    rules = load_toml(root / "configs/benchmark.toml")
    limit = attempts if attempts is not None else rules["max_attempts"]
    if limit <= 0:
        raise ValueError("attempt budget must be positive")
    universe = universe or profile["default_universe"]
    if universe not in profile["universes"]:
        raise ValueError(f"unsupported universe {universe}")
    if field_names is None and asset == "futures":
        requested_fields = BAR_FIELDS
    else:
        requested_fields = field_names or ["volume", "amount"]
    fields = list(dict.fromkeys([profile["price_field"], *requested_fields]))
    options = (
        load_toml(root / f"configs/{method_name}.toml")
        if method_name in {"alphaprobe", "react", "mcts_llm"}
        else None
    )
    method = method_impl if method_impl is not None else make_method(method_name, seed, options)
    if method_name == "mcts_llm" and rules.get("search_diagnostics") != "icir_turnover_v1":
        raise ValueError("MCTS-LLM requires shared icir_turnover_v1 diagnostics")
    resumable = not hasattr(method, "run") and all(
        callable(getattr(method, name, None)) for name in ("dump_state", "load_state")
    )
    run_id = f"{asset}-{universe}-{fold_id}-{method_name}-{seed}-{uuid.uuid4().hex[:10]}"
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
            else "mcts_llm_atlas_v1"
            if method_name == "mcts_llm"
            else "finite_grammar_baseline_v2"
        ),
        "source_fingerprint": source_fingerprint(),
        "resumable": resumable,
        "method_type": _method_type(method) if resumable else None,
        "continuity": "observed_native_bars; reset known gaps and dominant re-entry; "
        "unknown intraday missing bars are not detected",
    }
    if hasattr(method, "configuration"):
        spec["method_config"] = method.configuration
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
        with ParqMarketData(
            root / profile["data_dir"], universe, allow_incomplete=allow_incomplete
        ) as market:
            snapshot_id = market.snapshot_id()
            features = market.load_features(
                fields=_data_fields(fields, profile),
                start=one_year_before(fold.train.start),
                end=fold.val.end,
            )
        warmup = warmup_period(fold.train.start)
        terminal_progress("行情就绪", 行数=f"{features.height:,}")
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
    registry = OperatorRegistry(runtime=runtime)
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
        retain_training=hasattr(method, "correlation_pairs") or spec["method"] == "mcts_llm",
        diagnostics=rules.get("search_diagnostics", ""),
    )
    library = FactorLibrary(
        rules,
        min_train_val_ic=profile.get("min_train_val_ic"),
        snapshot_id=snapshot_id,
        context_id=evaluator.context_id,
        directory=directory / "cache/members",
        disk_cache=disk_cache,
        rebuild=lambda entry: _rebuild_member(evaluator, entry),
    )
    trials = store.trials()
    if checkpoint is not None:
        library.restore(store.library_view())
    session = SearchSession(
        evaluator, library, store, spec["attempts"], elapsed=progress.elapsed, run_spec=spec
    )
    if hasattr(method, "set_session"):
        method.set_session(session)
    atlas = FactorAtlas()
    for region in default_regions():
        atlas.register_hypothesis(region)
    pending = None
    if checkpoint is not None:
        method.load_state(checkpoint["method_state"])
        pending = checkpoint["pending"]
        for trial in trials[: checkpoint["completed"]]:
            atlas.observe(feedback_from_dict(trial["feedback"]))
    elif spec["resumable"]:
        progress.save(method, 0, 0, 0)

    def save(pending=None):
        if spec["resumable"]:
            progress.save(
                method, session.trial_count, session.attempts_used, library.view().version, pending
            )

    if hasattr(method, "set_checkpoint"):
        method.set_checkpoint(save)

    if hasattr(method, "run"):
        progress.stage = "method_run"
        method.run(session)
    else:
        while pending is not None or session.remaining_budget():
            if pending is None:
                progress.stage = "ask"
                terminal_progress("生成候选", 进度=f"{session.trial_count + 1}/{spec['attempts']}")
                step = time.perf_counter()
                context = session.get_context()
                candidate = method.ask(context, count=1)[0]
                pending = {
                    "trial_index": session.trial_count + 1,
                    "candidate": asdict(candidate),
                    "generation_seconds": time.perf_counter() - step,
                }
                progress.stage = "checkpoint"
                save(pending)
            progress.stage = "evaluate"
            index = pending["trial_index"]
            if index <= len(trials):
                feedback = feedback_from_dict(trials[index - 1]["feedback"])
                terminal_progress("恢复已提交反馈", 候选=index, 结果=feedback.reason)
            else:
                feedback = session.evaluate(
                    candidate_from_dict(pending["candidate"]),
                    generation_seconds=pending["generation_seconds"],
                )
            progress.stage = "tell"
            method.tell([feedback])
            atlas.observe(feedback)
            progress.stage = "checkpoint"
            save()
            pending = None
    progress.stage = "freeze"
    terminal_progress("冻结因子库", 成员=len(library.view().members))
    view = store.library_view()
    if view != library.view():
        raise ValueError("committed library differs from memory")
    archive = []
    for entry in view.members:
        compiled = compile_factor(
            entry.candidate.expression, set(fields), registry, **evaluator.compile_options
        )
        archive.append(
            {
                "candidate": asdict(entry.candidate),
                "report": asdict(entry.report),
                "compiled": asdict(compiled),
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
    )


def _rebuild_member(evaluator, entry):
    report = evaluator.evaluate(entry.candidate)
    if (
        report.expression_id != entry.factor_id
        or report.metrics != entry.report.metrics
        or report.direction != entry.report.direction
        or report.coverage != entry.report.coverage
        or report.status != entry.report.status
    ):
        raise ValueError("rebuilt member differs from committed evaluation")
    return evaluator.observation(report.observation_ref)


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
        registry = OperatorRegistry(runtime=_runtime(root, frozen["runtime"]))
        registry.restore(frozen["operators"])
        with ParqMarketData(
            root / profile["data_dir"],
            spec["universe"],
            allow_incomplete=spec["allow_incomplete"],
        ) as market:
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
                start=date.fromisoformat(spec["warmup"]["start"]),
                end=fold.test.end if test else fold.val.end,
            )
        if warmup_period(fold.train.start) != spec["warmup"]:
            raise ValueError("recorded warmup differs from model evaluation policy")
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
        if not spec.get("resumable") or not (directory / "checkpoint.json").exists():
            raise ValueError("run has no resumable method checkpoint")
        with tracked_run(directory, spec) as progress:
            progress.stage = "resume_checks"
            terminal_progress("恢复检查 / 加载行情", 运行=spec["run_id"])
            fold, profile = _check_config(root, spec)
            method = method_impl
            if method is None:
                if spec["implementation"] == "session_plugin":
                    raise ValueError("resume requires the original method_impl")
                method = make_method(spec["method"], spec["seed"], spec.get("method_config"))
            if _method_type(method) != spec["method_type"]:
                raise ValueError("method type changed")
            if spec.get("method_config") != getattr(method, "configuration", None):
                raise ValueError("method configuration changed")
            checkpoint = progress.load()
            store = RunStore(directory)
            trials = store.trials()
            _check_checkpoint(checkpoint, trials, spec["attempts"])
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
            with ParqMarketData(
                root / profile["data_dir"],
                spec["universe"],
                allow_incomplete=spec["allow_incomplete"],
            ) as market:
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


def _check_checkpoint(checkpoint, trials, limit):
    completed, pending = checkpoint["completed"], checkpoint["pending"]
    if type(completed) is not int or not 0 <= completed <= len(trials):
        raise ValueError("checkpoint trial cursor mismatch")
    if len(trials) > completed + int(pending is not None):
        raise ValueError("unexpected trials beyond checkpoint")
    attempts = sum(t["feedback"]["reason"] != "budget_rejected" for t in trials[:completed])
    version = trials[completed - 1]["feedback"]["library_version"] if completed else 0
    if checkpoint["attempts"] != attempts or checkpoint["library_version"] != version:
        raise ValueError("checkpoint budget or library version mismatch")
    if len(trials) > limit or pending is not None and completed >= limit:
        raise ValueError("checkpoint exceeds attempt budget")
    if pending is not None:
        if pending["trial_index"] != completed + 1:
            raise ValueError("pending trial index mismatch")
        if len(trials) > completed and candidate_from_dict(
            pending["candidate"]
        ) != candidate_from_dict(trials[completed]["feedback"]["candidate"]):
            raise ValueError("pending candidate differs from committed trial")


def _check_config(root, spec):
    if spec.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported run format; old artifacts are not migrated")
    if config_fingerprint(spec) != spec["config_fingerprint"]:
        raise ValueError("run configuration was modified")
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
    return fold, profile


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
    fold, profile = _check_config(root, spec)
    freeze_path = directory / "frozen/library.json"
    if hashlib.sha256(freeze_path.read_bytes()).hexdigest() != spec["library_sha256"]:
        raise ValueError("frozen library was modified")
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    registry = OperatorRegistry(runtime=_runtime(root, frozen["runtime"]))
    with ParqMarketData(
        root / profile["data_dir"], spec["universe"], allow_incomplete=spec["allow_incomplete"]
    ) as market:
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
        terminal_progress("OOS 计算", 进度=f"{index}/{len(frozen['factors'])}")
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
            values = execute(compiled, features, set(spec["fields"]), registry)
            panel = features.join(targets, on="row_id", validate="1:1").join(
                values, on="row_id", validate="1:1"
            )
            panel = split_panel(panel, fold.test).with_columns(
                pl.col("value") * item["report"]["direction"]
            )
            measured = metrics(panel, profile["metric"], "test", weights=weights)
            result = {
                "expression_id": compiled.factor_id,
                "status": "success",
                "metrics": [asdict(m) for m in measured],
                "coverage": panel["value"].is_finite().fill_null(False).mean()
                if panel.height
                else None,
            }
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
            IC=metric_text(measured) if result["status"] == "success" else None,
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
