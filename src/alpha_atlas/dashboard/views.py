"""Read-only views of run artifacts for the dashboard; never writes, locks or recomputes factors.

Every value comes from run.json, checkpoint.json and atomic factor trials. The map layout is a
display derivation of Regions and recorded library similarities; map states use the method's
own status function (V2 mechanism_status or V3 region_status), so the dashboard holds no
business facts of its own.
"""

from __future__ import annotations

import json
import math
import os
import signal
import time
from collections import Counter
from pathlib import Path

import numpy as np

from alpha_atlas.checkpoint import Checkpoint, run_lock
from alpha_atlas.methods.v2_sync import (
    V2SyncConfig,
    expression_band,
    mechanism_status,
    scale_status,
)
from alpha_atlas.methods.v3_async import (
    LEGACY_SCHEME,
    REGION_SCHEME,
    SCHEMES,
    V3AsyncConfig,
    region_status,
)
from alpha_atlas.storage import RunStore
from alpha_atlas.terminal import _expression

LINK_SIMILARITY = 0.2  # Regions attract only through a recorded |corr| at least this large.
GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


class RunIndex:
    """Discovers run directories under fixed roots; ids are opaque keys, never paths."""

    def __init__(self, root: Path, extra=()):
        self.root = root
        self.extra = list(extra)
        self._trials: dict[Path, tuple[int, list]] = {}
        self._maps: dict[Path, tuple[tuple, dict]] = {}
        self._listing: dict[Path, tuple[float, dict]] = {}
        self._checkpoints: dict[Path, tuple[int, dict]] = {}

    def directories(self) -> dict[str, Path]:
        found = {}
        # Rescanned on every call so experiments started after the server appear.
        bases = [
            self.root / "artifacts/runs",
            *sorted(self.root.glob("artifacts/comparisons/*/workspace/artifacts/runs")),
            *self.extra,
        ]
        for base in bases:
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

    def checkpoint(self, directory: Path) -> dict:
        """Parsed checkpoint.json, reused until the file changes (long runs reach tens of MB)."""
        path = directory / "checkpoint.json"
        try:
            stamp = path.stat().st_mtime_ns
        except FileNotFoundError:
            return {}
        cached = self._checkpoints.get(directory)
        if cached and cached[0] == stamp:
            return cached[1]
        value = _read(path) or {}
        self._checkpoints[directory] = (stamp, value)
        return value

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
    default = V3AsyncConfig() if spec.get("method") == "v3_async" else V2SyncConfig()
    return (
        config.get("sea_level", default.sea_level),
        config.get("probe_attempts", default.probe_attempts),
    )


def summary(index: RunIndex, key: str) -> dict:
    directory = index.get(key)
    spec = _read(directory / "run.json") or {}
    checkpoint = index.checkpoint(directory)
    state = checkpoint.get("method_state") or {}
    trials = index.trials(directory)
    reasons = Counter(t["feedback"]["reason"] for t in trials)
    curve, admitted, information = [], 0, 0.0
    for trial in trials:
        feedback = trial["feedback"]
        admitted += int(feedback["accepted"])
        # Only admitted factors add residual information to the library; rejected trials
        # carry signed noise around zero.
        if feedback["accepted"]:
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
    state = index.checkpoint(directory).get("method_state") or {}
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
            # Sources copy the cited evidence (hundreds of KB per batch, repeated for every plan
            # the batch started); the page shows only the proposals.
            plan["explorer"] = {
                k: v for k, v in record["explorer_proposals"].items() if k != "sources"
            }
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
        region: {"name": value.get("name"), "hypothesis": value.get("hypothesis")}
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


def _layout_groups(sizes: np.ndarray, affinity: np.ndarray, iterations: int = 600):
    """Deterministic force layout of Region discs (radius sizes).

    Linked groups (affinity >= LINK_SIMILARITY) are pulled to a distance that shrinks with
    affinity; unlinked groups only avoid overlap and are packed by a weak pull to the centre,
    so their distance carries no meaning.
    """
    n = len(sizes)
    if n < 2:
        return np.zeros((n, 2))
    k = np.arange(n)
    spread = np.sqrt(k + 0.5) * 1.6 * float(np.mean(sizes))
    position = np.c_[np.cos(k * GOLDEN_ANGLE), np.sin(k * GOLDEN_ANGLE)] * spread[:, None]
    gap = sizes[:, None] + sizes[None, :]
    linked = affinity >= LINK_SIMILARITY
    np.fill_diagonal(linked, False)
    target = gap * (0.7 + 1.1 * (1 - affinity))
    minimum = np.where(linked, gap * 0.7, gap * 1.7)
    # Averaging the pull over a group's links and capping each move keep the iteration stable
    # when one Region correlates with many others.
    degree = np.maximum(linked.sum(axis=1), 1)[:, None]
    cap = 0.5 * sizes[:, None]
    for step in range(iterations):
        rate = 0.25 * (1 - step / iterations) + 0.02
        delta = position[:, None, :] - position[None, :, :]
        distance = np.linalg.norm(delta, axis=2)
        np.fill_diagonal(distance, np.inf)
        unit = delta / np.maximum(distance, 1e-9)[:, :, None]
        pull = np.where(linked, distance - target, 0.0)
        push = np.where(distance < minimum, minimum - distance, 0.0)
        force = (push[:, :, None] * unit).sum(axis=1)
        force -= (pull[:, :, None] * unit).sum(axis=1) / degree
        force -= 0.04 * position
        move = rate * force
        length = np.maximum(np.linalg.norm(move, axis=1, keepdims=True), 1e-12)
        position = position + move * np.minimum(1.0, cap / length)
    return position


FULL_SPACING_ATTEMPTS = 20  # Beyond this a Region's disc grows slower and its points pack.
STRETCH = 1.3  # Region outline aspect; the layout reserves the longer axis.


def _disc_radius(count: int) -> float:
    """Unit spacing up to FULL_SPACING_ATTEMPTS, then radius grows as count ** (1/4).

    One Region with hundreds of attempts would otherwise cover the map by area alone.
    """
    full = 0.62 * math.sqrt(min(count, FULL_SPACING_ATTEMPTS))
    return full * (max(count, FULL_SPACING_ATTEMPTS) / FULL_SPACING_ATTEMPTS) ** 0.25


def _sunflower(count: int, phase: float) -> np.ndarray:
    """Points of one Region within _disc_radius(count), first at the centre.

    The disc is stretched, given a few lobes and rotated by phase so Regions do not all read as
    circles; area and the centre-first order are kept.
    """
    k = np.arange(count)
    radius = np.sqrt(k + 0.5) * (k > 0)
    if count > 1:
        radius *= _disc_radius(count) / math.sqrt(count - 0.5)
    angle = k * GOLDEN_ANGLE + phase
    radius = radius * (1 + 0.16 * np.sin(3 * angle + 2 * phase) + 0.08 * np.sin(5 * angle - phase))
    x, y = radius * np.cos(angle) * STRETCH, radius * np.sin(angle) / STRETCH
    turn = 1.7 * phase
    return np.c_[x * math.cos(turn) - y * math.sin(turn), x * math.sin(turn) + y * math.cos(turn)]


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
    state = index.checkpoint(directory).get("method_state") or {}
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
    points = [
        {
            "trial": 0,
            "kind": "initial",
            "name": member["name"],
            "residual": None,
            "region": None,
            "plan": None,
            "group": f"initial:{i}",
        }
        for i, member in enumerate(members)
        if member["trial"] == 0
    ]
    for trial in trials:
        feedback = trial["feedback"]
        if feedback.get("library_similarity") is None:
            continue  # Not measured (compile/compute failures): nothing to show.
        number = trial["factor_trial_index"]
        region, plan = regions_by_trial.get(number, (None, None))
        points.append(
            {
                "trial": number,
                "kind": "admitted" if feedback["accepted"] else "candidate",
                "name": feedback["candidate"].get("name"),
                "reason": feedback["reason"],
                "residual": feedback.get("residual_ic"),
                "library_r2": feedback.get("library_r2"),
                "val": _val(feedback),
                "region": region,
                "plan": plan,
                "group": region or "unassigned",
                "similarity": feedback["library_similarity"],
            }
        )
    # Each Region is a disc of its attempts; Regions attract through recorded correlations
    # between their attempts and members of other Regions (or initial members).
    groups = list(dict.fromkeys(p["group"] for p in points))
    slot = {group: i for i, group in enumerate(groups)}
    by_trial = {p["trial"]: p["group"] for p in points if p["trial"]}
    member_group = [
        f"initial:{i}" if member["trial"] == 0 else by_trial.get(member["trial"], "unassigned")
        for i, member in enumerate(members)
    ]
    affinity = np.zeros((len(groups), len(groups)))

    def link(a, b, value):
        if a != b and a in slot and b in slot and value is not None and math.isfinite(value):
            i, j = slot[a], slot[b]
            affinity[i, j] = affinity[j, i] = max(affinity[i, j], abs(value))

    for i, j in zip(*np.nonzero(np.isfinite(matrix)), strict=True):
        link(member_group[i], member_group[j], float(matrix[i, j]))
    for p in points:
        for j, value in enumerate(p.pop("similarity", None) or []):
            if j < len(member_group):
                link(p["group"], member_group[j], value)
    members_of = {group: [] for group in groups}
    for p in points:
        members_of[p.pop("group")].append(p)
    sizes = np.array([_disc_radius(len(members_of[g])) * STRETCH * 1.1 + 0.5 for g in groups])
    centres = _layout_groups(sizes, affinity) if groups else np.zeros((0, 2))
    for g, centre in zip(groups, centres, strict=True):
        # Highest residual information at the core, so a Region reads as a peak or a shelf.
        ordered = sorted(
            members_of[g],
            key=lambda p: (p["kind"] != "admitted", -(p["residual"] if p["residual"] else 0)),
        )
        phase = (sum(map(ord, g)) % 628) / 100
        for p, offset in zip(ordered, _sunflower(len(ordered), phase), strict=True):
            p["x"], p["y"] = float(centre[0] + offset[0]), float(centre[1] + offset[1])
    spacing = 0.0
    if points:
        xs = np.array([p["x"] for p in points])
        ys = np.array([p["y"] for p in points])
        # A floor on the span keeps a handful of early points from filling the whole map.
        span = max(xs.max() - xs.min(), ys.max() - ys.min(), 14.0)
        cx, cy = (xs.max() + xs.min()) / 2, (ys.max() + ys.min()) / 2
        for p in points:
            p["x"] = 0.5 + 0.84 * (p["x"] - cx) / span
            p["y"] = 0.5 + 0.84 * (p["y"] - cy) / span
        spacing = 0.84 / span
    timelines: dict[str, list] = {}
    values: dict[str, list] = {}
    bands: dict[str, list] = {}
    v3 = spec.get("method") == "v3_async"
    # V3 runs without a recorded scheme used the V2 bands and the all-bands closing rule.
    scheme = (spec.get("method_config") or {}).get("scale_bands", LEGACY_SCHEME) if v3 else None
    frequency = (spec.get("profile") or {}).get("frequency")
    by_index = {t["factor_trial_index"]: t for t in trials}
    for record in state.get("factor_trial_records", []):
        region = record.get("region_id")
        trial_row = by_index.get(record["factor_trial_index"])
        if trial_row is None or region is None:
            continue
        feedback = trial_row["feedback"]
        values.setdefault(region, []).append(feedback.get("residual_ic"))
        expression = feedback["candidate"]["expression"]
        if v3:
            scales = SCHEMES[scheme]
            band = expression_band(expression, frequency, scales) if frequency and scales else None
            bands.setdefault(region, []).append(
                (band, feedback.get("residual_ic"), feedback["accepted"])
            )
            step = region_status(bands[region], probe, scheme)
        else:
            band = expression_band(expression, frequency) if frequency else None
            bands.setdefault(region, []).append((band, feedback.get("residual_ic")))
            step = {
                **mechanism_status(values[region], sea_level, probe),
                "by_scale": scale_status(bands[region], sea_level, probe),
            }
        timelines.setdefault(region, []).append({"trial": record["factor_trial_index"], **step})
    proposed = state.get("proposed_regions") or {}
    result = {
        "available": True,
        "sea_level": sea_level,
        "probe_attempts": probe,
        "rule": {LEGACY_SCHEME: "v3_legacy", REGION_SCHEME: "v3_region"}.get(scheme, "v3")
        if v3
        else "v2",
        "points": points,
        "regions": [
            {
                "id": region,
                "name": proposed.get(region, {}).get("name"),
                "hypothesis": proposed.get(region, {}).get("hypothesis"),
                "timeline": timeline,
            }
            for region, timeline in timelines.items()
        ],
        "last_trial": trials[-1]["factor_trial_index"] if trials else 0,
        "unplaced": sum(1 for t in trials if t["feedback"].get("library_similarity") is None),
        "spacing": spacing,
        "note": "每个区域（机制）是一座岛或海台，中心是残差信息最高的尝试；区域间当记录到 |corr| ≥ "
        f"{LINK_SIMILARITY} 时相互靠近，越相关越近；无相关的区域只紧凑排布，距离不表示相关性。",
    }
    index._maps[directory] = (cache_key, result)
    return result


def _writer_pids(directory: Path) -> list[int]:
    """Processes holding the run's writer lock, found from /proc instead of stored PIDs."""
    target = os.path.realpath(directory / ".run.lock")
    holders = set()
    for fds in Path("/proc").glob("[0-9]*/fd"):
        try:
            if any(os.readlink(fd) == target for fd in fds.iterdir()):
                holders.add(int(fds.parent.name))
        except OSError:  # The process exited or belongs to another user.
            continue

    def parent(pid):
        try:
            return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None

    # Child processes inherit the descriptor; only the topmost holder is the search.
    return sorted(pid for pid in holders if parent(pid) not in holders)


def stop_run(index: RunIndex, key: str, *, grace: float = 20.0) -> dict:
    """Stop a live search: SIGINT like Ctrl+C, then SIGTERM if the process is still running.

    A run that exits on SIGINT records "interrupted" itself. After SIGTERM nothing records it,
    so once the writer lock is free the dashboard records "interrupted" under that lock.
    """
    directory = index.get(key)
    if os.name == "nt":
        raise ValueError("stopping a run from the dashboard needs Linux /proc")
    pids = _writer_pids(directory)
    if not pids:
        raise ValueError("no live process holds this run")
    sent = []
    for number in (signal.SIGINT, signal.SIGTERM):
        for pid in _writer_pids(directory):
            try:
                os.kill(pid, number)
            except ProcessLookupError:
                pass
        sent.append(number.name)
        deadline = time.monotonic() + grace
        while _writer_pids(directory) and time.monotonic() < deadline:
            time.sleep(1.0)
        if not _writer_pids(directory):
            break
    else:
        raise ValueError("the run process did not exit after SIGINT and SIGTERM")
    # /proc may lose the exiting process's fd before the kernel releases its lock.
    deadline = time.monotonic() + grace
    while True:
        try:
            with run_lock(directory):
                spec = _read(directory / "run.json") or {}
                if spec.get("status") == "running":
                    Checkpoint(directory, spec).status("interrupted")
            break
        except RuntimeError as exc:
            if str(exc) != "run is already open by another writer":
                raise
            if time.monotonic() >= deadline:
                raise ValueError("the run writer lock did not become available") from exc
            time.sleep(0.05)
    return {
        "id": key,
        "signals": sent,
        "status": (_read(directory / "run.json") or {}).get("status"),
    }
