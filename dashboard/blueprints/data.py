"""Data blueprint — /api/db/*, /api/charts/*, /api/subsets, /api/report, /api/log, /api/export-zip"""
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

from flask import Blueprint, Response, jsonify, request, send_file
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


# ── Report ─────────────────────────────────────────────────────────────────────

@data_bp.route("/api/report")
def api_report():
    err = require_auth()
    if err:
        return err
    uid = get_current_user_id()
    pipeline_id = request.args.get("pipeline_id", type=int)
    llm = request.args.get("llm", "0") == "1"

    if not pipeline_id:
        return jsonify({"error": "pipeline_id required"}), 400
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

    path_key = "llm_report_path" if llm else "report_path"
    report_path = pipeline.get(path_key)
    if not report_path or not Path(report_path).exists():
        return Response(
            "<html><body><h2>Report not yet generated.</h2></body></html>",
            mimetype="text/html",
        )
    return send_file(report_path, mimetype="text/html")


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


@data_bp.route("/api/pipelines/<int:pipeline_id>/export-zip")
def api_export_zip(pipeline_id: int):
    """Build and stream a ZIP with all charts, all DB tables, logs, reports, SQL, and metadata."""
    err = require_auth()
    if err:
        return err
    uid      = get_current_user_id()
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return jsonify({"error": "pipeline not found"}), 404

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

    # Collect all chart definitions, split into non-LLM and LLM groups
    from core.charts import _CHART_DEFINITIONS
    non_llm_charts = [c for c in _CHART_DEFINITIONS.values() if not c.llm_only]
    llm_charts     = [c for c in _CHART_DEFINITIONS.values() if c.llm_only]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # ── 1. Charts (all, watermarked PNGs) ────────────────────────────────
        if db_path and db_path.exists():
            for subfolder, chart_list in [("charts", non_llm_charts), ("charts_llm", llm_charts)]:
                for spec in chart_list:
                    try:
                        png = _chart_gen.generate(
                            db_path, spec.id, pipeline_config, top_n=20
                        )
                        zf.writestr(
                            f"{prefix}{subfolder}/{spec.id}.png",
                            _png_add_watermark(png),
                        )
                    except Exception:
                        pass

        # ── 2. Chart SQL queries ──────────────────────────────────────────────
        sql_lines: list[str] = []
        for spec in list(non_llm_charts) + list(llm_charts):
            sql_lines.append(f"-- {spec.title} ({spec.id})")
            sql_lines.append(spec.format_sql(top_n=20))
            sql_lines.append("")
        zf.writestr(f"{prefix}chart_queries.sql", "\n".join(sql_lines))

        # ── 3. All DB tables as CSV ───────────────────────────────────────────
        if db_path and db_path.exists():
            try:
                with sqlite3.connect(db_path) as conn:
                    tables = [
                        r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                        ).fetchall()
                    ]
                    for table in tables:
                        csv_text = _dump_table_csv(conn, table)
                        if csv_text:
                            zf.writestr(f"{prefix}data/{table}.csv", csv_text)
            except Exception:
                pass

        # ── 4. Logs (pipeline.log + any *.log in pipeline dir) ───────────────
        if pipeline_dir and pipeline_dir.exists():
            for log_file in sorted(pipeline_dir.glob("*.log")):
                zf.write(str(log_file), f"{prefix}logs/{log_file.name}")

        # ── 5. Pipeline metadata JSON ─────────────────────────────────────────
        meta = {k: v for k, v in pipeline.items() if k != "config"}
        meta["config"] = pipeline_config
        zf.writestr(
            f"{prefix}pipeline_metadata.json",
            json.dumps(meta, indent=2, default=str),
        )

        # ── 6. HTML reports ───────────────────────────────────────────────────
        for path_key, fname in [("report_path", "report.html"), ("llm_report_path", "llm_report.html")]:
            rp = pipeline.get(path_key)
            if rp and Path(rp).exists():
                zf.write(rp, f"{prefix}reports/{fname}")

        # ── 7. Manifest ───────────────────────────────────────────────────────
        record_count = 0
        table_sizes: list[str] = []
        if db_path and db_path.exists():
            try:
                with sqlite3.connect(db_path) as conn:
                    record_count = conn.execute(
                        "SELECT COUNT(*) FROM filtered_records"
                    ).fetchone()[0]
                    for tbl in tables:  # type: ignore[possibly-undefined]
                        n = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]  # noqa: S608
                        table_sizes.append(f"  {tbl}: {n:,} rows")
            except Exception:
                pass

        db_size_str = (
            f"{round(db_path.stat().st_size / 1024**2, 1)} MB"
            if db_path and db_path.exists() else "n/a"
        )
        table_summary = "\n".join(table_sizes) if table_sizes else "  (no tables)"
        manifest = (
            f"Pipeline:    {pipeline.get('name', '')}\n"
            f"Pipeline ID: {pipeline_id}\n"
            f"Exported:    {ts} UTC\n"
            f"Stage:       {pipeline.get('current_stage', 'unknown')}\n"
            f"Records:     {record_count:,}\n"
            f"DB size:     {db_size_str}\n"
            f"\nDB tables:\n{table_summary}\n"
            f"\nCharts exported: {len(non_llm_charts)} standard, {len(llm_charts)} LLM\n"
            f"\nZIP contents:\n"
            f"  charts/          Standard chart PNGs\n"
            f"  charts_llm/      LLM-only chart PNGs\n"
            f"  data/            All DB tables as CSV\n"
            f"  logs/            Pipeline log files\n"
            f"  reports/         HTML reports\n"
            f"  chart_queries.sql  SQL used for each chart\n"
            f"  pipeline_metadata.json  Pipeline configuration and metadata\n"
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
