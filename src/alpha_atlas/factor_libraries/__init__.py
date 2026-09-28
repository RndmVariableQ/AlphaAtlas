"""Optional reference libraries; importing Alpha Atlas does not load their engines."""


def load_library(
    name: str | None = None, *, frequency: str = "5m", bars_per_day: int | None = None
):
    """Load optional DSL definitions, or None for the empty-library experiment condition."""
    if name is None:
        return None
    if name == "futures_cta":
        from alpha_atlas.factor_libraries import futures_cta

        return futures_cta.load(frequency=frequency, bars_per_day=bars_per_day)
    raise ValueError(f"unknown reference library: {name}")
