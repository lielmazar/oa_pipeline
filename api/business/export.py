"""Export — Full ZIP or chunked-CSV ZIP for Claude Desktop."""
from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from api.business.qa import compute_irr, list_ratings

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / ".." / "pipeline" / "research_schema.json"
_CHUNK_TARGET_BYTES = 25 * 1024 * 1024  # 25 MB per CSV part


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _build_schema_csv(schema: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["column", "type", "source", "description"])
    for col_name, info in schema.get("columns", {}).items():
        w.writerow([col_name, info.get("type", ""), info.get("source", ""), info.get("description", "")])
    return buf.getvalue()


def _build_ratings_csv(ratings: list[dict]) -> str:
    if not ratings:
        return ""
    buf = io.StringIO()
    cols = ["id", "pipeline_id", "openalex_id", "user_id", "field",
            "llm_value", "human_value", "created_at"]
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in ratings:
        w.writerow(r)
    return buf.getvalue()


def _build_starred_csv(
    research_db_path: Path,
    meta_db: sqlite3.Connection,
    pipeline_id: int,
    user_id: int,
) -> str:
    """Build CSV of starred records (full row data from research DB)."""
    rows = meta_db.execute(
        "SELECT openalex_id FROM starred_records WHERE pipeline_id = ? AND user_id = ?",
        [pipeline_id, user_id],
    ).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return ""
    placeholders = ",".join(["?"] * len(ids))
    conn = sqlite3.connect(str(research_db_path), check_same_thread=False)
    try:
        cur = conn.execute(
            f"SELECT * FROM filtered_records WHERE openalex_id IN ({placeholders})", ids
        )
        columns = [d[0] for d in cur.description] if cur.description else []
        data_rows = cur.fetchall()
    finally:
        conn.close()
    if not columns:
        return ""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    for r in data_rows:
        w.writerow(list(r))
    return buf.getvalue()


def _write_common_artifacts(
    zf: zipfile.ZipFile,
    research_db_path: Path,
    meta_db: sqlite3.Connection | None,
    pipeline_id: int | None,
    user_id: int | None,
) -> dict:
    """Write schema, IRR, QA ratings, and starred records into the ZipFile."""
    schema = _load_schema()
    zf.writestr("research_schema.json", json.dumps(schema, indent=2, ensure_ascii=False))
    zf.writestr("schema.csv", _build_schema_csv(schema))

    if meta_db is not None and pipeline_id is not None:
        ratings = list_ratings(meta_db, pipeline_id)
        if ratings:
            zf.writestr("qa_ratings.csv", _build_ratings_csv(ratings))

        irr = compute_irr(meta_db, pipeline_id)
        if irr.get("fields"):
            zf.writestr("irr_statistics.json", json.dumps(irr, indent=2, ensure_ascii=False))

        if user_id is not None:
            starred_csv = _build_starred_csv(research_db_path, meta_db, pipeline_id, user_id)
            if starred_csv:
                zf.writestr("starred_records.csv", starred_csv)

    return schema


def _write_chunked_csvs(zf: zipfile.ZipFile, research_db_path: Path) -> None:
    """Write records as 25 MB chunked CSVs into the ZipFile."""
    from api.db.research import iter_all_records_csv

    part_num = 1
    part_buf = io.StringIO()
    part_size = 0
    header_line: str | None = None

    for line in iter_all_records_csv(research_db_path):
        if header_line is None:
            header_line = line
            part_buf.write(line)
            part_size += len(line.encode("utf-8"))
            continue

        line_bytes = len(line.encode("utf-8"))
        if part_size + line_bytes > _CHUNK_TARGET_BYTES and part_size > 0:
            zf.writestr(f"records_part{part_num}.csv", part_buf.getvalue())
            part_num += 1
            part_buf = io.StringIO()
            part_buf.write(header_line)
            part_size = len(header_line.encode("utf-8"))

        part_buf.write(line)
        part_size += line_bytes

    if part_size > 0:
        zf.writestr(f"records_part{part_num}.csv", part_buf.getvalue())


def build_csv_export_zip(
    research_db_path: Path,
    meta_db: sqlite3.Connection | None = None,
    pipeline_id: int | None = None,
    user_id: int | None = None,
) -> bytes:
    """For Claude: chunked CSVs + schema + IRR + starred."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_common_artifacts(zf, research_db_path, meta_db, pipeline_id, user_id)
        _write_chunked_csvs(zf, research_db_path)
    return buf.getvalue()


def build_full_export_zip(
    research_db_path: Path,
    meta_db: sqlite3.Connection | None = None,
    pipeline_id: int | None = None,
    user_id: int | None = None,
) -> bytes:
    """Full export: single records CSV + SQLite copy + schema + IRR + starred."""
    from api.db.research import iter_all_records_csv

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_common_artifacts(zf, research_db_path, meta_db, pipeline_id, user_id)

        csv_buf = io.StringIO()
        for line in iter_all_records_csv(research_db_path):
            csv_buf.write(line)
        if csv_buf.tell() > 0:
            zf.writestr("records.csv", csv_buf.getvalue())

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
        import os
        os.close(tmp_fd)
        try:
            shutil.copy2(str(research_db_path), tmp_path)
            zf.write(tmp_path, "research.sqlite")
        finally:
            os.unlink(tmp_path)

    return buf.getvalue()
