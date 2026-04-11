"""Simple export — SQLite download and auto-chunked CSV ZIP for Claude Desktop."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / ".." / "pipeline" / "research_schema.json"
_CHUNK_TARGET_BYTES = 25 * 1024 * 1024  # 25 MB per CSV part (safe margin under 30 MB)


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _build_schema_csv(schema: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["column", "type", "source", "description"])
    for col_name, info in schema.get("columns", {}).items():
        w.writerow([col_name, info.get("type", ""), info.get("source", ""), info.get("description", "")])
    return buf.getvalue()


def build_csv_export_zip(research_db_path: Path) -> bytes:
    """Build a ZIP containing chunked CSVs, schema.csv, and research_schema.json."""
    from api.db.research import iter_all_records_csv

    schema = _load_schema()
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("research_schema.json", json.dumps(schema, indent=2, ensure_ascii=False))
        zf.writestr("schema.csv", _build_schema_csv(schema))

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

    return buf.getvalue()
