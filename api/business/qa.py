"""QA business logic — smart sampling, ratings, IRR."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from api.business.qa_stats import (
    binary_confusion_matrix,
    krippendorff_alpha,
    interpret_alpha,
    multilabel_code_metrics,
)
from api.business.subset_sql import normalize_subset_inner, validate_subset_inner

_VALID_FIELD = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# ── Field definitions ─────────────────────────────────────────────────────────

QA_FIELDS = [
    {"id": "llm_fuel_classification", "label": "Fuel Classification", "type": "multi"},
    {"id": "llm_is_hydrocarbon",      "label": "Is Hydrocarbon",      "type": "binary"},
    {"id": "llm_ml_role",             "label": "ML Role",             "type": "multi"},
    {"id": "llm_ml_algorithm",        "label": "ML Algorithm",        "type": "multi"},
    {"id": "llm_confidence",          "label": "LLM Confidence",      "type": "ordinal"},
    {"id": "llm_scope",               "label": "Value Chain",         "type": "multi"},
]

ANNOTATION_DIMENSIONS = [
    {"id": "primary_topic",    "label": "Primary Topic",         "type": "text"},
    {"id": "methodology",      "label": "Methodology",           "type": "text"},
    {"id": "fuel_relevance",   "label": "Fuel Relevance",        "type": "ordinal"},
    {"id": "ml_relevance",     "label": "ML Relevance",          "type": "ordinal"},
    {"id": "data_quality",     "label": "Data Quality",          "type": "ordinal"},
    {"id": "overall_quality",  "label": "Overall Quality",       "type": "ordinal"},
]


# ── Smart sample ──────────────────────────────────────────────────────────────

def get_qa_sample(
    meta_db: sqlite3.Connection,
    pipeline_db_path: Path,
    pipeline_id: int,
    user_id: int,
    field: str = "llm_fuel_classification",
    size: int = 20,
    subset_id: int | None = None,
) -> list[dict]:
    """Return smart-sampled records prioritised by low confidence / unrated."""
    size = min(size, 100)
    if not _VALID_FIELD.match(field):
        return []

    already_rated: set[str] = {
        row[0]
        for row in meta_db.execute(
            "SELECT openalex_id FROM qa_ratings WHERE pipeline_id=? AND user_id=? AND field=?",
            (pipeline_id, user_id, field),
        ).fetchall()
    }

    subset_inner: str | None = None
    if subset_id is not None:
        sub = meta_db.execute(
            "SELECT sql_query FROM pipeline_subsets WHERE id=? AND pipeline_id=?",
            (subset_id, pipeline_id),
        ).fetchone()
        if not sub:
            return []
        inner = normalize_subset_inner(sub[0])
        err = validate_subset_inner(inner)
        if err:
            return []
        subset_inner = inner

    with sqlite3.connect(str(pipeline_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
        if field not in cols:
            return []
        has_confidence = "llm_confidence" in cols

        if subset_inner:
            cte = f"WITH _fr_subset AS (\n{subset_inner}\n)\n"
            base = f"{cte}SELECT openalex_id, title, doi, "
            if has_confidence:
                rows = conn.execute(
                    base
                    + f"""llm_confidence, {field} AS field_value
                    FROM _fr_subset
                    WHERE {field} IS NOT NULL
                    ORDER BY
                      CASE llm_confidence
                        WHEN 'Low' THEN 1 WHEN 'Medium' THEN 2 WHEN 'High' THEN 3 ELSE 4
                      END, RANDOM()
                    LIMIT ?""",
                    (size * 5,),
                ).fetchall()
            else:
                rows = conn.execute(
                    base
                    + f"""NULL AS llm_confidence, {field} AS field_value
                    FROM _fr_subset
                    WHERE {field} IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT ?""",
                    (size * 5,),
                ).fetchall()
        elif has_confidence:
            rows = conn.execute(
                f"""SELECT openalex_id, title, doi,
                           llm_confidence, {field} AS field_value
                    FROM filtered_records
                    WHERE {field} IS NOT NULL
                    ORDER BY
                      CASE llm_confidence
                        WHEN 'Low' THEN 1 WHEN 'Medium' THEN 2 WHEN 'High' THEN 3 ELSE 4
                      END, RANDOM()
                    LIMIT ?""",
                (size * 5,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT openalex_id, title, doi,
                           NULL AS llm_confidence, {field} AS field_value
                    FROM filtered_records
                    WHERE {field} IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT ?""",
                (size * 5,),
            ).fetchall()

    results = []
    for r in rows:
        if r["openalex_id"] in already_rated:
            continue
        results.append({
            "openalex_id":    r["openalex_id"],
            "title":          r["title"],
            "doi":            r["doi"],
            "llm_confidence": r["llm_confidence"],
            "llm_value":      r["field_value"],
        })
        if len(results) >= size:
            break
    return results


# ── Ratings CRUD ──────────────────────────────────────────────────────────────

def list_disagreements(meta_db: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    """Ratings where human value differs from stored LLM value."""
    out: list[dict] = []
    for r in list_ratings(meta_db, pipeline_id):
        h = (r.get("human_value") or "").strip()
        l = (r.get("llm_value") or "").strip()
        if h != l:
            out.append(r)
    return out


def list_ratings(meta_db: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    return [
        dict(r)
        for r in meta_db.execute(
            "SELECT * FROM qa_ratings WHERE pipeline_id=? ORDER BY created_at DESC",
            (pipeline_id,),
        ).fetchall()
    ]


def upsert_rating(
    meta_db: sqlite3.Connection,
    pipeline_id: int,
    user_id: int,
    openalex_id: str,
    field: str,
    human_value: str,
    llm_value: str | None = None,
) -> dict:
    meta_db.execute(
        """INSERT INTO qa_ratings (pipeline_id, openalex_id, user_id, field, llm_value, human_value)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(pipeline_id, openalex_id, user_id, field)
           DO UPDATE SET human_value=excluded.human_value, llm_value=excluded.llm_value""",
        (pipeline_id, openalex_id, user_id, field, llm_value, human_value),
    )
    meta_db.commit()
    row = meta_db.execute(
        "SELECT * FROM qa_ratings WHERE pipeline_id=? AND openalex_id=? AND user_id=? AND field=?",
        (pipeline_id, openalex_id, user_id, field),
    ).fetchone()
    return dict(row)


# ── IRR ───────────────────────────────────────────────────────────────────────

def compute_irr(meta_db: sqlite3.Connection, pipeline_id: int) -> dict:
    """Compute Krippendorff's alpha per QA field for the pipeline."""
    rows = meta_db.execute(
        "SELECT field, openalex_id, user_id, human_value FROM qa_ratings WHERE pipeline_id=?",
        (pipeline_id,),
    ).fetchall()
    if not rows:
        return {"fields": []}

    from collections import defaultdict
    by_field: dict[str, list] = defaultdict(list)
    for r in rows:
        by_field[r[0]].append({"unit": r[1], "rater": r[2], "value": r[3]})

    field_meta = {f["id"]: f for f in QA_FIELDS}
    results = []
    for fid, units in by_field.items():
        fmeta = field_meta.get(fid, {})
        vtype = "set" if fmeta.get("type") == "multi" else (
            "ordinal" if fmeta.get("type") == "ordinal" else "nominal"
        )
        alpha = krippendorff_alpha(units, value_type=vtype)
        by_unit: dict[str, list] = {}
        for u in units:
            uid = u["unit"]
            if uid not in by_unit:
                by_unit[uid] = []
            by_unit[uid].append(u)
        overlap_units = sum(1 for lst in by_unit.values() if len(lst) >= 2)
        results.append({
            "field":          fid,
            "label":          fmeta.get("label", fid),
            "raters":         len({u["rater"] for u in units}),
            "units":          len({u["unit"] for u in units}),
            "overlap_units":  overlap_units,
            "alpha":          round(alpha, 4) if alpha is not None else None,
            "quality":        interpret_alpha(alpha),
        })
    return {"fields": results}


# ── Annotations CRUD ──────────────────────────────────────────────────────────

def list_annotations(meta_db: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    return [
        dict(r)
        for r in meta_db.execute(
            "SELECT * FROM qa_annotations WHERE pipeline_id=? ORDER BY created_at DESC",
            (pipeline_id,),
        ).fetchall()
    ]


def upsert_annotation(
    meta_db: sqlite3.Connection,
    pipeline_id: int,
    annotator_id: int,
    openalex_id: str,
    dimension: str,
    human_value: str,
    llm_value: str | None = None,
) -> dict:
    meta_db.execute(
        """INSERT INTO qa_annotations (pipeline_id, openalex_id, annotator_id, dimension, human_value, llm_value)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(pipeline_id, openalex_id, annotator_id, dimension)
           DO UPDATE SET human_value=excluded.human_value, llm_value=excluded.llm_value""",
        (pipeline_id, openalex_id, annotator_id, dimension, human_value, llm_value),
    )
    meta_db.commit()
    row = meta_db.execute(
        "SELECT * FROM qa_annotations WHERE pipeline_id=? AND openalex_id=? AND annotator_id=? AND dimension=?",
        (pipeline_id, openalex_id, annotator_id, dimension),
    ).fetchone()
    return dict(row)
