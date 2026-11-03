"""Read-only v1/v2 feature parity on local MarketData; no targets, models or OOS scores."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np

from alpha_atlas.assets.common import atomic_json
from alpha_atlas.assets.zookeeper import (
    BASE_FIELDS,
    CURVE_FIELDS,
    ZookeeperMarketData,
    daily_base,
    daily_curve,
)
from alpha_atlas.factor_libraries.zookeeper.compute import compute_monthly as legacy_monthly
from alpha_atlas.factor_libraries.zookeeper.dsl import compute_monthly, recipe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--through", type=date.fromisoformat, default=date(2023, 12, 31))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    with ZookeeperMarketData(root) as market:
        base = market._daily(market.base, BASE_FIELDS, args.through, daily_base)
        curve = market._daily(market.curve, CURVE_FIELDS, args.through, daily_curve)
        sources = market.provenance()
    old, new = legacy_monthly(base, curve), compute_monthly(base, curve)
    if not new.select("exchange", "product", "month").equals(
        old.select("exchange", "product", "month")
    ):
        raise ValueError("v1/v2 calendar keys differ")
    checks = []
    for row in recipe()["factors"]:
        field = row["prepared_field"]
        a, b = old[field].to_numpy(), new[field].to_numpy()
        finite = np.isfinite(a)
        mask_equal = bool(np.array_equal(finite, np.isfinite(b)))
        checks.append(
            {
                "name": row["name"],
                "field": field,
                "finite_rows": int(finite.sum()),
                "null_mask_equal": mask_equal,
                "within_tolerance": bool(
                    mask_equal and np.allclose(a[finite], b[finite], rtol=1e-8, atol=1e-10)
                ),
                "max_absolute_error": float(np.abs(a[finite] - b[finite]).max())
                if finite.any()
                else None,
            }
        )
    report = {
        "kind": "read_only_feature_parity_no_oos",
        "through": str(args.through),
        "base_daily_rows": base.height,
        "curve_daily_rows": curve.height,
        "monthly_rows": new.height,
        "products": new.select("exchange", "product").unique().height,
        "relative_tolerance": 1e-8,
        "absolute_tolerance": 1e-10,
        "passed": all(row["within_tolerance"] for row in checks),
        "sources": sources,
        "factors": checks,
    }
    output = args.output or root / "artifacts/reference-evaluations/zookeeper_dsl_parity.json"
    atomic_json(output, report)
    print(output)
    if not report["passed"]:
        raise SystemExit("v1/v2 feature parity failed; see report")


if __name__ == "__main__":
    main()
