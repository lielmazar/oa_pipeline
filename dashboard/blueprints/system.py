"""System info blueprint — /api/system/*"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify

from blueprints.auth import require_auth

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/system/info", methods=["GET"])
def api_system_info():
    err = require_auth()
    if err:
        return err
    cpu_count = os.cpu_count() or 1
    return jsonify({"cpu_count": cpu_count})
