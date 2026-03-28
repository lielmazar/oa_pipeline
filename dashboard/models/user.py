"""User model — create, authenticate, list."""
from werkzeug.security import generate_password_hash, check_password_hash

from models.db import get_meta_db


def create_user(username: str, password: str) -> int:
    """Create a new user. Returns the new user id."""
    db = get_meta_db()
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, generate_password_hash(password)),
    )
    db.commit()
    return cur.lastrowid


def verify_password(stored_hash: str, password: str) -> bool:
    return check_password_hash(stored_hash, password)


def get_user_by_username(username: str) -> dict | None:
    db = get_meta_db()
    row = db.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    db = get_meta_db()
    row = db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    db = get_meta_db()
    return [
        dict(r)
        for r in db.execute(
            "SELECT id, username, created_at FROM users ORDER BY id"
        ).fetchall()
    ]


def delete_user(username: str) -> bool:
    db = get_meta_db()
    cur = db.execute("DELETE FROM users WHERE username = ?", (username,))
    db.commit()
    return cur.rowcount > 0
