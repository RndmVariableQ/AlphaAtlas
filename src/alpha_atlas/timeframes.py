"""Completed 5m-derived feature bars and causal broadcasting; never target resampling."""

from __future__ import annotations

from dataclasses import replace

import polars as pl

from alpha_atlas.contracts import Expression

INTERVALS = {"15m": 15, "30m": 30, "60m": 60, "1d": 1440}
AGGREGATIONS = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "adj_open": "first",
    "adj_high": "max",
    "adj_low": "min",
    "adj_close": "last",
    "adj_vwap": "volume_weighted",
    "volume": "sum",
    "amount": "sum",
    "open_interest": "last",
    "days_to_maturity": "last",
}
GROUP = ["exchange", "instrument_id"]


def field_reference(value: str) -> tuple[str, str | None]:
    name, marker, interval = value.partition("@")
    if marker:
        if interval not in INTERVALS:
            raise ValueError("supported field intervals are @15m, @30m, @60m, @1d")
        base = name.removesuffix("_p1").removesuffix("_p2")
        if base not in AGGREGATIONS:
            raise ValueError(f"no timeframe aggregation rule for field: {name}")
    return name, interval or None


def frequency_scopes(root: Expression) -> dict[Expression, str | None]:
    scopes = {}

    def visit(node):
        if node in scopes:
            return scopes[node]
        if node.op == "field":
            scope = field_reference(str(node.value))[1] or "base"
        elif node.op == "const":
            scope = None
        else:
            children = {visit(a) for a in node.args} - {None}
            scope = next(iter(children)) if len(children) == 1 else "base" if children else None
        scopes[node] = scope
        return scope

    visit(root)
    return scopes


def timeframe_panel(
    frame: pl.DataFrame, fields: set[str], interval: str, *, panel: str = "contract"
) -> pl.DataFrame:
    """Natural-clock intraday ends; a daily bar is confirmed by the next trading day's bar.

    Group all observed rows: continuity changes and invalid bars invalidate the bucket.
    Ordinary fields require finite quotes; adjusted VWAP weights finite quotes with positive
    finite volume. No synthetic bars or missing-value fills.
    Scheduled breaks need no invented 6/12-row requirement. A missing *whole* native bar
    cannot be distinguished from a break without an exchange schedule (as in MarketData).
    """
    if panel not in {"contract", "product"}:
        raise ValueError("panel must be 'contract' or 'product'")
    identity = GROUP if panel == "contract" else ["exchange", "product"]
    if panel == "product" and "instrument_id" not in frame.columns:
        raise ValueError("product timeframe panel requires instrument_id metadata")
    main_name = "segment_id" if panel == "contract" else "product_segment_id"
    leg_name = "segment_id_{leg}" if panel == "contract" else "product_segment_id_{leg}"
    source = frame.sort(identity + ["timestamp"])
    main_segment = [main_name]
    legs = {f"p{i}" for i in (1, 2) if any(f.endswith(f"_p{i}") for f in fields)}
    segments = main_segment + [leg_name.format(leg=leg) for leg in sorted(legs)]
    if missing := set(segments) - set(source.columns):
        raise ValueError(f"timeframes require continuity metadata: {sorted(missing)}")
    if interval == "1d":
        # The next observed day's first timestamp is known at publication, never before.
        # In contrast, max(timestamp) of a truncated current day is not a close schedule.
        publication = (
            source.group_by(identity + ["trading_day"])
            .agg(pl.col("timestamp").min().alias("__first"))
            .sort(identity + ["trading_day"])
            .with_columns(pl.col("__first").shift(-1).over(identity).alias("__end"))
            .drop("__first")
        )
        source = source.join(publication, on=identity + ["trading_day"], validate="m:1")
    else:
        minutes = INTERVALS[interval]
        source = source.with_columns(
            (
                (pl.col("timestamp") - pl.duration(microseconds=1)).dt.truncate(interval)
                + pl.duration(minutes=minutes)
            ).alias("__end")
        )
    aggregates = [pl.col(c).last() for c in segments]
    aggregates += [pl.col("eligible").last(), pl.col("trading_day").last()]
    if panel == "product":
        aggregates.append(pl.col("instrument_id").last())
    stable_main = pl.col(main_name).n_unique() == 1
    if "valid_bar" in source.columns:
        stable_main = stable_main & pl.col("valid_bar").fill_null(False).all()
    for field in sorted(fields):
        base = field.removesuffix("_p1").removesuffix("_p2")
        rule = AGGREGATIONS[base]
        valid = stable_main
        if rule == "volume_weighted":
            weight_name = "volume" + field[len(base) :]
            if weight_name not in source.columns:
                raise ValueError(f"{field}@{interval} requires {weight_name} for aggregation")
            price, weight = pl.col(field), pl.col(weight_name).cast(pl.Float64)
            usable = price.is_finite() & weight.is_finite() & (weight > 0)
            numerator = pl.when(usable).then(price * weight).otherwise(0.0).sum()
            denominator = pl.when(usable).then(weight).otherwise(0.0).sum()
            aggregate = pl.when(denominator > 0).then(numerator / denominator)
        else:
            valid = valid & pl.col(field).is_finite().fill_null(False).all()
            aggregate = getattr(pl.col(field), rule)()
        for leg in legs:
            if field.endswith(f"_{leg}"):
                valid = valid & (pl.col(leg_name.format(leg=leg)).n_unique() == 1)
                if f"valid_bar_{leg}" in source.columns:
                    valid = valid & pl.col(f"valid_bar_{leg}").fill_null(False).all()
        aggregates.append(pl.when(valid).then(aggregate).alias(field))
    return (
        source.group_by(identity + ["__end"], maintain_order=True)
        .agg(aggregates)
        .filter(pl.col("__end").is_not_null())
        .rename({"__end": "timestamp"})
        .sort(identity + ["timestamp"])
        .with_row_index("row_id")
    )


def execute_timeframes(compiled, frame, fields, registry, scopes, *, panel="contract"):
    from alpha_atlas.expressions import execute

    if panel not in {"contract", "product"}:
        raise ValueError("panel must be 'contract' or 'product'")
    panel_mode = panel
    identity = GROUP if panel == "contract" else ["exchange", "product"]
    main_name = "segment_id" if panel == "contract" else "product_segment_id"
    leg_name = "segment_id_{leg}" if panel == "contract" else "product_segment_id_{leg}"
    required = {"bar_interval_minutes", "trading_day", main_name, "eligible", *identity}
    if missing := required - set(frame.columns):
        raise ValueError(f"timeframes require native frequency/day/continuity metadata: {missing}")
    if frame.height and frame["bar_interval_minutes"].unique().to_list() != [5]:
        raise ValueError("@15m/@30m/@60m/@1d require base 5m input")
    if frame.select(identity + ["timestamp"]).is_duplicated().any():
        raise ValueError("duplicate native observation keys")
    if frame.select(
        pl.any_horizontal(pl.col(*identity, "timestamp", "trading_day").is_null()).any()
    ).item():
        raise ValueError("null native observation keys or trading days")
    work = frame.sort(identity + ["timestamp"])
    if work.select((pl.col("trading_day").diff().over(identity).dt.total_days() < 0).any()).item():
        raise ValueError("trading days must not move backward within a contract")
    generated = {}
    panels = {}
    field_legs = {f: {f"p{i}" for i in (1, 2) if f.endswith(f"_p{i}")} for f in compiled.fields}

    def strip(node):
        return Expression(
            node.op,
            tuple(strip(a) for a in node.args),
            str(node.value).split("@")[0] if node.op == "field" else node.value,
        )

    def field_names(node):
        if node.op == "field":
            return {str(node.value).split("@")[0]}
        return set().union(*(field_names(a) for a in node.args))

    def transform(node):
        nonlocal work
        interval = scopes[node]
        if interval in {None, "base"}:
            return Expression(node.op, tuple(transform(a) for a in node.args), node.value)
        if node in generated:
            return generated[node]
        names = field_names(node)
        key = (interval, tuple(sorted(names)))
        if key not in panels:
            panels[key] = timeframe_panel(frame, names, interval, panel=panel_mode)
        panel = panels[key]
        # Preserve the original registry dependencies, without creating new factor identities.
        native = replace(compiled, expression=strip(node), fields=tuple(sorted(names)))
        values = execute(native, panel, names, registry, panel=panel_mode)
        used_legs = set().union(*(field_legs[f] for f in names))
        keys = identity + [main_name] + [leg_name.format(leg=leg) for leg in sorted(used_legs)]
        name = f"__timeframe_{len(generated)}"
        field_legs[name] = used_legs
        right = (
            panel.select("row_id", "timestamp", *keys)
            .join(values.rename({"value": name}), on="row_id", validate="1:1")
            .drop("row_id")
        )
        work = work.join_asof(
            right.sort("timestamp"),
            on="timestamp",
            by=keys,
            strategy="backward",
            check_sortedness=False,
        )
        # join_asof keeps null publication events: never carry an older finite result over one.
        leaf = Expression("field", value=name)
        # The ordinary executor treats numeric field leaves as Float64. Restore a coarse
        # condition's type after that conversion, retaining null/unknown conditions.
        generated[node] = (
            Expression("EQ", (leaf, Expression("const", value=1.0)))
            if values.schema["value"] == pl.Boolean
            else leaf
        )
        return generated[node]

    expression = transform(compiled.expression)
    expanded_fields = set(compiled.fields) | set(field_legs)
    return execute(
        replace(compiled, expression=expression, fields=tuple(sorted(expanded_fields))),
        work,
        fields | expanded_fields,
        registry,
        panel=panel_mode,
        _field_legs=field_legs,
    )
