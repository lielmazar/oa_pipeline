"""QA router — /api/pipeline/{id}/qa/*"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.business.qa import (
    ANNOTATION_DIMENSIONS,
    QA_FIELDS,
    compute_irr,
    get_qa_sample,
    list_annotations,
    list_disagreements,
    list_ratings,
    upsert_annotation,
    upsert_rating,
)
from api.db.pipelines import get_pipeline
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["qa"])


def _resolve_pipeline_db(db, pipeline_id: int, user_id: int) -> Path:
    p = get_pipeline(db, pipeline_id, user_id)
    if not p:
        raise HTTPException(404, "pipeline not found")
    db_path_str = p.get("output_db_path")
    if not db_path_str:
        raise HTTPException(404, "pipeline has no database yet")
    db_path = Path(db_path_str)
    if not db_path.exists():
        raise HTTPException(404, "database file not found")
    return db_path


# ── Field definitions ─────────────────────────────────────────────────────────

@router.get("/qa/fields")
def qa_fields(user: CurrentUser):
    return {"qa_fields": QA_FIELDS, "annotation_dimensions": ANNOTATION_DIMENSIONS}


# ── Sample records ────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/qa/records")
def qa_records(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    field: str = "llm_fuel_classification",
    size: int = 20,
    subset_id: int | None = None,
):
    db_path = _resolve_pipeline_db(db, pipeline_id, user["id"])
    records = get_qa_sample(
        db, db_path, pipeline_id, user["id"],
        field=field, size=size, subset_id=subset_id,
    )
    return {"records": records, "field": field, "count": len(records)}


@router.get("/{pipeline_id}/qa/disagreements")
def qa_disagreements(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_disagreements(db, pipeline_id)


# ── Ratings ───────────────────────────────────────────────────────────────────

class RatingBody(BaseModel):
    openalex_id: str
    field: str
    human_value: str
    llm_value: str | None = None


@router.get("/{pipeline_id}/qa/ratings")
def get_ratings(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_ratings(db, pipeline_id)


@router.post("/{pipeline_id}/qa/ratings", status_code=201)
def submit_rating(pipeline_id: int, body: RatingBody, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    if not body.openalex_id.strip() or not body.field.strip() or not body.human_value.strip():
        raise HTTPException(400, "openalex_id, field, and human_value are required")
    return upsert_rating(
        db, pipeline_id, user["id"],
        body.openalex_id, body.field, body.human_value, body.llm_value,
    )


# ── IRR ───────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/qa/irr")
def get_irr(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return compute_irr(db, pipeline_id)


# ── Annotations ───────────────────────────────────────────────────────────────

class AnnotationBody(BaseModel):
    openalex_id: str
    dimension: str
    human_value: str
    llm_value: str | None = None


@router.get("/{pipeline_id}/qa/annotations")
def get_annotations(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_annotations(db, pipeline_id)


@router.post("/{pipeline_id}/qa/annotations", status_code=201)
def submit_annotation(pipeline_id: int, body: AnnotationBody, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    if not body.openalex_id.strip() or not body.dimension.strip() or not body.human_value.strip():
        raise HTTPException(400, "openalex_id, dimension, and human_value are required")
    return upsert_annotation(
        db, pipeline_id, user["id"],
        body.openalex_id, body.dimension, body.human_value, body.llm_value,
    )
