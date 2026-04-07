"""Chart config endpoints — GET /api/pipeline/{id}/charts/

The backend serves chart SQL definitions as JSON.
The frontend downloads all records once, stores them locally,
and runs these SQL queries client-side to render charts.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.business.chart_defs import get_chart_configs, get_chart_definition
from api.db.pipelines import get_pipeline
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["charts"])


@router.get("/{id}/charts")
def list_charts(id: int, user: CurrentUser, db: Db):
    """Return all chart config objects (id, title, sql, renderer, x_key, y_key, llm_only)."""
    pipeline = get_pipeline(db, id)
    if not pipeline or pipeline["user_id"] != user["id"]:
        raise HTTPException(404, "pipeline not found")
    return get_chart_configs()


@router.get("/{id}/charts/{chart_id}")
def get_chart(id: int, chart_id: str, user: CurrentUser, db: Db):
    """Return config for a single chart."""
    pipeline = get_pipeline(db, id)
    if not pipeline or pipeline["user_id"] != user["id"]:
        raise HTTPException(404, "pipeline not found")
    spec = get_chart_definition(chart_id)
    if not spec:
        raise HTTPException(404, "chart not found")
    return {
        "id": spec.id,
        "title": spec.title,
        "sql": spec.format_sql(),
        "renderer": spec.renderer,
        "x_key": spec.x_key,
        "y_key": spec.y_key,
        "llm_only": spec.llm_only,
        "params": list(spec.params),
    }
