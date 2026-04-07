"""User model — create, authenticate, list, delete."""
from __future__ import annotations

import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return check_password_hash(hashed, plain)


def create_user(db: sqlite3.Connection, username: str, password: str) -> int:
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, hash_password(password)),
    )
    db.commit()
    return cur.lastrowid


def get_user_by_username(db: sqlite3.Connection, username: str) -> dict | None:
    row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(db: sqlite3.Connection, user_id: int) -> dict | None:
    row = db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def list_users(db: sqlite3.Connection) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            "SELECT id, username, created_at FROM users ORDER BY id"
        ).fetchall()
    ]


def delete_user(db: sqlite3.Connection, username: str) -> bool:
    cur = db.execute("DELETE FROM users WHERE username = ?", (username,))
    db.commit()
    return cur.rowcount > 0
