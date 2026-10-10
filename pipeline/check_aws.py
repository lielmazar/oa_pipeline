"""Preflight check: can this machine run the pipeline against your bucket?

    python -m pipeline.check_aws
    python -m pipeline.check_aws --storage-root s3://my-bucket/openalex

Checks, in order: AWS credentials, read access to the public OpenAlex
bucket, write/read/delete in storage_root, and DuckDB reading Parquet from
storage_root (used by the reports and explore.py). Writes and removes one
small test file under <storage_root>/_preflight/.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.lib import duck, storage
from pipeline.lib.config import add_common_args, config_from_args


def _ok(msg: str) -> None:
    print(f"  OK    {msg}")


def _fail(msg: str, err: Exception | str) -> None:
    print(f"  FAIL  {msg}\n        {err}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    cfg = config_from_args(p.parse_args(argv))
    root = cfg.storage_root
    print(f"storage_root = {root}\nOpenAlex source = {cfg.stage1.source}\n")
    failures = 0

    if storage.is_s3(root):
        try:
            import boto3
            ident = boto3.client("sts").get_caller_identity()
            _ok(f"credentials: {ident['Arn']}")
        except Exception as e:  # noqa: BLE001 — report every check
            _fail("AWS credentials (configure a profile, or attach the IAM role to the instance)", e)
            return 1
        if "CHANGE-ME" in root:
            _fail("storage_root still has the placeholder bucket", "edit config.yaml or pass --storage-root")
            return 1

    try:
        m = storage.read_json(storage.join(cfg.stage1.source, "manifest.json"))
        works = next(e for e in m["entities"] if e["entity"] == "works")
        _ok(f"read OpenAlex release {m['date']}: {len(works['files']):,} works files, "
            f"{works['content_length'] / 1e9:,.0f} GB")
    except Exception as e:  # noqa: BLE001
        failures += 1
        _fail("read s3://openalex/data/jsonl/manifest.json", e)

    probe = storage.join(root, "_preflight", "probe.parquet")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "probe.parquet"
            pq.write_table(pa.table({"x": [1, 2, 3]}), local)
            storage.upload(local, probe)
        assert storage.size(probe), "file not found after upload"
        _ok(f"write {probe}")
    except Exception as e:  # noqa: BLE001
        failures += 1
        _fail(f"write to {root}", e)
        return 1

    try:
        con = duck.connect(root, cfg.aws_region)
        total = con.execute(f"SELECT sum(x) FROM read_parquet('{probe}')").fetchone()[0]
        assert total == 6, f"unexpected result {total}"
        _ok("DuckDB reads Parquet from storage_root (reports + explore.py)")
    except Exception as e:  # noqa: BLE001
        failures += 1
        _fail("DuckDB read from storage_root", e)

    try:
        storage.delete_prefix(storage.join(root, "_preflight"))
        _ok("delete (needed for --restart)")
    except Exception as e:  # noqa: BLE001
        failures += 1
        _fail("delete in storage_root", e)

    print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
