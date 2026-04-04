"""Dashboard configuration — paths and constants."""
from __future__ import annotations

import os
from pathlib import Path

# ── Base directories ──────────────────────────────────────────────────────────

# Where per-pipeline data directories live (each pipeline gets <id>/ subdir)
PIPELINES_BASE_DIR = Path(
    os.environ.get("PIPELINES_BASE_DIR", "/opt/oa-pipeline/data/pipelines")
)

# Dashboard metadata DB (users, pipelines, job associations)
META_DB_PATH = Path(
    os.environ.get("META_DB_PATH", "/opt/oa-pipeline/data/dashboard.db")
)

# Root of the repo (one level above dashboard/)
REPO_ROOT = Path(__file__).parent.parent.parent

# Pipeline scripts directory
PIPELINE_SCRIPTS_DIR = REPO_ROOT / "pipeline"

# Script registry — maps script_key → absolute path inside pipeline/
SCRIPTS = {
    "run_pipeline": {
        "path": str(PIPELINE_SCRIPTS_DIR / "pipeline.py"),
        "label": "pipeline.py",
        "description": "Parallel OpenAlex search — processes all .gz partition files",
        "no_color_flag": "--no-color",
    },
    "filter_dataset": {
        "path": str(PIPELINE_SCRIPTS_DIR / "pipeline.py"),
        "label": "pipeline.py filter",
        "description": "Single-file processor — filters one .gz partition",
        "no_color_flag": None,
    },
    "llm_classify": {
        "path": str(PIPELINE_SCRIPTS_DIR / "llm_classify.py"),
        "label": "llm_classify.py",
        "description": "OpenAI Batch API pipeline for LLM classification",
        "no_color_flag": None,
    },
}

# ── DB browser settings ───────────────────────────────────────────────────────

DB_PAGE_SIZE = 100
DB_QUERY_MAX_ROWS = 5000
