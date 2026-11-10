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
from alpha_atlas.terminal import terminal_progress


def validate_config(config):
    expected = {
        "seed": None,
        "linear": {"rcond"},
        "lightgbm": {
            "num_boost_round",
            "learning_rate",
            "num_leaves",
            "lambda_l2",
            "num_threads",
        },
    }
    if set(config) - {"preprocessing"} != set(expected) or any(
        not isinstance(config[k], dict)
        or set(config[k]) - ({"max_depth", "min_data_in_leaf"} if k == "lightgbm" else set())
        != keys
        for k, keys in expected.items()
        if keys is not None
    ):
        raise ValueError("model configuration must contain seed, linear and lightgbm settings")
    depth = config["lightgbm"].get("max_depth", -1)
    if type(depth) is not int or (depth != -1 and depth < 1):
        raise ValueError("max_depth must be -1 or a positive integer")
    if "preprocessing" in config:
        preprocessing = config["preprocessing"]
        if not isinstance(preprocessing, dict) or set(preprocessing) != {
            "clip_quantiles",
            "product_demean",
        }:
            raise ValueError("preprocessing requires clip_quantiles and product_demean")
        quantiles = preprocessing["clip_quantiles"]
        if (
            not isinstance(quantiles, list)
            or len(quantiles) != 2
            or any(type(q) not in {int, float} or not math.isfinite(q) for q in quantiles)
            or not 0 <= quantiles[0] < quantiles[1] <= 1
            or type(preprocessing["product_demean"]) is not bool
        ):
            raise ValueError("invalid model preprocessing settings")
    for name, value, minimum in [
        ("seed", config["seed"], 0),
        *[
            (k, config["lightgbm"][k], 2 if k == "num_leaves" else 1)
            for k in ("num_boost_round", "num_leaves", "min_data_in_leaf", "num_threads")
            if k in config["lightgbm"]
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
        terminal_progress(
            "模型 / 计算因子", transient=True, 进度=f"{index + 1}/{len(frozen['factors'])}"
        )
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
        values = execute(
            compiled, features, fields, registry, panel=profile.get("panel", "contract")
        ).select("row_id", (pl.col("value") * item["report"]["direction"]).alias(f"factor_{index}"))
        panel = panel.join(values, on="row_id", how="left", validate="1:1")
    return panel, identities


def fit_preprocessing(train, columns, *, clip_quantiles=None):
    """Fit each column using finite train observations only; remove unusable columns."""
    names, means, scales, dropped = [], [], [], []
    lower, upper = [], []
    for name in columns:
        values = train[name].to_numpy()
        finite = values[np.isfinite(values)]
        if finite.size and clip_quantiles is not None:
            lo, hi = np.quantile(finite, clip_quantiles)
            finite = np.clip(finite, lo, hi)
        mean = float(finite.mean()) if finite.size else None
        scale = float(finite.std()) if finite.size else None
        if mean is None or not math.isfinite(mean) or not math.isfinite(scale) or scale <= 1e-12:
            dropped.append(name)
            continue
        names.append(name)
        means.append(mean)
        scales.append(scale)
        if clip_quantiles is not None:
            lower.append(float(lo))
            upper.append(float(hi))
    if not names:
        raise ValueError("no nonconstant factor with finite training observations")
    result = {"columns": names, "mean": means, "scale": scales, "dropped": dropped}
    if clip_quantiles is not None:
        result.update(clip_quantiles=clip_quantiles, lower=lower, upper=upper)
    return result


def transform(panel, preprocessing):
    values = panel.select(preprocessing["columns"]).to_numpy().astype(np.float64, copy=True)
    finite = np.isfinite(values)
    valid = finite.any(axis=1)
    if "lower" in preprocessing:
        np.clip(values, preprocessing["lower"], preprocessing["upper"], out=values)
    np.subtract(values, preprocessing["mean"], out=values)
    np.divide(values, preprocessing["scale"], out=values)
    values[~finite] = 0.0  # Training-mean imputation, after standardization.
    if not np.isfinite(values).all():
        raise ValueError("factor standardization overflow")
    return values, valid


def predict(state, values, *, threads, products=None):
    if state["kind"] == "linear":
        prediction = values @ np.asarray(state["coefficients"]) + state["intercept"]
        if "product_offsets" in state:
            if products is None or len(products) != len(values):
                raise ValueError("product-centered linear prediction requires row products")
            prediction -= np.array([state["product_offsets"].get(p, 0.0) for p in products])
        return prediction
    if state["kind"] == "lightgbm":
        return lgb.Booster(model_str=state["model_text"]).predict(values, num_threads=threads)
    raise ValueError("unknown prediction model")


def fit_models(train, columns, config):
    validate_config(config)
    if train.height < 2:
        raise ValueError("model training requires at least two eligible labeled rows")
    options = config.get("preprocessing", {})
    quantiles = options.get("clip_quantiles")
    preprocessing = fit_preprocessing(train, columns, clip_quantiles=quantiles)
    values, valid = transform(train, preprocessing)
    x = values[valid]
    y = train["target"].to_numpy()[valid]
    if len(y) < 2 or not np.isfinite(y).all() or np.ptp(y) == 0:
        raise ValueError("model training requires finite, nonconstant targets")
    if quantiles is not None:
        labels = train["target"].to_numpy()
        bounds = np.quantile(labels[np.isfinite(labels)], quantiles)
        preprocessing["label_bounds"] = bounds.tolist()
        y = np.clip(y, *bounds)
        if np.ptp(y) == 0:
            raise ValueError("clipped model training targets are constant")
    product_demean = options.get("product_demean", False) and "product" in train.columns
    if options:
        preprocessing["product_demean"] = product_demean
    terminal_progress("模型 / 训练", 类型="linear", 行数=len(y), 因子数=x.shape[1])
    # Center again on usable rows so the intercept is exact even with missing factors.
    product_means = {}
    target = y
    if product_demean:
        products = train["product"].to_numpy()[valid]
        target = y.copy()
        for product in np.unique(products):
            rows = products == product
            product_means[str(product)] = x[rows].mean(axis=0)
            x[rows] -= product_means[str(product)]
            target[rows] -= y[rows].mean()
        coefficients, _, rank, _ = np.linalg.lstsq(x, target, rcond=config["linear"]["rcond"])
        center, intercept = np.zeros(x.shape[1]), 0.0
        # LightGBM mode 2 uses globally preprocessed features and centered labels.
        x = values[valid]
    else:
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
    if product_demean:
        linear["product_offsets"] = {
            product: float(mean @ coefficients) for product, mean in product_means.items()
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
        lgb.Dataset(x, label=target, feature_name=preprocessing["columns"]),
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
                state,
                values[valid],
                threads=config["lightgbm"]["num_threads"],
                products=scored["product"].to_numpy()[valid]
                if "product" in scored.columns
                else None,
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
