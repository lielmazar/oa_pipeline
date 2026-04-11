"""Data router — stats, schema, records, and chart queries for a pipeline's research DB."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from api.business.chart_defs import get_chart_configs
from api.config import settings
from api.db.pipelines import get_pipeline, get_subset
from api.db.research import get_db_stats, get_records, get_schema, run_query
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
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    per_page = page_size if page_size else settings.db_page_size
    result = get_records(db_path, page=max(1, page), per_page=per_page,
                         sort=sort, direction=dir, search=q)
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
