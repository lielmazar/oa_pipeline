"""Jobs blueprint — /api/jobs/*"""
from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from blueprints.auth import get_current_user_id, require_auth
from core.config import SCRIPTS, PIPELINE_SCRIPTS_DIR
from models.pipeline import get_pipeline, record_pipeline_job

jobs_bp = Blueprint("jobs", __name__)


def _jobs() -> object:
    return current_app.config["JOBS"]


def _build_args(script_key: str, raw_args: dict, pipeline: dict | None) -> list:
    """
    Convert a flat dict of {flag: value} pairs into a CLI arg list.

    - Keys starting with '--' become named flags.
    - Keys NOT starting with '--' and with value True become positional args
      (e.g. subcommands like 'estimate', 'run').  They are placed first.
    - Empty / False / None values are skipped.
    - Auto-appends --no-color when the script supports it.
    """
    positional = []
    named = []
    for flag, value in raw_args.items():
        if value is None or value == "" or value is False:
            continue
        if not str(flag).startswith("--"):
            # Positional / subcommand
            if value is True:
                positional.append(str(flag))
            # else skip (shouldn't happen)
        else:
            named.append(str(flag))
            if value is not True:
                named.append(str(value))

    script_cfg = SCRIPTS.get(script_key, {})
    no_color = [script_cfg["no_color_flag"]] if script_cfg.get("no_color_flag") else []

    return named + positional + no_color


# ── List jobs ─────────────────────────────────────────────────────────────────

@jobs_bp.route("/api/jobs", methods=["GET"])
def api_list_jobs():
    err = require_auth()
    if err:
        return err
    pipeline_id = request.args.get("pipeline_id", type=int)
    all_jobs = _jobs().list_jobs()
    if pipeline_id is not None:
        all_jobs = [j for j in all_jobs if j.get("pipeline_id") == pipeline_id]
    return jsonify(all_jobs)


# ── Launch job ────────────────────────────────────────────────────────────────

@jobs_bp.route("/api/jobs", methods=["POST"])
def api_launch_job():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}

    script_key = data.get("script")
    pipeline_id = data.get("pipeline_id")
    raw_args = data.get("args", {})

    if script_key not in SCRIPTS:
        return jsonify({"error": f"unknown script: {script_key}"}), 400

    # If pipeline_id provided, validate ownership and get pipeline config
    pipeline = None
    if pipeline_id is not None:
        pipeline = get_pipeline(int(pipeline_id), uid)
        if not pipeline:
            return jsonify({"error": "pipeline not found"}), 404
        if pipeline["user_id"] != uid:
            return jsonify({"error": "not pipeline owner"}), 403

    script_cfg = SCRIPTS[script_key]
    script_path = script_cfg["path"]

    # For run_pipeline / filter_dataset, auto-inject --keyword-config path
    if pipeline and script_key in ('run_pipeline', 'filter_dataset'):
        db_path = pipeline.get("output_db_path")
        if db_path:
            keywords_path = str(Path(db_path).parent / "keywords.json")
            if Path(keywords_path).exists():
                raw_args.setdefault("--keyword-config", keywords_path)

    args = _build_args(script_key, raw_args, pipeline)
    label = f"{script_cfg['label']} " + " ".join(
        str(v) for v in raw_args.values() if v and v is not True
    )

    # Extract worker count for display in the job monitor
    try:
        workers = int(raw_args.get("--workers", 1))
    except (TypeError, ValueError):
        workers = 1

    try:
        job_summary = _jobs().launch(
            script_key=script_key,
            script_path=script_path,
            args=args,
            cwd=str(PIPELINE_SCRIPTS_DIR),
            label=label.strip(),
            pipeline_id=pipeline_id,
            workers=workers,
        )
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 409

    # Record job association in metadata DB
    if pipeline_id is not None:
        stage = pipeline.get("current_stage", "unknown")
        record_pipeline_job(pipeline_id, job_summary["id"], stage)

    return jsonify(job_summary), 201


# ── Get single job ────────────────────────────────────────────────────────────

@jobs_bp.route("/api/jobs/<job_id>", methods=["GET"])
def api_get_job(job_id: str):
    err = require_auth()
    if err:
        return err
    job = _jobs().get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(_jobs()._summary(job))


# ── Kill job ──────────────────────────────────────────────────────────────────

@jobs_bp.route("/api/jobs/<job_id>", methods=["DELETE"])
def api_kill_job(job_id: str):
    err = require_auth()
    if err:
        return err
    ok = _jobs().kill(job_id)
    if not ok:
        return jsonify({"error": "job not found or not running"}), 404
    return jsonify({"ok": True})


# ── SSE stream ────────────────────────────────────────────────────────────────

@jobs_bp.route("/api/jobs/<job_id>/stream")
def api_stream_job(job_id: str):
    err = require_auth()
    if err:
        return err
    try:
        after_seq = int(request.args.get("after", -1))
    except ValueError:
        after_seq = -1

    job = _jobs().get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404

    def _generate():
        for seq, line in _jobs().stream_output(job_id, after_seq):
            yield f"event: line\ndata: {{'seq': {seq}, 'text': {repr(line)}}}\n\n"

        final = _jobs().get_job(job_id)
        if final:
            summary = _jobs()._summary(final)
            elapsed = (
                round(summary["ended_at"] - summary["started_at"], 1)
                if summary.get("ended_at")
                else None
            )
            yield (
                f"event: done\ndata: {{'status': '{summary['status']}', "
                f"'returncode': {summary['returncode']}, "
                f"'elapsed': {elapsed}}}\n\n"
            )

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
