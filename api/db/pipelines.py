"""Pipeline model — CRUD, stage progression, forking."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

STAGES = [
    "keyword_selection",
    "openalex_search",
    "analysis",
    "llm_classification",
    "llm_analysis",
    "summary",
]

STAGE_LABELS = {
    "keyword_selection":  "Keyword Selection",
    "openalex_search":    "OpenAlex Search",
    "analysis":           "Analysis",
    "llm_classification": "LLM Classification",
    "llm_analysis":       "LLM Analysis",
    "summary":            "Summary",
}

# Legacy stage IDs → current equivalents (applied on DB read)
_STAGE_MIGRATION: dict[str, str] = {
    "results_review":        "analysis",
    "report_generation":     "analysis",
    "report":                "analysis",
    "llm_results_review":    "llm_analysis",
    "llm_report_generation": "llm_analysis",
    "llm_report":            "llm_analysis",
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
    if d.get("current_stage") in _STAGE_MIGRATION:
        d["current_stage"] = _STAGE_MIGRATION[d["current_stage"]]
    if current_user_id is not None:
        d["is_owner"] = d["user_id"] == current_user_id
    return d


# ── Create ────────────────────────────────────────────────────────────────────

def create_pipeline(
    db: sqlite3.Connection,
    user_id: int,
    name: str,
    description: str = "",
    config: dict | None = None,
) -> int:
    cfg_json = json.dumps(config or {"fuel_terms": [], "impact_terms": [], "workers": 4, "min_free_gb": 2.0})
    cur = db.execute(
        "INSERT INTO pipelines (user_id, name, description, config) VALUES (?, ?, ?, ?)",
        (user_id, name, description, cfg_json),
    )
    db.commit()
    return cur.lastrowid


# ── Read ──────────────────────────────────────────────────────────────────────

def get_pipeline(db: sqlite3.Connection, pipeline_id: int, current_user_id: int | None = None) -> dict | None:
    row = db.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
    return _row_to_dict(row, current_user_id) if row else None


def list_all_pipelines(db: sqlite3.Connection, current_user_id: int | None = None) -> list[dict]:
    rows = db.execute(
        """SELECT p.*, u.username AS owner_username
           FROM pipelines p JOIN users u ON u.id = p.user_id
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


# ── Update ────────────────────────────────────────────────────────────────────

def update_pipeline(
    db: sqlite3.Connection,
    pipeline_id: int,
    user_id: int,
    name: str | None = None,
    description: str | None = None,
    config: dict | None = None,
) -> bool:
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


def update_pipeline_paths(db: sqlite3.Connection, pipeline_id: int, **kwargs) -> None:
    allowed = {"output_db_path", "state_file_path", "batch_state_path", "report_path", "llm_report_path"}
    sets, vals = [], []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?"); vals.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    db.execute(
        f"UPDATE pipelines SET {', '.join(sets)} WHERE id = ?",
        vals + [pipeline_id],
    )
    db.commit()


# ── Stage navigation ──────────────────────────────────────────────────────────

def advance_pipeline_stage(db: sqlite3.Connection, pipeline_id: int, user_id: int) -> str | None:
    p = get_pipeline(db, pipeline_id, user_id)
    if not p or p["user_id"] != user_id:
        return None
    next_stage = get_next_stage(p["current_stage"])
    if not next_stage:
        return None
    db.execute(
        "UPDATE pipelines SET current_stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (next_stage, pipeline_id),
    )
    db.commit()
    return next_stage


def go_back_pipeline_stage(db: sqlite3.Connection, pipeline_id: int, user_id: int) -> str | None:
    p = get_pipeline(db, pipeline_id, user_id)
    if not p or p["user_id"] != user_id:
        return None
    prev_stage = get_prev_stage(p["current_stage"])
    if not prev_stage:
        return None
    db.execute(
        "UPDATE pipelines SET current_stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (prev_stage, pipeline_id),
    )
    db.commit()
    return prev_stage


def set_pipeline_stage(db: sqlite3.Connection, pipeline_id: int, user_id: int, stage: str) -> str | None:
    if stage not in STAGES:
        return None
    p = get_pipeline(db, pipeline_id, user_id)
    if not p or p["user_id"] != user_id:
        return None
    db.execute(
        "UPDATE pipelines SET current_stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (stage, pipeline_id),
    )
    db.commit()
    return stage


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_pipeline(db: sqlite3.Connection, pipeline_id: int, user_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM pipelines WHERE id = ? AND user_id = ?", (pipeline_id, user_id)
    )
    db.commit()
    return cur.rowcount > 0


# ── Fork ──────────────────────────────────────────────────────────────────────

def fork_pipeline(
    db: sqlite3.Connection,
    source_id: int,
    new_user_id: int,
    new_name: str,
    pipelines_base_dir: Path,
) -> int:
    source = get_pipeline(db, source_id)
    if not source:
        raise ValueError(f"Pipeline {source_id} not found")

    cfg_json = json.dumps(source["config"])
    cur = db.execute(
        """INSERT INTO pipelines (user_id, forked_from_id, name, description, current_stage, config)
           VALUES (?, ?, ?, ?, 'analysis', ?)""",
        (new_user_id, source_id, new_name, f"Forked from: {source['name']}", cfg_json),
    )
    db.commit()
    new_id = cur.lastrowid

    fork_base = pipelines_base_dir / str(new_id)
    fork_base.mkdir(parents=True, exist_ok=True)
    fork_db = fork_base / "output.sqlite"

    if source.get("output_db_path"):
        src_db = Path(source["output_db_path"])
        if src_db.exists():
            shutil.copy2(src_db, fork_db)
        src_keywords = src_db.parent / "keywords.json"
        if src_keywords.exists():
            shutil.copy2(src_keywords, fork_base / "keywords.json")

    if source.get("state_file_path"):
        src_state = Path(source["state_file_path"])
        if src_state.exists():
            shutil.copy2(src_state, fork_base / "pipeline_state.json")

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


# ── Events ───────────────────────────────────────────────────────────────────

def record_event(db: sqlite3.Connection, pipeline_id: int, event: str, detail: str | None = None) -> None:
    db.execute(
        "INSERT INTO pipeline_events (pipeline_id, event, detail) VALUES (?, ?, ?)",
        (pipeline_id, event, detail),
    )
    db.commit()


def list_events(db: sqlite3.Connection, pipeline_id: int, limit: int = 200) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM pipeline_events WHERE pipeline_id = ? ORDER BY created_at DESC LIMIT ?",
            (pipeline_id, limit),
        ).fetchall()
    ]


# ── Filter runs ───────────────────────────────────────────────────────────────

def start_filter_run(db: sqlite3.Connection, pipeline_id: int, pid: int) -> int:
    cur = db.execute(
        "INSERT INTO filter_runs (pipeline_id, pid, status) VALUES (?, ?, 'running')",
        (pipeline_id, pid),
    )
    db.commit()
    return cur.lastrowid


def get_active_run(db: sqlite3.Connection) -> dict | None:
    """Return the single system-wide active filter run, or None."""
    row = db.execute(
        "SELECT * FROM filter_runs WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def finish_run(db: sqlite3.Connection, run_id: int, status: str) -> None:
    db.execute(
        "UPDATE filter_runs SET ended_at = CURRENT_TIMESTAMP, status = ? WHERE id = ?",
        (status, run_id),
    )
    db.commit()


def get_run(db: sqlite3.Connection, run_id: int) -> dict | None:
    row = db.execute("SELECT * FROM filter_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(db: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM filter_runs WHERE pipeline_id = ? ORDER BY started_at DESC",
            (pipeline_id,),
        ).fetchall()
    ]


# ── Subsets ───────────────────────────────────────────────────────────────────

def list_subsets(db: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM pipeline_subsets WHERE pipeline_id = ? ORDER BY created_at ASC",
            (pipeline_id,),
        ).fetchall()
    ]


def create_subset(db: sqlite3.Connection, pipeline_id: int, name: str, sql_query: str) -> dict:
    cur = db.execute(
        "INSERT INTO pipeline_subsets (pipeline_id, name, sql_query) VALUES (?, ?, ?)",
        (pipeline_id, name, sql_query),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM pipeline_subsets WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


def delete_subset(db: sqlite3.Connection, pipeline_id: int, subset_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM pipeline_subsets WHERE id = ? AND pipeline_id = ?",
        (subset_id, pipeline_id),
    )
    db.commit()
    return cur.rowcount > 0


def get_subset(db: sqlite3.Connection, pipeline_id: int, subset_id: int) -> dict | None:
    row = db.execute(
        "SELECT * FROM pipeline_subsets WHERE id = ? AND pipeline_id = ?",
        (subset_id, pipeline_id),
    ).fetchone()
    return dict(row) if row else None


# ── Saved charts (per user, per pipeline) ─────────────────────────────────────

def list_saved_charts(db: sqlite3.Connection, pipeline_id: int, user_id: int) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            """SELECT * FROM pipeline_saved_charts
               WHERE pipeline_id = ? AND user_id = ?
               ORDER BY created_at DESC""",
            (pipeline_id, user_id),
        ).fetchall()
    ]


def create_saved_chart(
    db: sqlite3.Connection, pipeline_id: int, user_id: int, name: str, config_json: str
) -> dict:
    cur = db.execute(
        """INSERT INTO pipeline_saved_charts (pipeline_id, user_id, name, config_json)
           VALUES (?, ?, ?, ?)""",
        (pipeline_id, user_id, name.strip(), config_json),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM pipeline_saved_charts WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


def delete_saved_chart(db: sqlite3.Connection, pipeline_id: int, chart_id: int, user_id: int) -> bool:
    cur = db.execute(
        """DELETE FROM pipeline_saved_charts
           WHERE id = ? AND pipeline_id = ? AND user_id = ?""",
        (chart_id, pipeline_id, user_id),
    )
    db.commit()
    return cur.rowcount > 0


def get_saved_chart(db: sqlite3.Connection, pipeline_id: int, chart_id: int, user_id: int) -> dict | None:
    row = db.execute(
        """SELECT * FROM pipeline_saved_charts
           WHERE id = ? AND pipeline_id = ? AND user_id = ?""",
        (chart_id, pipeline_id, user_id),
    ).fetchone()
    return dict(row) if row else None
