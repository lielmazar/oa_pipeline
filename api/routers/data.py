"""Data router — records, schema, stats, query for a pipeline's research DB.

All routes are namespaced under /api/pipeline/{pipeline_id}/
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.config import settings
from api.db.pipelines import get_pipeline
from api.db.research import get_db_stats, get_records, get_record_detail, get_schema, run_query
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["data"])


def _resolve_db(db, pipeline_id: int, user_id: int) -> Path:
    pipeline = get_pipeline(db, pipeline_id, user_id)
    if not pipeline:
        raise HTTPException(404, "pipeline not found")
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        raise HTTPException(404, "pipeline has no database yet")
    db_path = Path(db_path_str)
    if not db_path.exists():
        raise HTTPException(404, "database file not found")
    return db_path


# ── Schema ────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/schema")
def db_schema(pipeline_id: int, user: CurrentUser, db: Db):
    return get_schema(_resolve_db(db, pipeline_id, user["id"]))


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/stats")
def db_stats(pipeline_id: int, user: CurrentUser, db: Db):
    pipeline = get_pipeline(db, pipeline_id, user["id"])
    if not pipeline:
        raise HTTPException(404, "pipeline not found")
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return {"exists": False}
    return get_db_stats(Path(db_path_str))


# ── Records ───────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/records")
def db_records(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    page: int = 1,
    q: str = "",
    sort: str = "publication_year",
    dir: str = "desc",
    page_size: int | None = None,
    search_cols: str = "",
    visible_cols: str = "",
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    per_page = page_size if page_size else settings.db_page_size
    sc = [c.strip() for c in search_cols.split(",") if c.strip()] or None
    vc = [c.strip() for c in visible_cols.split(",") if c.strip()] or None
    result = get_records(db_path, page=max(1, page), per_page=per_page,
                         sort=sort, direction=dir, search=q, search_cols=sc, visible_cols=vc)
    if "error" in result:
        raise HTTPException(500, result["error"])
    return result


@router.get("/{pipeline_id}/records/{record_id}")
def db_record_detail(pipeline_id: int, record_id: str, user: CurrentUser, db: Db):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    record = get_record_detail(db_path, record_id)
    if record is None:
        raise HTTPException(404, "record not found")
    return record


# ── Bulk download (for frontend "load pipeline DB") ───────────────────────────

def _iter_ndjson(db_path: Path):
    """Stream all filtered_records rows as newline-delimited JSON."""
    import json
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM filtered_records")
        cols = [d[0] for d in cur.description]
        while True:
            rows = cur.fetchmany(500)
            if not rows:
                break
            for row in rows:
                yield json.dumps(dict(zip(cols, row)), default=str) + "\n"
    finally:
        conn.close()


@router.get("/{pipeline_id}/records/download")
def download_all_records(pipeline_id: int, user: CurrentUser, db: Db):
    """Stream all records as NDJSON for frontend local loading."""
    db_path = _resolve_db(db, pipeline_id, user["id"])
    return StreamingResponse(
        _iter_ndjson(db_path),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="pipeline_{pipeline_id}_records.ndjson"'},
    )


@router.get("/{pipeline_id}/data-version")
def data_version(pipeline_id: int, user: CurrentUser, db: Db):
    """Return a version token (mtime hash) so the frontend knows when data has changed."""
    pipeline = get_pipeline(db, pipeline_id, user["id"])
    if not pipeline:
        raise HTTPException(404, "pipeline not found")
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return {"version": None, "exists": False}
    db_path = Path(db_path_str)
    if not db_path.exists():
        return {"version": None, "exists": False}
    mtime_ns = str(db_path.stat().st_mtime_ns)
    version = hashlib.sha256(mtime_ns.encode()).hexdigest()[:16]
    return {"version": version, "exists": True, "mtime_ns": int(mtime_ns)}


# ── Ad-hoc query ──────────────────────────────────────────────────────────────

class QueryBody(BaseModel):
    sql: str
    max_rows: int | None = None


@router.post("/{pipeline_id}/query")
def db_query(pipeline_id: int, body: QueryBody, user: CurrentUser, db: Db):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    if not body.sql.strip():
        raise HTTPException(400, "sql is required")
    max_rows = min(int(body.max_rows), 200_000) if body.max_rows else settings.db_query_max_rows
    result = run_query(db_path, body.sql, max_rows=max_rows)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result
