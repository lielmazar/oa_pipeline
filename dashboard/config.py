from pathlib import Path

BASE_DIR = Path("/root/data/openalex")
SCRIPT_DIR = BASE_DIR
SCRIPT_DIR_2 = BASE_DIR / "2"

SCRIPTS = {
    "process_all_works": {
        "path": str(SCRIPT_DIR / "process_all_works.py"),
        "label": "process_all_works.py",
        "description": "Parallel pipeline orchestrator — processes all OpenAlex work files",
        "no_color_flag": "--no-color",
    },
    "process_dataset": {
        "path": str(SCRIPT_DIR / "Proccess_dataset.py"),
        "label": "Proccess_dataset.py",
        "description": "Single-file processor — filters one .gz partition",
        "no_color_flag": None,
    },
    "generate_report": {
        "path": str(SCRIPT_DIR / "generate_report.py"),
        "label": "generate_report.py",
        "description": "Generate HTML report with charts from SQLite database",
        "no_color_flag": None,
    },
    "batch_process": {
        "path": str(SCRIPT_DIR / "batch_process_papers.py"),
        "label": "batch_process_papers.py",
        "description": "OpenAI Batch API pipeline for LLM classification",
        "no_color_flag": None,
    },
    "postprocess_ml": {
        "path": str(SCRIPT_DIR_2 / "postprocess_ml_check.py"),
        "label": "postprocess_ml_check.py",
        "description": "Full-text ML term scanning with Unpaywall/CrossRef integration",
        "no_color_flag": None,
    },
}

DB_PATH = BASE_DIR / "openalex_works_filtered.sqlite"
PIPELINE_STATE_PATH = BASE_DIR / "pipeline_state.json"
PIPELINE_LOG_PATH = BASE_DIR / "pipeline.log"
BATCH_STATE_PATH = BASE_DIR / "batch_runs" / "state.json"
REPORT_HTML_PATH = BASE_DIR / "report.html"
POSTPROCESS_LOG_PATH = BASE_DIR / "2" / "postprocess.log"

DB_PAGE_SIZE = 100
DB_QUERY_MAX_ROWS = 5000

AUTH_PASSWORD_ENV = "DASHBOARD_PASSWORD"
SECRET_KEY_ENV = "SECRET_KEY"
