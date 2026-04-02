"""Pipelines blueprint — /api/pipelines/*"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import yaml

from flask import Blueprint, jsonify, request, current_app, Response

from blueprints.auth import get_current_user_id, require_auth
from core.config import PIPELINES_BASE_DIR, PIPELINE_SCRIPTS_DIR
from core.pipeline_status import build_stage_status
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
    go_back_pipeline_stage,
    set_pipeline_stage,
    fork_pipeline,
    record_pipeline_job,
    get_pipeline_jobs,
    get_next_stage,
    get_prev_stage,
)

pipelines_bp = Blueprint("pipelines", __name__)

DEFAULT_KEYWORDS_PATH = PIPELINE_SCRIPTS_DIR / "default_keywords.json"
DEFAULT_KEYWORDS_YAML_PATH = PIPELINE_SCRIPTS_DIR / "default_keywords.yaml"


def _groups_to_yaml(groups: list) -> str:
    """Convert internal groups structure to a clean YAML string."""
    doc = []
    for g in groups:
        grp: dict = {"name": g["name"], "subgroups": []}
        for sg in g.get("subgroups", []):
            entry: dict = {"name": sg["name"], "terms": sg.get("terms", [])}
            if sg.get("is_others"):
                entry["is_default"] = True
            grp["subgroups"].append(entry)
        doc.append(grp)
    return yaml.dump({"groups": doc}, allow_unicode=True, sort_keys=False,
                     default_flow_style=False)


def _yaml_to_groups(text: str) -> list:
    """Parse YAML keyword config and return canonical groups list."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    raw_groups = data.get("groups", [])
    result = []
    for g in raw_groups:
        subgroups = []
        for sg in g.get("subgroups", []):
            subgroups.append({
                "name": sg["name"],
                "terms": sg.get("terms", []),
                **({"is_others": True} if sg.get("is_default") else {}),
            })
        if not subgroups:
            subgroups = [{"name": "Default", "is_others": True, "terms": []}]
        result.append({"name": g["name"], "subgroups": subgroups})
    return result


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
    pipeline["prev_stage"] = get_prev_stage(pipeline["current_stage"])
    pipeline["prev_stage_label"] = STAGE_LABELS.get(pipeline.get("prev_stage") or "", "")
    pipeline["stages"] = [{"id": s, "label": STAGE_LABELS[s]} for s in STAGES]
    return pipeline


# ── Default keywords ──────────────────────────────────────────────────────────

@pipelines_bp.route("/api/default-keywords", methods=["GET"])
def api_default_keywords():
    err = require_auth()
    if err:
        return err
    return jsonify(_load_default_keywords())


@pipelines_bp.route("/api/default-keywords/yaml", methods=["GET"])
def api_default_keywords_yaml_get():
    """Download the server default keywords as YAML."""
    err = require_auth()
    if err:
        return err
    kw = _load_default_keywords()
    groups = kw.get("groups", [])
    text = _groups_to_yaml(groups)
    return Response(text, mimetype="text/yaml",
                    headers={"Content-Disposition": 'attachment; filename="keywords.yaml"'})


@pipelines_bp.route("/api/default-keywords/yaml", methods=["POST"])
def api_default_keywords_yaml_post():
    """Upload a YAML file to replace the server default keywords (owner only — any admin)."""
    err = require_auth()
    if err:
        return err
    body = request.get_json(silent=True)
    raw = ((body.get("yaml") or "") if isinstance(body, dict) else "").strip()
    if not raw:
        raw = (request.get_data(as_text=True) or "").strip()
    if not raw:
        return jsonify({"error": "empty body"}), 400
    try:
        groups = _yaml_to_groups(raw)
    except Exception as exc:
        return jsonify({"error": f"Invalid YAML: {exc}"}), 400
    kw_data = {"groups": groups}
    DEFAULT_KEYWORDS_JSON_PATH = PIPELINE_SCRIPTS_DIR / "default_keywords.json"
    DEFAULT_KEYWORDS_JSON_PATH.write_text(json.dumps(kw_data, indent=2, ensure_ascii=False), encoding="utf-8")
    DEFAULT_KEYWORDS_YAML_PATH.write_text(raw, encoding="utf-8")
    return jsonify({"ok": True, "groups": len(groups)})


@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/keywords/yaml", methods=["GET"])
def api_pipeline_keywords_yaml_get(pipeline_id: int):
    """Download this pipeline's keywords as YAML."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404
    config = pipeline.get("config") or {}
    groups = config.get("groups", [])
    if not groups:
        kw = _load_default_keywords()
        groups = kw.get("groups", [])
    text = _groups_to_yaml(groups)
    return Response(text, mimetype="text/yaml",
                    headers={"Content-Disposition": 'attachment; filename="keywords.yaml"'})


@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/keywords/yaml", methods=["POST"])
def api_pipeline_keywords_yaml_post(pipeline_id: int):
    """Upload YAML to replace this pipeline's keywords (owner only)."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404
    if pipeline["user_id"] != uid:
        return jsonify({"error": "not owner"}), 403
    body = request.get_json(silent=True)
    raw = ((body.get("yaml") or "") if isinstance(body, dict) else "").strip()
    if not raw:
        raw = (request.get_data(as_text=True) or "").strip()
    if not raw:
        return jsonify({"error": "empty body"}), 400
    try:
        groups = _yaml_to_groups(raw)
    except Exception as exc:
        return jsonify({"error": f"Invalid YAML: {exc}"}), 400
    config = dict(pipeline.get("config") or {})
    config["groups"] = groups
    ok = update_pipeline(pipeline_id, uid, config=config)
    if not ok:
        return jsonify({"error": "update failed"}), 500
    # Also rewrite keywords.json
    paths = _pipeline_paths(pipeline_id)
    kw_path = Path(paths["keywords_path"])
    kw_path.parent.mkdir(parents=True, exist_ok=True)
    kw_path.write_text(json.dumps({"groups": groups}, indent=2, ensure_ascii=False), encoding="utf-8")
    pipeline = get_pipeline(pipeline_id, uid)
    return jsonify({"ok": True, "groups": len(groups), "config": pipeline.get("config")})


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

    # Load defaults (always uses the new subgroup format)
    default_config = _load_default_keywords()
    provided = data.get("groups") or data.get("fuel_terms") or data.get("impact_terms")
    if provided and "groups" in data:
        groups = data["groups"]
    else:
        # Use defaults (subgroup format from default_keywords.json)
        groups = default_config.get("groups", [])

    config = {
        "groups": groups,
        "workers": int(data.get("workers", 4)),
        "min_free_gb": float(data.get("min_free_gb", 2.0)),
    }

    pipeline_id = create_pipeline(uid, name, description, config)

    # Set up per-pipeline paths and create the directory
    paths = _pipeline_paths(pipeline_id)
    Path(paths["base"]).mkdir(parents=True, exist_ok=True)

    # Write keywords.json for use by pipeline scripts
    keywords_path = Path(paths["keywords_path"])
    kw_data = {"groups": config["groups"]}
    keywords_path.write_text(json.dumps(kw_data, indent=2), encoding="utf-8")

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
        if "groups" in cfg or "fuel_terms" in cfg or "impact_terms" in cfg:
            existing = {}
            if keywords_path.exists():
                try:
                    existing = json.loads(keywords_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            if "groups" in cfg:
                existing = {"groups": cfg["groups"]}
            else:
                existing.update({k: cfg[k] for k in ("fuel_terms", "impact_terms") if k in cfg})
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

    # Kill any running jobs for this pipeline before deleting it
    jobs = current_app.config.get("JOBS")
    if jobs:
        for job in jobs.list_jobs():
            if job.get("pipeline_id") == pipeline_id and job.get("status") == "running":
                jobs.kill(job["id"])

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


# ── Go back to previous stage (owner only) ───────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/go-back", methods=["POST"])
def api_go_back_stage(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    prev_stage = go_back_pipeline_stage(pipeline_id, uid)
    if prev_stage is None:
        return jsonify({"error": "cannot go back: not owner, or already at first stage"}), 403
    pipeline = get_pipeline(pipeline_id, uid)
    return jsonify(_pipeline_response(pipeline))


# ── Jump to any stage (owner only) ───────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/set-stage", methods=["POST"])
def api_set_stage(pipeline_id: int):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    stage = (data.get("stage") or "").strip()
    result = set_pipeline_stage(pipeline_id, uid, stage)
    if result is None:
        return jsonify({"error": "invalid stage or not owner"}), 400
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

    status = build_stage_status(pipeline)
    return jsonify(status)


@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/file-count", methods=["GET"])
def api_file_count(pipeline_id: int):
    """Return total .gz file count from pipeline state (if available)."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404
    state_path = pipeline.get("state_file_path")
    total = None
    if state_path and Path(state_path).exists():
        try:
            import json as _json
            state = _json.loads(Path(state_path).read_text())
            total = len(state.get("all_files", []))
        except Exception:
            pass
    return jsonify({"total_files": total})



# ── Live batch status from OpenAI ─────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/batch-status", methods=["GET"])
def api_batch_status(pipeline_id: int):
    """Query OpenAI directly for the current batch status and persist it."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404

    batch_path = pipeline.get("batch_state_path")
    if not batch_path or not Path(batch_path).exists():
        return jsonify({"error": "no batch state"}), 404

    try:
        state = json.loads(Path(batch_path).read_text())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    batch_id = state.get("batch_id")
    if not batch_id:
        return jsonify({"error": "no batch_id in state"}), 404

    if not os.environ.get("OPENAI_API_KEY"):
        return jsonify({"error": "OPENAI_API_KEY not set"}), 500

    try:
        from openai import OpenAI
        client = OpenAI()
        batch = client.batches.retrieve(batch_id)
        counts = batch.request_counts
        state["status"] = batch.status
        state["request_counts"] = {
            "completed": counts.completed if counts else 0,
            "failed": counts.failed if counts else 0,
            "total": counts.total if counts else 0,
        }
        terminal = batch.status in {"completed", "failed", "expired", "cancelled"}
        if terminal:
            import datetime as _dt
            state["completed_at"] = _dt.datetime.now().isoformat()
            if batch.output_file_id:
                state["output_file_id"] = batch.output_file_id
            if batch.error_file_id:
                state["error_file_id"] = batch.error_file_id
        # Persist updated state
        Path(batch_path).write_text(json.dumps(state, indent=2))
        return jsonify(state)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Cancel batch on OpenAI ────────────────────────────────────────────────────

@pipelines_bp.route("/api/pipelines/<int:pipeline_id>/batch-cancel", methods=["POST"])
def api_batch_cancel(pipeline_id: int):
    """Send a cancellation request to OpenAI for the active batch."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "not found"}), 404
    if pipeline["user_id"] != uid:
        return jsonify({"error": "not owner"}), 403

    batch_path = pipeline.get("batch_state_path")
    if not batch_path or not Path(batch_path).exists():
        return jsonify({"error": "no batch state"}), 404

    try:
        state = json.loads(Path(batch_path).read_text())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    batch_id = state.get("batch_id")
    if not batch_id:
        return jsonify({"error": "no batch_id in state"}), 404

    if not os.environ.get("OPENAI_API_KEY"):
        return jsonify({"error": "OPENAI_API_KEY not set"}), 500

    try:
        from openai import OpenAI
        client = OpenAI()
        batch = client.batches.cancel(batch_id)
        state["status"] = batch.status
        Path(batch_path).write_text(json.dumps(state, indent=2))
        return jsonify({"ok": True, "status": batch.status})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
