"""Authentication — POST /api/auth/login|logout, GET /api/auth/me"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from api.config import settings
from api.db.users import get_user_by_username, verify_password
from api.deps import CurrentUser, Db, make_session_cookie

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE = settings.session_cookie_name
_MAX_AGE = settings.session_max_age_days * 86400


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Db):
    user = get_user_by_username(db, body.username.strip())
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    token = make_session_cookie(user["id"], user["username"])
    response.set_cookie(
        key=_COOKIE, value=token, max_age=_MAX_AGE,
        httponly=True, samesite="lax", secure=False,
    )
    return {"ok": True, "username": user["username"]}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser):
    return {"user_id": user["id"], "username": user["username"]}
