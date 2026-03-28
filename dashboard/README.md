# Dashboard

> The main project README at `../README.md` has full setup, deployment, and usage instructions.
> This file covers dashboard-specific internals.

---

## Running locally (Windows)

```powershell
# From the repo root:
cd code\dashboard

# Create venv (one-time)
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# Set environment variables
$env:PIPELINES_BASE_DIR = "$env:LOCALAPPDATA\oa-pipeline\pipelines"
$env:META_DB_PATH       = "$env:LOCALAPPDATA\oa-pipeline\dashboard.db"
$env:SECRET_KEY         = "local-dev-secret"
$env:FLASK_APP          = "app:create_app()"
$env:FLASK_DEBUG        = "1"

# Run
venv\Scripts\python -m flask run
```

Then in a second terminal:
```powershell
cd code\dashboard
venv\Scripts\python manage.py create-user admin
```

Open `http://localhost:5000/`.

> `gunicorn` and `start.sh` are Linux-only. Use `flask run` on Windows.

---

## Architecture

```
Browser
   │
   ▼  HTTP / SSE
nginx :80              ← reverse proxy
   │
   ▼
gunicorn :5000         ← -w 1 -k gthread --threads 16
   │
   ├── blueprints/auth.py       Session-cookie auth (/api/auth/*)
   ├── blueprints/pipelines.py  Pipeline CRUD + stage advance + fork
   ├── blueprints/jobs.py       Subprocess launch / stream / kill
   ├── blueprints/data.py       DB browser, SQL query, report, log tail
   ├── models/db.py             Metadata SQLite (users, pipelines, jobs)
   ├── models/user.py           User model
   ├── models/pipeline.py       Pipeline model + stage logic
   └── core/job_manager.py      Popen + threading.Condition + SSE deque
```

**Why single gunicorn worker?** `JobManager` is an in-memory dict of running subprocesses. Multiple workers would each have a separate dict, making it impossible to stream output from a job started by a different worker. `-w 1 --threads 16` gives concurrency through threads without splitting in-memory state.

**Why SSE, not WebSockets?** `EventSource` is built into every browser with no JS library needed. It also auto-reconnects on disconnect.

**Why session cookies, not token auth?** The browser's `EventSource` API cannot set custom headers, so `Authorization: Bearer ...` doesn't work for the SSE stream endpoint. Session cookies are sent automatically.

---

## Working directory for scripts

Pipeline scripts are launched by `job_manager.py` with:
- `cmd = ["python3", "-u", "<absolute-path-to-script>", ...args]`
- `cwd = PIPELINE_SCRIPTS_DIR`  (i.e. `code/pipeline/` on the server: `/opt/oa-pipeline/pipeline/`)

The scripts receive absolute paths for `--output-db`, `--state-file`, `--log-file` etc., so the working directory only matters for imports inside the scripts themselves.

---

## Metadata database schema (dashboard.db)

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pipelines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    forked_from_id   INTEGER REFERENCES pipelines(id),
    name             TEXT NOT NULL,
    description      TEXT DEFAULT '',
    current_stage    TEXT NOT NULL DEFAULT 'keyword_selection',
    config           TEXT NOT NULL DEFAULT '{}',  -- JSON
    output_db_path   TEXT,
    state_file_path  TEXT,
    batch_state_path TEXT,
    report_path      TEXT,
    llm_report_path  TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pipeline_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id),
    job_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## API reference

### Auth

| Method | Path | Body / Params | Description |
|--------|------|---------------|-------------|
| POST | `/api/auth/login` | `{username, password}` | Set session cookie |
| POST | `/api/auth/logout` | — | Clear session |
| GET | `/api/auth/me` | — | Current user info |

### Pipelines

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pipelines` | List all pipelines (all users visible) |
| POST | `/api/pipelines` | Create pipeline `{name, description?, fuel_terms?, impact_terms?, workers?, min_free_gb?}` |
| GET | `/api/pipelines/<id>` | Get single pipeline |
| PATCH | `/api/pipelines/<id>` | Update `{name?, description?, config?}` — owner only |
| DELETE | `/api/pipelines/<id>` | Delete — owner only |
| POST | `/api/pipelines/<id>/advance` | Advance to next stage — owner only |
| POST | `/api/pipelines/<id>/fork` | Fork to caller's account `{name}` |
| GET | `/api/pipelines/<id>/status` | Stage-specific status (DB stats, job state, report existence) |

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/jobs?pipeline_id=<id>` | List jobs (optionally filtered) |
| POST | `/api/jobs` | Launch `{script, pipeline_id, args: {flag: value}}` |
| GET | `/api/jobs/<id>` | Get job summary |
| DELETE | `/api/jobs/<id>` | Kill job |
| GET | `/api/jobs/<id>/stream` | SSE stream of stdout/stderr |

### Data

All data endpoints require `?pipeline_id=<id>`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/schema` | Table schema |
| GET | `/api/db/records` | Paginated records `?page=1&q=search&sort=col&dir=asc&page_size=100` |
| GET | `/api/db/records/<id>` | Single record detail |
| POST | `/api/db/query` | Run SQL `{sql, pipeline_id}` |
| GET | `/api/report?llm=0|1` | Serve report HTML |
| GET | `/api/log?lines=200` | Last N lines of pipeline.log |

---

## Troubleshooting

See the [main README troubleshooting section](../README.md#troubleshooting) for common issues.

### Local dev: PIPELINES_BASE_DIR doesn't exist

The dashboard creates pipeline subdirectories automatically on pipeline creation, but the base directory must exist first:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\oa-pipeline\pipelines"
```

### Import errors when running manage.py

`manage.py` must be run from inside `code/dashboard/` so Python can find the `blueprints`, `models`, and `core` packages:

```powershell
cd code\dashboard
venv\Scripts\python manage.py list-users  # correct
venv\Scripts\python dashboard\manage.py   # wrong — imports will fail
```
