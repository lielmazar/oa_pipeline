"""Pipeline model — CRUD, stage progression, forking."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from models.db import get_meta_db

STAGES = [
    "keyword_selection",
    "openalex_search",
    "results_review",
    "report_generation",
    "llm_classification",
    "llm_results_review",
    "llm_report_generation",
]

STAGE_LABELS = {
    "keyword_selection":    "Keyword Selection",
    "openalex_search":      "OpenAlex Search",
    "results_review":       "Results Review",
    "report_generation":    "Report Generation",
    "llm_classification":   "LLM Classification",
    "llm_results_review":   "LLM Results Review",
    "llm_report_generation": "Final Report",
}


def get_next_stage(current: str) -> str | None:
    try:
        idx = STAGES.index(current)
        return STAGES[idx + 1] if idx + 1 < len(STAGES) else None
    except ValueError:
        return None


def get_prev_stage(current: str) -> str | None:
    try:
        idx = STAGES.index(current)
        return STAGES[idx - 1] if idx > 0 else None
    except ValueError:
        return None


def _row_to_dict(row, current_user_id: int | None = None) -> dict:
    d = dict(row)
    d["config"] = json.loads(d.get("config") or "{}")
    if current_user_id is not None:
        d["is_owner"] = d["user_id"] == current_user_id
    return d


# ── Create ────────────────────────────────────────────────────────────────────

def create_pipeline(
    user_id: int,
    name: str,
    description: str = "",
    config: dict | None = None,
) -> int:
    db = get_meta_db()
    cfg_json = json.dumps(config or {
        "fuel_terms": [],
        "impact_terms": [],
        "workers": 4,
        "min_free_gb": 2.0,
    })
    cur = db.execute(
        """INSERT INTO pipelines (user_id, name, description, config)
           VALUES (?, ?, ?, ?)""",
        (user_id, name, description, cfg_json),
    )
    db.commit()
    return cur.lastrowid


# ── Read ──────────────────────────────────────────────────────────────────────

def get_pipeline(pipeline_id: int, current_user_id: int | None = None) -> dict | None:
    db = get_meta_db()
    row = db.execute(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    ).fetchone()
    if not row:
        return None
    return _row_to_dict(row, current_user_id)


def list_all_pipelines(current_user_id: int | None = None) -> list[dict]:
    """All pipelines visible to all logged-in users."""
    db = get_meta_db()
    rows = db.execute(
        """SELECT p.*, u.username AS owner_username
           FROM pipelines p
           JOIN users u ON u.id = p.user_id
           ORDER BY p.updated_at DESC"""
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["config"] = json.loads(d.get("config") or "{}")
        if current_user_id is not None:
            d["is_owner"] = d["user_id"] == current_user_id
        result.append(d)
    return result


def list_user_pipelines(user_id: int) -> list[dict]:
    db = get_meta_db()
    rows = db.execute(
        "SELECT * FROM pipelines WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    return [_row_to_dict(r, user_id) for r in rows]


# ── Update ────────────────────────────────────────────────────────────────────

def update_pipeline(
    pipeline_id: int,
    user_id: int,
    name: str | None = None,
    description: str | None = None,
    config: dict | None = None,
) -> bool:
    """Update mutable fields. Only the owner may update."""
    db = get_meta_db()
    sets, vals = [], []
    if name is not None:
        sets.append("name = ?"); vals.append(name)
    if description is not None:
        sets.append("description = ?"); vals.append(description)
    if config is not None:
        sets.append("config = ?"); vals.append(json.dumps(config))
    if not sets:
        return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    cur = db.execute(
        f"UPDATE pipelines SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
        vals + [pipeline_id, user_id],
    )
    db.commit()
    return cur.rowcount > 0


def update_pipeline_paths(pipeline_id: int, **kwargs) -> None:
    """Set file paths (output_db_path, state_file_path, etc.)."""
    allowed = {
        "output_db_path", "state_file_path", "batch_state_path",
        "report_path", "llm_report_path",
    }
    sets, vals = [], []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    db = get_meta_db()
    db.execute(
        f"UPDATE pipelines SET {', '.join(sets)} WHERE id = ?",
        vals + [pipeline_id],
    )
    db.commit()


def advance_pipeline_stage(pipeline_id: int, user_id: int) -> str | None:
    """Advance to next stage. Returns new stage name or None if at last stage."""
    p = get_pipeline(pipeline_id, user_id)
    if not p or p["user_id"] != user_id:
        return None
    next_stage = get_next_stage(p["current_stage"])
    if not next_stage:
        return None
    db = get_meta_db()
    db.execute(
        "UPDATE pipelines SET current_stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (next_stage, pipeline_id),
    )
    db.commit()
    return next_stage


def go_back_pipeline_stage(pipeline_id: int, user_id: int) -> str | None:
    """Go back to previous stage. Returns new stage name or None if at first stage."""
    p = get_pipeline(pipeline_id, user_id)
    if not p or p["user_id"] != user_id:
        return None
    prev_stage = get_prev_stage(p["current_stage"])
    if not prev_stage:
        return None
    db = get_meta_db()
    db.execute(
        "UPDATE pipelines SET current_stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (prev_stage, pipeline_id),
    )
    db.commit()
    return prev_stage


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_pipeline(pipeline_id: int, user_id: int) -> bool:
    """Delete pipeline (owner only). Returns True if deleted."""
    db = get_meta_db()
    cur = db.execute(
        "DELETE FROM pipelines WHERE id = ? AND user_id = ?",
        (pipeline_id, user_id),
    )
    db.commit()
    return cur.rowcount > 0


# ── Fork ──────────────────────────────────────────────────────────────────────

def fork_pipeline(
    source_id: int,
    new_user_id: int,
    new_name: str,
    pipelines_base_dir: Path,
) -> int:
    """
    Fork a pipeline: copy config + duplicate the output SQLite.
    The fork starts at 'results_review' since the DB is already populated.

    Returns the new pipeline id.
    """
    source = get_pipeline(source_id)
    if not source:
        raise ValueError(f"Pipeline {source_id} not found")

    db = get_meta_db()
    cfg_json = json.dumps(source["config"])
    cur = db.execute(
        """INSERT INTO pipelines
               (user_id, forked_from_id, name, description, current_stage, config)
           VALUES (?, ?, ?, ?, 'results_review', ?)""",
        (new_user_id, source_id, new_name, f"Forked from: {source['name']}", cfg_json),
    )
    db.commit()
    new_id = cur.lastrowid

    # Derive paths for the fork
    fork_base = pipelines_base_dir / str(new_id)
    fork_base.mkdir(parents=True, exist_ok=True)
    fork_db = fork_base / "output.sqlite"

    # Copy source DB if it exists
    if source.get("output_db_path"):
        src_db = Path(source["output_db_path"])
        if src_db.exists():
            shutil.copy2(src_db, fork_db)

    # Copy keywords.json so the forked pipeline can run search
    if source.get("output_db_path"):
        src_keywords = Path(source["output_db_path"]).parent / "keywords.json"
        if src_keywords.exists():
            shutil.copy2(src_keywords, fork_base / "keywords.json")

    # Copy pipeline state so the fork can resume from where the source left off
    if source.get("state_file_path"):
        src_state = Path(source["state_file_path"])
        if src_state.exists():
            shutil.copy2(src_state, fork_base / "pipeline_state.json")

    # Record paths
    db.execute(
        """UPDATE pipelines
           SET output_db_path = ?, state_file_path = ?, batch_state_path = ?,
               report_path = ?, llm_report_path = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            str(fork_db),
            str(fork_base / "pipeline_state.json"),
            str(fork_base / "batch_runs" / "state.json"),
            str(fork_base / "report.html"),
            str(fork_base / "llm_report.html"),
            new_id,
        ),
    )
    db.commit()
    return new_id


# ── Job association ───────────────────────────────────────────────────────────

def record_pipeline_job(pipeline_id: int, job_id: str, stage: str) -> None:
    db = get_meta_db()
    db.execute(
        "INSERT INTO pipeline_jobs (pipeline_id, job_id, stage) VALUES (?, ?, ?)",
        (pipeline_id, job_id, stage),
    )
    db.commit()


def get_pipeline_jobs(pipeline_id: int) -> list[dict]:
    db = get_meta_db()
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM pipeline_jobs WHERE pipeline_id = ? ORDER BY created_at DESC",
            (pipeline_id,),
        ).fetchall()
    ]
