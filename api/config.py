"""Application settings — all values come from environment variables."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-secret-change-in-production"

    # Data directories
    pipelines_base_dir: Path = Path("/opt/oa-pipeline/data/pipelines")
    meta_db_path: Path = Path("/opt/oa-pipeline/data/dashboard.db")
    gz_files_base_dir: Path = Path("/opt/oa-pipeline/data/gz")

    # Filtering job constraints
    filtering_workers: int = 16

    # Signed-cookie session
    session_cookie_name: str = "session"
    session_max_age_days: int = 7

    # DB browser
    db_page_size: int = 100
    db_query_max_rows: int = 5000

    @property
    def repo_root(self) -> Path:
        return Path(__file__).parent.parent

    @property
    def pipeline_scripts_dir(self) -> Path:
        return self.repo_root / "pipeline"

    @property
    def scripts(self) -> dict:
        d = self.pipeline_scripts_dir
        return {
            "run_pipeline": {
                "path": str(d / "pipeline.py"),
                "label": "pipeline.py",
                "description": "Parallel OpenAlex search — processes all .gz partition files",
                "no_color_flag": "--no-color",
            },
            "filter_dataset": {
                "path": str(d / "pipeline.py"),
                "label": "pipeline.py filter",
                "description": "Single-file processor — filters one .gz partition",
                "no_color_flag": None,
            },
            "llm_classify": {
                "path": str(d / "llm_classify.py"),
                "label": "llm_classify.py",
                "description": "OpenAI Batch API pipeline for LLM classification",
                "no_color_flag": None,
            },
        }


# Module-level singleton — import this everywhere.
settings = Settings()
