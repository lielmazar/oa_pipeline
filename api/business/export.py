"""Export business logic — build ZIP archive of pipeline results.

Ported from dashboard/blueprints/data.py (api_export_zip + helpers).
No Flask imports.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import struct
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from api.business.chart_defs import _CHART_DEFINITIONS


# ── LLM columns ───────────────────────────────────────────────────────────────

_LLM_EXPORT_COLUMNS = [
    ("llm_is_hydrocarbon",      "INTEGER"),
    ("llm_fuel_classification", "TEXT"),
    ("llm_ml_paradigm",         "TEXT"),
    ("llm_ml_method",           "TEXT"),
    ("llm_ml_role",             "TEXT"),
    ("llm_ml_algorithm",        "TEXT"),
    ("llm_scope",               "TEXT"),
    ("llm_ml_data_source",      "TEXT"),
    ("llm_confidence",          "TEXT"),
    ("llm_justification",       "TEXT"),
    ("llm_models",              "TEXT"),
    ("llm_result_raw",          "TEXT"),
    ("llm_batch_id",            "TEXT"),
    ("llm_processed_at",        "TEXT"),
    ("llm_tokens_input",        "INTEGER"),
    ("llm_tokens_output",       "INTEGER"),
    ("llm_tokens_reasoning",    "INTEGER"),
]


def _ensure_llm_columns(conn: sqlite3.Connection) -> None:
    for col, typedef in _LLM_EXPORT_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE filtered_records ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


# ── PNG watermark ─────────────────────────────────────────────────────────────

def _png_add_watermark(png_bytes: bytes, text: str = "cservices.online") -> bytes:
    PNG_SIG = b"\x89PNG\r\n\x1a\n"
    if not png_bytes.startswith(PNG_SIG):
        return png_bytes
    kw      = b"Comment"
    payload = kw + b"\x00" + text.encode("latin-1")
    crc_val = zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF
    chunk   = struct.pack(">I", len(payload)) + b"tEXt" + payload + struct.pack(">I", crc_val)
    ihdr_end = 33
    return png_bytes[:ihdr_end] + chunk + png_bytes[ihdr_end:]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _dump_table_csv(conn: sqlite3.Connection, table: str) -> str:
    try:
        cur  = conn.execute(f"SELECT * FROM [{table}]")  # noqa: S608
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception:
        return ""
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue()


def _dump_table_csv_chunks(conn: sqlite3.Connection, table: str, chunk_mb: int) -> list[str]:
    try:
        cur  = conn.execute(f"SELECT * FROM [{table}]")  # noqa: S608
        cols = [d[0] for d in cur.description]
    except Exception:
        return []
    header_buf = io.StringIO()
    csv.writer(header_buf).writerow(cols)
    header = header_buf.getvalue()
    target  = chunk_mb * 1024 * 1024
    chunks: list[str] = []
    buf = io.StringIO()
    buf.write(header)
    w   = csv.writer(buf)
    for row in cur:
        w.writerow(row)
        if buf.tell() >= target:
            chunks.append(buf.getvalue())
            buf = io.StringIO()
            buf.write(header)
            w = csv.writer(buf)
    tail = buf.getvalue()
    if len(tail) > len(header):
        chunks.append(tail)
    return chunks


# ── Co-occurrence export ──────────────────────────────────────────────────────

def _cooc_axes_from_config(pipeline_config: dict) -> list[dict]:
    groups = pipeline_config.get("groups") or pipeline_config.get("keyword_groups") or []
    if not groups and pipeline_config.get("subgroups"):
        groups = [
            {"name": "Fuel terms",   "subgroups": pipeline_config.get("subgroups", [])},
            {"name": "Impact terms", "subgroups": pipeline_config.get("impact_subgroups", [])},
        ]
    axes = []
    for gi, g in enumerate(groups):
        term_col = "matched_fuel_terms" if gi == 0 else "matched_impact_terms"
        sg_col   = "matched_fuel_subgroups" if gi == 0 else "matched_impact_subgroups"
        axes.append({"id": f"{gi}_subgroups", "label": f"{g.get('name', f'Group {gi}')} — subgroups",
                      "col": sg_col, "is_array": True, "terms": None})
        for sg in (g.get("subgroups") or []):
            if sg.get("is_others"):
                continue
            terms = sg.get("terms") or []
            if terms:
                axes.append({"id": f"{gi}_{sg['name']}", "label": sg["name"],
                              "col": term_col, "is_array": True, "terms": terms})
    axes += [
        {"id": "countries",    "label": "Countries",         "col": "authors_countries",       "is_array": True,  "terms": None},
        {"id": "institutions", "label": "Institutions",      "col": "authors_institutions",    "is_array": True,  "terms": None},
        {"id": "work_type",    "label": "Work Type",         "col": "type",                    "is_array": False, "terms": None},
        {"id": "oa_status",    "label": "OA Status",         "col": "open_access_oa_status",   "is_array": False, "terms": None},
        {"id": "pub_year",     "label": "Publication Year",  "col": "publication_year",        "is_array": False, "terms": None},
        {"id": "llm_fuel_cls", "label": "Fuel Classification","col": "llm_fuel_classification","is_array": True,  "terms": None},
        {"id": "llm_ml_role",  "label": "ML Role",           "col": "llm_ml_role",             "is_array": True,  "terms": None},
        {"id": "llm_ml_algo",  "label": "ML Algorithm",      "col": "llm_ml_algorithm",        "is_array": True,  "terms": None},
        {"id": "llm_scope",    "label": "Value Chain",       "col": "llm_scope",               "is_array": True,  "terms": None},
    ]
    return axes


def _cooc_query_sql(a: dict, b: dict) -> str:
    dim_a = "_a.value" if a["is_array"] else a["col"]
    dim_b = "_b.value" if b["is_array"] else b["col"]
    from_parts = ["filtered_records"]
    if a["is_array"]:
        from_parts.append(f"json_each({a['col']}) _a")
    if b["is_array"]:
        from_parts.append(f"json_each({b['col']}) _b")
    where = []
    if a["is_array"]:
        if a.get("terms"):
            vals = ",".join(f"'{t}'" for t in a["terms"])
            where.append(f"_a.value IN ({vals})")
        else:
            where.append("_a.value IS NOT NULL AND _a.value != '' AND LOWER(_a.value) NOT IN ('none','null','[]')")
    else:
        where.append(f"{a['col']} IS NOT NULL AND CAST({a['col']} AS TEXT) != ''")
    if b["is_array"]:
        if b.get("terms"):
            vals = ",".join(f"'{t}'" for t in b["terms"])
            where.append(f"_b.value IN ({vals})")
        else:
            where.append("_b.value IS NOT NULL AND _b.value != '' AND LOWER(_b.value) NOT IN ('none','null','[]')")
    else:
        where.append(f"{b['col']} IS NOT NULL AND CAST({b['col']} AS TEXT) != ''")
    return (
        f"SELECT {dim_a} AS dim_a, {dim_b} AS dim_b, COUNT(DISTINCT openalex_id) AS count "
        f"FROM {', '.join(from_parts)} WHERE {' AND '.join(where)} GROUP BY dim_a, dim_b"
    )


def _export_cooccurrence_data(
    zf: zipfile.ZipFile, prefix: str, db_path: Path, pipeline_config: dict,
) -> list[str]:
    axes = _cooc_axes_from_config(pipeline_config)
    written: list[str] = []
    if len(axes) < 2:
        return written
    combos = [(a, b) for i, a in enumerate(axes[:10]) for j, b in enumerate(axes[:10]) if i != j]
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA query_only = ON")
        for ax_a, ax_b in combos:
            sql = _cooc_query_sql(ax_a, ax_b)
            try:
                raw = conn.execute(sql).fetchall()
            except Exception:
                continue
            rows = [(r[0], r[1], r[2]) for r in raw if r[0] and r[1] and r[2] and r[2] > 0]
            if not rows:
                continue
            safe = f"{ax_a['id']}_x_{ax_b['id']}"
            csv_buf = io.StringIO()
            w = csv.writer(csv_buf)
            w.writerow([ax_a["label"], ax_b["label"], "count"])
            w.writerows(rows)
            zf.writestr(f"{prefix}charts/cooccurrence/{safe}.csv", csv_buf.getvalue())
            written.append(safe)
    return written


# ── Main build function ───────────────────────────────────────────────────────

def build_export_zip(pipeline: dict, db_format: str = "csv", chunk_mb: int = 50) -> bytes:
    """Build the full export ZIP and return its bytes."""
    pipeline_id     = pipeline["id"]
    db_path_str     = pipeline.get("output_db_path")
    db_path         = Path(db_path_str) if db_path_str else None
    pipeline_dir    = db_path.parent if db_path else None
    pipeline_config = pipeline.get("config") or {}
    ts              = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name       = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (pipeline.get("name") or f"pipeline_{pipeline_id}")
    )
    prefix = f"{safe_name}/"

    all_charts     = list(_CHART_DEFINITIONS.values())

    tables: list[str] = []
    record_count = 0
    table_row_counts: dict[str, int] = {}

    if db_path and db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                tables = [
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                ]
                for tbl in tables:
                    try:
                        n = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]  # noqa: S608
                        table_row_counts[tbl] = n
                        if tbl == "filtered_records":
                            record_count = n
                    except Exception:
                        table_row_counts[tbl] = 0
        except Exception:
            pass

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # 1. Root config files
        if pipeline_dir:
            kw = pipeline_dir / "keywords.json"
            if kw.exists():
                zf.write(str(kw), f"{prefix}keywords.json")
            sp = pipeline_dir / "pipeline_state.json"
            if sp.exists():
                zf.write(str(sp), f"{prefix}pipeline_state.json")
            bs = pipeline_dir / "batch_runs" / "state.json"
            if bs.exists():
                zf.write(str(bs), f"{prefix}batch_state.json")

        # 2. Chart SQL
        sql_lines: list[str] = []
        for spec in all_charts:
            sql_lines.append(f"-- {spec.title} ({spec.id})")
            if spec.llm_only:
                sql_lines.append("-- NOTE: requires LLM classification")
            sql_lines.append(spec.format_sql(top_n=20))
            sql_lines.append("")
        zf.writestr(f"{prefix}chart_queries.sql", "\n".join(sql_lines))

        # 3. Co-occurrence CSVs
        if db_path and db_path.exists():
            _export_cooccurrence_data(zf, prefix, db_path, pipeline_config)

        # 4. Database export
        if db_path and db_path.exists() and tables:
            try:
                with sqlite3.connect(db_path) as _wal_conn:
                    _ensure_llm_columns(_wal_conn)
                    _wal_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass

            if db_format in ("sqlite", "both"):
                zf.write(str(db_path), f"{prefix}data/output.sqlite")

            if db_format in ("csv", "both"):
                with sqlite3.connect(db_path) as conn:
                    for table in tables:
                        csv_text = _dump_table_csv(conn, table)
                        if csv_text:
                            zf.writestr(f"{prefix}data/{table}.csv", csv_text)

            if db_format == "csv_chunks":
                with sqlite3.connect(db_path) as conn:
                    for table in tables:
                        chunks = _dump_table_csv_chunks(conn, table, chunk_mb)
                        if len(chunks) == 1:
                            zf.writestr(f"{prefix}data/{table}.csv", chunks[0])
                        else:
                            for i, chunk in enumerate(chunks):
                                zf.writestr(f"{prefix}data/{table}_part{i+1:03d}.csv", chunk)

        # 5. Logs
        if pipeline_dir and pipeline_dir.exists():
            for log_file in sorted(pipeline_dir.glob("*.log")):
                zf.write(str(log_file), f"{prefix}logs/{log_file.name}")
            for log_file in sorted(pipeline_dir.rglob("**/*.log")):
                if log_file.parent == pipeline_dir:
                    continue
                rel = log_file.relative_to(pipeline_dir)
                zf.write(str(log_file), f"{prefix}logs/{str(rel).replace(chr(92), '/')}")
            batch_dir = pipeline_dir / "batch_runs"
            if batch_dir.exists():
                for jl in sorted(batch_dir.glob("*.jsonl")):
                    if jl.stat().st_size <= 50 * 1024 * 1024:
                        zf.write(str(jl), f"{prefix}logs/batch/{jl.name}")

        # 6. Skip-stats summary
        state_path = pipeline_dir / "pipeline_state.json" if pipeline_dir else None
        if state_path and state_path.exists():
            try:
                _state = json.loads(state_path.read_text(encoding="utf-8"))
                _skip  = _state.get("skip_stats", {})
                _seen  = _state.get("total_records_seen", 0)
                _ins   = _state.get("total_records_inserted", 0)
                _ferr  = _state.get("file_errors", 0)
                _rerr  = _state.get("record_errors", 0)
                _SKIP_ORDER = ["not_english", "no_doi", "no_abstract", "no_keywords",
                               "empty_abstract", "json_error", "empty_line", "no_id", "processing_error"]
                _SKIP_LABEL = {
                    "not_english": "non-English (pre-screened)", "no_doi": "no DOI",
                    "no_abstract": "no abstract", "no_keywords": "no matching keywords",
                    "empty_abstract": "empty abstract", "json_error": "JSON parse error",
                    "empty_line": "empty/blank line", "no_id": "no OpenAlex ID",
                    "processing_error": "processing error",
                }
                skip_lines = [
                    "Screening funnel / skip-reason summary", "=" * 40,
                    f"Records scanned  : {_seen:,}",
                ]
                for k in _SKIP_ORDER:
                    if k in _skip:
                        skip_lines.append(f"  ↳ {_SKIP_LABEL.get(k, k):<32}: {_skip[k]:>12,}")
                for k, v in sorted(_skip.items(), key=lambda x: -x[1]):
                    if k not in _SKIP_ORDER:
                        skip_lines.append(f"  ↳ {k:<32}: {v:>12,}")
                _unaccounted = _seen - sum(_skip.values()) - _ins if _seen > 0 else 0
                if _unaccounted > 0:
                    skip_lines.append(f"  ↳ {'unaccounted (not tracked)':<32}: {_unaccounted:>12,}")
                skip_lines += [
                    f"Saved (matched)  : {_ins:,}",
                    f"Match rate       : {_ins/_seen*100:.4f}%" if _seen else "Match rate       : n/a",
                    "",
                    f"File errors      : {_ferr:,}",
                    f"Record errors    : {_rerr:,}",
                    "",
                    "See pipeline.log for per-record error details.",
                ]
                zf.writestr(f"{prefix}logs/skip_stats_summary.txt", "\n".join(skip_lines))
            except Exception:
                pass

        # 7. Pipeline metadata
        meta = {k: v for k, v in pipeline.items() if k != "config"}
        meta["config"] = pipeline_config
        zf.writestr(f"{prefix}pipeline_metadata.json", json.dumps(meta, indent=2, default=str))

        # 8. README
        db_size_str = (
            f"{round(db_path.stat().st_size / 1024**2, 1)} MB"
            if db_path and db_path.exists() else "n/a"
        )
        db_format_desc = (
            "SQLite + CSV" if db_format == "both"
            else f"CSV chunks ({chunk_mb} MB)" if db_format == "csv_chunks"
            else db_format.upper()
        )
        table_lines_md = "\n".join(
            f"- `{tbl}`: {table_row_counts.get(tbl, 0):,} rows" for tbl in tables
        ) or "*(no tables yet)*"
        readme = (
            f"# Pipeline Export: {pipeline.get('name', f'Pipeline {pipeline_id}')}\n\n"
            f"**Exported:** {ts} UTC  \n"
            f"**Records:** {record_count:,}  \n"
            f"**DB size:** {db_size_str}  \n"
            f"**Format:** {db_format_desc}  \n\n"
            "---\n\n"
            "## Files\n\n"
            "| File / Folder | Description |\n|---|---|\n"
            "| `keywords.json` | Keyword groups used to screen OpenAlex records |\n"
            "| `pipeline_state.json` | Pipeline state: skip statistics, record counts |\n"
            "| `batch_state.json` | LLM batch API state |\n"
            "| `pipeline_metadata.json` | Full pipeline configuration |\n"
            "| `chart_queries.sql` | All SQL queries for charts |\n"
            "| `charts/cooccurrence/` | Co-occurrence CSV exports |\n"
            f"| `data/` | Database export ({db_format_desc}) |\n"
            "| `logs/` | Pipeline log files and skip-stats summary |\n\n"
            "### Database tables\n\n"
            + table_lines_md
        )
        zf.writestr(f"{prefix}README.md", readme)

    buf.seek(0)
    return buf.read()
