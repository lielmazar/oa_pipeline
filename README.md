# OA Pipeline

Web application for systematic literature review on the OpenAlex corpus.
Runs keyword filtering across the full OpenAlex snapshot, LLM classification,
inter-rater QA, and analysis — all from a browser, with the heavy processing
on a Ubuntu server.

## Architecture

```
web/          React + Vite + Tailwind  (frontend)
api/          FastAPI backend
  routers/    HTTP endpoints
  db/         SQLite access (metadata + research DBs)
  business/   Domain logic (runner, charts, export, QA, LLM)
pipeline/     Python processing scripts (pipeline.py, llm_classify.py)
deploy/       Deployment scripts (systemd, nginx, bootstrap)
```

**Data flow:**
1. User defines keyword groups in the browser
2. Server runs `pipeline.py` across `~/data/openalex/works/` (700+ gzip partitions, 16 cores)
3. Results stored in per-pipeline `output.sqlite`
4. Browser queries charts, browses records, manages subsets
5. LLM batch classification via OpenAI Batch API (backend polls, frontend polls backend)
6. Inter-rater QA with Krippendorff's alpha
7. Export as ZIP (SQLite + CSV + charts)

## Stack

- **Backend:** FastAPI + uvicorn, SQLite (WAL), itsdangerous sessions
- **Frontend:** React 18, Vite, Tailwind CSS
- **Processing:** Python scripts (multiprocessing, OpenAI Batch API)
- **Deployment:** systemd + nginx on Ubuntu 24.04

## Quick start (Docker)

```bash
cp .env.example .env          # set SECRET_KEY and OPENAI_API_KEY
docker compose up -d
```

App available at http://localhost:8000

## Deploy to server

```bash
bash deploy/deploy.sh root@your-server
```

First deploy — create an admin user:
```bash
ssh root@your-server
/opt/oa-pipeline/venv/bin/python3 - << 'EOF'
from api.db.meta import init_db, get_db
from api.db.users import create_user
init_db()
with get_db() as db:
    create_user(db, "admin", "your-password")
EOF
```

Subsequent deploys are safe to run repeatedly (idempotent).

## Local development

```bash
# Backend
pip install -e .
uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd web && npm install && npm run dev
```

Add to `web/vite.config.js` for dev proxy:
```js
server: { proxy: { '/api': 'http://localhost:8000' } }
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-…` | Session signing key — **must set in production** |
| `PIPELINES_BASE_DIR` | `/opt/oa-pipeline/data/pipelines` | Pipeline output directory |
| `META_DB_PATH` | `/opt/oa-pipeline/data/dashboard.db` | Metadata SQLite path |
| `GZ_FILES_BASE_DIR` | `/opt/oa-pipeline/data/gz` | OpenAlex .gz partition files |
| `FILTERING_WORKERS` | `16` | Cores used for gz filtering |
| `OPENAI_API_KEY` | — | Required for LLM classification |

## Pipeline stages

| Stage | Description |
|---|---|
| `keyword_selection` | Define keyword groups and subgroups |
| `openalex_search` | Run gz filtering across OpenAlex corpus |
| `analysis` | Charts, record browser, subsets |
| `llm_classification` | OpenAI Batch API classification |
| `llm_analysis` | Post-LLM charts and analysis |
| `summary` | Export results as ZIP |

## API reference

OpenAPI docs at `/api/docs`.

Key endpoint groups:
- `POST /api/auth/login` — session cookie auth
- `GET/POST /api/pipeline` — pipeline CRUD
- `POST/GET/DELETE /api/pipeline/{id}/run` — filter run (polling)
- `GET /api/pipeline/{id}/records` — paginated record browser
- `POST /api/pipeline/{id}/query` — ad-hoc SQL on research DB
- `GET /api/pipeline/{id}/charts` — chart definitions (SQL + renderer)
- `POST /api/pipeline/{id}/llm/estimate|submit|collect` — LLM workflow
- `GET /api/pipeline/{id}/llm/status` — lazy-refresh batch status
- `GET /api/pipeline/{id}/export` — download ZIP
- `GET/POST /api/pipeline/{id}/qa/ratings` — inter-rater ratings
- `GET /api/pipeline/{id}/qa/irr` — Krippendorff's alpha
- `GET/POST /api/system/default-keywords` — system-level keyword template
