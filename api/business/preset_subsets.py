"""Preset pipeline subsets — reads definitions from research_schema.json."""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path

from api.db.research import _connect_ro

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / ".." / "pipeline" / "research_schema.json"


@lru_cache(maxsize=1)
def _load_subsets() -> list[dict]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))["subsets"]


def _research_columns(research_db_path: Path | None) -> set[str]:
    if not research_db_path or not research_db_path.exists():
        return set()
    try:
        with _connect_ro(research_db_path) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
    except Exception:
        return set()


def ensure_preset_subsets(
    db: sqlite3.Connection,
    pipeline_id: int,
    research_db_path: Path | None,
) -> None:
    """Insert missing preset subset rows for this pipeline (idempotent)."""
    cols = _research_columns(research_db_path)
    for defn in _load_subsets():
        key = defn["key"]
        required = defn.get("requires_columns", [])
        if required and not all(c in cols for c in required):
            continue
        existing = db.execute(
            "SELECT id FROM pipeline_subsets WHERE pipeline_id = ? AND preset_key = ?",
            (pipeline_id, key),
        ).fetchone()
        if existing:
            continue
        db.execute(
            "INSERT INTO pipeline_subsets (pipeline_id, name, sql_query, preset_key) VALUES (?, ?, ?, ?)",
            (pipeline_id, defn["name"], defn["sql"], key),
        )
    db.commit()
