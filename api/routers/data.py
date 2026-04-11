"""Data router — stats, schema, records, and chart queries for a pipeline's research DB."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from api.business.chart_defs import get_chart_configs
from api.config import settings
from api.db.pipelines import get_pipeline, get_subset
from api.db.research import get_db_stats, get_distinct_values, get_records, get_schema, run_query
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


@router.get("/{pipeline_id}/schema")
def db_schema(pipeline_id: int, user: CurrentUser, db: Db):
    return get_schema(_resolve_db(db, pipeline_id, user["id"]))


@router.get("/{pipeline_id}/stats")
def db_stats(pipeline_id: int, user: CurrentUser, db: Db):
    pipeline = get_pipeline(db, pipeline_id, user["id"])
    if not pipeline:
        raise HTTPException(404, "pipeline not found")
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return {"exists": False}
    return get_db_stats(Path(db_path_str))


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
    subset_id: int | None = None,
    filters: str = "",
):
    import json as _json

    db_path = _resolve_db(db, pipeline_id, user["id"])
    per_page = page_size if page_size else settings.db_page_size

    subset_sql = None
    if subset_id is not None:
        sub = get_subset(db, pipeline_id, subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        subset_sql = sub["sql_query"]

    parsed_filters = None
    if filters and filters.strip():
        try:
            parsed_filters = _json.loads(filters)
            if not isinstance(parsed_filters, dict):
                parsed_filters = None
        except _json.JSONDecodeError:
            raise HTTPException(400, "filters must be a JSON object")

    result = get_records(
        db_path, page=max(1, page), per_page=per_page,
        sort=sort, direction=dir, search=q,
        subset_sql=subset_sql, filters=parsed_filters,
    )
    if "error" in result:
        raise HTTPException(500, result["error"])
    return result


@router.get("/{pipeline_id}/charts")
def list_charts(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    tab: str | None = Query(default=None, description="Tab to get charts for: search or llm"),
):
    pipeline = get_pipeline(db, pipeline_id, user["id"])
    if not pipeline:
        raise HTTPException(404, "pipeline not found")
    return get_chart_configs(tab=tab)


from pydantic import BaseModel


class QueryBody(BaseModel):
    sql: str = ""
    subset_id: int | None = None


@router.post("/{pipeline_id}/query")
def db_query(pipeline_id: int, body: QueryBody, user: CurrentUser, db: Db):
    """Run a preset chart SQL query against the research DB, optionally scoped to a subset."""
    db_path = _resolve_db(db, pipeline_id, user["id"])
    if not body.sql or not body.sql.strip():
        raise HTTPException(400, "sql is required")

    final_sql = body.sql.strip()
    if body.subset_id is not None:
        sub = get_subset(db, pipeline_id, body.subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        inner = sub["sql_query"].strip().rstrip(";")
        final_sql = final_sql.replace("FROM filtered_records", f"FROM ({inner}) AS filtered_records")

    result = run_query(db_path, final_sql, max_rows=settings.db_query_max_rows)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/{pipeline_id}/records/distinct")
def distinct_values(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    column: str = Query(..., description="Column name"),
    subset_id: int | None = None,
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    subset_sql = None
    if subset_id is not None:
        sub = get_subset(db, pipeline_id, subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        subset_sql = sub["sql_query"]
    result = get_distinct_values(db_path, column, subset_sql=subset_sql)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# -- Starred records ---------------------------------------------------------

@router.get("/{pipeline_id}/starred")
def list_starred(pipeline_id: int, user: CurrentUser, db: Db):
    rows = db.execute(
        "SELECT openalex_id FROM starred_records WHERE pipeline_id = ? AND user_id = ?",
        [pipeline_id, user["id"]],
    ).fetchall()
    return {"ids": [r[0] for r in rows]}


class StarBody(BaseModel):
    openalex_id: str


@router.post("/{pipeline_id}/starred")
def add_star(pipeline_id: int, body: StarBody, user: CurrentUser, db: Db):
    db.execute(
        "INSERT OR IGNORE INTO starred_records (pipeline_id, openalex_id, user_id) VALUES (?, ?, ?)",
        [pipeline_id, body.openalex_id, user["id"]],
    )
    db.commit()
    return {"ok": True}


@router.delete("/{pipeline_id}/starred/{openalex_id}")
def remove_star(pipeline_id: int, openalex_id: str, user: CurrentUser, db: Db):
    db.execute(
        "DELETE FROM starred_records WHERE pipeline_id = ? AND openalex_id = ? AND user_id = ?",
        [pipeline_id, openalex_id, user["id"]],
    )
    db.commit()
    return {"ok": True}


@router.get("/{pipeline_id}/starred/export")
def export_starred(pipeline_id: int, user: CurrentUser, db: Db):
    """Return all starred records with their full data from the research DB."""
    db_path = _resolve_db(db, pipeline_id, user["id"])
    star_rows = db.execute(
        "SELECT openalex_id FROM starred_records WHERE pipeline_id = ? AND user_id = ?",
        [pipeline_id, user["id"]],
    ).fetchall()
    ids = [r[0] for r in star_rows]
    if not ids:
        return {"columns": [], "rows": [], "total": 0}
    from api.db.research import run_query as _rq
    placeholders = ",".join(["?"] * len(ids))
    sql = f"SELECT * FROM filtered_records WHERE openalex_id IN ({placeholders})"
    import sqlite3
    conn = sqlite3.connect(str(db_path), uri=False, check_same_thread=False)
    try:
        cur = conn.execute(sql, ids)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"columns": columns, "rows": rows, "total": len(rows)}
