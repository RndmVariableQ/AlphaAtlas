from pathlib import Path

from alpha_atlas import reporting


def test_fold2_overview_publishes_model_spearman_without_changing_grouped_rows(
    tmp_path, monkeypatch
):
    root = Path(__file__).resolve().parents[1]
    original = (root / "experiment_overview.md").read_text(encoding="utf-8")
    (tmp_path / "experiment_overview.md").write_text(original, encoding="utf-8")
    (tmp_path / "artifacts").mkdir()
    directory = tmp_path / "artifacts" / "example-run"
    directory.mkdir()
    monkeypatch.setattr(
        reporting, "RunStore", lambda _: type("Store", (), {"trials": lambda self: []})()
    )
    fold = {
        "id": "fold2",
        "train": {"start": "2019-01-01", "end": "2021-12-31"},
        "val": {"start": "2022-01-01", "end": "2023-12-31"},
        "test": {"start": "2024-01-01", "end": "2025-12-31"},
    }
    pearson = "time_series_weighted_pearson_ic"
    spearman = "time_series_weighted_spearman_ic"

    def metrics(p, s):
        return [
            {"name": pearson, "split": "test", "value": p},
            {"name": spearman, "split": "test", "value": s},
        ]

    spec = {
        "library_size": 1,
        "profile": {"metric": pearson, "panel": "product"},
        "asset": "futures_kernel_5m_full",
        "fields": list("abcdefgh"),
        "rules": {"clip_quantiles": [0.01, 0.99]},
        "universe": "all",
        "method": "v3_async",
        "method_config": {"chat_model": "gpt-6-luna"},
        "fold": fold,
        "seed": 42,
    }
    oos = {
        "results": [{"expression_id": "factor", "status": "success", "metrics": metrics(0.1, 0.2)}]
    }
    tested = {
        "reports": {
            "test": {
                "linear": {"metrics": metrics(0.3, 0.4)},
                "lightgbm": {"metrics": metrics(0.5, 0.6)},
            }
        }
    }
    similarity = {
        "factor_ids": ["factor"],
        "valid_pairs": 0,
        "pair_count": 0,
        "panel": "product",
        "mean_absolute_spearman": None,
        "median_absolute_spearman": None,
        "p90_absolute_spearman": None,
        "max_absolute_spearman": None,
        "pairs_at_least_0_7": 0,
    }
    reporting.update_experiment_overview(tmp_path, directory, spec, oos, tested, similarity)
    updated = (tmp_path / "experiment_overview.md").read_text(encoding="utf-8")
    row = next(line for line in updated.splitlines() if "example-run/report.md" in line)
    assert row.startswith("| — | [AlphaAtlas V3](")
    assert row.endswith("| 0.300000 | 0.500000 | 0.400000 | 0.600000 |")
    grouped = next(line for line in original.splitlines() if "AlphaAtlas V3（3 组）" in line)
    assert grouped in updated
    reporting.update_experiment_overview(tmp_path, directory, spec, oos, tested, similarity)
    assert (tmp_path / "experiment_overview.md").read_text(encoding="utf-8") == updated


def test_older_overview_table_can_omit_model_spearman_columns():
    text = "### example\n\n| 实验人 | 方法 |\n| --- | --- |\n"
    headers = ["实验人", "方法", "Linear 加权 Spearman", "LightGBM 加权 Spearman"]
    updated = reporting._overview_row(
        text, "example", headers, ["—", "[x](run)", 0.1, 0.2], "(run)", ""
    )
    assert "| — | [x](run) |" in updated
