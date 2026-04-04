"""Data blueprint — /api/db/*, /api/charts/*, /api/subsets, /api/log, /api/export-zip"""
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

from flask import Blueprint, Response, jsonify, request
from core import chart_gen as _chart_gen

from blueprints.auth import require_auth, get_current_user_id
from core import db_api
from core.charts import (
    get_chart_configs,
    get_chart_definition,
    get_chart_sql,
    has_chart,
)
from core.config import DB_PAGE_SIZE, DB_QUERY_MAX_ROWS
from core.utils import tail as _tail
from models.pipeline import get_pipeline

data_bp = Blueprint("data", __name__)


def _get_pipeline_db(pipeline_id: int, uid: int) -> tuple[Path | None, str | None]:
    """Return (db_path, error_msg) for a pipeline's output SQLite."""
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return None, "pipeline not found"
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return None, "pipeline has no database yet"
    db_path = Path(db_path_str)
    if not db_path.exists():
        return None, "database file not found"
    return db_path, None


def _get_subsets_path(pipeline_id: int, uid: int) -> tuple[Path | None, str | None]:
    """Return (subsets_json_path, error_msg) for a pipeline's saved subsets file."""
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return None, "pipeline not found"
    db_path_str = pipeline.get("output_db_path")
    if not db_path_str:
        return None, "pipeline has no database yet"
    return Path(db_path_str).parent / "subsets.json", None


def _read_subsets(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


# ── Saved subsets ──────────────────────────────────────────────────────────────

@data_bp.route("/api/subsets", methods=["GET", "POST", "DELETE"])
def api_subsets():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()

    if request.method == "GET":
        pipeline_id = request.args.get("pipeline_id", type=int)
        if not pipeline_id:
            return jsonify({"error": "pipeline_id required"}), 400
        path, err_msg = _get_subsets_path(pipeline_id, uid)
        if err_msg:
            return jsonify([])   # no DB yet → empty list, not an error
        return jsonify(_read_subsets(path))

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        pipeline_id = data.get("pipeline_id")
        name = (data.get("name") or "").strip()
        sql = (data.get("sql") or "").strip()
        if not pipeline_id or not name or not sql:
            return jsonify({"error": "pipeline_id, name, and sql required"}), 400
        path, err_msg = _get_subsets_path(int(pipeline_id), uid)
        if err_msg:
            return jsonify({"error": err_msg}), 404
        subsets = _read_subsets(path)
        subsets.append({"name": name, "sql": sql,
                        "created_at": datetime.now(timezone.utc).isoformat()})
        path.write_text(json.dumps(subsets, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return jsonify(subsets)

    if request.method == "DELETE":
        data = request.get_json(silent=True) or {}
        pipeline_id = data.get("pipeline_id")
        idx = data.get("index")
        if not pipeline_id or idx is None:
            return jsonify({"error": "pipeline_id and index required"}), 400
        path, err_msg = _get_subsets_path(int(pipeline_id), uid)
        if err_msg:
            return jsonify({"error": err_msg}), 404
        subsets = _read_subsets(path)
        if 0 <= int(idx) < len(subsets):
            subsets.pop(int(idx))
            path.write_text(json.dumps(subsets, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return jsonify(subsets)

    return jsonify({"error": "method not allowed"}), 405


# ── DB schema ──────────────────────────────────────────────────────────────────

@data_bp.route("/api/db/schema")
def api_db_schema():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404
    return jsonify(db_api.get_schema(db_path))


# ── DB records ─────────────────────────────────────────────────────────────────

@data_bp.route("/api/db/records")
def api_db_records():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    page = max(1, request.args.get("page", 1, type=int))
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "publication_year")
    direction = request.args.get("dir", "desc")
    page_size = request.args.get("page_size", DB_PAGE_SIZE, type=int)
    # Optional comma-separated lists for search fields and visible columns
    search_cols_raw = request.args.get("search_cols", "").strip()
    visible_cols_raw = request.args.get("visible_cols", "").strip()
    search_cols = [c.strip() for c in search_cols_raw.split(",") if c.strip()] or None
    visible_cols = [c.strip() for c in visible_cols_raw.split(",") if c.strip()] or None

    result = db_api.get_records(
        db_path, page=page, search=search, sort=sort, direction=direction,
        per_page=page_size, search_cols=search_cols, visible_cols=visible_cols,
    )
    return jsonify(result)


@data_bp.route("/api/db/records/<record_id>")
def api_db_record_detail(record_id: str):
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    record = db_api.get_record_detail(db_path, record_id)
    if record is None:
        return jsonify({"error": "record not found"}), 404
    return jsonify(record)


# ── DB query ───────────────────────────────────────────────────────────────────

@data_bp.route("/api/db/query", methods=["POST"])
def api_db_query():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    pipeline_id = data.get("pipeline_id") or request.args.get("pipeline_id", type=int)
    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    db_path, err_msg = _get_pipeline_db(int(pipeline_id), uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    sql = (data.get("sql") or "").strip()
    if not sql:
        return jsonify({"error": "sql is required"}), 400

    # Allow caller to request a higher limit (capped for safety)
    requested = data.get("max_rows")
    max_rows = min(int(requested), 200_000) if requested else DB_QUERY_MAX_ROWS
    result = db_api.run_query(db_path, sql, max_rows=max_rows)
    return jsonify(result)


# ── Chart data ────────────────────────────────────────────────────────────────

@data_bp.route("/api/charts/configs")
def api_chart_configs():
    """Return all chart configs (with interpolated SQL) — no DB required."""
    err = require_auth()
    if err:
        return err
    top_n = request.args.get("top_n", 20, type=int)
    return jsonify(get_chart_configs(top_n))


@data_bp.route("/api/charts/data")
def api_chart_data():
    """Run a single chart query and return data + the SQL used."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    chart_id = request.args.get("chart", "").strip()
    top_n = request.args.get("top_n", 20, type=int)

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    if not has_chart(chart_id):
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400

    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    cfg = get_chart_definition(chart_id)
    if cfg is None:
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400
    sql = get_chart_sql(chart_id, top_n)
    result = db_api.run_query(db_path, sql, max_rows=500)

    return jsonify({
        "id": chart_id,
        "title": cfg.title,
        "sql": sql,
        "type": cfg.renderer,
        "x_key": cfg.x_key,
        "y_key": cfg.y_key,
        "llm_only": cfg.llm_only,
        "data": result.get("rows", []),
        "columns": result.get("columns", []),
        "error": result.get("error"),
    })


@data_bp.route("/api/charts/image")
def api_chart_image():
    """Render a chart server-side with matplotlib and return a PNG image."""
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    chart_id = request.args.get("chart", "").strip()
    top_n = request.args.get("top_n", 20, type=int)

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    if not has_chart(chart_id):
        return jsonify({"error": f"unknown chart '{chart_id}'"}), 400

    db_path, err_msg = _get_pipeline_db(pipeline_id, uid)
    if err_msg:
        return jsonify({"error": err_msg}), 404

    pipeline = get_pipeline(pipeline_id, uid)
    pipeline_config = (pipeline.get("config") or {}) if pipeline else {}

    cutoff_year        = request.args.get("cutoff_year",        2000, type=int)
    terms_per_subgroup = request.args.get("terms_per_subgroup", None, type=int)

    subset_idx = request.args.get("subset_idx", type=int)
    subset_sql = None
    if subset_idx is not None:
        subsets_path, _ = _get_subsets_path(pipeline_id, uid)
        if subsets_path:
            subsets = _read_subsets(subsets_path)
            if 0 <= subset_idx < len(subsets):
                subset_sql = subsets[subset_idx].get("sql")

    png = _chart_gen.generate(
        db_path,
        chart_id,
        pipeline_config,
        top_n=top_n,
        cutoff_year=cutoff_year,
        subset_sql=subset_sql,
        terms_per_subgroup=terms_per_subgroup,
    )
    return Response(png, mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


# ── Log tail ──────────────────────────────────────────────────────────────────

@data_bp.route("/api/log")
def api_log():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    lines = request.args.get("lines", 200, type=int)

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    # Determine log path from pipeline base dir
    db_path = pipeline.get("output_db_path")
    if db_path:
        log_path = Path(db_path).parent / "pipeline.log"
    else:
        return jsonify({"lines": []})

    if not log_path.exists():
        return jsonify({"lines": []})

    return jsonify({"lines": _tail(log_path, lines)})


# ── Export ZIP ────────────────────────────────────────────────────────────────

# Charts included in the ZIP (non-LLM primary charts only for speed/reliability)
def _png_add_watermark(png_bytes: bytes, text: str = "cservices.online") -> bytes:
    """Inject a hidden tEXt metadata chunk into a PNG byte string."""
    PNG_SIG = b"\x89PNG\r\n\x1a\n"
    if not png_bytes.startswith(PNG_SIG):
        return png_bytes
    kw      = b"Comment"
    payload = kw + b"\x00" + text.encode("latin-1")
    crc_val = zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF
    chunk   = struct.pack(">I", len(payload)) + b"tEXt" + payload + struct.pack(">I", crc_val)
    ihdr_end = 33
    return png_bytes[:ihdr_end] + chunk + png_bytes[ihdr_end:]


def _dump_table_csv(conn: sqlite3.Connection, table: str) -> str:
    """Return a CSV string for the given table, empty string on error."""
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


def _dump_table_csv_chunks(
    conn: sqlite3.Connection, table: str, chunk_mb: int
) -> list[str]:
    """Return a list of CSV strings, each roughly <= chunk_mb MB, with header repeated."""
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


@data_bp.route("/api/pipelines/<int:pipeline_id>/export-zip")
def api_export_zip(pipeline_id: int):
    """Build and stream a ZIP with per-stage HTML reports, charts, data, logs, and metadata.

    Query params:
      db_format = csv | sqlite | both | csv_chunks_N   (default: csv)
        csv            — all tables as individual CSV files
        sqlite         — copy of the raw SQLite database
        both           — CSV + SQLite
        csv_chunks_N   — each table split into N-MB CSV chunks (e.g. csv_chunks_50)

    ZIP structure:
      summary_report.html             pipeline overview + key charts (root level)
      pipeline_metadata.json
      manifest.txt
      data/                           record data (format per db_format)
      logs/                           pipeline log files
      stages/
        1_keyword_selection/          report.html, keywords.json
        2_openalex_search/            report.html, pipeline_state.json
        3_analysis/                   report.html, charts/*.png, chart_queries.sql
        4_llm_classification/         report.html, batch_state.json
        5_llm_analysis/               report.html, charts_llm/*.png
    """
    err = require_auth()
    if err:
        return err
    uid      = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    # ── DB export format ──────────────────────────────────────────────────────
    db_format_raw = request.args.get("db_format", "csv")
    chunk_mb: int | None = None
    if db_format_raw.startswith("csv_chunks_"):
        try:
            chunk_mb = int(db_format_raw.split("_")[-1])
        except ValueError:
            chunk_mb = 50
        db_format = "csv_chunks"
    else:
        db_format = db_format_raw if db_format_raw in ("csv", "sqlite", "both") else "csv"

    # ── Paths ─────────────────────────────────────────────────────────────────
    db_path_str     = pipeline.get("output_db_path")
    db_path         = Path(db_path_str) if db_path_str else None
    pipeline_dir    = Path(db_path_str).parent if db_path_str else None
    pipeline_config = pipeline.get("config") or {}
    ts              = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name       = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (pipeline.get("name") or f"pipeline_{pipeline_id}")
    )
    prefix = f"{safe_name}/"

    from core.charts import _CHART_DEFINITIONS
    from core import report_builder as _rb

    non_llm_charts = [c for c in _CHART_DEFINITIONS.values() if not c.llm_only]
    llm_charts     = [c for c in _CHART_DEFINITIONS.values() if c.llm_only]

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

        # ── 1. Summary report (root level) ────────────────────────────────────
        try:
            summary_html = _rb.build_summary_report(
                pipeline, db_path, pipeline_dir, pipeline_config, _chart_gen
            )
            zf.writestr(f"{prefix}summary_report.html", summary_html)
        except Exception:
            pass

        # ── 2. Stage 1 — Keyword Selection ────────────────────────────────────
        s1 = f"{prefix}stages/1_keyword_selection/"
        try:
            zf.writestr(s1 + "report.html",
                        _rb.build_keywords_report(pipeline, pipeline_dir))
        except Exception:
            pass
        if pipeline_dir:
            kw = pipeline_dir / "keywords.json"
            if kw.exists():
                zf.write(str(kw), s1 + "keywords.json")

        # ── 3. Stage 2 — OpenAlex Search ─────────────────────────────────────
        s2 = f"{prefix}stages/2_openalex_search/"
        try:
            zf.writestr(s2 + "report.html",
                        _rb.build_search_report(pipeline, pipeline_dir))
        except Exception:
            pass
        if pipeline_dir:
            sp = pipeline_dir / "pipeline_state.json"
            if sp.exists():
                zf.write(str(sp), s2 + "pipeline_state.json")

        # ── 4. Stage 3 — Analysis (non-LLM charts) ───────────────────────────
        s3 = f"{prefix}stages/3_analysis/"
        try:
            zf.writestr(s3 + "report.html",
                        _rb.build_analysis_report(
                            pipeline, db_path, pipeline_config, _chart_gen, llm=False))
        except Exception:
            pass

        analysis_charts_written = 0
        if db_path and db_path.exists():
            for spec in non_llm_charts:
                try:
                    png = _chart_gen.generate(db_path, spec.id, pipeline_config, top_n=20)
                    zf.writestr(s3 + f"charts/{spec.id}.png", _png_add_watermark(png))
                    analysis_charts_written += 1
                except Exception:
                    pass

        sql_lines: list[str] = []
        for spec in non_llm_charts:
            sql_lines.append(f"-- {spec.title} ({spec.id})")
            sql_lines.append(spec.format_sql(top_n=20))
            sql_lines.append("")
        zf.writestr(s3 + "chart_queries.sql", "\n".join(sql_lines))

        # ── 5. Stage 4 — LLM Classification ──────────────────────────────────
        s4 = f"{prefix}stages/4_llm_classification/"
        try:
            zf.writestr(s4 + "report.html",
                        _rb.build_llm_classification_report(pipeline, pipeline_dir))
        except Exception:
            pass
        if pipeline_dir:
            bs = pipeline_dir / "batch_runs" / "state.json"
            if bs.exists():
                zf.write(str(bs), s4 + "batch_state.json")

        # ── 6. Stage 5 — LLM Analysis ────────────────────────────────────────
        s5 = f"{prefix}stages/5_llm_analysis/"
        try:
            zf.writestr(s5 + "report.html",
                        _rb.build_analysis_report(
                            pipeline, db_path, pipeline_config, _chart_gen, llm=True))
        except Exception:
            pass

        llm_charts_written = 0
        if db_path and db_path.exists():
            for spec in llm_charts:
                try:
                    png = _chart_gen.generate(db_path, spec.id, pipeline_config, top_n=20)
                    zf.writestr(s5 + f"charts_llm/{spec.id}.png", _png_add_watermark(png))
                    llm_charts_written += 1
                except Exception:
                    pass

        llm_sql_lines: list[str] = []
        for spec in llm_charts:
            llm_sql_lines.append(f"-- {spec.title} ({spec.id})")
            llm_sql_lines.append(spec.format_sql(top_n=20))
            llm_sql_lines.append("")
        zf.writestr(s5 + "chart_queries.sql", "\n".join(llm_sql_lines))

        # ── 7. Database export (data/ at root level) ──────────────────────────
        if db_path and db_path.exists() and tables:
            if db_format in ("sqlite", "both"):
                zf.write(str(db_path), f"{prefix}data/output.sqlite")

            if db_format in ("csv", "both"):
                with sqlite3.connect(db_path) as conn:
                    for table in tables:
                        csv_text = _dump_table_csv(conn, table)
                        if csv_text:
                            zf.writestr(f"{prefix}data/{table}.csv", csv_text)

            if db_format == "csv_chunks":
                assert chunk_mb is not None
                with sqlite3.connect(db_path) as conn:
                    for table in tables:
                        chunks = _dump_table_csv_chunks(conn, table, chunk_mb)
                        if len(chunks) == 1:
                            zf.writestr(f"{prefix}data/{table}.csv", chunks[0])
                        else:
                            for i, chunk in enumerate(chunks):
                                zf.writestr(f"{prefix}data/{table}_part{i+1:03d}.csv", chunk)

        # ── 8. Logs ───────────────────────────────────────────────────────────
        logs_written: list[str] = []
        if pipeline_dir and pipeline_dir.exists():
            for log_file in sorted(pipeline_dir.glob("*.log")):
                zf.write(str(log_file), f"{prefix}logs/{log_file.name}")
                logs_written.append(log_file.name)
            for log_file in sorted(pipeline_dir.rglob("**/*.log")):
                if log_file.parent == pipeline_dir:
                    continue
                rel = log_file.relative_to(pipeline_dir)
                zf.write(str(log_file), f"{prefix}logs/{rel}")
                logs_written.append(str(rel))

        # ── 9. Pipeline metadata JSON ─────────────────────────────────────────
        meta = {k: v for k, v in pipeline.items() if k != "config"}
        meta["config"] = pipeline_config
        zf.writestr(
            f"{prefix}pipeline_metadata.json",
            json.dumps(meta, indent=2, default=str),
        )

        # ── 10. Manifest ──────────────────────────────────────────────────────
        db_size_str = (
            f"{round(db_path.stat().st_size / 1024**2, 1)} MB"
            if db_path and db_path.exists() else "n/a"
        )
        table_lines = "\n".join(
            f"  {tbl}: {table_row_counts.get(tbl, 0):,} rows" for tbl in tables
        ) or "  (none)"
        db_format_desc = (
            "SQLite + CSV"          if db_format == "both"
            else f"CSV chunks ({chunk_mb} MB)" if db_format == "csv_chunks"
            else db_format.upper()
        )
        charts_written = analysis_charts_written + llm_charts_written
        manifest = (
            f"Pipeline:    {pipeline.get('name', '')}\n"
            f"Pipeline ID: {pipeline_id}\n"
            f"Owner:       {pipeline.get('user_id', '')}\n"
            f"Exported:    {ts} UTC\n"
            f"Stage:       {pipeline.get('current_stage', 'unknown')}\n"
            f"Records:     {record_count:,}\n"
            f"DB size:     {db_size_str}\n"
            f"DB format:   {db_format_desc}\n"
            f"\nDB tables:\n{table_lines}\n"
            f"\nCharts:      {charts_written} total "
            f"({analysis_charts_written} analysis, {llm_charts_written} LLM)\n"
            f"Logs:        {', '.join(logs_written) or 'none (run a pipeline stage first)'}\n"
            f"\nZIP structure:\n"
            f"  summary_report.html                 Pipeline overview + key charts\n"
            f"  data/                               Database export ({db_format_desc})\n"
            f"  logs/                               Pipeline log files\n"
            f"  stages/1_keyword_selection/         Keywords report + keywords.json\n"
            f"  stages/2_openalex_search/           Search report + pipeline_state.json\n"
            f"  stages/3_analysis/                  Analysis report + chart PNGs + SQL\n"
            f"  stages/4_llm_classification/        LLM classification report + batch state\n"
            f"  stages/5_llm_analysis/              LLM analysis report + LLM chart PNGs\n"
            f"  pipeline_metadata.json              Full pipeline configuration\n"
        )
        zf.writestr(f"{prefix}manifest.txt", manifest)

    buf.seek(0)
    filename = f"{safe_name}_{ts}.zip"
    return Response(
        buf.read(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
