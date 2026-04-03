"""Data blueprint — /api/db/*, /api/charts/*, /api/subsets, /api/report, /api/log"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file
from core import chart_gen as _chart_gen

from blueprints.auth import require_auth, get_current_user_id
from core import db_api
from core.charts import CHART_QUERIES, get_chart_configs, get_chart_sql
from core.config import DB_PAGE_SIZE, DB_QUERY_MAX_ROWS
from core.utils import tail as _tail
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


def _get_subsets_path(pipeline_id: int, uid: int) -> tuple[Path | None, str | None]:
    """Return (subsets_json_path, error_msg) for a pipeline's saved subsets file."""
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return None, "pipeline not found"
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return None, "pipeline has no database yet"
    return Path(db_path_str).parent / "subsets.json", None


def _read_subsets(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


# ── Saved subsets ──────────────────────────────────────────────────────────────

@data_bp.route("/api/subsets", methods=["GET", "POST", "DELETE"])
def api_subsets():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()

    if request.method == "GET":
        pipeline_id = request.args.get("pipeline_id", type=int)
        if not pipeline_id:
            return jsonify({"error": "pipeline_id required"}), 400
        path, err_msg = _get_subsets_path(pipeline_id, uid)
        if err_msg:
            return jsonify([])   # no DB yet → empty list, not an error
        return jsonify(_read_subsets(path))

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        pipeline_id = data.get("pipeline_id")
        name = (data.get("name") or "").strip()
        sql = (data.get("sql") or "").strip()
        if not pipeline_id or not name or not sql:
            return jsonify({"error": "pipeline_id, name, and sql required"}), 400
        path, err_msg = _get_subsets_path(int(pipeline_id), uid)
        if err_msg:
            return jsonify({"error": err_msg}), 404
        subsets = _read_subsets(path)
        subsets.append({"name": name, "sql": sql,
                        "created_at": datetime.now(timezone.utc).isoformat()})
        path.write_text(json.dumps(subsets, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return jsonify(subsets)

    if request.method == "DELETE":
        data = request.get_json(silent=True) or {}
        pipeline_id = data.get("pipeline_id")
        idx = data.get("index")
        if not pipeline_id or idx is None:
            return jsonify({"error": "pipeline_id and index required"}), 400
        path, err_msg = _get_subsets_path(int(pipeline_id), uid)
        if err_msg:
            return jsonify({"error": err_msg}), 404
        subsets = _read_subsets(path)
        if 0 <= int(idx) < len(subsets):
            subsets.pop(int(idx))
            path.write_text(json.dumps(subsets, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return jsonify(subsets)

    return jsonify({"error": "method not allowed"}), 405


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

    # Allow caller to request a higher limit (capped for safety)
    requested = data.get("max_rows")
    max_rows = min(int(requested), 200_000) if requested else DB_QUERY_MAX_ROWS
    result = db_api.run_query(db_path, sql, max_rows=max_rows)
    return jsonify(result)


# ── Chart data ────────────────────────────────────────────────────────────────

@data_bp.route("/api/charts/configs")
def api_chart_configs():
    """Return all chart configs (with interpolated SQL) — no DB required."""
    err = require_auth()
    if err:
        return err
    top_n = request.args.get("top_n", 20, type=int)
    return jsonify(get_chart_configs(top_n))


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
    if chart_id not in CHART_QUERIES:
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400

    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    cfg = CHART_QUERIES[chart_id]
    sql = get_chart_sql(chart_id, top_n)
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
    if chart_id not in CHART_QUERIES:
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400

    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    pipeline = get_pipeline(pipeline_id, uid)
    pipeline_config = (pipeline.get("config") or {}) if pipeline else {}

    cfg = CHART_QUERIES[chart_id]
    cutoff_year = request.args.get("cutoff_year", 2000, type=int)
    sql = get_chart_sql(chart_id, top_n, cutoff_year)

    # Optional: render chart over a saved subset instead of all records
    subset_idx = request.args.get("subset_idx", type=int)
    subset_sql = None
    if subset_idx is not None:
        subsets_path, _ = _get_subsets_path(pipeline_id, uid)
        if subsets_path:
            subsets = _read_subsets(subsets_path)
            if 0 <= subset_idx < len(subsets):
                subset_sql = subsets[subset_idx].get("sql")

    png = _chart_gen.generate(
        db_path=db_path,
        sql=sql,
        x_key=cfg["x_key"],
        y_key=cfg["y_key"],
        chart_type=cfg["type"],
        title=cfg["title"],
        pipeline_config=pipeline_config,
        subgroup_key=cfg.get("subgroup_key"),
        subset_sql=subset_sql,
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
