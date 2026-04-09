"""Data router — records, schema, stats, query for a pipeline's research DB.

All routes are namespaced under /api/pipeline/{pipeline_id}/
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.business.chart_defs import get_chart_definition
from api.business.dynamic_chart import build_dynamic_sql
from api.business.subset_sql import apply_subset_to_chart_sql, compose_subset_query
from api.config import settings
from api.db.pipelines import get_pipeline, get_subset
from api.business.orthogonality import (
    chi_square_cramers_residuals,
    contingency_from_labeled_counts,
    interpret_association,
)
from api.db.research import (
    explain_records_view_sql,
    fetch_pairwise_contingency,
    get_column_distinct_values,
    get_db_stats,
    get_record_detail,
    get_record_detail_in_subset,
    get_records,
    get_records_view,
    get_schema,
    run_query,
)
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


@router.get("/{pipeline_id}/record-detail")
def db_record_detail_safe(
    pipeline_id: int,
    record_id: str,
    user: CurrentUser,
    db: Db,
    subset_id: int | None = None,
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    if subset_id is not None:
        sub = get_subset(db, pipeline_id, subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        record = get_record_detail_in_subset(db_path, sub["sql_query"], record_id)
    else:
        record = get_record_detail(db_path, record_id)
    if record is None:
        raise HTTPException(404, "record not found")
    return record

@router.get("/{pipeline_id}/records/{record_id}")
def db_record_detail(
    pipeline_id: int,
    record_id: str,
    user: CurrentUser,
    db: Db,
    subset_id: int | None = None,
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    if subset_id is not None:
        sub = get_subset(db, pipeline_id, subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        record = get_record_detail_in_subset(db_path, sub["sql_query"], record_id)
    else:
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


# ── Subset-scoped record view (Research DB Viewer) ────────────────────────────


class FilterRule(BaseModel):
    column: str
    op: str = "eq"
    value: str | int | float | None = None


class SortRule(BaseModel):
    column: str
    dir: str = "desc"


class RecordsViewBody(BaseModel):
    subset_id: int
    filters: list[FilterRule] = Field(default_factory=list)
    sort: list[SortRule] = Field(default_factory=list)
    page: int = 1
    page_size: int = 50
    visible_cols: list[str] | None = None
    custom_sql: str | None = None


class RecordsExplainBody(BaseModel):
    subset_id: int
    filters: list[FilterRule] = Field(default_factory=list)
    sort: list[SortRule] = Field(default_factory=list)
    visible_cols: list[str] | None = None
    custom_sql: str | None = None


@router.post("/{pipeline_id}/records/view")
def db_records_view(pipeline_id: int, body: RecordsViewBody, user: CurrentUser, db: Db):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    sub = get_subset(db, pipeline_id, body.subset_id)
    if not sub:
        raise HTTPException(404, "subset not found")
    result = get_records_view(
        db_path,
        sub["sql_query"],
        filters=[f.model_dump() for f in body.filters],
        sort_rules=[s.model_dump() for s in body.sort],
        page=body.page,
        per_page=body.page_size,
        visible_cols=body.visible_cols,
        custom_sql=body.custom_sql,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/{pipeline_id}/records/explain-sql")
def db_records_explain_sql(pipeline_id: int, body: RecordsExplainBody, user: CurrentUser, db: Db):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    sub = get_subset(db, pipeline_id, body.subset_id)
    if not sub:
        raise HTTPException(404, "subset not found")
    result = explain_records_view_sql(
        db_path,
        sub["sql_query"],
        filters=[f.model_dump() for f in body.filters],
        sort_rules=[s.model_dump() for s in body.sort],
        visible_cols=body.visible_cols,
        custom_sql=body.custom_sql,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/{pipeline_id}/column-values/{column}")
def column_distinct_values(
    pipeline_id: int, column: str, user: CurrentUser, db: Db,
    subset_id: int | None = None,
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    subset_sql = "SELECT * FROM filtered_records"
    if subset_id is not None:
        sub = get_subset(db, pipeline_id, subset_id)
        if sub:
            subset_sql = sub["sql_query"]
    result = get_column_distinct_values(db_path, subset_sql, column)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class OrthogonalityBody(BaseModel):
    column_x: str
    column_y: str
    json_x: bool = False
    json_y: bool = False
    subset_id: int | None = None


@router.post("/{pipeline_id}/orthogonality/pair")
def orthogonality_pair(
    pipeline_id: int, body: OrthogonalityBody, user: CurrentUser, db: Db
):
    """Chi-square / Cramér's V on pairwise counts (same construction as heatmap charts)."""
    db_path = _resolve_db(db, pipeline_id, user["id"])
    subset_sql = None
    if body.subset_id is not None:
        sub = get_subset(db, pipeline_id, body.subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        subset_sql = sub["sql_query"]
    raw = fetch_pairwise_contingency(
        db_path,
        subset_sql,
        body.column_x,
        body.column_y,
        body.json_x,
        body.json_y,
    )
    if "error" in raw:
        raise HTTPException(400, raw["error"])
    rows = raw.get("rows") or []
    row_labels, col_labels, matrix = contingency_from_labeled_counts(rows)
    stats = chi_square_cramers_residuals(matrix)
    err_msg = stats.get("error")
    interp = err_msg or interpret_association(stats.get("cramers_v"), stats.get("p_value"))
    return {
        "row_labels": row_labels,
        "col_labels": col_labels,
        "matrix": matrix,
        "stats": stats,
        "interpretation": interp,
        "raw_rows": [{"x": a, "y": b, "count": c} for a, b, c in rows[:2000]],
    }


class DynamicChartBody(BaseModel):
    chart_type: str
    column_x: str
    column_y: str | None = None
    stack_column: str | None = None
    json_x: bool = False
    json_y: bool = False
    top_n: int = 20


@router.post("/{pipeline_id}/dynamic-chart")
def build_dynamic_chart_route(
    pipeline_id: int, body: DynamicChartBody, user: CurrentUser, db: Db
):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    schema = get_schema(db_path)
    allowed = {c["name"] for c in schema}
    try:
        sql, renderer = build_dynamic_sql(
            chart_type=body.chart_type,
            column_x=body.column_x,
            column_y=body.column_y,
            stack_column=body.stack_column,
            json_x=body.json_x,
            json_y=body.json_y,
            top_n=body.top_n,
            allowed_cols=allowed,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"sql": sql, "renderer": renderer}


# ── Ad-hoc query ──────────────────────────────────────────────────────────────

class QueryBody(BaseModel):
    sql: str | None = None
    chart_id: str | None = None
    subset_id: int | None = None
    top_n: int | None = None
    cutoff_year: int | None = None
    max_rows: int | None = None


@router.post("/{pipeline_id}/query")
def db_query(pipeline_id: int, body: QueryBody, user: CurrentUser, db: Db):
    db_path = _resolve_db(db, pipeline_id, user["id"])
    max_rows = min(int(body.max_rows), 200_000) if body.max_rows else settings.db_query_max_rows

    subset_inner: str | None = None
    if body.subset_id is not None:
        sub = get_subset(db, pipeline_id, body.subset_id)
        if not sub:
            raise HTTPException(404, "subset not found")
        subset_inner = sub["sql_query"]

    if body.chart_id:
        spec = get_chart_definition(body.chart_id)
        if not spec:
            raise HTTPException(404, "unknown chart_id")
        tn = body.top_n if body.top_n is not None else 20
        cy = body.cutoff_year if body.cutoff_year is not None else 2000
        chart_sql = spec.format_sql(top_n=tn, cutoff_year=cy)
        if subset_inner:
            chart_sql = apply_subset_to_chart_sql(chart_sql)
            final_sql = compose_subset_query(subset_inner, chart_sql)
        else:
            final_sql = chart_sql
        result = run_query(db_path, final_sql, max_rows=max_rows)
    elif body.sql and body.sql.strip():
        sql_part = body.sql.strip()
        if subset_inner:
            result = run_query(
                db_path, sql_part, max_rows=max_rows, subset_inner_sql=subset_inner
            )
        else:
            result = run_query(db_path, sql_part, max_rows=max_rows)
    else:
        raise HTTPException(400, "Provide sql or chart_id")

    if "error" in result:
        raise HTTPException(400, result["error"])
    return result
