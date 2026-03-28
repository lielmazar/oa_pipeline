"""Authentication blueprint — /api/auth/*"""
from flask import Blueprint, jsonify, request, session

from models.user import get_user_by_username, verify_password

auth_bp = Blueprint("auth", __name__)


def get_current_user_id() -> int | None:
    return session.get("user_id")


def get_current_username() -> str | None:
    return session.get("username")


def require_auth():
    """Return a 401 response if the request is not authenticated, else None."""
    if not get_current_user_id():
        return jsonify({"error": "unauthorized"}), 401
    return None


@auth_bp.before_app_request
def _guard():
    """Block unauthenticated API requests (except login)."""
    if request.path.startswith("/static/"):
        return
    if request.path in ("/api/auth/login",):
        return
    if request.path == "/":
        return
    if not get_current_user_id() and request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized"}), 401


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = get_user_by_username(username)
    if not user or not verify_password(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"ok": True, "username": user["username"]})


@auth_bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/me")
def api_me():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"user_id": uid, "username": get_current_username()})
