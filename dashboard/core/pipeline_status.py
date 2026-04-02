"""core/pipeline_status.py — Stage-status aggregation for the dashboard."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def build_stage_status(pipeline: dict) -> dict:
    """Gather stage-specific status info (DB record count, job state, etc.)."""
    status: dict = {
        "pipeline_id": pipeline["id"],
        "current_stage": pipeline["current_stage"],
    }

    db_path = pipeline.get("output_db_path")

    # DB stats
    if db_path and Path(db_path).exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
            if "llm_is_hydrocarbon" in existing_cols:
                row = conn.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN llm_is_hydrocarbon IS NOT NULL THEN 1 ELSE 0 END) as llm_done "
                    "FROM filtered_records"
                ).fetchone()
                status["llm_done"] = row[1] if row else 0
            else:
                row = conn.execute("SELECT COUNT(*) FROM filtered_records").fetchone()
                status["llm_done"] = 0
            status["record_count"] = row[0] if row else 0
            conn.close()
        except Exception:
            status["record_count"] = 0
            status["llm_done"] = 0
    else:
        status["record_count"] = 0
        status["llm_done"] = 0

    # DB file size
    if db_path and Path(db_path).exists():
        status["db_size_mb"] = round(os.path.getsize(db_path) / (1024 ** 2), 1)
    else:
        status["db_size_mb"] = 0

    # Pipeline state file
    state_path = pipeline.get("state_file_path")
    if state_path and Path(state_path).exists():
        try:
            pipeline_state = json.loads(Path(state_path).read_text())
            all_files = len(pipeline_state.get("all_files", []))
            processed = len(pipeline_state.get("processed_files", []))
            seen = pipeline_state.get("total_records_seen", 0)
            inserted = pipeline_state.get("total_records_inserted", 0)
            status["search_progress"] = {
                "total": all_files,
                "processed": processed,
                "percent": round(100 * processed / all_files, 1) if all_files else 0,
                "seen": seen,
                "inserted": inserted,
                "skip_stats": pipeline_state.get("skip_stats", {}),
                "file_errors": pipeline_state.get("file_errors", 0),
                "record_errors": pipeline_state.get("record_errors", 0),
            }
        except Exception:
            status["search_progress"] = None
    else:
        status["search_progress"] = None

    # Batch state
    batch_path = pipeline.get("batch_state_path")
    if batch_path and Path(batch_path).exists():
        try:
            status["batch_state"] = json.loads(Path(batch_path).read_text())
        except Exception:
            status["batch_state"] = None
    else:
        status["batch_state"] = None

    # Report exists?
    status["has_report"] = bool(
        pipeline.get("report_path") and Path(pipeline["report_path"]).exists()
    )
    status["has_llm_report"] = bool(
        pipeline.get("llm_report_path") and Path(pipeline["llm_report_path"]).exists()
    )

    return status
