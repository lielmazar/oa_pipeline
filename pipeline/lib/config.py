"""Pipeline configuration: one YAML file, overridable from the CLI and environment."""
from __future__ import annotations

import argparse
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"


@dataclass
class Stage1Config:
    source: str = "s3://openalex/data/jsonl"
    entities: list[str] = field(default_factory=lambda: ["works", "sources"])
    copy_concurrency: int = 32


@dataclass
class Stage2Config:
    work_types: list[str] = field(default_factory=lambda: ["article", "review"])
    require_journal_source: bool = True
    allow_location_fallback: bool = False
    exclude_paratext: bool = True
    exclude_retracted: bool = True
    exclude_xpac: bool = True
    require_doi: bool = True
    languages: list[str] = field(default_factory=lambda: ["en"])
    allow_unknown_language: bool = False
    require_abstract: bool = True
    store_raw_json: bool = True


@dataclass
class Stage3Config:
    keywords_file: str = "pipeline/research_schema.json"
    sample_size: int = 40


@dataclass
class Config:
    storage_root: str = "./data"
    aws_region: str = "us-east-1"
    snapshot_id: str | None = None
    run_id: str = "default"
    workers: int | None = None
    tmp_dir: str = "/tmp/oa-pipeline"
    stage1: Stage1Config = field(default_factory=Stage1Config)
    stage2: Stage2Config = field(default_factory=Stage2Config)
    stage3: Stage3Config = field(default_factory=Stage3Config)
    config_path: Path | None = None

    @property
    def n_workers(self) -> int:
        return max(1, self.workers or os.cpu_count() or 1)

    def resolve_path(self, p: str) -> Path:
        """Resolve a path from the config relative to the repository root."""
        path = Path(p)
        return path if path.is_absolute() else REPO_ROOT / path

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("config_path", None)
        return d


def _build(cls, data: dict | None):
    data = data or {}
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    stages = {k: raw.pop(k, None) for k in ("stage1", "stage2", "stage3")}
    cfg = _build(Config, raw)
    cfg.stage1 = _build(Stage1Config, stages["stage1"])
    cfg.stage2 = _build(Stage2Config, stages["stage2"])
    cfg.stage3 = _build(Stage3Config, stages["stage3"])
    cfg.config_path = path

    # Environment overrides (used by remote jobs launched with deploy/remote.py).
    cfg.storage_root = os.environ.get("OA_STORAGE_ROOT") or cfg.storage_root
    cfg.run_id = os.environ.get("OA_RUN_ID") or cfg.run_id
    cfg.tmp_dir = os.environ.get("OA_TMP_DIR") or cfg.tmp_dir
    cfg.snapshot_id = os.environ.get("OA_SNAPSHOT_ID") or cfg.snapshot_id
    if os.environ.get("OA_WORKERS"):
        cfg.workers = int(os.environ["OA_WORKERS"])
    os.environ.setdefault("AWS_DEFAULT_REGION", cfg.aws_region)
    return cfg


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="Path to config.yaml (default: repo root config.yaml)")
    parser.add_argument("--storage-root", help="Override storage_root (s3://bucket/prefix or local dir)")
    parser.add_argument("--snapshot-id", help="Override snapshot_id")
    parser.add_argument("--run-id", help="Override run_id")
    parser.add_argument("--workers", type=int, help="Parallel worker processes (default: all cores)")
    parser.add_argument("--tmp-dir", help="Local scratch directory for partition downloads")


def config_from_args(args: argparse.Namespace) -> Config:
    cfg = load_config(args.config)
    for name in ("storage_root", "snapshot_id", "run_id", "workers", "tmp_dir"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(cfg, name, value)
    return cfg
