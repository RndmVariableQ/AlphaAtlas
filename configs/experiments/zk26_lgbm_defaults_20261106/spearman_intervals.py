"""Add reranked Spearman intervals to the frozen B26 300-attempt comparisons."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import polars as pl

from alpha_atlas.evaluation import paired_block_bootstrap, paired_prediction_metrics

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = ROOT / "docs/records/zookeeper26_fold2/lgbm_defaults_20261106"
OUTPUT = ROOT / "artifacts/comparisons/zk26_lgbm_defaults_20261106"
GROUPS = (
    "luna_earlier_methods",
    "luna_overview_methods",
    "luna_v3",
    "haiku_ready_methods",
    "haiku_v3",
    "haiku_mcts",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=("original", "val-end"), default="original")
    args = parser.parse_args()
    selected = None
    previous = None
    output = OUTPUT
    if args.cohort == "val-end":
        selected_archive = ROOT / "docs/records/zookeeper26_fold2/val_end_products_20261106"
        selected = json.loads((selected_archive / "cohort.json").read_text())["products"]
        previous = {
            (r["group"], r["method"], r["budget"], r["model"]): r
            for r in (
                json.loads(line)
                for line in (selected_archive / "results.jsonl").read_text().splitlines()
            )
        }
        output = ROOT / "artifacts/comparisons/zk26_val_end_products_20261106"
    results = []
    reference = None
    for group in GROUPS:
        source = ARCHIVE / group / "test.json"
        test = json.loads(source.read_text())
        frame = pl.read_parquet(ROOT / test["predictions"])
        if selected is not None:
            frame = frame.filter(pl.col("product").is_in(selected))
        keys = ["row_id", "trading_day", "product", "target"]
        baseline = frame.select(*keys, "baseline_linear", "baseline_lightgbm")
        if reference is None:
            reference = baseline
        else:
            assert reference.equals(baseline)
        for row in test["results"]:
            if row["budget"] != 300:
                continue
            started = time.monotonic()
            panel = frame.select(
                *keys,
                pl.col(f"baseline_{row['model']}").alias("baseline"),
                pl.col(f"{row['arm']}_{row['model']}").alias("prediction"),
            )
            weights = pl.DataFrame(row["test"]["per_product"]).select("product", "weight")
            if selected is not None:
                weights = weights.filter(pl.col("product").is_in(selected))
            point = paired_prediction_metrics(panel, weights)
            if previous is None:
                expected_metrics = row["test"]["metrics"]
                expected_products = row["test"]["ic_products"]
                expected_rows = 2401248
                expected_interval = row["bootstrap"]["delta_weighted_pearson"]["interval_95"]
            else:
                old = previous[group, row["method"], row["budget"], row["model"]]
                expected_metrics = {"weighted_pearson": old["ic"]}
                expected_products = old["ic_products"]
                expected_rows = 2326761
                expected_interval = old["ic_interval_95"]
            assert point["ic_products"] == expected_products
            assert point["ic_rows"] == expected_rows
            for metric, expected in expected_metrics.items():
                for field in ("baseline", "augmented", "delta"):
                    np.testing.assert_allclose(
                        point["metrics"][metric][field], expected[field], atol=1e-14, rtol=0
                    )
            pearson = paired_block_bootstrap(panel, weights)
            np.testing.assert_allclose(
                pearson["delta_weighted_pearson"]["interval_95"],
                expected_interval,
                atol=1e-12,
                rtol=0,
            )
            spearman = paired_block_bootstrap(panel, weights, correlation="spearman")
            assert spearman["delta_weighted_spearman"]["valid_replicates"] == 2000
            results.append(
                {
                    "group": group,
                    "method": row["method"],
                    "budget": row["budget"],
                    "model": row["model"],
                    "source_test": str(source.relative_to(ROOT)),
                    "predictions": test["predictions"],
                    "ic_products": point["ic_products"],
                    "ic_rows": point["ic_rows"],
                    "weighted_pearson": point["metrics"]["weighted_pearson"],
                    "weighted_spearman": point["metrics"]["weighted_spearman"],
                    "delta_weighted_pearson": pearson["delta_weighted_pearson"],
                    "delta_weighted_spearman": spearman["delta_weighted_spearman"],
                }
            )
            print(
                json.dumps(
                    {
                        "completed": len(results),
                        "group": group,
                        "method": row["method"],
                        "model": row["model"],
                        "seconds": round(time.monotonic() - started, 2),
                        "spearman_delta": point["metrics"]["weighted_spearman"]["delta"],
                        "spearman_interval": spearman["delta_weighted_spearman"]["interval_95"],
                    }
                ),
                flush=True,
            )
    assert len(results) == 20
    report = {
        "cohort": args.cohort,
        "budget": 300,
        "block_days": 20,
        "repeats": 2000,
        "seed": 20261102,
        "resampling": "year-stratified synchronous market trading-day blocks",
        "rank_policy": "rerank within each product and resample, with average ranks for ties",
        "weight_policy": "fixed full-Test sqrt(sum(amount)); normalize over paired valid products",
        "target": "original target",
        "models_refitted": False,
        "pearson_intervals_reproduced": True,
        "results": results,
    }
    path = output / "spearman_intervals.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(path, flush=True)


if __name__ == "__main__":
    main()
