# OpenAlex Dashboard

Interactive web UI for running the OpenAlex pipeline scripts, browsing the
SQLite database, streaming live job output, and viewing reports — all from a
browser.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Server: First-time Setup](#server-first-time-setup)
4. [Server: Deploy Code Changes](#server-deploy-code-changes)
5. [Local Development (run on laptop)](#local-development)
6. [Access from Your Laptop](#access-from-your-laptop)
7. [Using the Dashboard](#using-the-dashboard)
8. [Multiple Databases](#multiple-databases)
9. [Configuration Reference](#configuration-reference)
10. [Troubleshooting](#troubleshooting)

---

## Architecture

```
Browser
   │
   ▼
nginx :80          ← reverse proxy (proxy_buffering off for SSE)
   │
   ▼
gunicorn :5000     ← single worker, 16 threads (keeps JobManager in memory)
   │
   ├── app.py          Flask routes + SSE streaming
   ├── job_manager.py  Subprocess lifecycle (Popen + threading.Condition)
   ├── db_api.py       Read-only SQLite helpers
   └── config.py       Path constants
```

**Key design choices**

| Choice | Reason |
|---|---|
| SSE not WebSockets | Simpler; `EventSource` is built into every browser |
| Single gunicorn worker | `JobManager` dict lives in memory; multiple workers would fragment state |
| `start_new_session=True` | Puts each script in its own process group so Kill terminates all children |
| `mode=ro` SQLite URI | Prevents any writes even if the SQL query contains mutation keywords |
| Session cookie auth | `EventSource` cannot send `Authorization` headers, so Basic Auth doesn't work |

---

## Prerequisites

| Where | What |
|---|---|
| Server | Ubuntu 22.04 / 24.04, Python 3.10+, nginx |
| Laptop | Python 3.10+, OpenSSH (`ssh`/`scp` in PATH) |

---

## Server: First-time Setup

### 1 — Install system packages

```bash
apt update && apt install -y nginx python3-venv python3-full
```

### 2 — Copy files from laptop

From your laptop (PowerShell):

```powershell
# Set once, reuse forever
$env:OPENALEX_SERVER = "YOUR_SERVER_IP"

# First deploy: copy dashboard + all scripts
.\deploy.ps1 -IncludeScripts -IncludeSubdir2
```

See [deploy.ps1 reference](#server-deploy-code-changes) for all options.

### 3 — Create Python virtualenv

```bash
cd /root/data/openalex/dashboard
python3 -m venv venv
venv/bin/pip install flask gunicorn
```

### 4 — Configure the service

Generate a secret key:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Edit the service file and fill in the values:

```bash
nano /root/data/openalex/dashboard/openalex-dashboard.service
# Set:  SECRET_KEY=<paste generated key>
# Set:  DASHBOARD_PASSWORD=<your chosen password>
#       (remove the DASHBOARD_PASSWORD line for open access on a private network)
```

Install and start:

```bash
cp /root/data/openalex/dashboard/openalex-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now openalex-dashboard
systemctl status openalex-dashboard   # expect: Active: active (running)
```

### 5 — Configure nginx

```bash
cp /root/data/openalex/dashboard/nginx_openalex.conf /etc/nginx/sites-available/openalex-dashboard
ln -sf /etc/nginx/sites-available/openalex-dashboard /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default   # remove default placeholder if present
nginx -t                                  # must print "test is successful"
systemctl reload nginx
```

### 6 — Verify

```bash
curl -s http://localhost/api/status | python3 -m json.tool
# Should return JSON with db.record_count etc.
```

Open `http://YOUR_SERVER_IP/` in a browser.

---

## Server: Deploy Code Changes

Use `deploy.ps1` from your laptop. It copies files via scp and restarts the service.

```powershell
# Dashboard code only (most common during UI development)
.\deploy.ps1

# Dashboard + pipeline scripts
.\deploy.ps1 -IncludeScripts

# Dashboard + all scripts including 2/ subdirectory
.\deploy.ps1 -IncludeScripts -IncludeSubdir2

# Also copy the local database
.\deploy.ps1 -CopyDb

# Copy a specific database (e.g. from subdirectory)
.\deploy.ps1 -CopyDb -DbFile "0\openalex_works_filtered.sqlite"

# Deploy but don't restart (useful if service isn't set up yet)
.\deploy.ps1 -NoRestart

# Full deploy with everything
.\deploy.ps1 -IncludeScripts -IncludeSubdir2 -CopyDb
```

**Parameters**

| Parameter | Default | Description |
|---|---|---|
| `-Server` | `$env:OPENALEX_SERVER` | Server IP or hostname |
| `-User` | `root` | SSH user |
| `-RemoteBase` | `/root/data/openalex` | Remote base directory |
| `-IncludeScripts` | off | Copy root-level Python scripts |
| `-IncludeSubdir2` | off | Copy `2/` subdirectory scripts |
| `-CopyDb` | off | Copy a SQLite database |
| `-DbFile` | `openalex_works_filtered.sqlite` | Which DB to copy |
| `-NoRestart` | off | Skip service restart |

**Tip:** set once and forget:
```powershell
$env:OPENALEX_SERVER = "192.168.1.100"
# Now just:
.\deploy.ps1
```

---

## Local Development

Run the dashboard on your laptop against the local database (no server needed).

### 1 — Install deps

```powershell
cd "C:\Users\Liel\OneDrive\Desktop\part_0009\server\dashboard"
python -m venv venv
venv\Scripts\pip install flask gunicorn
```

### 2 — Run Flask in dev mode

```powershell
cd "C:\Users\Liel\OneDrive\Desktop\part_0009\server\dashboard"
venv\Scripts\python app.py
```

Open `http://localhost:5000/` — no nginx needed.

### 3 — Edit → refresh cycle

- Edit `templates/index.html` or `static/app.js` → hard-refresh the browser (`Ctrl+Shift+R`)
- Edit `app.py` / `job_manager.py` / `db_api.py` → Flask auto-reloads in debug mode
- Edit `config.py` → restart Flask

### 4 — Point to the local database

`config.py` hardcodes `/root/data/openalex` as `BASE_DIR`.
For local dev, either:

**Option A** — override via environment variable (cleanest):
```powershell
$env:OPENALEX_BASE = "C:\Users\Liel\OneDrive\Desktop\part_0009\server"
venv\Scripts\python app.py
```
Then add this to the top of `config.py`:
```python
BASE_DIR = Path(os.environ.get("OPENALEX_BASE", "/root/data/openalex"))
```

**Option B** — temporarily edit `config.py`:
```python
BASE_DIR = Path(r"C:\Users\Liel\OneDrive\Desktop\part_0009\server")
```
Remember to revert before deploying.

---

## Access from Your Laptop

### Direct (server has a reachable IP)

```
http://YOUR_SERVER_IP/
```

### SSH Tunnel (server behind NAT or for extra security)

Run in PowerShell and keep the window open:

```powershell
ssh -L 8080:127.0.0.1:80 root@YOUR_SERVER_IP -N
```

Then open: `http://localhost:8080/`

**Persistent tunnel config** — add to `~/.ssh/config`:
```
Host openalex
    HostName YOUR_SERVER_IP
    User root
    LocalForward 8080 127.0.0.1:80
```
Then: `ssh openalex -N` and browse to `http://localhost:8080/`

---

## Using the Dashboard

### Dashboard tab

Overview of the current state. Auto-refreshes every 8 seconds.

| Card | Source |
|---|---|
| Records in DB | `SELECT COUNT(*) FROM filtered_records` |
| LLM Processed | records where `llm_processed_at IS NOT NULL` |
| DB Size | file size on disk |
| Disk Free | `shutil.disk_usage(BASE_DIR)` |
| Pipeline State | `pipeline_state.json` |
| Batch State | `batch_runs/state.json` |
| Active Jobs | in-memory job registry |

### Scripts tab

Launch any of the five pipeline scripts with configurable arguments.
Output streams live in the terminal panel on the right.

| Panel | Script | Notes |
|---|---|---|
| process_all_works.py | Parallel pipeline | Long-running (hours). `--no-color` is added automatically. |
| Proccess_dataset.py | Single file | Provide full path to a `.gz` file. |
| generate_report.py | HTML report | Fast (~seconds). |
| batch_process_papers.py | OpenAI Batch API | Choose subcommand via radio buttons. |
| postprocess_ml_check.py | Full-text ML scan | Requires Unpaywall email. |

**Job controls**
- **Launch** — starts the script; output appears immediately in the terminal
- **Kill Process** — sends SIGTERM to the entire process group; escalates to SIGKILL after 5s
- **Job pills** (below the terminal) — click any pill to switch to that job's output
- **Scroll lock** — scroll up to pause auto-scroll; click ↓ to resume

### Database tab

Browse and query `filtered_records`.

| Feature | How |
|---|---|
| Search | Type in the search box, press Enter or click Search |
| Sort | Click any column header, or use the Sort dropdown |
| Row detail | Click any row to open a modal with all 40+ columns |
| SQL query | Type any `SELECT` statement and click Run |
| Export CSV | After running a SQL query, click Export CSV |

Default columns shown: `openalex_id`, `doi`, `title`, `publication_year`, `fwci`, journal, countries, fuel terms, impact terms, LLM classification, hydrocarbon flag, LLM confidence.

For all other columns, use the SQL query:
```sql
SELECT openalex_id, abstract_text, llm_justification
FROM filtered_records
WHERE llm_is_hydrocarbon = 0
LIMIT 50
```

### Logs tab

Tail `pipeline.log` or `postprocess.log`.
Enable **Auto-refresh** to poll every 10 seconds while a job is running.

### Report tab

Embeds `report.html` in an iframe.
Click **Regenerate Report** to run `generate_report.py` and refresh automatically.

---

## Multiple Databases

The DB selector in the top-right of the nav bar lists every `.sqlite` file found
at the root of `BASE_DIR` and one level deep (e.g. `0/`, `1/`, `2/`).

Switching the selector updates the Dashboard stats, Database browser, and SQL
query — everything uses the selected database.

**To copy additional databases to the server:**

```powershell
# Copy a specific DB from a subdirectory
.\deploy.ps1 -CopyDb -DbFile "1\openalex_works_filtered.sqlite"

# Or from the root
.\deploy.ps1 -CopyDb -DbFile "my_other_run.sqlite"
```

Or manually:
```bash
scp "path\to\my.sqlite" root@SERVER:/root/data/openalex/
# The dashboard will detect it automatically on next page load
```

The API endpoint `GET /api/db/list` returns all available databases.
Any API endpoint that queries the DB accepts a `?db=relative/path.sqlite`
query parameter.

---

## Configuration Reference

All paths are set in `config.py`. Environment variables override at runtime.

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_PASSWORD` | *(unset = open)* | Login password. Unset for private networks. |
| `SECRET_KEY` | fallback string | Flask session signing key. **Always set in production.** |
| `OPENALEX_BASE` | `/root/data/openalex` | Override `BASE_DIR` without editing `config.py` |

**`config.py` constants**

| Constant | Value | Description |
|---|---|---|
| `BASE_DIR` | `/root/data/openalex` | Root of all data files |
| `DB_PATH` | `BASE_DIR/openalex_works_filtered.sqlite` | Default database |
| `DB_PAGE_SIZE` | `100` | Rows per page in the browser |
| `DB_QUERY_MAX_ROWS` | `5000` | Max rows returned by SQL query |

---

## Troubleshooting

### Service won't start

```bash
journalctl -u openalex-dashboard -n 50
# Look for Python ImportError or missing venv
```

Common fixes:
```bash
# Venv missing
cd /root/data/openalex/dashboard
python3 -m venv venv && venv/bin/pip install flask gunicorn

# start.sh not executable
chmod +x /root/data/openalex/dashboard/start.sh
```

### 502 Bad Gateway

Flask isn't running. Check:
```bash
systemctl status openalex-dashboard
curl http://127.0.0.1:5000/api/status   # test Flask directly
```

### SSE output doesn't stream (output arrives in chunks)

`proxy_buffering off` must be in the nginx config. Verify:
```bash
grep "proxy_buffering" /etc/nginx/sites-enabled/openalex-dashboard
# Should print:  proxy_buffering off;
nginx -t && systemctl reload nginx
```

### Job output cuts off after 60 seconds

Default nginx read timeout is 60s. Verify the custom timeout is applied:
```bash
grep "proxy_read_timeout" /etc/nginx/sites-enabled/openalex-dashboard
# Should print:  proxy_read_timeout 3600s;
```

### "Database not found" after switching DBs

The selector only shows databases that physically exist on the server.
Copy the database first (see [Multiple Databases](#multiple-databases)), then
reload the page to refresh the selector.

### Script runs but produces no output

The script might be heavily buffered. The dashboard automatically sets
`PYTHONUNBUFFERED=1` and `TERM=dumb` for all subprocess launches.
If output still doesn't appear, check that the script writes to stdout
(not only to a log file).

### Cannot kill a job

The Kill button sends `SIGTERM` to the process group. If the process ignores
`SIGTERM`, a `SIGKILL` is sent automatically after 5 seconds.
If the button is greyed out, the job already finished.

### Port 80 already in use

```bash
ss -tlnp | grep :80          # find what's using port 80
nginx -t && systemctl reload nginx   # don't restart, just reload config
```

### Forgot the dashboard password

Edit the service file and change `DASHBOARD_PASSWORD`, then restart:
```bash
nano /etc/systemd/system/openalex-dashboard.service
systemctl daemon-reload && systemctl restart openalex-dashboard
```
