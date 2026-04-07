"""
Metadata SQLite — schema, connection helpers, FastAPI dependency.

Stores users, pipelines, filter_runs, pipeline_events, qa_ratings, qa_annotations,
pipeline_subsets.  Separate from the research data DB produced by pipeline scripts.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from api.config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipelines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    forked_from_id   INTEGER REFERENCES pipelines(id),
    name             TEXT NOT NULL,
    description      TEXT DEFAULT '',
    current_stage    TEXT NOT NULL DEFAULT 'keyword_selection',
    config           TEXT NOT NULL DEFAULT '{}',
    output_db_path   TEXT,
    state_file_path  TEXT,
    batch_state_path TEXT,
    report_path      TEXT,
    llm_report_path  TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filter_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    pid         INTEGER,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at    TIMESTAMP,
    status      TEXT NOT NULL DEFAULT 'running'  -- running | done | failed | killed
);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    event       TEXT NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qa_ratings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    openalex_id TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    field       TEXT NOT NULL,
    llm_value   TEXT,
    human_value TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pipeline_id, openalex_id, user_id, field)
);

CREATE TABLE IF NOT EXISTS qa_annotations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id  INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    openalex_id  TEXT NOT NULL,
    annotator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dimension    TEXT NOT NULL,
    human_value  TEXT NOT NULL,
    llm_value    TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pipeline_id, openalex_id, annotator_id, dimension)
);

CREATE TABLE IF NOT EXISTS pipeline_subsets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sql_query   TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables on first run. Safe to call every startup."""
    settings.meta_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open(settings.meta_db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context-manager connection — use as a FastAPI dependency or direct call."""
    conn = _open(settings.meta_db_path)
    try:
        yield conn
    finally:
        conn.close()


# FastAPI dependency — yields a connection, closed after the request.
def db_dep() -> Generator[sqlite3.Connection, None, None]:
    with get_db() as conn:
        yield conn
