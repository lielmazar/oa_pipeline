"""
Shared FastAPI dependencies.

Usage in routers:
    from api.deps import CurrentUser, Db

    @router.get("/something")
    def handler(user: CurrentUser, db: Db):
        ...
"""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from api.config import settings
from api.db.meta import db_dep
from api.db.users import get_user_by_id

# ── Signed-cookie session ─────────────────────────────────────────────────────

_signer = URLSafeTimedSerializer(settings.secret_key, salt="session")


def make_session_cookie(user_id: int, username: str) -> str:
    return _signer.dumps({"user_id": user_id, "username": username})


def read_session_cookie(token: str) -> dict | None:
    try:
        return _signer.loads(token, max_age=settings.session_max_age_days * 86400)
    except (BadSignature, SignatureExpired):
        return None


# ── Auth dependency ───────────────────────────────────────────────────────────

def _session_data(
    session: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
) -> dict:
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    data = read_session_cookie(session)
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return data


def current_user(
    session_data: Annotated[dict, Depends(_session_data)],
    db: Annotated[sqlite3.Connection, Depends(db_dep)],
) -> dict:
    """Returns the authenticated user dict. Raises 401 if not authenticated."""
    user = get_user_by_id(db, session_data["user_id"])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return user


# ── Convenience type aliases (use with Annotated in function signatures) ──────

Db = Annotated[sqlite3.Connection, Depends(db_dep)]
CurrentUser = Annotated[dict, Depends(current_user)]
