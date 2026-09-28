"""Train-only factor prediction models; shared DSL and split-safe IC evaluation."""

import math
from dataclasses import asdict

import lightgbm as lgb
import numpy as np
import polars as pl

from alpha_atlas.contracts import Expression
from alpha_atlas.evaluation import (
    FUTURES_METRICS,
    build_targets,
    metrics,
    product_weights,
    split_panel,
)
from alpha_atlas.expressions import compile_factor, execute
from alpha_atlas.reporting import terminal_progress


def validate_config(config):
    expected = {
        "seed": None,
        "linear": {"rcond"},
        "lightgbm": {
            "num_boost_round",
            "learning_rate",
            "num_leaves",
            "min_data_in_leaf",
            "lambda_l2",
            "num_threads",
        },
    }
    if set(config) != set(expected) or any(
        not isinstance(config[k], dict) or set(config[k]) != keys
        for k, keys in expected.items()
        if keys is not None
    ):
        raise ValueError("model configuration must contain seed, linear and lightgbm settings")
    for name, value, minimum in [
        ("seed", config["seed"], 0),
        *[
            (k, config["lightgbm"][k], 2 if k == "num_leaves" else 1)
            for k in ("num_boost_round", "num_leaves", "min_data_in_leaf", "num_threads")
        ],
    ]:
        if type(value) is not int or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    for name, value, lower, upper in (
        ("rcond", config["linear"]["rcond"], 0, 1),
        ("learning_rate", config["lightgbm"]["learning_rate"], 0, 1),
        ("lambda_l2", config["lightgbm"]["lambda_l2"], 0, math.inf),
    ):
        if (
            type(value) not in {int, float}
            or not math.isfinite(value)
            or value > upper
            or (value < lower if name == "lambda_l2" else value <= lower)
        ):
            raise ValueError(f"invalid {name}")


def factor_panel(features, frozen, spec, registry):
    """Compute imported frozen expressions, joining by row ID rather than array position."""
    fields = set(spec["fields"])
    profile = spec["profile"]
    metadata = ["row_id", "trading_day", "eligible"]
    if profile["metric"] in FUTURES_METRICS:
        metadata.append("product")
    targets = build_targets(features, profile["price_field"], profile["target_horizon_bars"])
    panel = features.select(metadata).join(targets, on="row_id", validate="1:1")
    identities = []
    for index, item in enumerate(frozen["factors"]):
        terminal_progress("模型 / 计算因子", 进度=f"{index + 1}/{len(frozen['factors'])}")
        compiled = compile_factor(
            Expression.from_dict(item["compiled"]["expression"]),
            fields,
            registry,
            max_nodes=spec["rules"]["max_expression_nodes"],
            max_depth=spec["rules"]["max_expression_depth"],
        )
        if compiled.factor_id != item["compiled"]["factor_id"]:
            raise ValueError("frozen factor semantics changed; model evaluation refused")
        if compiled.factor_id in identities or item["report"]["direction"] not in {-1, 1}:
            raise ValueError("invalid frozen factor identity or direction")
        identities.append(compiled.factor_id)
        values = execute(compiled, features, fields, registry).select(
            "row_id", (pl.col("value") * item["report"]["direction"]).alias(f"factor_{index}")
        )
        panel = panel.join(values, on="row_id", how="left", validate="1:1")
    return panel, identities


def fit_preprocessing(train, columns):
    """Fit each column using finite train observations only; remove unusable columns."""
    names, means, scales, dropped = [], [], [], []
    for name in columns:
        values = train[name].to_numpy()
        finite = values[np.isfinite(values)]
        mean = float(finite.mean()) if finite.size else None
        scale = float(finite.std()) if finite.size else None
        if mean is None or not math.isfinite(mean) or not math.isfinite(scale) or scale <= 1e-12:
            dropped.append(name)
            continue
        names.append(name)
        means.append(mean)
        scales.append(scale)
    if not names:
        raise ValueError("no nonconstant factor with finite training observations")
    return {"columns": names, "mean": means, "scale": scales, "dropped": dropped}


def transform(panel, preprocessing):
    values = panel.select(preprocessing["columns"]).to_numpy().astype(np.float64, copy=True)
    finite = np.isfinite(values)
    valid = finite.any(axis=1)
    np.subtract(values, preprocessing["mean"], out=values)
    np.divide(values, preprocessing["scale"], out=values)
    values[~finite] = 0.0  # Training-mean imputation, after standardization.
    if not np.isfinite(values).all():
        raise ValueError("factor standardization overflow")
    return values, valid


def predict(state, values, *, threads):
    if state["kind"] == "linear":
        return values @ np.asarray(state["coefficients"]) + state["intercept"]
    if state["kind"] == "lightgbm":
        return lgb.Booster(model_str=state["model_text"]).predict(values, num_threads=threads)
    raise ValueError("unknown prediction model")


def fit_models(train, columns, config):
    validate_config(config)
    if train.height < 2:
        raise ValueError("model training requires at least two eligible labeled rows")
    preprocessing = fit_preprocessing(train, columns)
    values, valid = transform(train, preprocessing)
    x = values[valid]
    y = train["target"].to_numpy()[valid]
    if len(y) < 2 or not np.isfinite(y).all() or np.ptp(y) == 0:
        raise ValueError("model training requires finite, nonconstant targets")
    terminal_progress("模型 / 训练", 类型="linear", 行数=len(y), 因子数=x.shape[1])
    # Center again on usable rows so the intercept is exact even with missing factors.
    center, intercept = x.mean(axis=0), float(y.mean())
    coefficients, _, rank, _ = np.linalg.lstsq(
        x - center, y - intercept, rcond=config["linear"]["rcond"]
    )
    linear = {
        "kind": "linear",
        "coefficients": coefficients.tolist(),
        "intercept": float(intercept - center @ coefficients),
        "rank": int(rank),
    }
    options = dict(config["lightgbm"])
    rounds = options.pop("num_boost_round")
    terminal_progress("模型 / 训练", 类型="lightgbm", 行数=len(y), 因子数=x.shape[1])
    booster = lgb.train(
        {
            **options,
            "objective": "regression",
            "metric": "None",
            "verbosity": -1,
            "seed": config["seed"],
            "deterministic": True,
            "force_col_wise": True,
        },
        lgb.Dataset(x, label=y, feature_name=preprocessing["columns"]),
        num_boost_round=rounds,
    )
    return {
        "preprocessing": preprocessing,
        "training_rows": len(y),
        "models": {
            "linear": linear,
            "lightgbm": {"kind": "lightgbm", "model_text": booster.model_to_string()},
        },
    }


def evaluate_models(panel, fitted, profile, features, period, split, config):
    scored = split_panel(panel, period)
    values, valid = transform(scored, fitted["preprocessing"])
    weights = product_weights(features, period) if profile["metric"] in FUTURES_METRICS else None
    reports = {}
    for name, state in fitted["models"].items():
        predictions = np.full(scored.height, np.nan)
        if valid.any():
            predictions[valid] = predict(
                state, values[valid], threads=config["lightgbm"]["num_threads"]
            )
            if not np.isfinite(predictions[valid]).all():
                raise ValueError(f"{name} returned nonfinite predictions")
        measured = metrics(
            scored.with_columns(pl.Series("value", predictions)),
            profile["metric"],
            split,
            weights=weights,
        )
        reports[name] = {
            "metrics": [asdict(m) for m in measured],
            "eligible_rows": scored.height,
            "predicted_rows": int(valid.sum()),
            "coverage": float(valid.mean()) if scored.height else None,
        }
    return reports
