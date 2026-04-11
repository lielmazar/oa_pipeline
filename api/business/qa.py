"""QA business logic — smart sampling, ratings, IRR."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from api.business.qa_stats import (
    binary_confusion_matrix,
    krippendorff_alpha,
    krippendorff_alpha_bootstrap_ci,
    interpret_alpha,
    multilabel_code_metrics,
)
def _normalize_subset_inner(sql: str) -> str:
    return sql.strip().rstrip(";")

def _compose_subset_query(inner_sql: str, outer_sql: str) -> str:
    clean = _normalize_subset_inner(inner_sql)
    return f"WITH _fr_subset AS (\n{clean}\n)\n{outer_sql}"

_VALID_FIELD = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# ── Field definitions ─────────────────────────────────────────────────────────

QA_FIELDS = [
    {"id": "llm_is_hydrocarbon",      "label": "Is Hydrocarbon",       "type": "binary"},
    {"id": "llm_research_objective",  "label": "Research Objective",   "type": "json_scalar"},
    {"id": "llm_ml_role",             "label": "ML Role",              "type": "json_scalar"},
    {"id": "llm_ml_algorithm",        "label": "ML Algorithm",         "type": "multi"},
    {"id": "llm_ml_data_source",      "label": "ML Data Source",       "type": "ordinal"},
    {"id": "llm_confidence",          "label": "Confidence",           "type": "ordinal"},
]

FIELD_BY_ID = {f["id"]: f for f in QA_FIELDS}


def _normalize_llm_value(field: str, raw: str) -> str:
    """Normalize a raw DB value so it matches human-label format.

    ``json_scalar`` fields (e.g. ``llm_research_objective``) are stored in the
    research DB as single-element JSON arrays like ``'["fuel_production"]'``.
    The QA UI presents — and the human selects — plain scalars, so we unwrap
    before storing in ``qa_ratings`` / comparing for disagreements & IRR.
    """
    meta = FIELD_BY_ID.get(field)
    if not meta:
        return raw
    ftype = meta.get("type")
    if ftype == "json_scalar":
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) == 1:
                return str(parsed[0])
            if isinstance(parsed, list) and len(parsed) == 0:
                return "none"
        except (json.JSONDecodeError, TypeError):
            pass
    return raw


def read_llm_field_value(pipeline_db_path: Path, openalex_id: str, field: str) -> str | None:
    """Read column value from the research DB, normalized for QA comparison."""
    if not _VALID_FIELD.match(field):
        return None
    try:
        with sqlite3.connect(str(pipeline_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f'SELECT "{field}" FROM filtered_records WHERE openalex_id = ? LIMIT 1',
                (openalex_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    v = row[field]
    if v is None:
        return None
    raw = v if isinstance(v, str) else str(v)
    return _normalize_llm_value(field, raw)


def list_distinct_field_labels(
    pipeline_db_path: Path,
    field: str,
    subset_inner: str | None,
    *,
    limit: int = 150,
) -> list[str]:
    """Distinct label options for UI pills (subset-scoped when *subset_inner* is set)."""
    if not _VALID_FIELD.match(field):
        return []
    meta = FIELD_BY_ID.get(field)
    if not meta:
        return []
    t = meta.get("type")
    qi = f'"{field}"'

    def wrap(inner_sql: str) -> str:
        if subset_inner:
            return _compose_subset_query(subset_inner, inner_sql)
        return inner_sql.replace("_fr_subset", "filtered_records")

    try:
        with sqlite3.connect(str(pipeline_db_path)) as conn:
            if t == "binary":
                return ["0", "1"]

            if t == "ordinal":
                inner_sql = (
                    f"SELECT DISTINCT {qi} AS v FROM _fr_subset WHERE {qi} IS NOT NULL "
                    f"AND TRIM(CAST({qi} AS TEXT)) != '' ORDER BY v LIMIT ?"
                )
                rows = conn.execute(wrap(inner_sql), (limit,)).fetchall()
                return [str(r[0]) for r in rows if r[0] is not None]

            if t in ("multi", "json_scalar"):
                inner_sql = (
                    f"SELECT DISTINCT TRIM(CAST(value AS TEXT)) AS v FROM _fr_subset, json_each({qi}) "
                    f"WHERE value IS NOT NULL AND TRIM(CAST(value AS TEXT)) != '' "
                    f"AND LOWER(TRIM(CAST(value AS TEXT))) NOT IN ('none', 'null') "
                    f"ORDER BY v LIMIT ?"
                )
                rows = conn.execute(wrap(inner_sql), (limit,)).fetchall()
                out = [str(r[0]) for r in rows if r[0] is not None]
                return sorted(set(out), key=lambda x: x.lower())
    except Exception:
        return []

    return []


ANNOTATION_DIMENSIONS = [
    {"id": "primary_topic",    "label": "Primary Topic",         "type": "text"},
    {"id": "methodology",      "label": "Methodology",           "type": "text"},
    {"id": "fuel_relevance",   "label": "Fuel Relevance",        "type": "ordinal"},
    {"id": "ml_relevance",     "label": "ML Relevance",          "type": "ordinal"},
    {"id": "data_quality",     "label": "Data Quality",          "type": "ordinal"},
    {"id": "overall_quality",  "label": "Overall Quality",       "type": "ordinal"},
]


def resolve_subset_inner(
    meta_db: sqlite3.Connection,
    pipeline_id: int,
    subset_id: int | None,
) -> str | None:
    if subset_id is None:
        return None
    sub = meta_db.execute(
        "SELECT sql_query FROM pipeline_subsets WHERE id=? AND pipeline_id=?",
        (subset_id, pipeline_id),
    ).fetchone()
    if not sub:
        return None
    return _normalize_subset_inner(sub[0])


def resolve_subset_by_preset_key(
    meta_db: sqlite3.Connection,
    pipeline_id: int,
    preset_key: str,
) -> str | None:
    """Resolve a subset SQL by its preset_key (e.g. 'oa_llm')."""
    sub = meta_db.execute(
        "SELECT sql_query FROM pipeline_subsets WHERE pipeline_id=? AND preset_key=?",
        (pipeline_id, preset_key),
    ).fetchone()
    if not sub:
        return None
    return _normalize_subset_inner(sub[0])


# ── Smart sample ──────────────────────────────────────────────────────────────

def get_qa_sample(
    meta_db: sqlite3.Connection,
    pipeline_db_path: Path,
    pipeline_id: int,
    user_id: int,
    field: str = "llm_is_hydrocarbon",
    size: int = 20,
    subset_id: int | None = None,
    subset_inner: str | None = None,
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

    if subset_inner is None and subset_id is not None:
        subset_inner = resolve_subset_inner(meta_db, pipeline_id, subset_id)
        if subset_inner is None:
            return []

    with sqlite3.connect(str(pipeline_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
        if field not in cols:
            return []
        has_confidence = "llm_confidence" in cols
        abs_expr = (
            "COALESCE(abstract_text, '') AS abstract_text"
            if "abstract_text" in cols
            else "'' AS abstract_text"
        )

        if subset_inner:
            cte = f"WITH _fr_subset AS (\n{subset_inner}\n)\n"
            base = f"{cte}SELECT openalex_id, title, doi, {abs_expr}, "
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
                f"""SELECT openalex_id, title, doi, {abs_expr},
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
                f"""SELECT openalex_id, title, doi, {abs_expr},
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
            "openalex_id": r["openalex_id"],
            "title":       r["title"],
            "doi":         r["doi"],
            "abstract":    (r["abstract_text"] or "").strip() if r["abstract_text"] is not None else "",
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
        l = _normalize_llm_value(
            r.get("field", ""),
            (r.get("llm_value") or "").strip(),
        )
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


def fix_malformed_llm_values(meta_db: sqlite3.Connection, pipeline_id: int) -> int:
    """Retroactively normalize llm_value in existing qa_ratings rows.

    Fixes json_scalar fields where llm_value was stored as a JSON array
    (e.g. ``'["fuel_production"]'``) instead of a plain scalar.
    Returns the number of rows updated.
    """
    json_scalar_fields = [
        fid for fid, fmeta in FIELD_BY_ID.items()
        if fmeta.get("type") == "json_scalar"
    ]
    if not json_scalar_fields:
        return 0

    ph = ",".join("?" for _ in json_scalar_fields)
    rows = meta_db.execute(
        f"SELECT id, field, llm_value FROM qa_ratings "
        f"WHERE pipeline_id=? AND field IN ({ph}) AND llm_value IS NOT NULL",
        [pipeline_id] + json_scalar_fields,
    ).fetchall()

    updated = 0
    for row in rows:
        rid, field, raw = row[0], row[1], row[2]
        normalized = _normalize_llm_value(field, raw)
        if normalized != raw:
            meta_db.execute(
                "UPDATE qa_ratings SET llm_value=? WHERE id=?",
                (normalized, rid),
            )
            updated += 1
    if updated:
        meta_db.commit()
    return updated


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

def _qa_rows_to_krippendorff_units(rows: list) -> list[dict]:
    """Build Krippendorff unit list from qa_ratings rows.

    Each human rating is one (unit, rater, value). For each (field, openalex_id) we also
    attach the model once as rater ``"llm"`` when *llm_value* is present, so human vs.
    model agreement counts as overlap (same paper, two ratings).
    """
    from collections import defaultdict

    # field -> openalex_id -> {"humans": [(user_id, value), ...], "llm": str|None}
    cells: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"humans": [], "llm": None}))
    for r in rows:
        field, oa_id, uid, human_val, llm_val = r[0], r[1], r[2], r[3], r[4]
        c = cells[field][oa_id]
        c["humans"].append((uid, human_val))
        if llm_val is not None and str(llm_val).strip():
            raw = llm_val if isinstance(llm_val, str) else str(llm_val)
            c["llm"] = _normalize_llm_value(field, raw)

    units: list[dict] = []
    for field, by_oa in cells.items():
        for oa_id, c in by_oa.items():
            for uid, human_val in c["humans"]:
                units.append({"unit": oa_id, "rater": uid, "value": human_val})
            if c["llm"] is not None:
                units.append({"unit": oa_id, "rater": "llm", "value": c["llm"]})
    return units


def compute_irr(meta_db: sqlite3.Connection, pipeline_id: int) -> dict:
    """Compute Krippendorff's alpha per QA field for the pipeline."""
    rows = meta_db.execute(
        "SELECT field, openalex_id, user_id, human_value, llm_value FROM qa_ratings WHERE pipeline_id=?",
        (pipeline_id,),
    ).fetchall()
    if not rows:
        return {"fields": []}

    from collections import defaultdict
    by_field: dict[str, list] = defaultdict(list)
    for r in rows:
        by_field[r[0]].append(r)

    for fid in list(by_field.keys()):
        by_field[fid] = _qa_rows_to_krippendorff_units(by_field[fid])

    results = []
    for fid, units in by_field.items():
        fmeta = FIELD_BY_ID.get(fid, {})
        ft = fmeta.get("type")
        vtype = "set" if ft == "multi" else (
            "ordinal" if ft == "ordinal" else "nominal"
        )
        alpha = krippendorff_alpha(units, value_type=vtype)
        by_unit: dict[str, list] = {}
        for u in units:
            uid = u["unit"]
            if uid not in by_unit:
                by_unit[uid] = []
            by_unit[uid].append(u)
        overlap_units = sum(1 for lst in by_unit.values() if len(lst) >= 2)
        n_raters = len({u["rater"] for u in units})
        # Krippendorff α only applies when ≥2 ratings exist on the same unit (same paper + field).
        if overlap_units == 0:
            quality = "no overlapping ratings"
        else:
            quality = interpret_alpha(alpha)

        ci_lo = ci_hi = None
        if overlap_units >= 2 and alpha is not None:
            ci_lo, ci_hi = krippendorff_alpha_bootstrap_ci(units, value_type=vtype)

        results.append({
            "field":          fid,
            "label":          fmeta.get("label", fid),
            "raters":         n_raters,
            "units":          len({u["unit"] for u in units}),
            "overlap_units":  overlap_units,
            "alpha":          round(alpha, 4) if alpha is not None else None,
            "alpha_ci_low":   ci_lo,
            "alpha_ci_high":  ci_hi,
            "alpha_ci_level": 0.95 if ci_lo is not None else None,
            "quality":        quality,
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
