"""Fixed pipeline subsets — created automatically; users cannot add or remove them."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from api.db.research import _connect_ro

PRESET_DEFS: list[tuple[str, str, str]] = [
    (
        "all",
        "All filtered records",
        "SELECT * FROM filtered_records",
    ),
    (
        "oa",
        "Open access",
        "SELECT * FROM filtered_records WHERE is_oa = 1",
    ),
    (
        "oa_llm",
        "Open access · LLM classified",
        "SELECT * FROM filtered_records WHERE is_oa = 1 AND llm_is_hydrocarbon IS NOT NULL",
    ),
    (
        "oa_llm_hc",
        "Open access · LLM · hydrocarbon",
        "SELECT * FROM filtered_records WHERE is_oa = 1 AND llm_is_hydrocarbon = 1",
    ),
    (
        "oa_llm_hc_ml",
        "Open access · LLM · hydrocarbon · ML",
        "SELECT * FROM filtered_records WHERE is_oa = 1 AND llm_is_hydrocarbon = 1 "
        "AND EXISTS (SELECT 1 FROM json_each(llm_ml_role) j WHERE j.value IS NOT NULL "
        "AND TRIM(CAST(j.value AS TEXT)) != '' AND LOWER(CAST(j.value AS TEXT)) NOT IN ('none'))",
    ),
]


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
    for key, name, sql in PRESET_DEFS:
        if key != "all" and key != "oa":
            if "llm_is_hydrocarbon" not in cols:
                continue
        if key == "oa_llm_hc_ml" and "llm_ml_role" not in cols:
            continue
        existing = db.execute(
            "SELECT id FROM pipeline_subsets WHERE pipeline_id = ? AND preset_key = ?",
            (pipeline_id, key),
        ).fetchone()
        if existing:
            continue
        db.execute(
            "INSERT INTO pipeline_subsets (pipeline_id, name, sql_query, preset_key) VALUES (?, ?, ?, ?)",
            (pipeline_id, name, sql, key),
        )
    db.commit()
