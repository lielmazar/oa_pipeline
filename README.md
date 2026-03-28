# OpenAlex Sustainable Fuels Pipeline

Filter the entire [OpenAlex](https://openalex.org/) works dataset (~470 M records) down to papers that discuss **sustainable fuels** alongside **environmental or performance impacts**. Outputs a SQLite database with full metadata and keyword provenance, with optional LLM classification via the OpenAI Batch API. Includes a multi-user web dashboard to manage pipeline runs from a browser.

---

## Repository structure

```
code/
├── pipeline/                        # Data processing scripts
│   ├── filter_dataset.py            # Core filter: terms, stemming, matching, DB schema
│   ├── run_pipeline.py              # Orchestrator: parallelism, progress TUI, state mgmt
│   ├── llm_classify.py              # LLM batch classification via OpenAI Batch API
│   ├── generate_report.py           # HTML report generator
│   └── default_keywords.json        # Default fuel (59) + impact (48) keyword lists
├── dashboard/                       # Flask web dashboard
│   ├── app.py                       # App factory — create_app()
│   ├── manage.py                    # Admin CLI: create-user, list-users, delete-user
│   ├── requirements.txt             # Flask, gunicorn, werkzeug
│   ├── start.sh                     # Convenience wrapper for gunicorn
│   ├── blueprints/
│   │   ├── auth.py                  # /api/auth/* — login, logout, me
│   │   ├── pipelines.py             # /api/pipelines/* — CRUD + stage advance + fork
│   │   ├── jobs.py                  # /api/jobs/* — launch, stream, kill
│   │   └── data.py                  # /api/db/*, /api/report, /api/log
│   ├── models/
│   │   ├── db.py                    # Metadata SQLite schema + g-scoped connection
│   │   ├── user.py                  # User model (create, verify, list)
│   │   └── pipeline.py              # Pipeline model + stage progression + fork
│   ├── core/
│   │   ├── config.py                # Path constants + script registry
│   │   ├── db_api.py                # Read-only helpers for research SQLite
│   │   └── job_manager.py           # Subprocess lifecycle + SSE streaming
│   ├── templates/index.html         # Single-page app shell
│   └── static/app.js                # Frontend JS (~600 lines, no build step)
└── deploy/
    ├── setup.sh                     # First-time server setup (run once on the server)
    ├── deploy.sh                    # Push code updates + restart (run from laptop)
    ├── nginx.conf                   # nginx reverse proxy config
    └── dashboard.service            # systemd service unit (written by setup.sh)
```

---

## How the filter works

Every OpenAlex record is a JSON line inside a gzipped partition file. The pipeline applies a cascade before deserializing:

```
Raw line  ──→  "is_oa": true?  ──→  "language": "en"?  ──→  parse JSON
                    │                       │
                   skip                    skip
                    ▼
            has DOI + abstract?  ──→  fuel term ∩ impact term?  ──→  save
                    │                          │
                   skip                       skip
```

A record is saved only if it contains **at least one fuel term** AND **at least one impact term** in the title or abstract. The matched terms are stored with every row so every inclusion is auditable.

### Filter optimisations

| Technique | Effect |
|-----------|--------|
| Raw-line pre-screen | Substring check for `"is_oa": true` + `"language": "en"` before JSON parsing — skips ~86% of records |
| orjson | 3–5× faster than stdlib `json` for the records that pass pre-screen |
| Title-first matching | If the title alone matches both categories, abstract inversion is skipped |
| Inverted-index key pre-screen | Cheap set-intersection test on abstract keys before full inversion |
| Multiprocessing | Configurable parallel workers, each processing a separate `.gz` file |

---

## Pipeline stages (dashboard workflow)

Each pipeline created in the dashboard progresses through 7 ordered stages:

| # | Stage ID | Label | What happens |
|---|----------|-------|--------------|
| 1 | `keyword_selection` | Keyword Selection | Edit fuel + impact term lists for this pipeline |
| 2 | `openalex_search` | OpenAlex Search | Run `run_pipeline.py` against the OpenAlex snapshot |
| 3 | `results_review` | Results Review | Browse the output SQLite (paginated table + SQL) |
| 4 | `report_generation` | Report Generation | Run `generate_report.py` → HTML summary report |
| 5 | `llm_classification` | LLM Classification | Classify papers via OpenAI Batch API |
| 6 | `llm_results_review` | LLM Results Review | Browse results with LLM columns visible/filterable |
| 7 | `llm_report_generation` | Final Report | Re-run report generator with LLM data included |

The owner of a pipeline controls stage advancement. Any logged-in user can view all pipelines and fork them (forking copies the config + duplicates the output SQLite; the fork starts at stage 3 since the DB is already populated).

---

## Prerequisites

### Python

Python **3.11 or newer** is required (tested on 3.12 and 3.14).

- **Windows:** download from [python.org](https://www.python.org/downloads/) and check "Add Python to PATH". Make sure `python3` is accessible — on Windows 11 you may need to run `python` instead and add a `python3` alias, or install from the Microsoft Store (`python3`).
- **Ubuntu server:** `python3` is pre-installed; install `python3-venv` and `python3-full` if missing.

### System packages (server only)

```bash
apt update && apt install -y nginx python3-venv python3-full rsync git
```

---

## Local development (Windows)

This covers running the dashboard and pipeline scripts on your local machine for development and testing. You do **not** need the full OpenAlex dataset — a handful of `.gz` partition files (a few GB) is enough to verify everything works.

### 1. Clone and create a virtual environment

```powershell
cd code\dashboard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

To also run pipeline scripts locally, install the optional pipeline dependencies into the same venv:

```powershell
venv\Scripts\pip install orjson   # faster JSON (optional but recommended)
# boto3 is only needed if you want to stream from S3 rather than a local dir
```

### 2. Set local environment variables

The dashboard defaults to server paths (`/opt/oa-pipeline/...`). Override them for local dev:

**PowerShell:**
```powershell
$env:PIPELINES_BASE_DIR = "$env:LOCALAPPDATA\oa-pipeline\pipelines"
$env:META_DB_PATH       = "$env:LOCALAPPDATA\oa-pipeline\dashboard.db"
$env:SECRET_KEY         = "local-dev-secret"
$env:FLASK_APP          = "app:create_app()"
$env:FLASK_DEBUG        = "1"
```

Or create a `.env` file and load it with a tool like `python-dotenv`, or just set the vars inline each session.

**Tip:** Create a helper script `dev.ps1` in the repo root:
```powershell
# dev.ps1
$env:PIPELINES_BASE_DIR = "$env:LOCALAPPDATA\oa-pipeline\pipelines"
$env:META_DB_PATH       = "$env:LOCALAPPDATA\oa-pipeline\dashboard.db"
$env:SECRET_KEY         = "local-dev-secret"
$env:FLASK_APP          = "app:create_app()"
$env:FLASK_DEBUG        = "1"
cd code\dashboard
..\code\dashboard\venv\Scripts\python -m flask run
```
Then: `.\dev.ps1`

### 3. Run the dashboard

```powershell
cd code\dashboard
# (with env vars set as above)
venv\Scripts\python -m flask run
```

Open `http://localhost:5000/`.

> **Note:** gunicorn is Linux-only. On Windows, Flask's built-in dev server (`flask run`) is fine for development. Do not use `start.sh` on Windows.

### 4. Create the first user

In a second terminal (same `code\dashboard` directory):

```powershell
venv\Scripts\python manage.py create-user admin
```

Enter a password when prompted. Log in at `http://localhost:5000/`.

### 5. Test with sample data

To test pipeline scripts locally, you need at least one OpenAlex `.gz` partition file.

**Get a sample file from S3 (no credentials needed):**
```powershell
# Install AWS CLI or use Python
venv\Scripts\pip install boto3
venv\Scripts\python -c "
import boto3
from botocore import UNSIGNED
from botocore.config import Config
s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
s3.download_file('openalex', 'data/works/updated_date=2024-01-01/part_0001.gz', 'sample.gz')
print('Downloaded sample.gz')
"
```

Or just copy a few `.gz` files from the server if you already have them there.

**Test the filter script directly:**
```powershell
cd code\pipeline
..\dashboard\venv\Scripts\python filter_dataset.py --input path\to\sample.gz --output-db test.sqlite
```

**Test via the dashboard:**
When you create a pipeline and launch the "OpenAlex Search" stage from the browser, the dashboard calls `run_pipeline.py` as a subprocess using `python3`. On Windows, ensure `python3` is in your PATH — run `python3 --version` to verify. If it's not, either:
- Add a `python3.bat` shim on your PATH that calls `python`, or
- Test pipeline script execution on the Ubuntu server instead.

### 6. Edit → refresh cycle

| Change | Action |
|--------|--------|
| `templates/index.html`, `static/app.js` | Hard-refresh the browser (`Ctrl+Shift+R`) |
| Any `blueprints/*.py` or `models/*.py` | Flask auto-reloads (debug mode) |
| `core/config.py` | Restart Flask |
| Pipeline scripts (`pipeline/*.py`) | Take effect immediately on next job launch |

---

## Running pipeline scripts directly (CLI)

All scripts live in `pipeline/`. Run them from anywhere by passing absolute paths, or `cd pipeline/` first to use relative paths.

### filter_dataset.py — filter a single `.gz` file

```bash
python pipeline/filter_dataset.py \
  --input path/to/part_0001.gz \
  --output-db output.sqlite

# Options
# --max-records N        Stop after N records (default: unlimited)
# --commit-interval N    SQLite commit every N records (default: 10 000)
# --keyword-config PATH  JSON file with custom {fuel_terms, impact_terms} lists
```

### run_pipeline.py — parallel full-dataset pipeline

```bash
# Local snapshot (recommended — much faster than S3)
python pipeline/run_pipeline.py --local-dir ~/data/openalex/works

# Stream from public S3 bucket (no setup, ~550 GB download)
python pipeline/run_pipeline.py
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--local-dir DIR` | — | Path to local OpenAlex works directory |
| `--output-db PATH` | `output.sqlite` | Output SQLite database |
| `--state-file PATH` | `pipeline_state.json` | Resume state file |
| `--log-file PATH` | `pipeline.log` | Detailed log (all workers) |
| `--workers N` | `4` | Parallel worker processes |
| `--max-files N` | — | Stop after N files (smoke test) |
| `--min-free-gb N` | `2.0` | Abort if free disk < N GB |
| `--no-color` | off | Plain output (for logs/piping) |
| `--keyword-config PATH` | — | Custom keyword JSON file |

**Resume:** State is written after every file. Re-run the same command to resume. Delete `pipeline_state.json` to start from scratch.

### generate_report.py — HTML report

```bash
python pipeline/generate_report.py \
  --db output.sqlite \
  --output report.html

# --state PATH   Pipeline state JSON for progress metadata (optional)
```

### llm_classify.py — LLM classification via OpenAI Batch API

Requires `OPENAI_API_KEY` in the environment.

```bash
# Step-by-step (recommended for large datasets)
python pipeline/llm_classify.py --db output.sqlite --batch-dir ./batches estimate
python pipeline/llm_classify.py --db output.sqlite --batch-dir ./batches prepare
python pipeline/llm_classify.py --db output.sqlite --batch-dir ./batches submit
python pipeline/llm_classify.py --db output.sqlite --batch-dir ./batches poll
python pipeline/llm_classify.py --db output.sqlite --batch-dir ./batches save

# Or run the full pipeline in one command
python pipeline/llm_classify.py --db output.sqlite --batch-dir ./batches run
```

**Subcommand options:**

| Subcommand | Key options |
|------------|-------------|
| `estimate` | `--model MODEL`, `--limit N`, `--overwrite` |
| `prepare` | `--limit N`, `--overwrite` |
| `submit` | — |
| `poll` | `--interval SECS` (default: 60) |
| `save` | `--file PATH` (use local JSONL instead of downloading) |
| `run` | `--limit N`, `--interval SECS`, `--overwrite` |

---

## Output database schema

One table: `filtered_records`

| Column | Type | Description |
|--------|------|-------------|
| `openalex_id` | TEXT (PK) | OpenAlex work ID |
| `doi` | TEXT | DOI |
| `title` | TEXT | Paper title |
| `abstract_text` | TEXT | Reconstructed abstract |
| `publication_year` | INTEGER | Year |
| `publication_date` | TEXT | ISO date |
| `fwci` | REAL | Field-Weighted Citation Impact |
| `language` | TEXT | Always `en` |
| `type` | TEXT | `article`, `review`, … |
| `is_oa` | INTEGER | Always `1` |
| `open_access_oa_status` | TEXT | `gold`, `green`, `hybrid`, … |
| `open_access_oa_url` | TEXT | OA landing URL |
| `primary_location_source_display_name` | TEXT | Journal / source name |
| `authors_institutions` | TEXT | JSON list of institution names |
| `authors_countries` | TEXT | JSON list of ISO country codes |
| `matched_fuel_terms` | TEXT | JSON list — fuel terms found |
| `matched_impact_terms` | TEXT | JSON list — impact terms found |
| `matched_fuel_stems` | TEXT | JSON list — stemmed tokens that fired |
| `matched_impact_stems` | TEXT | JSON list — stemmed tokens that fired |
| `llm_is_hydrocarbon` | INTEGER | LLM verdict: `1` / `0` / `NULL` |
| `llm_confidence` | REAL | LLM confidence score |
| `llm_justification` | TEXT | LLM reasoning text |

*(plus `open_access_any_repository_has_fulltext`, `primary_location_is_oa`, `primary_location_landing_page_url`, `primary_location_license`, `primary_location_source_host_organization_name`, `primary_location_source_type`)*

### Example queries

```sql
-- All matched records, newest first
SELECT title, doi, publication_year, matched_fuel_terms, matched_impact_terms
FROM filtered_records
ORDER BY publication_year DESC;

-- Papers about biodiesel + emissions
SELECT title, doi FROM filtered_records
WHERE matched_fuel_terms LIKE '%biodiesel%'
  AND matched_impact_terms LIKE '%emission%';

-- Most common fuel terms
SELECT value, COUNT(*) AS n
FROM filtered_records, json_each(filtered_records.matched_fuel_terms)
GROUP BY value ORDER BY n DESC;

-- LLM-confirmed hydrocarbon papers
SELECT title, llm_confidence, llm_justification
FROM filtered_records
WHERE llm_is_hydrocarbon = 1
ORDER BY llm_confidence DESC;

-- Papers by country
SELECT value AS country, COUNT(*) AS n
FROM filtered_records, json_each(filtered_records.authors_countries)
GROUP BY value ORDER BY n DESC;
```

---

## Dashboard architecture

```
Browser
   │
   ▼  HTTP / SSE
nginx :80              ← reverse proxy (proxy_buffering off for SSE)
   │
   ▼
gunicorn :5000         ← single worker (-w 1), 16 gthreads
   │                     (single worker required: JobManager is in-memory)
   ├── blueprints/auth.py      Session-cookie auth
   ├── blueprints/pipelines.py Pipeline CRUD + stage advance + fork
   ├── blueprints/jobs.py      Launch / stream / kill subprocesses
   ├── blueprints/data.py      DB browser, SQL query, report, log tail
   ├── models/db.py            Metadata SQLite (users, pipelines, jobs)
   └── core/job_manager.py     Popen + threading.Condition + SSE deque
```

**Key design choices:**

| Choice | Reason |
|--------|--------|
| SSE, not WebSockets | `EventSource` is built into every browser; no JS library needed |
| Single gunicorn worker | `JobManager` lives in-process; multiple workers would split job state |
| `start_new_session=True` in Popen | Each script in its own process group — Kill terminates all children cleanly |
| `mode=ro` SQLite URI | Prevents any writes even if a SQL query contains mutation keywords |
| Session cookie auth | `EventSource` cannot send `Authorization` headers, so token auth doesn't work for SSE |

---

## User management (admin CLI)

Run from inside `code/dashboard/`:

```bash
# Create a user (prompts for password; min 6 characters)
python manage.py create-user <username>

# List all users
python manage.py list-users

# Delete a user
python manage.py delete-user <username>
```

On Windows: `venv\Scripts\python manage.py create-user admin`
On the server: `/opt/oa-pipeline/venv/bin/python3 manage.py create-user admin`

---

## Server deployment

### Requirements

- Ubuntu 22.04+ server with SSH access
- Root or sudo access on the server
- The repo cloned locally (deploy scripts use `rsync` + `ssh`)

### First-time setup

Run **once** on the target server (copy the repo there first, or run the script via SSH):

```bash
# From the repo root on the server, or via ssh pipe:
bash deploy/setup.sh
```

The script:
1. Installs `nginx`, `python3-venv`, `python3-full`, `rsync`, `git`
2. Creates `/opt/oa-pipeline/{dashboard,pipeline,data/pipelines}/`
3. Creates `/opt/oa-pipeline/venv` and installs Python dependencies
4. Generates a random `SECRET_KEY` → `/opt/oa-pipeline/data/.secret_key` (chmod 600)
5. Writes `/etc/systemd/system/oa-pipeline.service` with the generated key and correct paths
6. Enables and starts the service
7. Copies `deploy/nginx.conf` to `/etc/nginx/sites-available/oa-pipeline` and enables it
8. Does an initial rsync of `dashboard/` and `pipeline/`

After setup, **create the first user**:

```bash
cd /opt/oa-pipeline/dashboard
/opt/oa-pipeline/venv/bin/python3 manage.py create-user admin
```

### Deploying code updates

From your **local machine** (Windows or Linux), after making code changes:

```bash
./deploy/deploy.sh user@your-server
```

This rsyncs `dashboard/` and `pipeline/` to the server (skipping `venv/` and `__pycache__/`) then restarts the service:

```bash
# Push code + restart (normal deploy)
./deploy/deploy.sh user@your-server

# Restart without pushing code (e.g. after a config change)
./deploy/deploy.sh user@your-server --restart-only
```

> **Windows note:** `deploy.sh` requires a bash shell. Run it from Git Bash, WSL, or any Linux-compatible terminal. Alternatively, run the two rsync commands manually.

### Server directory layout

```
/opt/oa-pipeline/
├── dashboard/              # Flask app (synced from code/dashboard/)
│   └── venv -> ../venv     # symlink, created by setup.sh
├── pipeline/               # Pipeline scripts (synced from code/pipeline/)
├── venv/                   # Python virtualenv
│   └── bin/
│       ├── python3
│       ├── gunicorn
│       └── flask
└── data/
    ├── dashboard.db        # User + pipeline metadata
    ├── .secret_key         # Flask SECRET_KEY (generated once, never overwritten)
    └── pipelines/
        ├── 1/              # Pipeline #1 data
        │   ├── output.sqlite
        │   ├── keywords.json
        │   ├── pipeline_state.json
        │   ├── pipeline.log
        │   ├── report.html
        │   └── llm_report.html
        ├── 2/              # Pipeline #2 data
        └── ...
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-secret-change-in-production` | Flask session signing key — **always set in production** |
| `PIPELINES_BASE_DIR` | `/opt/oa-pipeline/data/pipelines` | Root directory for per-pipeline data |
| `META_DB_PATH` | `/opt/oa-pipeline/data/dashboard.db` | Dashboard metadata database path |

These are set in the systemd service file by `setup.sh`. To change them:

```bash
systemctl edit oa-pipeline --force
# Add under [Service]:
# Environment=META_DB_PATH=/new/path/dashboard.db
systemctl daemon-reload && systemctl restart oa-pipeline
```

### Service management

```bash
# Follow live logs
journalctl -u oa-pipeline -f

# Status
systemctl status oa-pipeline

# Restart / stop
systemctl restart oa-pipeline
systemctl stop oa-pipeline
```

### HTTPS (optional)

The default nginx config listens on port 80. To add TLS:

```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d yourdomain.com
```

### Accessing via SSH tunnel

If the server has no public IP or is behind NAT:

```powershell
# PowerShell — keep this window open
ssh -L 8080:127.0.0.1:80 user@your-server -N
# Then open: http://localhost:8080/
```

Add to `~/.ssh/config` for a permanent alias:
```
Host openalex
    HostName your-server-ip
    User root
    LocalForward 8080 127.0.0.1:80
```
Then: `ssh openalex -N` and browse to `http://localhost:8080/`.

---

## Customising keywords

- **Default terms for new pipelines:** edit `pipeline/default_keywords.json`.
- **Per-pipeline terms:** configure in the dashboard (Keyword Selection stage), or pass `--keyword-config path/to/keywords.json` on the CLI.
- **Format:** `{"fuel_terms": ["term1", "term2", ...], "impact_terms": ["term1", ...]}`
- After changing terms for an in-progress run, delete `pipeline_state.json` and the output database to reprocess from scratch.

---

## Troubleshooting

### Service won't start

```bash
journalctl -u oa-pipeline -n 50 --no-pager
# Look for Python ImportError, missing venv, or path errors
```

Common fixes:
```bash
# Missing venv or packages
/opt/oa-pipeline/venv/bin/pip install flask gunicorn werkzeug

# start.sh not executable (shouldn't happen after rsync, but just in case)
chmod +x /opt/oa-pipeline/dashboard/start.sh
```

### 502 Bad Gateway

Flask isn't running or crashed. Check:
```bash
systemctl status oa-pipeline
curl http://127.0.0.1:5000/  # test Flask directly, bypassing nginx
```

### SSE output doesn't stream (arrives in chunks)

`proxy_buffering off` must be set in the nginx config. Verify:
```bash
grep proxy_buffering /etc/nginx/sites-enabled/oa-pipeline
# Should print: proxy_buffering    off;
nginx -t && systemctl reload nginx
```

### SSE stream cuts off after 60 seconds

Default nginx `proxy_read_timeout` is 60 s. Verify the custom timeout is applied:
```bash
grep proxy_read_timeout /etc/nginx/sites-enabled/oa-pipeline
# Should print: proxy_read_timeout    3600s;
```

### "pipeline_id required" errors from the API

All data endpoints (`/api/db/*`, `/api/report`, `/api/log`) require a `pipeline_id` query parameter. This is set automatically by the frontend; if you're calling the API directly, append `?pipeline_id=<id>`.

### Pipeline scripts not found on startup

The dashboard resolves script paths relative to the `dashboard/` directory at import time:
```
REPO_ROOT = code/
PIPELINE_SCRIPTS_DIR = code/pipeline/
```
Verify `code/pipeline/run_pipeline.py` exists on the server. If you moved the install directory, update `PIPELINE_SCRIPTS_DIR` in `dashboard/core/config.py` and redeploy.

### `python3` not found on Windows (local dev)

The job manager calls `python3 -u script.py`. On Windows, if `python3` isn't in PATH:
- Install Python from the Microsoft Store (it registers `python3`)
- Or add a shim: create `python3.bat` somewhere on your PATH containing `@python %*`
- Or test pipeline execution on the server rather than locally

### Forgot a user's password

Delete and recreate the user (pipelines are not deleted):
```bash
cd /opt/oa-pipeline/dashboard
/opt/oa-pipeline/venv/bin/python3 manage.py delete-user alice
/opt/oa-pipeline/venv/bin/python3 manage.py create-user alice
```
