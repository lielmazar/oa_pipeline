"""Data blueprint — /api/db/*, /api/charts/*, /api/report, /api/log"""
from __future__ import annotations

import io
import os
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file
from core import chart_gen as _chart_gen

from blueprints.auth import require_auth, get_current_user_id
from core import db_api
from core.config import DB_PAGE_SIZE, DB_QUERY_MAX_ROWS
from models.pipeline import get_pipeline

data_bp = Blueprint("data", __name__)


def _get_pipeline_db(pipeline_id: int, uid: int) -> tuple[Path | None, str | None]:
    """Return (db_path, error_msg) for a pipeline's output SQLite."""
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return None, "pipeline not found"
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return None, "pipeline has no database yet"
    db_path = Path(db_path_str)
    if not db_path.exists():
        return None, "database file not found"
    return db_path, None


# ── DB schema ──────────────────────────────────────────────────────────────────

@data_bp.route("/api/db/schema")
def api_db_schema():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404
    return jsonify(db_api.get_schema(db_path))


# ── DB records ─────────────────────────────────────────────────────────────────

@data_bp.route("/api/db/records")
def api_db_records():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    page = max(1, request.args.get("page", 1, type=int))
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "publication_year")
    direction = request.args.get("dir", "desc")
    page_size = request.args.get("page_size", DB_PAGE_SIZE, type=int)
    # Optional comma-separated lists for search fields and visible columns
    search_cols_raw = request.args.get("search_cols", "").strip()
    visible_cols_raw = request.args.get("visible_cols", "").strip()
    search_cols = [c.strip() for c in search_cols_raw.split(",") if c.strip()] or None
    visible_cols = [c.strip() for c in visible_cols_raw.split(",") if c.strip()] or None

    result = db_api.get_records(
        db_path, page=page, search=search, sort=sort, direction=direction,
        per_page=page_size, search_cols=search_cols, visible_cols=visible_cols,
    )
    return jsonify(result)


@data_bp.route("/api/db/records/<record_id>")
def api_db_record_detail(record_id: str):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    record = db_api.get_record_detail(db_path, record_id)
    if record is None:
        return jsonify({"error": "record not found"}), 404
    return jsonify(record)


# ── DB query ───────────────────────────────────────────────────────────────────

@data_bp.route("/api/db/query", methods=["POST"])
def api_db_query():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    pipeline_id = data.get("pipeline_id") or request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(int(pipeline_id), uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    sql = (data.get("sql") or "").strip()
    if not sql:
        return jsonify({"error": "sql is required"}), 400

    result = db_api.run_query(db_path, sql, max_rows=DB_QUERY_MAX_ROWS)
    return jsonify(result)


# ── Chart data ────────────────────────────────────────────────────────────────

# Each entry: title, sql (may contain {top_n}), type (bar|hbar|pie), x_key, y_key
# llm_only=True means only meaningful when LLM columns are populated.
_CHART_QUERIES = {
    "publications_by_year": {
        "title": "Publications by Year",
        "sql": (
            "SELECT "
            "  CASE WHEN publication_year IS NULL OR publication_year <= 2000 "
            "       THEN 'Unknown/≤2000' "
            "       ELSE CAST(publication_year AS TEXT) END AS year, "
            "  COUNT(*) AS count "
            "FROM filtered_records "
            "GROUP BY year "
            "ORDER BY MIN(COALESCE(publication_year, 0))"
        ),
        "type": "bar", "x_key": "year", "y_key": "count",
    },
    "top_fuel_terms": {
        "title": "Top Fuel Terms",
        "sql": (
            "SELECT value AS term, COUNT(*) AS count "
            "FROM filtered_records, json_each(matched_fuel_terms) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "term", "y_key": "count",
    },
    "top_impact_terms": {
        "title": "Top Impact Terms",
        "sql": (
            "SELECT value AS term, COUNT(*) AS count "
            "FROM filtered_records, json_each(matched_impact_terms) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "term", "y_key": "count",
    },
    "top_countries": {
        "title": "Top Contributing Countries",
        "sql": (
            "SELECT value AS country, COUNT(*) AS count "
            "FROM filtered_records, json_each(authors_countries) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "country", "y_key": "count",
    },
    "top_journals": {
        "title": "Top Journals / Sources",
        "sql": (
            "SELECT primary_location_source_display_name AS journal, COUNT(*) AS count "
            "FROM filtered_records "
            "WHERE primary_location_source_display_name IS NOT NULL "
            "GROUP BY journal ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "journal", "y_key": "count",
    },
    "oa_status": {
        "title": "Open Access Status",
        "sql": (
            "SELECT COALESCE(open_access_oa_status, 'unknown') AS status, COUNT(*) AS count "
            "FROM filtered_records GROUP BY status ORDER BY count DESC"
        ),
        "type": "pie", "x_key": "status", "y_key": "count",
    },
    "work_types": {
        "title": "Work Types",
        "sql": (
            "SELECT COALESCE(type, 'unknown') AS type, COUNT(*) AS count "
            "FROM filtered_records GROUP BY type ORDER BY count DESC LIMIT 10"
        ),
        "type": "bar", "x_key": "type", "y_key": "count",
    },
    "top_institutions": {
        "title": "Top Institutions",
        "sql": (
            "SELECT value AS institution, COUNT(*) AS count "
            "FROM filtered_records, json_each(authors_institutions) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "institution", "y_key": "count",
    },
    "llm_scope": {
        "title": "Fuel Classification (LLM)",
        "sql": (
            "SELECT value AS fuel_type, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_fuel_classification) "
            "WHERE value IS NOT NULL AND value != '' "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "fuel_type", "y_key": "count",
        "llm_only": True,
    },
    "llm_ml_paradigm": {
        "title": "ML/AI Paradigm (LLM)",
        "sql": (
            "SELECT value AS paradigm, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_ml_paradigm) "
            "WHERE value IS NOT NULL AND value != '' AND LOWER(value) != 'none' "
            "GROUP BY value ORDER BY count DESC"
        ),
        "type": "hbar", "x_key": "paradigm", "y_key": "count",
        "llm_only": True,
    },
    "llm_ml_usage": {
        "title": "ML/AI Usage Rate (LLM)",
        "sql": (
            "SELECT "
            "  CASE WHEN llm_ml_paradigm IS NOT NULL "
            "            AND llm_ml_paradigm NOT IN ('[]', 'null', '') "
            "            AND LOWER(llm_ml_paradigm) NOT LIKE '%\"none\"%' "
            "       THEN 'Uses ML/AI' ELSE 'No ML/AI' END AS uses_ml, "
            "  COUNT(*) AS count "
            "FROM filtered_records GROUP BY uses_ml"
        ),
        "type": "pie", "x_key": "uses_ml", "y_key": "count",
        "llm_only": True,
    },
}


@data_bp.route("/api/charts/configs")
def api_chart_configs():
    """Return all chart configs (with interpolated SQL) — no DB required."""
    err = require_auth()
    if err:
        return err
    top_n = request.args.get("top_n", 20, type=int)
    configs = []
    for cid, cfg in _CHART_QUERIES.items():
        configs.append({
            "id": cid,
            "title": cfg["title"],
            "sql": cfg["sql"].replace("{top_n}", str(top_n)),
            "type": cfg["type"],
            "x_key": cfg["x_key"],
            "y_key": cfg["y_key"],
            "llm_only": cfg.get("llm_only", False),
        })
    return jsonify(configs)


@data_bp.route("/api/charts/data")
def api_chart_data():
    """Run a single chart query and return data + the SQL used."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    chart_id = request.args.get("chart", "").strip()
    top_n = request.args.get("top_n", 20, type=int)

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    if chart_id not in _CHART_QUERIES:
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400

    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    cfg = _CHART_QUERIES[chart_id]
    sql = cfg["sql"].replace("{top_n}", str(top_n))
    result = db_api.run_query(db_path, sql, max_rows=500)

    return jsonify({
        "id": chart_id,
        "title": cfg["title"],
        "sql": sql,
        "type": cfg["type"],
        "x_key": cfg["x_key"],
        "y_key": cfg["y_key"],
        "llm_only": cfg.get("llm_only", False),
        "data": result.get("rows", []),
        "columns": result.get("columns", []),
        "error": result.get("error"),
    })


@data_bp.route("/api/charts/image")
def api_chart_image():
    """Render a chart server-side with matplotlib and return a PNG image."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    chart_id = request.args.get("chart", "").strip()
    top_n = request.args.get("top_n", 20, type=int)

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    if chart_id not in _CHART_QUERIES:
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400

    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    cfg = _CHART_QUERIES[chart_id]
    sql = cfg["sql"].replace("{top_n}", str(top_n))

    png = _chart_gen.generate(
        db_path=db_path,
        sql=sql,
        x_key=cfg["x_key"],
        y_key=cfg["y_key"],
        chart_type=cfg["type"],
        title=cfg["title"],
    )
    return Response(png, mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


# ── Report ─────────────────────────────────────────────────────────────────────

@data_bp.route("/api/report")
def api_report():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    llm = request.args.get("llm", "0") == "1"

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    path_key = "llm_report_path" if llm else "report_path"
    report_path = pipeline.get(path_key)
    if not report_path or not Path(report_path).exists():
        return Response(
            "<html><body><h2>Report not yet generated.</h2></body></html>",
            mimetype="text/html",
        )
    return send_file(report_path, mimetype="text/html")


# ── Log tail ──────────────────────────────────────────────────────────────────

@data_bp.route("/api/log")
def api_log():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    lines = request.args.get("lines", 200, type=int)

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    # Determine log path from pipeline base dir
    db_path = pipeline.get("output_db_path")
    if db_path:
        log_path = Path(db_path).parent / "pipeline.log"
    else:
        return jsonify({"lines": []})

    if not log_path.exists():
        return jsonify({"lines": []})

    return jsonify({"lines": _tail(log_path, lines)})


def _tail(path: Path, n: int) -> list[str]:
    """Return the last n lines of a potentially large file."""
    chunk_size = 8192
    lines: list[str] = []
    with open(path, "rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        buf = b""
        while remaining > 0 and len(lines) < n + 1:
            read_size = min(chunk_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            buf = f.read(read_size) + buf
            lines = buf.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]
