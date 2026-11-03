"""Paired downstream comparison of a fixed baseline and frozen search libraries."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from importlib.metadata import version as dependency_version

import numpy as np
import polars as pl

from alpha_atlas.assets.common import atomic_json, atomic_parq
from alpha_atlas.checkpoint import config_fingerprint, read_json, run_lock
from alpha_atlas.evaluation import (
    paired_block_bootstrap,
    paired_prediction_metrics,
    product_weights,
    split_panel,
)
from alpha_atlas.model import factor_panel, fit_models, fit_preprocessing, predict, transform
from alpha_atlas.reporting import _expression, terminal_progress
from alpha_atlas.runner import (
    _check_config,
    _data_fields,
    _market,
    _registry,
    _runtime,
    source_fingerprint,
)
from alpha_atlas.storage import RunStore
from alpha_atlas.versions import load_version


def _frozen(directory):
    spec = read_json(directory / "run.json")
    if spec["status"] != "frozen":
        raise ValueError(f"all source searches must be frozen: {directory.name}")
    if config_fingerprint(spec) != spec["config_fingerprint"]:
        raise ValueError("source configuration changed")
    path = directory / "frozen/library.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec["library_sha256"]:
        raise ValueError("source frozen library changed")
    return spec, read_json(path)


def assemble_arms(baseline, sources, nodes, *, include_union=True):
    """Keep all accepted prefix members; deduplicate only existing canonical identities."""
    factors, positions, origins = [], {}, []

    def add(item, origin):
        identity = item["compiled"]["factor_id"]
        if identity not in positions:
            positions[identity] = len(factors)
            factors.append(item)
            origins.append([])
        i = positions[identity]
        origins[i].append(origin)
        return i

    base = [add(item, {"source": "baseline"}) for item in baseline]
    if len(set(base)) != len(base) or not base:
        raise ValueError("baseline must have distinct factors")
    arms = {"baseline": {"columns": base, "method": "baseline", "budget": None}}
    selected = {}
    for method, frozen, trials, usage in sources:
        lookup = {
            item["compiled"]["factor_id"]: item
            for item in frozen["factors"]
            if not item.get("initial", False)
        }
        accepted, used, accepted_ids = [], 0, set()
        for trial in trials:
            feedback = trial["feedback"]
            if feedback["reason"] != "budget_rejected":
                used += 1
            if feedback["accepted"]:
                identity = feedback["report"]["expression_id"]
                if identity in accepted_ids:
                    raise ValueError("duplicate accepted factor identity")
                accepted_ids.add(identity)
                item = lookup[identity]
                index = add(
                    item, {"source": method, "trial": trial["factor_trial_index"], "attempt": used}
                )
                accepted.append((used, index))
        if accepted_ids != set(lookup):
            raise ValueError("accepted trials differ from frozen membership")
        for node in nodes:
            members = [i for attempt, i in accepted if attempt <= node]
            selected[method, node] = members
            columns = list(dict.fromkeys([*base, *members]))
            arms[f"{method}_{node}"] = {
                "method": method,
                "budget": node,
                "actual_attempts": min(node, used),
                "budget_reached": used >= node,
                "admitted_count": len(members),
                "duplicate_baseline_count": len(members) - (len(columns) - len(base)),
                "columns": columns,
                "usage_full_run": usage if node == max(nodes) else None,
                "outcome_counts": dict(Counter(t["feedback"]["reason"] for t in trials[:node])),
            }
    for node in nodes if include_union else []:
        members = [i for method, *_ in sources for i in selected[method, node]]
        arms[f"union_{node}"] = {
            "method": "union",
            "source_methods": [m for m, *_ in sources],
            "budget": node,
            "actual_attempts": sum(arms[f"{m}_{node}"]["actual_attempts"] for m, *_ in sources),
            "admitted_count": len(members),
            "duplicate_count": len(base) + len(members) - len(set([*base, *members])),
            "columns": list(dict.fromkeys([*base, *members])),
        }
    return factors, origins, arms


def _load_study(root, scope_path, directories):
    scope = read_json(scope_path)
    plan = read_json(root / scope["parent_plan"])
    if scope["seeds"] != [42] or scope["experiment_version"] != plan["experiment_version"]:
        raise ValueError("comparison requires the predeclared single-seed version")
    source_version = scope.get("source_experiment_version", scope["experiment_version"])
    include_union = scope.get("include_union", True)
    if not isinstance(include_union, bool):
        raise ValueError("include_union must be boolean")
    sources = {}
    for directory in directories:
        spec, frozen = _frozen(directory)
        fold, _ = _check_config(root, spec)
        if (
            spec["method"] in sources
            or spec["method"] not in scope["methods"]
            or spec["seed"] != scope["seeds"][0]
            or spec["attempts"] != scope["attempts_per_method"]
            or spec["experiment_version"]["id"] != source_version
        ):
            raise ValueError("search inputs differ from planned method/seed/budget/version")
        sources[spec["method"]] = (directory, spec, frozen)
    if set(sources) != set(scope["methods"]):
        raise ValueError("comparison requires every planned search")
    ordered = [sources[name] for name in scope["methods"]]
    first = ordered[0][1]
    if any(spec["snapshot_id"] != first["snapshot_id"] for _, spec, _ in ordered):
        raise ValueError("search snapshots differ")
    baseline_dir = (root / plan["baseline_frozen_library"]).parent.parent
    baseline_spec, baseline_frozen = _frozen(baseline_dir)
    baseline_fit = read_json(root / plan["baseline_model_config"].split("#")[0])
    version = first["experiment_version"]
    if source_version != scope["experiment_version"]:
        version = load_version(root, scope["experiment_version"])
        for key in (
            "asset",
            "universe",
            "profile",
            "fold",
            "fields",
            "data",
            "data_recipe",
            "operator_versions",
            "initial_library",
        ):
            if any(spec["experiment_version"][key] != version[key] for _, spec, _ in ordered):
                raise ValueError(f"model refit version changes source {key}")
    if baseline_fit["spec"]["config"] != version["model"]:
        raise ValueError("paired model configuration differs from baseline")
    if baseline_spec["fold"] != first["fold"] or baseline_spec["profile"] != first["profile"]:
        raise ValueError("baseline asset or fold differs")
    for name in ("base", "curve"):
        if (
            baseline_spec["data_provenance"][f"{name}_snapshot"]
            != version["data"]["sources"][name]["snapshot_id"]
        ):
            raise ValueError("baseline raw data snapshot differs")
    if [f["compiled"]["factor_id"] for f in baseline_frozen["factors"]] != baseline_fit[
        "factor_ids"
    ]:
        raise ValueError("baseline factor order differs from its saved model")
    registry = _registry(first, _runtime(root, ordered[0][2]["runtime"]))
    definitions = {}
    for _, _spec, frozen in ordered:
        if frozen["runtime"] != ordered[0][2]["runtime"]:
            raise ValueError("search operator runtimes differ")
        for definition in frozen["operators"]:
            name = definition["name"]
            if name in definitions and definitions[name] != definition:
                raise ValueError("conflicting frozen operator definitions")
            definitions[name] = definition
    registry.restore(list(definitions.values()))
    members = []
    for directory, spec, frozen in ordered:
        initial_ids = {
            item["compiled"]["factor_id"] for item in frozen["factors"] if item.get("initial")
        }
        expected_initial = {
            item["factor_id"]
            for item in spec["experiment_version"].get("initial_library", {}).get("factors", [])
        }
        baseline_ids = {item["compiled"]["factor_id"] for item in baseline_frozen["factors"]}
        if initial_ids != expected_initial or initial_ids and initial_ids != baseline_ids:
            raise ValueError("source initial library differs from the paired baseline")
        usage = read_json(directory / "checkpoint.json")["method_state"].get("usage", {})
        members.append((spec["method"], frozen, RunStore(directory).trials(), usage))
    factors, origins, arms = assemble_arms(
        baseline_frozen["factors"], members, scope["evaluation_nodes"], include_union=include_union
    )
    source_records = [
        {
            "directory": str(d.relative_to(root)),
            "method": s.get("method", "baseline"),
            "seed": s.get("seed"),
            **{k: s[k] for k in ("run_id", "config_fingerprint", "library_sha256", "snapshot_id")},
        }
        for d, s, _ in [(baseline_dir, baseline_spec, baseline_frozen), *ordered]
    ]
    run_ids = {s["method"]: s["run_id"] for s in source_records}
    for origin_list in origins:
        for origin in origin_list:
            origin["run_id"] = run_ids[origin["source"]]
    study = {
        "implementation": "paired_frozen_library_v1",
        "scope": scope,
        "plan": plan,
        "sources": source_records,
        "model_config": version["model"],
        "factor_ids": [f["compiled"]["factor_id"] for f in factors],
        "arms": arms,
        "dependencies": {n: dependency_version(n) for n in ("numpy", "polars", "lightgbm")},
    }
    return study, first, fold, registry, factors, origins


def fit_arms(training, arms, config):
    """Anchor every fitted model to exactly the baseline's usable training cohort."""
    base_columns = [f"factor_{i}" for i in arms["baseline"]["columns"]]
    anchor = fit_preprocessing(
        training, base_columns, clip_quantiles=config.get("preprocessing", {}).get("clip_quantiles")
    )
    _, valid = transform(training, anchor)
    training = training.filter(pl.Series(valid))
    results = {}
    for name, arm in arms.items():
        terminal_progress("增量 / 拟合", 组=name, 行数=training.height, 列数=len(arm["columns"]))
        if name != "baseline" and arm["columns"] == arms["baseline"]["columns"]:
            results[name] = {"reuse": "baseline"}
            continue
        fitted = fit_models(training, [f"factor_{i}" for i in arm["columns"]], config)
        if fitted["training_rows"] != training.height:
            raise ValueError("paired model training cohorts differ")
        results[name] = fitted
    return {"training_rows": training.height, "arms": results}


def prediction_panel(scored, trained, config):
    """No extra coverage from newly added features in the primary comparison."""
    base = trained["arms"]["baseline"]
    _, valid = transform(scored, base["preprocessing"])
    scored = scored.filter(pl.Series(valid))
    output = scored.select("row_id", "trading_day", "product", "target")
    for name, saved in trained["arms"].items():
        if "reuse" in saved:
            output = output.with_columns(
                pl.col(f"{saved['reuse']}_{model}").alias(f"{name}_{model}")
                for model in ("linear", "lightgbm")
            )
            continue
        values, usable = transform(scored, saved["preprocessing"])
        if not usable.all():
            raise ValueError("augmented model lost baseline-predictable rows")
        for model, state in saved["models"].items():
            prediction = predict(
                state,
                values,
                threads=config["lightgbm"]["num_threads"],
                products=scored["product"].to_numpy(),
            )
            if not np.isfinite(prediction).all():
                raise ValueError("nonfinite model predictions")
            output = output.with_columns(pl.Series(f"{name}_{model}", prediction))
    return output


def compare_predictions(predictions, weights, study, trained):
    rows = []
    boot = study["plan"]["uncertainty"]
    for name, arm in study["arms"].items():
        if name == "baseline":
            continue
        for model in ("linear", "lightgbm"):
            terminal_progress("增量 / 配对评分", 组=name, 模型=model)
            panel = predictions.select(
                "row_id",
                "trading_day",
                "product",
                "target",
                pl.col(f"baseline_{model}").alias("baseline"),
                pl.col(f"{name}_{model}").alias("prediction"),
            )
            fitted = trained["arms"][name]
            if "reuse" in fitted:
                fitted = trained["arms"][fitted["reuse"]]
            report = paired_prediction_metrics(panel, weights)
            rows.append(
                {
                    "arm": name,
                    **arm,
                    "model": model,
                    "seed": study["scope"]["seeds"][0],
                    "retained_columns": len(fitted["preprocessing"]["columns"]),
                    "dropped_columns": fitted["preprocessing"]["dropped"],
                    "linear_rank": fitted["models"]["linear"]["rank"],
                    "test": report,
                    "by_year": {
                        str(year): paired_prediction_metrics(part, weights)
                        for year in sorted(panel["trading_day"].dt.year().unique().to_list())
                        if (part := panel.filter(pl.col("trading_day").dt.year() == year)).height
                    },
                    "bootstrap": paired_block_bootstrap(
                        panel,
                        weights,
                        days=boot["block_length_days"],
                        repeats=boot["replicates"],
                        seed=boot["seed"],
                    ),
                }
            )
    # Cross-method scores use one common product set, separately for each model and budget.
    for model in ("linear", "lightgbm"):
        for node in study["scope"]["evaluation_nodes"]:
            candidates = [
                r
                for r in rows
                if r["model"] == model
                and r["budget"] == node
                and r["method"] != "union"
                and r["test"]["ic_products"]
            ]
            common = (
                sorted(set.intersection(*(set(r["test"]["ic_products"]) for r in candidates)))
                if candidates
                else []
            )
            for row in candidates:
                panel = predictions.filter(pl.col("product").is_in(common)).select(
                    "row_id",
                    "trading_day",
                    "product",
                    "target",
                    pl.col(f"baseline_{model}").alias("baseline"),
                    pl.col(f"{row['arm']}_{model}").alias("prediction"),
                )
                row["cross_method_common_products"] = (
                    paired_prediction_metrics(panel, weights) if panel.height else None
                )
    return rows


def evaluate_incremental(root, scope_path, directories, output, *, test=False):
    """Fit all arms before opening Test; existing run/frozen identities remain authoritative."""
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with run_lock(output):
        study, spec, fold, registry, factors, origins = _load_study(
            root, scope_path, [p.resolve() for p in directories]
        )
        fit_path, test_path = output / "fit.json", output / "test.json"
        saved = read_json(fit_path) if fit_path.exists() else None
        if saved is not None and saved["spec"] != study:
            raise ValueError("paired fit inputs or configuration changed")
        if test and saved is None:
            raise ValueError("fit and freeze every paired model before Test")
        if saved is None and test_path.exists():
            raise ValueError("paired Test has no saved models")
        with _market(root, spec) as market:
            if market.snapshot_id() != spec["snapshot_id"]:
                raise ValueError("comparison data snapshot changed")
            if test and test_path.exists():
                if not saved or read_json(test_path)["spec"] != study:
                    raise ValueError("paired Test inputs changed")
                return {"output": str(test_path), "cached": True}
            if saved and not test:
                return {"output": str(fit_path), "cached": True}
            terminal_progress("增量 / 加载数据", 阶段="test" if test else "train/val")
            features = market.load_features(
                fields=_data_fields(spec["fields"], spec["profile"]),
                start=date.fromisoformat(spec["warmup"]["start"]),
                end=fold.test.end if test else fold.val.end,
            )
        panel, identities = factor_panel(features, {"factors": factors}, spec, registry)
        if identities != study["factor_ids"]:
            raise ValueError("paired factor identities changed")
        if not test:
            training = pl.concat([split_panel(panel, fold.train), split_panel(panel, fold.val)])
            trained = fit_arms(training, study["arms"], study["model_config"])
            exports = [
                {
                    "study_id": study["scope"]["id"],
                    "search_seed": study["scope"]["seeds"][0],
                    "factor_id": identities[i],
                    "expression": _expression(item["compiled"]["expression"]),
                    "canonical_expression": item["compiled"]["expression"],
                    "description": item["candidate"]["hypothesis"],
                    "direction": item["report"]["direction"],
                    "origins": origins[i],
                }
                for i, item in enumerate(factors)
            ]
            (output / "factors.jsonl").write_text(
                "".join(json.dumps(x, ensure_ascii=False, allow_nan=False) + "\n" for x in exports)
            )
            atomic_json(
                fit_path,
                {"spec": study, "fitted": trained, "source_fingerprint": source_fingerprint()},
            )
        else:
            scored = split_panel(panel, fold.test)
            predictions = prediction_panel(scored, saved["fitted"], study["model_config"])
            weights = product_weights(features, fold.test)
            destination = root / "data/comparisons" / study["scope"]["id"] / "predictions.parq"
            atomic_parq(predictions, destination)
            rows = compare_predictions(predictions, weights, study, saved["fitted"])
            for row in rows:
                state = saved["fitted"]["arms"][row["arm"]]
                if "reuse" in state:
                    state = saved["fitted"]["arms"][state["reuse"]]
                row["natural_model_coverage"] = scored.select(
                    pl.any_horizontal(
                        pl.col(state["preprocessing"]["columns"]).is_finite().fill_null(False)
                    ).mean()
                ).item()
                row["common_coverage"] = predictions.height / scored.height
                row["eligible_rows"] = scored.height
            result = {
                "spec": study,
                "eligible_rows": scored.height,
                "common_rows": predictions.height,
                "coverage": predictions.height / scored.height,
                "predictions": str(destination.relative_to(root)),
                "factor_coverage": [
                    {
                        "factor_id": identity,
                        "coverage": scored.select(
                            pl.col(f"factor_{i}").is_finite().fill_null(False).mean()
                        ).item(),
                    }
                    for i, identity in enumerate(identities)
                ],
                "results": rows,
            }
            (output / "results.jsonl").write_text(
                "".join(json.dumps(x, ensure_ascii=False, allow_nan=False) + "\n" for x in rows)
            )
            atomic_json(test_path, result)
        return {"output": str(test_path if test else fit_path)}
