"""Named, reviewable experiment settings. Uses existing run/config/data identities."""

from __future__ import annotations

import json
import re
from datetime import date

from alpha_atlas.contracts import AssetProfile, DateRange, Fold
from alpha_atlas.operators import OperatorRegistry


def fold_from_version(version):
    value = version["fold"]
    return Fold(
        value["id"],
        *(
            DateRange(*(date.fromisoformat(value[s][k]) for k in ("start", "end")))
            for s in ("train", "val", "test")
        ),
    )


def load_version(root, name):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("invalid experiment version name")
    value = json.loads((root / "configs/versions" / f"{name}.json").read_text())
    if value["id"] != name or value["schema_version"] != 1:
        raise ValueError("unsupported experiment version")
    if value["profile"]["asset_id"] != value["asset"]:
        raise ValueError("version asset/profile mismatch")
    AssetProfile.from_mapping(value["profile"])
    if value["universe"] not in value["profile"]["universes"]:
        raise ValueError("version universe/profile mismatch")
    if not value["fields"] or len(set(value["fields"])) != len(value["fields"]):
        raise ValueError("version requires distinct feature fields")
    if value["profile"]["price_field"] not in value["fields"]:
        raise ValueError("version must include its target price field")
    for source in value["data"]["sources"].values():
        path = root / source["path"]
        if (
            not path.resolve().is_relative_to((root / "data").resolve())
            or not source["snapshot_id"]
        ):
            raise ValueError("version sources require pinned local data paths")
    if value["data"]["adapter"] == "zookeeper_monthly_dsl_v2":
        from alpha_atlas.factor_libraries.zookeeper.dsl import recipe

        current = recipe()
        if current != value["data_recipe"]:
            raise ValueError("version monthly recipe changed; create a new version")
    registry = OperatorRegistry(versions=value["operator_versions"])
    if initial := value.get("initial_library"):
        from alpha_atlas.expressions import compile_factor
        from alpha_atlas.storage import candidate_from_dict

        if not initial.get("source") or not initial.get("factors"):
            raise ValueError("initial library requires source and fixed definitions")
        identities = set()
        for item in initial["factors"]:
            if set(item) != {"candidate", "factor_id"}:
                raise ValueError("initial library accepts definitions, not preloaded metrics")
            compiled = compile_factor(
                candidate_from_dict(item["candidate"]).expression,
                set(value["fields"]),
                registry,
                max_nodes=value["rules"]["max_expression_nodes"],
                max_depth=value["rules"]["max_expression_depth"],
            )
            if compiled.factor_id != item["factor_id"] or compiled.factor_id in identities:
                raise ValueError("initial library factor identity mismatch or duplicate")
            identities.add(compiled.factor_id)
    fold_from_version(value)
    return value
