"""DuckDB connection that reads Parquet from local disk or S3."""
from __future__ import annotations

import duckdb

from pipeline.lib import storage


def connect(storage_root: str, region: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if storage.is_s3(storage_root):
        # Uses the same credentials as boto3 (env vars, ~/.aws, or the EC2 instance role).
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
        con.execute(f"CREATE OR REPLACE SECRET oa_s3 (TYPE s3, PROVIDER credential_chain, REGION '{region}')")
    return con


def parquet_source(dir_uri: str) -> str:
    """SQL expression reading every Parquet file in a directory."""
    pattern = storage.join(dir_uri, "*.parquet").replace("'", "''")
    return f"read_parquet('{pattern}', union_by_name=true)"


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    return con.execute(sql, params or []).fetchall()


def pairs(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[str, int]]:
    """Run a (label, count) query, rendering NULL labels as '(none)'."""
    return [("(none)" if k is None else str(k), int(v)) for k, v in rows(con, sql)]
