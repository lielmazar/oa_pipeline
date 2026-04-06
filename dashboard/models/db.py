"""
Metadata SQLite connection and schema initialisation.

The metadata DB (dashboard.db) stores users, pipelines, and job associations.
It is separate from the research data SQLite produced by the pipeline scripts.
"""
import sqlite3
from pathlib import Path

from flask import g, current_app

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

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    job_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    openalex_id TEXT NOT NULL,
    annotator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dimension   TEXT NOT NULL,
    human_value TEXT NOT NULL,
    llm_value   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pipeline_id, openalex_id, annotator_id, dimension)
);
"""


def get_meta_db() -> sqlite3.Connection:
    """Return a request-scoped connection to dashboard.db."""
    if "meta_db" not in g:
        path = current_app.config["META_DB_PATH"]
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        g.meta_db = conn
    return g.meta_db


def open_meta_db(path: Path) -> sqlite3.Connection:
    """Open a direct (non-request-scoped) connection — for background threads."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_meta_db(app) -> None:
    """Create tables on first run. Safe to call on every startup."""
    with app.app_context():
        db = get_meta_db()
        db.executescript(SCHEMA_SQL)
        db.commit()


def close_meta_db(e=None) -> None:
    """Teardown appcontext hook."""
    db = g.pop("meta_db", None)
    if db is not None:
        db.close()
