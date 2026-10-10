"""Where everything lives under storage_root.

    <storage_root>/
      snapshots/<snapshot_id>/
        data/works/updated_date=YYYY-MM-DD/part_NNNN.gz    stage 1 mirror
        data/sources/...
        snapshot_manifest.json                             stage 1 inventory
        stage1_report.html / stage1_report.json
      runs/<run_id>/
        run.json                                           snapshot + settings used
        stage2/parquet/<partition>.parquet                 journal articles
        stage2/stats/<partition>.json                      per-partition counters
        stage2/journals.parquet                            journal metrics (sources entity)
        stage2/report.html / report.json
        stage3/parquet/<partition>.parquet                 keyword matches
        stage3/stats/<partition>.json
        stage3/results.parquet / filtered_records.sqlite   consolidated matches
        stage3/report.html / report.json
"""
from __future__ import annotations

import re

from pipeline.lib import storage
from pipeline.lib.config import Config

_DATE = re.compile(r"updated_date=(\d{4}-\d{2}-\d{2})")


def snapshots_root(cfg: Config) -> str:
    return storage.join(cfg.storage_root, "snapshots")


def snapshot_root(cfg: Config, snapshot_id: str) -> str:
    return storage.join(snapshots_root(cfg), snapshot_id)


def snapshot_manifest(cfg: Config, snapshot_id: str) -> str:
    return storage.join(snapshot_root(cfg, snapshot_id), "snapshot_manifest.json")


def run_root(cfg: Config) -> str:
    return storage.join(cfg.storage_root, "runs", cfg.run_id)


def stage_root(cfg: Config, stage: str) -> str:
    return storage.join(run_root(cfg), stage)


def updated_date(key: str) -> str | None:
    m = _DATE.search(key)
    return m.group(1) if m else None


def partition_name(rel_key: str) -> str:
    """'works/updated_date=2024-01-01/part_000.gz' -> 'updated_date=2024-01-01__part_000'."""
    parts = rel_key.split("/")
    stem = parts[-1].split(".")[0]
    return "__".join([*parts[1:-1], stem]) if len(parts) > 1 else stem


def resolve_snapshot(cfg: Config) -> str:
    """The configured snapshot, or the newest one stage 1 has completed."""
    if cfg.snapshot_id:
        if not storage.exists(snapshot_manifest(cfg, cfg.snapshot_id)):
            raise SystemExit(f"Snapshot {cfg.snapshot_id!r} not found under {snapshots_root(cfg)} "
                             "— run stage 1 first.")
        return cfg.snapshot_id
    candidates = sorted(
        storage.relative(f.uri, snapshots_root(cfg)).split("/")[0]
        for f in storage.list_files(snapshots_root(cfg), suffix="snapshot_manifest.json"))
    if not candidates:
        raise SystemExit(f"No snapshots under {snapshots_root(cfg)} — run stage 1 first.")
    return candidates[-1]
