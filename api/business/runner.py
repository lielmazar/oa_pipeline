"""
Filter-run runner — polling-based subprocess management.

Design decisions
----------------
* One system-wide active run at a time (enforced at start time).
* PID stored in meta DB → survives server restarts.
* Liveness checked with os.kill(pid, 0) — no in-memory state.
* Progress read from pipeline_state.json written by the pipeline script.
* Frontend polls GET /api/pipeline/{id}/run — this module owns the state.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from api.config import settings
from api.db.pipelines import (
    finish_run,
    get_active_run,
    get_run,
    list_runs,
    record_event,
    start_filter_run,
)


# ── Liveness ──────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ── State-file reading ────────────────────────────────────────────────────────

def _read_state(pipeline_id: int) -> dict[str, Any]:
    state_path = settings.pipelines_base_dir / str(pipeline_id) / "pipeline_state.json"
    try:
        return json.loads(state_path.read_text())
    except Exception:
        return {}


def _tail_log(pipeline_id: int, n: int = 40) -> list[str]:
    log_path = settings.pipelines_base_dir / str(pipeline_id) / "pipeline.log"
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


# ── Core operations ───────────────────────────────────────────────────────────

def start_filter_run_for(
    db: sqlite3.Connection,
    pipeline_id: int,
    pipeline_config: dict,
) -> dict:
    """
    Launch pipeline.py as a subprocess for the given pipeline.

    Raises ValueError if another run is already active system-wide.
    Returns the new run row as a dict.
    """
    # System-wide single-job constraint
    active = get_active_run(db)
    if active:
        # Re-check PID liveness — might be stale
        if active["pid"] and _pid_alive(active["pid"]):
            raise ValueError(
                f"A filter run is already active (run_id={active['id']}, "
                f"pipeline={active['pipeline_id']})"
            )
        # Dead process — mark it finished so we can proceed
        finish_run(db, active["id"], "failed")
        record_event(db, active["pipeline_id"], "run_auto_closed",
                     f"Stale run {active['id']} closed at new-run start")

    script = settings.pipeline_scripts_dir / "pipeline.py"
    gz_dir = settings.gz_files_base_dir
    out_dir = settings.pipelines_base_dir / str(pipeline_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    groups = pipeline_config.get("groups", [])

    # Write keyword config to file so pipeline.py can read it.
    # Format: {"groups": [...]} — the same structure stored in pipeline.config.groups.
    keyword_config_path = out_dir / "keywords_run.json"
    keyword_config_path.write_text(json.dumps({"groups": groups}))

    cmd = [
        sys.executable, str(script),
        "run",
        "--local-dir",  str(gz_dir),
        "--output-db",  str(out_dir / "output.sqlite"),
        "--state-file", str(out_dir / "pipeline_state.json"),
        "--log-file",   str(out_dir / "pipeline.log"),
        "--workers",    str(settings.filtering_workers),
        "--no-color",
        "--keyword-config", str(keyword_config_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    run_id = start_filter_run(db, pipeline_id, proc.pid)
    record_event(db, pipeline_id, "run_started", f"run_id={run_id} pid={proc.pid}")
    return get_run(db, run_id)


def poll_filter_run(db: sqlite3.Connection, pipeline_id: int) -> dict:
    """
    Return current run status for *pipeline_id*.

    Checks PID liveness; auto-closes stale runs.
    Reads pipeline_state.json for progress details.
    """
    runs = list_runs(db, pipeline_id)
    if not runs:
        return {"status": "idle", "run": None, "state": {}, "log_tail": []}

    latest = runs[0]
    run_id = latest["id"]

    # If already terminal, just return it
    if latest["ended_at"] is not None:
        return {
            "status": latest["status"],
            "run": dict(latest),
            "state": _read_state(pipeline_id),
            "log_tail": _tail_log(pipeline_id),
        }

    # Active run — check liveness
    pid = latest["pid"]
    if pid and not _pid_alive(pid):
        finish_run(db, run_id, "done")
        record_event(db, pipeline_id, "run_finished", f"run_id={run_id}")
        latest = get_run(db, run_id)

    return {
        "status": latest["status"],
        "run": dict(latest),
        "state": _read_state(pipeline_id),
        "log_tail": _tail_log(pipeline_id),
    }


def kill_filter_run(db: sqlite3.Connection, pipeline_id: int) -> dict:
    """
    SIGTERM the active run for *pipeline_id*, if any.
    Returns updated poll result.
    """
    runs = list_runs(db, pipeline_id)
    if not runs or runs[0]["ended_at"] is not None:
        return poll_filter_run(db, pipeline_id)

    latest = runs[0]
    pid = latest["pid"]
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    finish_run(db, latest["id"], "killed")
    record_event(db, pipeline_id, "run_killed", f"run_id={latest['id']} pid={pid}")
    return poll_filter_run(db, pipeline_id)
