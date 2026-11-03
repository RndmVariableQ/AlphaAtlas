"""Read-only views of run artifacts for the dashboard; never writes, locks or recomputes factors.

Every value comes from run.json, checkpoint.json and atomic factor trials. The map layout is a
display derivation of recorded library similarities; map states use the method's own
mechanism_status, so the dashboard holds no business facts of its own.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np

from alpha_atlas.methods.v2_sync import V2SyncConfig, mechanism_status
from alpha_atlas.storage import RunStore
from alpha_atlas.terminal import _expression

OFFSHORE_SIMILARITY = 0.2  # Below this |corr| to every member there is no measured position.


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


class RunIndex:
    """Discovers run directories under fixed roots; ids are opaque keys, never paths."""

    def __init__(self, root: Path, extra=()):
        self.root = root
        self.patterns = [
            root / "artifacts/runs",
            *sorted(root.glob("artifacts/comparisons/*/workspace/artifacts/runs")),
            *extra,
        ]
        self._trials: dict[Path, tuple[int, list]] = {}
        self._maps: dict[Path, tuple[tuple, dict]] = {}
        self._listing: dict[Path, tuple[float, dict]] = {}

    def directories(self) -> dict[str, Path]:
        found = {}
        for base in self.patterns:
            if not base.is_dir():
                continue
            for directory in base.iterdir():
                if (directory / "run.json").is_file():
                    key = directory.name
                    if key in found:
                        key = f"{directory.parent.parent.parent.name}/{directory.name}"
                    found[key] = directory
        return found

    def get(self, key: str) -> Path:
        directory = self.directories().get(key)
        if directory is None:
            raise KeyError(key)
        return directory

    def trials(self, directory: Path) -> list[dict]:
        store = RunStore(directory)
        folder = store.factor_trials_directory
        count = len(list(folder.glob("*.json"))) if folder.is_dir() else 0
        cached = self._trials.get(directory)
        if cached and cached[0] == count:
            return cached[1]
        rows = store.trials() if count else []
        self._trials[directory] = (count, rows)
        return rows


def _activity(directory: Path) -> float | None:
    paths = [directory / "checkpoint.json", directory / "run.json"]
    folder = directory / "factor_trials"
    if folder.is_dir():
        latest = max(folder.glob("*.json"), default=None, key=lambda p: p.name)
        if latest:
            paths.append(latest)
    times = [p.stat().st_mtime for p in paths if p.exists()]
    return max(times) if times else None


def _val(feedback):
    report = feedback.get("report") or {}
    return next((m["value"] for m in report.get("metrics", []) if m.get("split") == "val"), None)


def run_list(index: RunIndex) -> list[dict]:
    rows = []
    for key, directory in index.directories().items():
        folder = directory / "factor_trials"
        completed = len(list(folder.glob("*.json"))) if folder.is_dir() else 0
        stamp = (directory / "run.json").stat().st_mtime
        cached = index._listing.get(directory)
        if cached and cached[0] == stamp:
            spec = cached[1]
        else:
            full = _read(directory / "run.json") or {}
            spec = {
                "method": full.get("method"),
                "asset": full.get("asset"),
                "fold": (full.get("fold") or {}).get("id"),
                "model": (full.get("method_config") or {}).get("chat_model"),
                "version": (full.get("experiment_version") or {}).get("id"),
                "status": full.get("status"),
                "attempts": full.get("attempts"),
                "library_size": full.get("library_size"),
                "residual": bool((full.get("rules") or {}).get("library_residual")),
            }
            index._listing[directory] = (stamp, spec)
        rows.append(
            {"id": key, **spec, "completed": completed, "last_activity": _activity(directory)}
        )
    return sorted(rows, key=lambda r: -(r["last_activity"] or 0))


def _thresholds(spec):
    config = spec.get("method_config") or {}
    default = V2SyncConfig()
    return (
        config.get("sea_level", default.sea_level),
        config.get("probe_attempts", default.probe_attempts),
    )


def summary(index: RunIndex, key: str) -> dict:
    directory = index.get(key)
    spec = _read(directory / "run.json") or {}
    checkpoint = _read(directory / "checkpoint.json") or {}
    state = checkpoint.get("method_state") or {}
    trials = index.trials(directory)
    reasons = Counter(t["feedback"]["reason"] for t in trials)
    curve, admitted, information = [], 0, 0.0
    for trial in trials:
        feedback = trial["feedback"]
        admitted += int(feedback["accepted"])
        information += feedback.get("residual_ic") or 0.0
        curve.append(
            {
                "trial": trial["factor_trial_index"],
                "accepted": feedback["accepted"],
                "reason": feedback["reason"],
                "val": _val(feedback),
                "residual": feedback.get("residual_ic"),
                "admitted": admitted,
                "information": information,
                "seconds": trial.get("cumulative_seconds"),
            }
        )
    pending = state.get("research_plan_state") or next(
        (g.get("research_plan_state") for g in (state.get("groups") or {}).values()), None
    )
    current = None
    if pending:
        exploration = pending.get("exploration") or {}
        current = {
            "research_plan_index": pending.get("research_plan_index"),
            "mode": pending.get("mode"),
            "temperature": pending.get("temperature"),
            "region_id": (pending.get("plan") or {}).get("region_id")
            or exploration.get("region_id"),
            "direction": exploration.get("direction")
            or (pending.get("plan") or {}).get("direction"),
            "candidates": len(pending.get("outcomes", [])),
        }
    initial = len((spec.get("initial_library") or {}).get("members", []))
    return {
        "id": key,
        "run_id": spec.get("run_id"),
        "method": spec.get("method"),
        "implementation": spec.get("implementation"),
        "status": spec.get("status"),
        "asset": spec.get("asset"),
        "fold": (spec.get("fold") or {}).get("id"),
        "version": (spec.get("experiment_version") or {}).get("id"),
        "model": (spec.get("method_config") or {}).get("chat_model"),
        "method_config": spec.get("method_config"),
        "attempts": spec.get("attempts"),
        "completed": len(trials),
        "initial_members": initial,
        "admitted": admitted,
        "library_size": initial + admitted,
        "stop_reason": spec.get("search_stop_reason"),
        "total_seconds": spec.get("total_seconds") or checkpoint.get("elapsed_seconds"),
        "last_activity": _activity(directory),
        "now": time.time(),
        "usage": state.get("usage"),
        "reasons": dict(reasons),
        "curve": curve,
        "current": current,
        "active_region": state.get("active_region_id"),
        "thresholds": dict(zip(("sea_level", "probe_attempts"), _thresholds(spec), strict=True)),
        "residual": bool((spec.get("rules") or {}).get("library_residual")),
    }


def _trial_row(trial):
    feedback = trial["feedback"]
    candidate = feedback["candidate"]
    report = feedback.get("report") or {}
    expression = report.get("canonical_expression") or candidate.get("expression")
    return {
        "trial": trial["factor_trial_index"],
        "name": candidate.get("name"),
        "expression": _expression(expression) if expression else None,
        "accepted": feedback["accepted"],
        "reason": feedback["reason"],
        "status": report.get("status"),
        "val": _val(feedback),
        "residual": feedback.get("residual_ic"),
        "library_r2": feedback.get("library_r2"),
        "max_abs_corr": feedback.get("max_abs_corr"),
        "coverage": report.get("coverage"),
    }


def plans(index: RunIndex, key: str) -> dict:
    directory = index.get(key)
    state = (_read(directory / "checkpoint.json") or {}).get("method_state") or {}
    trials = {t["factor_trial_index"]: t for t in index.trials(directory)}
    grouped: dict[int, dict] = {}
    for record in state.get("factor_trial_records", []):
        index_ = record["research_plan_index"]
        plan = grouped.setdefault(
            index_,
            {
                "research_plan_index": index_,
                "region_id": record.get("region_id"),
                "direction": record.get("direction"),
                "plan": record.get("plan"),
                "evidence_refs": record.get("evidence_refs", []),
                "explorer": None,
                "annealing": None,
                "trials": [],
            },
        )
        if record.get("explorer_proposals"):
            plan["explorer"] = record["explorer_proposals"]
        if record.get("annealing_result"):
            plan["annealing"] = record["annealing_result"]
        trial = trials.get(record["factor_trial_index"])
        if trial:
            plan["trials"].append(_trial_row(trial))
    blocked = [
        {
            "research_plan_index": item.get("research_plan_index"),
            "region_id": (item.get("plan") or {}).get("region_id"),
            "direction": (item.get("plan") or {}).get("direction"),
            "blocked": (item.get("plan") or {}).get("blocked"),
        }
        for item in state.get("blocked_plans", [])
    ]
    regions = {
        region: value.get("hypothesis")
        for region, value in (state.get("proposed_regions") or {}).items()
    }
    return {"plans": [grouped[k] for k in sorted(grouped)], "blocked": blocked, "regions": regions}


def trial(index: RunIndex, key: str, number: int) -> dict:
    directory = index.get(key)
    row = next((t for t in index.trials(directory) if t["factor_trial_index"] == number), None)
    if row is None:
        raise KeyError(number)
    detail = _trial_row(row)
    detail["feedback"] = {
        k: v for k, v in row["feedback"].items() if k not in {"library_similarity"}
    }
    detail["generation_seconds"] = row.get("generation_seconds")
    detail["elapsed_seconds"] = row.get("elapsed_seconds")
    return detail


def _classical_mds(distance: np.ndarray) -> np.ndarray:
    n = distance.shape[0]
    if n == 1:
        return np.zeros((1, 2))
    if n == 2:
        return np.array([[-distance[0, 1] / 2, 0.0], [distance[0, 1] / 2, 0.0]])
    center = np.eye(n) - np.ones((n, n)) / n
    gram = -0.5 * center @ (distance**2) @ center
    values, vectors = np.linalg.eigh(gram)
    order = np.argsort(values)[::-1][:2]
    coordinates = vectors[:, order] * np.sqrt(np.clip(values[order], 0, None))
    # Fix sign so repeated layouts of the same members do not mirror.
    for axis in range(2):
        if coordinates[np.argmax(np.abs(coordinates[:, axis])), axis] < 0:
            coordinates[:, axis] *= -1
    return coordinates


def _place(landmarks: np.ndarray, similarity: list) -> tuple[np.ndarray, float] | None:
    """Single-point stress minimisation against landmark distances sqrt(2(1-|c|))."""
    pairs = [(i, abs(c)) for i, c in enumerate(similarity) if c is not None and i < len(landmarks)]
    if not pairs:
        return None
    strongest = max(c for _, c in pairs)
    if strongest < OFFSHORE_SIMILARITY:
        return None
    indices = np.array([i for i, _ in pairs])
    weights = np.array([c for _, c in pairs]) ** 4
    target = np.sqrt(np.clip(2 * (1 - np.array([c for _, c in pairs])), 0, None))
    points = landmarks[indices]
    position = (weights[:, None] * points).sum(axis=0) / weights.sum()
    for _ in range(40):
        delta = position - points
        norms = np.maximum(np.linalg.norm(delta, axis=1), 1e-9)
        position = (weights[:, None] * (points + target[:, None] * delta / norms[:, None])).sum(
            axis=0
        ) / weights.sum()
    return position, strongest


def _members(spec, trials):
    initial = (spec.get("initial_library") or {}).get("members", [])
    members = [
        {"factor_id": m["report"]["expression_id"], "trial": 0, "name": m["candidate"].get("name")}
        for m in initial
    ]
    correlations = (spec.get("initial_library") or {}).get("correlations")
    size = len(members)
    matrix = np.full((size, size), np.nan)
    if correlations:
        for i, row in enumerate(correlations):
            for j, value in enumerate(row):
                if value is not None:
                    matrix[i, j] = value
    for trial in trials:
        feedback = trial["feedback"]
        if not feedback["accepted"] or feedback.get("library_similarity") is None:
            continue
        similarity = feedback["library_similarity"]
        grown = np.full((size + 1, size + 1), np.nan)
        grown[:size, :size] = matrix
        for j, value in enumerate(similarity[:size]):
            if value is not None:
                grown[size, j] = grown[j, size] = value
        grown[size, size] = 1.0
        matrix, size = grown, size + 1
        members.append(
            {
                "factor_id": (feedback.get("report") or {}).get("expression_id"),
                "trial": trial["factor_trial_index"],
                "name": feedback["candidate"].get("name"),
            }
        )
    return members, matrix


def map_view(index: RunIndex, key: str) -> dict:
    directory = index.get(key)
    spec = _read(directory / "run.json") or {}
    state = (_read(directory / "checkpoint.json") or {}).get("method_state") or {}
    trials = index.trials(directory)
    cache_key = (len(trials), len(state.get("factor_trial_records", [])))
    cached = index._maps.get(directory)
    if cached and cached[0] == cache_key:
        return cached[1]
    if not (spec.get("rules") or {}).get("library_residual"):
        return {"available": False, "reason": "该运行未开启 library_residual，没有记录库相似度。"}
    sea_level, probe = _thresholds(spec)
    regions_by_trial = {
        r["factor_trial_index"]: (r.get("region_id"), r.get("research_plan_index"))
        for r in state.get("factor_trial_records", [])
    }
    members, matrix = _members(spec, trials)
    # Landmarks without a recorded pair are treated as unrelated (maximum distance).
    similarity = np.nan_to_num(np.abs(matrix), nan=0.0)
    np.fill_diagonal(similarity, 1.0)
    landmarks = (
        _classical_mds(np.sqrt(np.clip(2 * (1 - similarity), 0, None)))
        if members
        else np.zeros((0, 2))
    )
    member_index = {m["trial"]: i for i, m in enumerate(members) if m["trial"]}
    points = []
    for i, member in enumerate(members):
        if member["trial"] == 0:
            points.append(
                {
                    "trial": 0,
                    "kind": "initial",
                    "name": member["name"],
                    "x": float(landmarks[i, 0]),
                    "y": float(landmarks[i, 1]),
                    "residual": None,
                    "region": None,
                    "plan": None,
                }
            )
    offshore = []
    for trial in trials:
        feedback = trial["feedback"]
        number = trial["factor_trial_index"]
        region, plan = regions_by_trial.get(number, (None, None))
        row = {
            "trial": number,
            "kind": "admitted" if feedback["accepted"] else "candidate",
            "name": feedback["candidate"].get("name"),
            "reason": feedback["reason"],
            "residual": feedback.get("residual_ic"),
            "library_r2": feedback.get("library_r2"),
            "val": _val(feedback),
            "region": region,
            "plan": plan,
        }
        if number in member_index:
            position = landmarks[member_index[number]]
            row.update(x=float(position[0]), y=float(position[1]), offshore=False)
            points.append(row)
            continue
        recorded = feedback.get("library_similarity")
        if recorded is None:
            continue  # Not measured (compile/compute failures): no position exists.
        placed = _place(landmarks, recorded)
        if placed is None:
            offshore.append(row)
            continue
        row.update(x=float(placed[0][0]), y=float(placed[0][1]), offshore=False)
        points.append(row)
    # Offshore: no measured similarity, so group by mechanism on a ring; distance is not data.
    radius = max([math.hypot(p["x"], p["y"]) for p in points] + [0.9])
    region_order = sorted({str(r["region"]) for r in offshore})
    for row in offshore:
        group = region_order.index(str(row["region"]))
        angle = 2 * math.pi * (group + 0.5) / max(1, len(region_order))
        spread = ((row["trial"] * 2654435761) % 1000) / 1000 - 0.5
        ring = radius * (1.35 + 0.12 * (((row["trial"] * 40503) % 997) / 997))
        row.update(
            x=ring * math.cos(angle + 0.35 * spread / max(1, len(region_order)) * 3),
            y=ring * math.sin(angle + 0.35 * spread / max(1, len(region_order)) * 3),
            offshore=True,
        )
        points.append(row)
    if points:
        xs = np.array([p["x"] for p in points])
        ys = np.array([p["y"] for p in points])
        # A floor on the span keeps a handful of early points from stretching across the map;
        # sqrt(2) is the distance between unrelated factors.
        span = max(xs.max() - xs.min(), ys.max() - ys.min(), 2.5 * math.sqrt(2))
        cx, cy = (xs.max() + xs.min()) / 2, (ys.max() + ys.min()) / 2
        for p in points:
            p["x"] = 0.5 + 0.84 * (p["x"] - cx) / span
            p["y"] = 0.5 + 0.84 * (p["y"] - cy) / span
    timelines: dict[str, list] = {}
    values: dict[str, list] = {}
    by_index = {t["factor_trial_index"]: t for t in trials}
    for record in state.get("factor_trial_records", []):
        region = record.get("region_id")
        trial_row = by_index.get(record["factor_trial_index"])
        if trial_row is None or region is None:
            continue
        values.setdefault(region, []).append(trial_row["feedback"].get("residual_ic"))
        status = mechanism_status(values[region], sea_level, probe)
        timelines.setdefault(region, []).append({"trial": record["factor_trial_index"], **status})
    hypotheses = {r: v.get("hypothesis") for r, v in (state.get("proposed_regions") or {}).items()}
    result = {
        "available": True,
        "sea_level": sea_level,
        "probe_attempts": probe,
        "points": points,
        "regions": [
            {"id": region, "hypothesis": hypotheses.get(region), "timeline": timeline}
            for region, timeline in timelines.items()
        ],
        "last_trial": trials[-1]["factor_trial_index"] if trials else 0,
        "unplaced": sum(1 for t in trials if t["feedback"].get("library_similarity") is None),
        "note": "地标为库成员（经典 MDS），候选按与地标的相关性定位；外海点与全库相关性低于 "
        f"{OFFSHORE_SIMILARITY}，按机制分组排在外围，距离不代表相关性。",
    }
    index._maps[directory] = (cache_key, result)
    return result
