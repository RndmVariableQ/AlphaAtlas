"""Portable definitions for 26 explicitly adapted Zookeeper characteristics."""

from __future__ import annotations

import json
from pathlib import Path

from alpha_atlas.contracts import Candidate

VERSION = "zookeeper_rq_monthly_v1"


def definitions() -> tuple[dict, ...]:
    """Return independent JSON-compatible descriptions; no market or test data is read."""
    path = Path(__file__).with_name("factors.jsonl")
    rows = tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    if len(rows) != 26 or len({row["name"] for row in rows}) != 26:
        raise ValueError("expected 26 distinct Zookeeper definitions")
    if any(row["version"] != VERSION for row in rows):
        raise ValueError("Zookeeper definition version mismatch")
    return rows


def candidates() -> tuple[Candidate, ...]:
    """Ordinary DSL field candidates, usable after ZookeeperMarketData prepares the fields."""
    return tuple(
        Candidate(row["expression"], name=row["name"], hypothesis=row["description"])
        for row in definitions()
    )
