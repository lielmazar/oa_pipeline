"""Pipelines blueprint — /api/pipelines/*"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

from blueprints.auth import get_current_user_id, require_auth
from core.config import PIPELINES_BASE_DIR, PIPELINE_SCRIPTS_DIR
from models.pipeline import (
    STAGES,
    STAGE_LABELS,
    create_pipeline,
    get_pipeline,
    list_all_pipelines,
    update_pipeline,
    update_pipeline_paths,
    delete_pipeline,
    advance_pipeline_stage,
    fork_pipeline,
    record_pipeline_job,
    get_pipeline_jobs,
    get_next_stage,
)

pipelines_bp = Blueprint("pipelines", __name__)

DEFAULT_KEYWORDS_PATH = PIPELINE_SCRIPTS_DIR / "default_keywords.json"


def _pipeline_base(pipeline_id: int) -> Path:
    return PIPELINES_BASE_DIR / str(pipeline_id)


def _pipeline_paths(pipeline_id: int) -> dict:
    base = _pipeline_base(pipeline_id)
    return {
        "output_db_path":   str(base / "output.sqlite"),
        "state_file_path":  str(base / "pipeline_state.json"),
        "batch_state_path": str(base / "batch_runs" / "state.json"),
        "report_path":      str(base / "report.html"),
        "llm_report_path":  str(base / "llm_report.html"),
        "keywords_path":    str(base / "keywords.json"),
        "log_path":         str(base / "pipeline.log"),
        "base":             str(base),
    }


def _load_default_keywords() -> dict:
    if DEFAULT_KEYWORDS_PATH.exists():
        return json.loads(DEFAULT_KEYWORDS_PATH.read_text(encoding="utf-8"))
    return {"fuel_terms": [], "impact_terms": []}


def _pipeline_response(pipeline: dict) -> dict:
    """Add computed fields to a pipeline dict."""
    pipeline["stage_label"] = STAGE_LABELS.get(pipeline["current_stage"], pipeline["current_stage"])
    pipeline["next_stage"] = get_next_stage(pipeline["current_stage"])
    pipeline["next_stage_label"] = STAGE_LABELS.get(pipeline.get("next_stage") or "", "")
    pipeline["stages"] = [{"id": s, "label": STAGE_LABELS[s]} for s in STAGES]
    return pipeline


# ── List all pipelines ────────────────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines", methods=["GET"])
def api_list_pipelines():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipelines = list_all_pipelines(uid)
    return jsonify([_pipeline_response(p) for p in pipelines])


# ── Create pipeline ───────────────────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines", methods=["POST"])
def api_create_pipeline():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    description = data.get("description", "")

    # Merge provided keywords with defaults (empty lists = use defaults)
    default_config = _load_default_keywords()
    config = {
        "fuel_terms": data.get("fuel_terms") or default_config.get("fuel_terms", []),
        "impact_terms": data.get("impact_terms") or default_config.get("impact_terms", []),
        "workers": int(data.get("workers", 4)),
        "min_free_gb": float(data.get("min_free_gb", 2.0)),
    }

    pipeline_id = create_pipeline(uid, name, description, config)

    # Set up per-pipeline paths and create the directory
    paths = _pipeline_paths(pipeline_id)
    Path(paths["base"]).mkdir(parents=True, exist_ok=True)

    # Write keywords.json for use by pipeline scripts
    keywords_path = Path(paths["keywords_path"])
    keywords_path.write_text(
        json.dumps({"fuel_terms": config["fuel_terms"], "impact_terms": config["impact_terms"]},
                   indent=2),
        encoding="utf-8",
    )

    update_pipeline_paths(
        pipeline_id,
        output_db_path=paths["output_db_path"],
        state_file_path=paths["state_file_path"],
        batch_state_path=paths["batch_state_path"],
        report_path=paths["report_path"],
        llm_report_path=paths["llm_report_path"],
    )

    pipeline = get_pipeline(pipeline_id, uid)
    return jsonify(_pipeline_response(pipeline)), 201


# ── Get single pipeline ───────────────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>", methods=["GET"])
def api_get_pipeline(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404
    return jsonify(_pipeline_response(pipeline))


# ── Update pipeline (owner only) ──────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>", methods=["PATCH"])
def api_update_pipeline(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}

    ok = update_pipeline(
        pipeline_id,
        uid,
        name=data.get("name"),
        description=data.get("description"),
        config=data.get("config"),
    )
    if not ok:
        return jsonify({"error": "not found or not owner"}), 403

    # If keywords changed, rewrite keywords.json
    if "config" in data:
        cfg = data["config"]
        paths = _pipeline_paths(pipeline_id)
        keywords_path = Path(paths["keywords_path"])
        keywords_path.parent.mkdir(parents=True, exist_ok=True)
        if "fuel_terms" in cfg or "impact_terms" in cfg:
            existing = {}
            if keywords_path.exists():
                existing = json.loads(keywords_path.read_text(encoding="utf-8"))
            existing.update({
                k: cfg[k] for k in ("fuel_terms", "impact_terms") if k in cfg
            })
            keywords_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    pipeline = get_pipeline(pipeline_id, uid)
    return jsonify(_pipeline_response(pipeline))


# ── Delete pipeline (owner only) ──────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>", methods=["DELETE"])
def api_delete_pipeline(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    ok = delete_pipeline(pipeline_id, uid)
    if not ok:
        return jsonify({"error": "not found or not owner"}), 403
    return jsonify({"ok": True})


# ── Advance stage (owner only) ────────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/advance", methods=["POST"])
def api_advance_stage(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    next_stage = advance_pipeline_stage(pipeline_id, uid)
    if next_stage is None:
        return jsonify({"error": "cannot advance: not owner, or already at last stage"}), 403
    pipeline = get_pipeline(pipeline_id, uid)
    return jsonify(_pipeline_response(pipeline))


# ── Fork pipeline ─────────────────────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/fork", methods=["POST"])
def api_fork_pipeline(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "name is required"}), 400

    try:
        new_id = fork_pipeline(pipeline_id, uid, new_name, PIPELINES_BASE_DIR)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    pipeline = get_pipeline(new_id, uid)
    return jsonify(_pipeline_response(pipeline)), 201


# ── Pipeline status (stage-specific info) ─────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/status", methods=["GET"])
def api_pipeline_status(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404

    status = _build_stage_status(pipeline)
    return jsonify(status)


def _build_stage_status(pipeline: dict) -> dict:
    """Gather stage-specific status info (DB record count, job state, etc.)."""
    import sqlite3, json as _json, os

    status: dict = {
        "pipeline_id": pipeline["id"],
        "current_stage": pipeline["current_stage"],
    }

    # DB stats
    db_path = pipeline.get("output_db_path")
    if db_path and Path(db_path).exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN llm_is_hydrocarbon IS NOT NULL THEN 1 ELSE 0 END) as llm_done "
                "FROM filtered_records"
            ).fetchone()
            conn.close()
            status["record_count"] = row[0] if row else 0
            status["llm_done"] = row[1] if row else 0
        except Exception:
            status["record_count"] = 0
            status["llm_done"] = 0
    else:
        status["record_count"] = 0
        status["llm_done"] = 0

    # DB file size
    if db_path and Path(db_path).exists():
        status["db_size_mb"] = round(os.path.getsize(db_path) / (1024 ** 2), 1)
    else:
        status["db_size_mb"] = 0

    # Pipeline state file
    state_path = pipeline.get("state_file_path")
    if state_path and Path(state_path).exists():
        try:
            pipeline_state = _json.loads(Path(state_path).read_text())
            all_files = len(pipeline_state.get("all_files", []))
            processed = len(pipeline_state.get("processed_files", []))
            status["search_progress"] = {
                "total": all_files,
                "processed": processed,
                "percent": round(100 * processed / all_files, 1) if all_files else 0,
            }
        except Exception:
            status["search_progress"] = None
    else:
        status["search_progress"] = None

    # Batch state
    batch_path = pipeline.get("batch_state_path")
    if batch_path and Path(batch_path).exists():
        try:
            status["batch_state"] = _json.loads(Path(batch_path).read_text())
        except Exception:
            status["batch_state"] = None
    else:
        status["batch_state"] = None

    # Report exists?
    status["has_report"] = bool(
        pipeline.get("report_path") and Path(pipeline["report_path"]).exists()
    )
    status["has_llm_report"] = bool(
        pipeline.get("llm_report_path") and Path(pipeline["llm_report_path"]).exists()
    )

    return status
