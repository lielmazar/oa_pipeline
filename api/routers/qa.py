"""QA router — /api/pipeline/{id}/qa/*"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.business.qa import (
    ANNOTATION_DIMENSIONS,
    FIELD_BY_ID,
    QA_FIELDS,
    compute_irr,
    fix_malformed_llm_values,
    get_qa_sample,
    list_annotations,
    list_disagreements,
    list_distinct_field_labels,
    list_ratings,
    read_llm_field_value,
    resolve_subset_by_preset_key,
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

_QA_PRESET_KEY = "oa_llm"


@router.get("/{pipeline_id}/qa/records")
def qa_records(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    field: str = "fuel_relevant",
    size: int = 20,
):
    db_path = _resolve_pipeline_db(db, pipeline_id, user["id"])

    subset_inner = resolve_subset_by_preset_key(db, pipeline_id, _QA_PRESET_KEY)

    records = get_qa_sample(
        db, db_path, pipeline_id, user["id"],
        field=field, size=size, subset_inner=subset_inner,
    )
    labels = list_distinct_field_labels(db_path, field, subset_inner)

    _EXTRA_LABELS = ["other (rater labels)", "none", "skip"]
    for extra in _EXTRA_LABELS:
        if extra not in labels:
            labels.append(extra)

    fmeta = FIELD_BY_ID.get(field, {})
    bs = min(max(1, size), 100)
    return {
        "records": records,
        "field": field,
        "count": len(records),
        "batch_size": bs,
        "field_type": fmeta.get("type"),
        "labels": labels,
    }


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
    llm_val = body.llm_value
    if llm_val is None or (isinstance(llm_val, str) and not llm_val.strip()):
        db_path = _resolve_pipeline_db(db, pipeline_id, user["id"])
        llm_val = read_llm_field_value(db_path, body.openalex_id.strip(), body.field.strip())
    return upsert_rating(
        db, pipeline_id, user["id"],
        body.openalex_id, body.field, body.human_value, llm_val,
    )


# ── IRR ───────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/qa/irr")
def get_irr(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return compute_irr(db, pipeline_id)


@router.post("/{pipeline_id}/qa/fix-llm-values")
def fix_llm_values(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    updated = fix_malformed_llm_values(db, pipeline_id)
    return {"updated": updated, "pipeline_id": pipeline_id}


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
