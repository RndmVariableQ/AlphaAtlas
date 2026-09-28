from __future__ import annotations

import re
import tomllib
from pathlib import Path

from alpha_atlas.contracts import AssetProfile, DateRange, Fold


def load_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def load_fold(root: Path, fold_id: str) -> Fold:
    for item in load_toml(root / "configs/folds.toml")["folds"]:
        if item["id"] == fold_id:
            return Fold(fold_id, *(DateRange(*item[s]) for s in ("train", "val", "test")))
    raise ValueError(f"unknown fold: {fold_id}")


def asset_config(root: Path, asset: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", asset):
        raise ValueError(f"invalid asset id: {asset}")
    result = load_toml(root / "configs/assets" / f"{asset}.toml")
    AssetProfile.from_mapping(result)
    return result
