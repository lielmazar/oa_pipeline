"""Orthogonality blueprint — /api/orthogonality/*

Endpoints for taxonomy orthogonality analysis:
  - Cramér's V matrix
  - Conditional entropy / mutual information
  - Within-dimension Jaccard heatmap
  - Tautological pair detection
  - PMI analysis
  - Data quality audit
  - Exemplar paper extraction
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from blueprints.auth import require_auth, get_current_user_id
from models.pipeline import get_pipeline

ortho_bp = Blueprint("orthogonality", __name__)


def _pipeline_db(pipeline_id: int, uid: int) -> tuple[Path | None, str | None]:
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return None, "pipeline not found"
    p = pipeline.get("output_db_path")
    if not p:
        return None, "pipeline has no database yet"
    path = Path(p)
    if not path.exists():
        return None, "database file not found"
    return path, None


@ortho_bp.route("/api/orthogonality/cramers-v/<int:pipeline_id>")
def api_cramers_v(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    ml_only = request.args.get("ml_only", "false").lower() == "true"

    from core.orthogonality import cramers_v_matrix
    result = cramers_v_matrix(db_path, ml_only=ml_only)
    return jsonify(result)


@ortho_bp.route("/api/orthogonality/entropy/<int:pipeline_id>")
def api_conditional_entropy(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    ml_only = request.args.get("ml_only", "true").lower() != "false"

    from core.orthogonality import conditional_entropy_analysis
    result = conditional_entropy_analysis(db_path, ml_only=ml_only)
    return jsonify(result)


@ortho_bp.route("/api/orthogonality/jaccard/<int:pipeline_id>")
def api_jaccard(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    dimension = request.args.get("dimension", "ml_role")
    ml_only = request.args.get("ml_only", "true").lower() != "false"

    from core.orthogonality import jaccard_within_dimension
    result = jaccard_within_dimension(db_path, dimension=dimension, ml_only=ml_only)
    return jsonify(result)


@ortho_bp.route("/api/orthogonality/tautological/<int:pipeline_id>")
def api_tautological(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    from core.orthogonality import tautological_pairs
    result = tautological_pairs(db_path, ml_only=True)
    return jsonify(result)


@ortho_bp.route("/api/orthogonality/pmi/<int:pipeline_id>")
def api_pmi(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    min_count = request.args.get("min_count", 5, type=int)

    from core.orthogonality import pmi_analysis
    result = pmi_analysis(db_path, ml_only=True, min_count=min_count)
    return jsonify(result)


@ortho_bp.route("/api/orthogonality/audit/<int:pipeline_id>")
def api_audit(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    from core.orthogonality import data_quality_audit
    result = data_quality_audit(db_path)
    return jsonify(result)


@ortho_bp.route("/api/orthogonality/exemplars/<int:pipeline_id>")
def api_exemplars(pipeline_id: int):
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    min_papers = request.args.get("min_papers", 3, type=int)

    from core.orthogonality import exemplar_papers
    result = exemplar_papers(db_path, min_papers=min_papers)
    return jsonify(result)
