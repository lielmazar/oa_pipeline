"""QA / Krippendorff's Alpha blueprint — /api/qa/*

Endpoints for inter-rater reliability QA:
  - Smart sampling of articles for human review
  - Storing human ratings
  - Computing Krippendorff's Alpha agreement metric
"""
from __future__ import annotations

import json
import math
import sqlite3
from itertools import combinations
from pathlib import Path

from flask import Blueprint, jsonify, request

from blueprints.auth import require_auth, get_current_user_id
from models.db import get_meta_db
from models.pipeline import get_pipeline

qa_bp = Blueprint("qa", __name__)

# ── LLM fields that can be QA-rated ──────────────────────────────────────────

QA_FIELDS = [
    {"id": "llm_fuel_classification", "label": "Fuel Classification", "type": "multi"},
    {"id": "llm_is_hydrocarbon",      "label": "Is Hydrocarbon",      "type": "binary"},
    {"id": "llm_ml_role",             "label": "ML Role",             "type": "multi"},
    {"id": "llm_ml_algorithm",        "label": "ML Algorithm",        "type": "multi"},
    {"id": "llm_confidence",          "label": "LLM Confidence",      "type": "ordinal"},
    {"id": "llm_scope",              "label": "Value Chain",          "type": "multi"},
]


def _pipeline_db(pipeline: dict) -> Path | None:
    p = pipeline.get("output_db_path")
    return Path(p) if p else None


# ── Smart sampling ────────────────────────────────────────────────────────────

@qa_bp.route("/api/qa/sample/<int:pipeline_id>", methods=["GET"])
def api_qa_sample(pipeline_id: int):
    """Return a smart-sampled batch of articles for QA review.

    Priority order:
      1. Low-confidence articles not yet rated by this user
      2. Medium-confidence articles not yet rated
      3. High-confidence articles not yet rated
      4. Random unrated articles
    Query params: ?size=20&field=llm_fuel_classification
    """
    auth = require_auth()
    if auth:
        return auth

    user_id = get_current_user_id()
    pipeline = get_pipeline(pipeline_id)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    db_path = _pipeline_db(pipeline)
    if not db_path or not db_path.exists():
        return jsonify({"error": "pipeline database not found"}), 404

    size = min(int(request.args.get("size", 20)), 100)
    field = request.args.get("field", "llm_fuel_classification")

    meta_db = get_meta_db()
    already_rated = {
        row["openalex_id"]
        for row in meta_db.execute(
            "SELECT openalex_id FROM qa_ratings WHERE pipeline_id=? AND user_id=? AND field=?",
            (pipeline_id, user_id, field),
        ).fetchall()
    }

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Check which columns exist
        cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
        has_confidence = "llm_confidence" in cols
        has_field = field in cols

        if not has_field:
            return jsonify({"error": f"field '{field}' not found in database"}), 400

        # Fetch candidates ordered by confidence priority
        if has_confidence:
            rows = conn.execute(
                """SELECT openalex_id, title, doi,
                          llm_confidence, """ + field + """ AS field_value
                   FROM filtered_records
                   WHERE """ + field + """ IS NOT NULL
                   ORDER BY
                     CASE llm_confidence
                       WHEN 'Low' THEN 1
                       WHEN 'Medium' THEN 2
                       WHEN 'High' THEN 3
                       ELSE 4
                     END,
                     RANDOM()
                   LIMIT ?""",
                (size * 5,),   # over-fetch so we can filter rated ones
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

    # Filter out already rated and trim
    results = []
    for r in rows:
        if r["openalex_id"] in already_rated:
            continue
        results.append({
            "openalex_id": r["openalex_id"],
            "title": r["title"],
            "doi": r["doi"],
            "llm_confidence": r["llm_confidence"],
            "llm_value": r["field_value"],
        })
        if len(results) >= size:
            break

    return jsonify({"articles": results, "field": field, "total_rated": len(already_rated)})


# ── Submit ratings ────────────────────────────────────────────────────────────

@qa_bp.route("/api/qa/rate/<int:pipeline_id>", methods=["POST"])
def api_qa_rate(pipeline_id: int):
    """Submit one or more human ratings.

    Body: { "ratings": [ { "openalex_id": "...", "field": "...", "llm_value": "...", "human_value": "..." }, ... ] }
    """
    auth = require_auth()
    if auth:
        return auth

    user_id = get_current_user_id()
    pipeline = get_pipeline(pipeline_id)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    data = request.get_json(silent=True) or {}
    ratings = data.get("ratings", [])
    if not ratings:
        return jsonify({"error": "no ratings provided"}), 400

    meta_db = get_meta_db()
    inserted = 0
    for r in ratings:
        oa_id = (r.get("openalex_id") or "").strip()
        fld = (r.get("field") or "").strip()
        human_val = (r.get("human_value") or "").strip()
        llm_val = r.get("llm_value")
        if not oa_id or not fld or not human_val:
            continue
        # Validate field name against whitelist
        if fld not in {f["id"] for f in QA_FIELDS}:
            continue
        meta_db.execute(
            """INSERT INTO qa_ratings (pipeline_id, openalex_id, user_id, field, llm_value, human_value)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(pipeline_id, openalex_id, user_id, field)
               DO UPDATE SET human_value=excluded.human_value, llm_value=excluded.llm_value,
                             created_at=CURRENT_TIMESTAMP""",
            (pipeline_id, oa_id, user_id, fld, llm_val, human_val),
        )
        inserted += 1
    meta_db.commit()
    return jsonify({"ok": True, "saved": inserted})


# ── Krippendorff's Alpha computation ─────────────────────────────────────────

def _krippendorff_alpha_nominal(units: list[dict]) -> float | None:
    """Compute Krippendorff's alpha for nominal data.

    `units` is a list of dicts: [{ "unit": str, "rater": int, "value": str }, ...]
    Returns alpha or None if not computable (< 2 raters or < 1 unit with 2+ ratings).
    """
    # Group by unit
    from collections import defaultdict
    unit_ratings: dict[str, list[str]] = defaultdict(list)
    for item in units:
        unit_ratings[item["unit"]].append(item["value"])

    # Keep only units with 2+ ratings
    valid_units = {u: vals for u, vals in unit_ratings.items() if len(vals) >= 2}
    if not valid_units:
        return None

    # Collect all value categories
    all_values: set[str] = set()
    for vals in valid_units.values():
        all_values.update(vals)
    if len(all_values) < 2:
        return 1.0  # perfect agreement if only one category used

    # Observed disagreement (Do)
    n_total = 0
    do = 0.0
    for vals in valid_units.values():
        m = len(vals)
        if m < 2:
            continue
        for i in range(m):
            for j in range(i + 1, m):
                n_total += 1
                if vals[i] != vals[j]:
                    do += 1

    if n_total == 0:
        return None
    do /= n_total

    # Expected disagreement (De) – based on overall value frequencies
    freq: dict[str, int] = defaultdict(int)
    total_labels = 0
    for vals in valid_units.values():
        for v in vals:
            freq[v] += 1
            total_labels += 1

    de = 0.0
    cats = list(all_values)
    for i in range(len(cats)):
        for j in range(i + 1, len(cats)):
            de += 2 * freq[cats[i]] * freq[cats[j]]
    de /= total_labels * (total_labels - 1)

    if de == 0:
        return 1.0

    return 1.0 - (do / de)


@qa_bp.route("/api/qa/alpha/<int:pipeline_id>", methods=["GET"])
def api_qa_alpha(pipeline_id: int):
    """Compute Krippendorff's Alpha per field for this pipeline.

    Query params: ?field=llm_fuel_classification (optional, computes all if omitted)
    """
    auth = require_auth()
    if auth:
        return auth

    pipeline = get_pipeline(pipeline_id)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    meta_db = get_meta_db()
    target_field = request.args.get("field")

    if target_field:
        fields_to_check = [target_field]
    else:
        fields_to_check = [f["id"] for f in QA_FIELDS]

    results = {}
    for fld in fields_to_check:
        rows = meta_db.execute(
            "SELECT openalex_id, user_id, human_value FROM qa_ratings WHERE pipeline_id=? AND field=?",
            (pipeline_id, fld),
        ).fetchall()
        if not rows:
            results[fld] = {"alpha": None, "n_units": 0, "n_raters": 0, "n_ratings": 0}
            continue

        units = [{"unit": r["openalex_id"], "rater": r["user_id"], "value": r["human_value"]} for r in rows]
        raters = len(set(item["rater"] for item in units))
        unique_units = len(set(item["unit"] for item in units))
        alpha = _krippendorff_alpha_nominal(units)

        results[fld] = {
            "alpha": round(alpha, 4) if alpha is not None else None,
            "n_units": unique_units,
            "n_raters": raters,
            "n_ratings": len(rows),
        }

    return jsonify({"fields": results})


# ── QA progress / stats ──────────────────────────────────────────────────────

@qa_bp.route("/api/qa/stats/<int:pipeline_id>", methods=["GET"])
def api_qa_stats(pipeline_id: int):
    """Get QA progress stats for dashboard display."""
    auth = require_auth()
    if auth:
        return auth

    pipeline = get_pipeline(pipeline_id)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    meta_db = get_meta_db()
    rows = meta_db.execute(
        """SELECT field, COUNT(DISTINCT openalex_id) AS articles, COUNT(DISTINCT user_id) AS raters, COUNT(*) AS total
           FROM qa_ratings WHERE pipeline_id=? GROUP BY field""",
        (pipeline_id,),
    ).fetchall()

    per_field = {}
    for r in rows:
        per_field[r["field"]] = {
            "articles": r["articles"],
            "raters": r["raters"],
            "total_ratings": r["total"],
        }

    # Total unique articles in output DB
    db_path = _pipeline_db(pipeline)
    total_articles = 0
    if db_path and db_path.exists():
        with sqlite3.connect(db_path) as conn:
            total_articles = conn.execute("SELECT COUNT(*) FROM filtered_records").fetchone()[0]

    return jsonify({
        "per_field": per_field,
        "total_articles": total_articles,
        "qa_fields": QA_FIELDS,
    })


@qa_bp.route("/api/qa/my-ratings/<int:pipeline_id>", methods=["GET"])
def api_qa_my_ratings(pipeline_id: int):
    """Get current user's ratings for a pipeline."""
    auth = require_auth()
    if auth:
        return auth

    user_id = get_current_user_id()
    field = request.args.get("field")

    meta_db = get_meta_db()
    if field:
        rows = meta_db.execute(
            "SELECT openalex_id, field, llm_value, human_value, created_at FROM qa_ratings "
            "WHERE pipeline_id=? AND user_id=? AND field=?",
            (pipeline_id, user_id, field),
        ).fetchall()
    else:
        rows = meta_db.execute(
            "SELECT openalex_id, field, llm_value, human_value, created_at FROM qa_ratings "
            "WHERE pipeline_id=? AND user_id=?",
            (pipeline_id, user_id),
        ).fetchall()

    return jsonify({
        "ratings": [dict(r) for r in rows],
    })
