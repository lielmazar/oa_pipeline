"""S3 code paths (mocked with moto) and the multi-process runner."""
from __future__ import annotations

import gzip
import json
from dataclasses import asdict

import boto3
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from pipeline import stage1_mirror, stage2_journals, stage3_keywords
from pipeline.lib import storage
from pipeline.lib.config import Stage2Config
from tests.conftest import corpus


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        storage._clients.clear()
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="openalex")
        client.create_bucket(Bucket="mine")
        yield client
    storage._clients.clear()


def _put_works(client, key: str, records: list[dict]) -> None:
    body = gzip.compress(b"".join(json.dumps(r).encode() + b"\n" for r in records))
    client.put_object(Bucket="openalex", Key=key, Body=body)


def test_stage1_mirrors_s3_to_s3_and_stage2_worker_reads_s3(s3, tmp_path):
    recs = corpus()
    _put_works(s3, "data/jsonl/works/updated_date=2025-03-01/part_0000.gz", recs)
    _put_works(s3, "data/jsonl/works/updated_date=2025-03-02/part_0000.gz", recs[:2])
    s3.put_object(Bucket="openalex", Key="data/jsonl/works/deleted_ids.csv.gz", Body=b"x")
    s3.put_object(Bucket="openalex", Key="data/jsonl/works/manifest.json", Body=json.dumps({
        "date": "2025-03-31", "entity": "works", "files": [
            {"url": "s3://openalex/data/jsonl/works/updated_date=2025-03-01/part_0000.gz", "meta": {"record_count": 14}},
            {"url": "s3://openalex/data/jsonl/works/updated_date=2025-03-02/part_0000.gz", "meta": {"record_count": 2}},
        ]}))
    s3.put_object(Bucket="openalex", Key="data/jsonl/manifest.json", Body=json.dumps({"date": "2025-03-31"}))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"storage_root: s3://mine/oa\nrun_id: t\nworkers: 1\ntmp_dir: {tmp_path / 'tmp'}\n"
                   "stage1:\n  source: s3://openalex/data/jsonl\n  entities: [works]\n")

    s1 = stage1_mirror.main(["--config", str(cfg)])
    assert s1["snapshot_id"] == "2025-03-31" and s1["copied"] == 2 and s1["complete"]
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket="mine")["Contents"])
    assert "oa/snapshots/2025-03-31/data/works/updated_date=2025-03-01/part_0000.gz" in keys
    assert "oa/snapshots/2025-03-31/snapshot_manifest.json" in keys
    assert "oa/snapshots/2025-03-31/data/works/manifest.json" in keys
    assert not any("deleted_ids" in k for k in keys)
    assert stage1_mirror.main(["--config", str(cfg)])["skipped_existing"] == 2

    # Stage 2 worker against S3 input/output (reports use DuckDB+httpfs, which moto can't serve).
    stage2_journals._init_worker(asdict(Stage2Config()))
    stats = stage2_journals.process_partition({
        "name": "p0", "size": 1,
        "uri": "s3://mine/oa/snapshots/2025-03-31/data/works/updated_date=2025-03-01/part_0000.gz",
        "out_parquet": "s3://mine/oa/runs/t/stage2/parquet/p0.parquet",
        "out_stats": "s3://mine/oa/runs/t/stage2/stats/p0.json",
        "tmp_dir": str(tmp_path / "w"),
    })
    assert stats["records_in"] == 14 and stats["kept"] == 5
    assert storage.read_json("s3://mine/oa/runs/t/stage2/stats/p0.json")["kept"] == 5
    assert not any((tmp_path / "w").iterdir())   # temp files cleaned up


def test_multiprocess_workers(workspace):
    ws = workspace
    ws["config"].write_text(ws["config"].read_text().replace("workers: 1", "workers: 2"))
    stage1_mirror.main(["--config", str(ws["config"])])
    s2 = stage2_journals.main(["--config", str(ws["config"])])
    s3 = stage3_keywords.main(["--config", str(ws["config"])])
    assert (s2["kept"], s3["matched"]) == (5, 3)
    t = pq.read_table(ws["store"] / "runs" / "test" / "stage3" / "results.parquet")
    assert t.num_rows == 3 and "matched_impact_subgroups" in t.column_names


def test_failed_partition_is_reported_and_retried(workspace, monkeypatch):
    ws = workspace
    stage1_mirror.main(["--config", str(ws["config"])])
    real = stage2_journals.process_partition
    monkeypatch.setattr("pipeline.lib.parallel.time.sleep", lambda s: None)

    def flaky(task):
        if task["name"].startswith("updated_date=2025-02-01"):
            raise OSError("simulated network error")
        return real(task)

    monkeypatch.setattr(stage2_journals, "process_partition", flaky)
    s2 = stage2_journals.main(["--config", str(ws["config"])])
    assert not s2["complete"] and len(s2["partitions_failed"]) == 1
    assert "simulated network error" in s2["partitions_failed"][0]["error"]

    monkeypatch.setattr(stage2_journals, "process_partition", real)
    s2 = stage2_journals.main(["--config", str(ws["config"])])   # resume picks up the failed one
    assert s2["complete"] and s2["kept"] == 5
