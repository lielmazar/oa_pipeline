"""
OpenAlex Dashboard — Flask application.

Run directly:  python3 app.py
Production:    gunicorn -w 1 -k gthread --threads 16 --timeout 0 -b 127.0.0.1:5000 app:app

Environment variables:
  DASHBOARD_PASSWORD  — if set, enables login page (leave unset for open access)
  SECRET_KEY          — Flask session signing key (required for auth)
"""
import json
import os
import shutil
import time
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)

from config import (
    AUTH_PASSWORD_ENV,
    BASE_DIR,
    BATCH_STATE_PATH,
    DB_PAGE_SIZE,
    DB_PATH,
    DB_QUERY_MAX_ROWS,
    PIPELINE_LOG_PATH,
    PIPELINE_STATE_PATH,
    POSTPROCESS_LOG_PATH,
    REPORT_HTML_PATH,
    SCRIPTS,
    SECRET_KEY_ENV,
)

# ── Multi-DB helper ────────────────────────────────────────────────────────────

def _resolve_db(name: str | None = None) -> Path:
    """
    Return a validated Path for the requested database.

    `name` is a relative path from BASE_DIR (e.g. "results.sqlite" or
    "2/openalex_works_filtered.sqlite").  At most one directory level is
    allowed; '..' is rejected.  Falls back to DB_PATH when name is empty.
    """
    if not name:
        return DB_PATH
    parts = Path(name).parts
    if len(parts) > 2 or ".." in parts or not name.endswith(".sqlite"):
        return DB_PATH
    resolved = BASE_DIR / name
    if not resolved.exists():
        raise FileNotFoundError(f"Database not found: {name}")
    return resolved


def _db_from_request() -> Path:
    """Extract and validate the ?db= query param from the current request."""
    try:
        return _resolve_db(request.args.get("db", ""))
    except FileNotFoundError as exc:
        raise exc
from db_api import get_db_stats, get_record_detail, get_records, get_schema, run_query
from job_manager import JobManager

app = Flask(__name__)
app.secret_key = os.environ.get(SECRET_KEY_ENV, "dev-secret-fallback-change-me")

_jobs = JobManager()


# ── Auth ───────────────────────────────────────────────────────────────────────

def _password() -> str:
    return os.environ.get(AUTH_PASSWORD_ENV, "")


def _authenticated() -> bool:
    if not _password():
        return True
    return session.get("authenticated") is True


@app.before_request
def _guard():
    if request.path.startswith("/static/"):
        return
    if request.path == "/api/login":
        return
    if not _authenticated():
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        # HTML requests: still serve index.html (login overlay will handle it)


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    pw = _password()
    if not pw or data.get("password") == pw:
        session["authenticated"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid password"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ── Main page ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", authenticated=_authenticated())


# ── Status ─────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        db_path = _db_from_request()
    except FileNotFoundError:
        db_path = DB_PATH
    db_stats = get_db_stats(db_path)

    pipeline_state: dict = {}
    if PIPELINE_STATE_PATH.exists():
        try:
            pipeline_state = json.loads(PIPELINE_STATE_PATH.read_text())
        except Exception:
            pass

    batch_state: dict = {}
    if BATCH_STATE_PATH.exists():
        try:
            batch_state = json.loads(BATCH_STATE_PATH.read_text())
        except Exception:
            pass

    disk_free_gb = None
    try:
        usage = shutil.disk_usage(str(BASE_DIR))
        disk_free_gb = round(usage.free / (1024 ** 3), 1)
    except Exception:
        pass

    active = [j for j in _jobs.list_jobs() if j["status"] == "running"]

    return jsonify({
        "db": db_stats,
        "pipeline_state": pipeline_state,
        "batch_state": batch_state,
        "disk_free_gb": disk_free_gb,
        "active_jobs": len(active),
        "jobs": _jobs.list_jobs(),
    })


# ── Jobs ───────────────────────────────────────────────────────────────────────

def _build_args(script_key: str, raw: dict) -> list:
    """Convert an args dict from the frontend into a flat CLI argument list."""
    args: list = []

    # Subcommand comes first (used by batch_process_papers.py)
    subcommand = raw.pop("subcommand", None)
    if subcommand:
        args.append(str(subcommand))

    for key, value in raw.items():
        if not key.startswith("--"):
            continue
        val = str(value).strip() if value is not None else ""
        if val in ("", "false"):
            continue
        args.append(key)
        if val != "true":
            args.append(val)

    # Auto-append --no-color for scripts that support it
    flag = SCRIPTS.get(script_key, {}).get("no_color_flag")
    if flag and flag not in args:
        args.append(flag)

    return args


@app.route("/api/jobs", methods=["GET"])
def api_jobs_list():
    return jsonify(_jobs.list_jobs())


@app.route("/api/jobs", methods=["POST"])
def api_jobs_create():
    data = request.get_json(silent=True) or {}
    script_key = data.get("script", "")
    raw_args = dict(data.get("args", {}))

    if script_key not in SCRIPTS:
        return jsonify({"error": f"Unknown script: {script_key!r}"}), 400

    info = SCRIPTS[script_key]
    try:
        args = _build_args(script_key, raw_args)
        label = f"{info['label']} {' '.join(args)}".strip()
        job = _jobs.launch(script_key, info["path"], args, str(BASE_DIR), label)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(job), 201


@app.route("/api/jobs/<job_id>", methods=["GET"])
def api_job_get(job_id: str):
    job = _jobs.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_jobs._summary(job))


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def api_job_kill(job_id: str):
    if _jobs.kill(job_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Job not found or not running"}), 404


@app.route("/api/jobs/<job_id>/stream")
def api_job_stream(job_id: str):
    after_seq = int(request.args.get("after", -1))
    if not _jobs.get_job(job_id):
        return jsonify({"error": "Job not found"}), 404

    def generate():
        try:
            for seq, line in _jobs.stream_output(job_id, after_seq):
                payload = json.dumps({"seq": seq, "text": line})
                yield f"id: {seq}\nevent: line\ndata: {payload}\n\n"

            j = _jobs.get_job(job_id)
            if j:
                elapsed = None
                if j.get("ended_at") and j.get("started_at"):
                    elapsed = round(j["ended_at"] - j["started_at"], 1)
                final = json.dumps({
                    "status": j["status"],
                    "returncode": j.get("returncode"),
                    "elapsed": elapsed,
                })
                yield f"event: done\ndata: {final}\n\n"
        except GeneratorExit:
            pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── State files ────────────────────────────────────────────────────────────────

@app.route("/api/state/pipeline")
def api_state_pipeline():
    if not PIPELINE_STATE_PATH.exists():
        return jsonify({"error": "pipeline_state.json not found"}), 404
    try:
        return jsonify(json.loads(PIPELINE_STATE_PATH.read_text()))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/state/batch")
def api_state_batch():
    if not BATCH_STATE_PATH.exists():
        return jsonify({"error": "batch state not found"}), 404
    try:
        return jsonify(json.loads(BATCH_STATE_PATH.read_text()))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Logs ───────────────────────────────────────────────────────────────────────

def _tail(path: Path, n: int) -> list:
    """Read last n lines of a (potentially large) file without loading it all."""
    if not path.exists():
        return []
    size = path.stat().st_size
    if size == 0:
        return []

    buf = b""
    chunk = 8192
    pos = size

    with open(path, "rb") as f:
        while len(buf.split(b"\n")) <= n + 1 and pos > 0:
            read_size = min(chunk, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf

    lines = buf.decode("utf-8", errors="replace").splitlines()
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines[-n:] if len(lines) > n else lines


@app.route("/api/log")
def api_log():
    log_name = request.args.get("log", "pipeline")
    n = min(int(request.args.get("lines", 200)), 2000)

    paths = {
        "pipeline": PIPELINE_LOG_PATH,
        "postprocess": POSTPROCESS_LOG_PATH,
    }
    path = paths.get(log_name)
    if not path:
        return jsonify({"error": "Unknown log file"}), 400

    lines = _tail(path, n)
    total_bytes = path.stat().st_size if path.exists() else 0
    return jsonify({"lines": lines, "total_bytes": total_bytes, "log": log_name})


# ── Database ───────────────────────────────────────────────────────────────────

@app.route("/api/db/list")
def api_db_list():
    """Return all .sqlite files at the root and one level deep in BASE_DIR."""
    found = []
    for depth, pattern in enumerate(["*.sqlite", "*/*.sqlite"]):
        for p in sorted(BASE_DIR.glob(pattern)):
            rel = str(p.relative_to(BASE_DIR))
            found.append({
                "name": p.name,
                "rel_path": rel,
                "size_mb": round(p.stat().st_size / (1024 * 1024), 1),
            })
    return jsonify(found)


@app.route("/api/db/schema")
def api_db_schema():
    try:
        return jsonify(get_schema(_db_from_request()))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/db/records")
def api_db_records():
    try:
        db = _db_from_request()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(500, max(10, int(request.args.get("per_page", DB_PAGE_SIZE))))
    sort = request.args.get("sort", "publication_year")
    direction = request.args.get("dir", "desc")
    search = request.args.get("q", "")
    return jsonify(get_records(db, page, per_page, sort, direction, search))


@app.route("/api/db/records/<path:openalex_id>")
def api_db_record_detail(openalex_id: str):
    try:
        db = _db_from_request()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    if not openalex_id.startswith("http"):
        openalex_id = f"https://openalex.org/{openalex_id}"
    return jsonify(get_record_detail(db, openalex_id))


@app.route("/api/db/query", methods=["POST"])
def api_db_query():
    try:
        db = _db_from_request()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    data = request.get_json(silent=True) or {}
    sql = data.get("sql", "").strip()
    if not sql:
        return jsonify({"error": "No SQL provided"}), 400
    return jsonify(run_query(db, sql, DB_QUERY_MAX_ROWS))


# ── Report ─────────────────────────────────────────────────────────────────────

@app.route("/api/report")
def api_report():
    if not REPORT_HTML_PATH.exists():
        return (
            "<html><body style='font-family:sans-serif;padding:2rem'>"
            "<h2>Report not yet generated</h2>"
            "<p>Go to the <b>Scripts</b> tab and run <code>generate_report.py</code>.</p>"
            "</body></html>",
            404,
            {"Content-Type": "text/html"},
        )
    return send_file(str(REPORT_HTML_PATH), mimetype="text/html")


# ── Dev entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000, host="127.0.0.1")
