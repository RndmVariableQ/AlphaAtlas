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


def timeframe_panel(frame: pl.DataFrame, fields: set[str], interval: str) -> pl.DataFrame:
    """Natural-clock intraday ends; a daily bar is confirmed by the next trading day's bar.

    Group all observed rows, including invalid ones: a null/invalid input or a continuity
    change invalidates that field's entire bucket. No synthetic bars or missing-value fills.
    Scheduled breaks need no invented 6/12-row requirement. A missing *whole* native bar
    cannot be distinguished from a break without an exchange schedule (as in MarketData).
    """
    source = frame.sort(GROUP + ["timestamp"])
    main_segment = ["segment_id"]
    legs = {f"p{i}" for i in (1, 2) if any(f.endswith(f"_p{i}") for f in fields)}
    segments = main_segment + [f"segment_id_{leg}" for leg in sorted(legs)]
    if missing := set(segments) - set(source.columns):
        raise ValueError(f"timeframes require continuity metadata: {sorted(missing)}")
    if interval == "1d":
        # The next observed day's first timestamp is known at publication, never before.
        # In contrast, max(timestamp) of a truncated current day is not a close schedule.
        publication = (
            source.group_by(GROUP + ["trading_day"])
            .agg(pl.col("timestamp").min().alias("__first"))
            .sort(GROUP + ["trading_day"])
            .with_columns(pl.col("__first").shift(-1).over(GROUP).alias("__end"))
            .drop("__first")
        )
        source = source.join(publication, on=GROUP + ["trading_day"], validate="m:1")
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
    stable_main = pl.col("segment_id").n_unique() == 1
    if "valid_bar" in source.columns:
        stable_main = stable_main & pl.col("valid_bar").fill_null(False).all()
    for field in sorted(fields):
        base = field.removesuffix("_p1").removesuffix("_p2")
        valid = stable_main & pl.col(field).is_finite().fill_null(False).all()
        for leg in legs:
            if field.endswith(f"_{leg}"):
                valid = valid & (pl.col(f"segment_id_{leg}").n_unique() == 1)
                if f"valid_bar_{leg}" in source.columns:
                    valid = valid & pl.col(f"valid_bar_{leg}").fill_null(False).all()
        aggregates.append(
            pl.when(valid).then(getattr(pl.col(field), AGGREGATIONS[base])()).alias(field)
        )
    return (
        source.group_by(GROUP + ["__end"], maintain_order=True)
        .agg(aggregates)
        .filter(pl.col("__end").is_not_null())
        .rename({"__end": "timestamp"})
        .sort(GROUP + ["timestamp"])
        .with_row_index("row_id")
    )


def execute_timeframes(compiled, frame, fields, registry, scopes):
    from alpha_atlas.expressions import execute

    required = {"bar_interval_minutes", "trading_day", "segment_id", "eligible"}
    if missing := required - set(frame.columns):
        raise ValueError(f"timeframes require native frequency/day/continuity metadata: {missing}")
    if frame.height and frame["bar_interval_minutes"].unique().to_list() != [5]:
        raise ValueError("@15m/@30m/@60m/@1d require native 5m input")
    if frame.select(GROUP + ["timestamp"]).is_duplicated().any():
        raise ValueError("duplicate native observation keys")
    if frame.select(
        pl.any_horizontal(pl.col(*GROUP, "timestamp", "trading_day").is_null()).any()
    ).item():
        raise ValueError("null native observation keys or trading days")
    work = frame.sort(GROUP + ["timestamp"])
    if work.select((pl.col("trading_day").diff().over(GROUP).dt.total_days() < 0).any()).item():
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
            panels[key] = timeframe_panel(frame, names, interval)
        panel = panels[key]
        # Preserve the original registry dependencies, without creating new factor identities.
        native = replace(compiled, expression=strip(node), fields=tuple(sorted(names)))
        values = execute(native, panel, names, registry)
        used_legs = set().union(*(field_legs[f] for f in names))
        keys = GROUP + ["segment_id"] + [f"segment_id_{leg}" for leg in sorted(used_legs)]
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
        _field_legs=field_legs,
    )
