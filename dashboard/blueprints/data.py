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
    import hashlib as _hl
    etag = _hl.md5(png).hexdigest()
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)
    return Response(png, mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=120",
                             "ETag": etag})


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

# LLM columns that llm_classify.py adds via ALTER TABLE.
# Inlined here so the export always includes them (even if the LLM collect
# phase crashed or the WAL wasn't checkpointed when the user last ran it).
_LLM_EXPORT_COLUMNS = [
    ("llm_is_hydrocarbon",      "INTEGER"),
    ("llm_fuel_classification", "TEXT"),
    ("llm_ml_paradigm",         "TEXT"),
    ("llm_ml_method",           "TEXT"),
    ("llm_ml_role",             "TEXT"),
    ("llm_ml_algorithm",        "TEXT"),
    ("llm_scope",               "TEXT"),       # v5 — value-chain position
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
    """Add LLM result columns if they don't already exist (no-op when present)."""
    for col, typedef in _LLM_EXPORT_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE filtered_records ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # already exists
    conn.commit()


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


# ── Co-occurrence export helper ───────────────────────────────────────────────

def _cooc_axes_from_config(pipeline_config: dict) -> list[dict]:
    """Build the same axis list the frontend TermsCooccurrencePanel uses."""
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
    # Standard bibliometric + LLM axes
    axes += [
        {"id": "countries",    "label": "Countries",          "col": "authors_countries",       "is_array": True,  "terms": None},
        {"id": "institutions", "label": "Institutions",       "col": "authors_institutions",    "is_array": True,  "terms": None},
        {"id": "work_type",    "label": "Work Type",          "col": "type",                    "is_array": False, "terms": None},
        {"id": "oa_status",    "label": "OA Status",          "col": "open_access_oa_status",   "is_array": False, "terms": None},
        {"id": "pub_year",     "label": "Publication Year",   "col": "publication_year",        "is_array": False, "terms": None},
        {"id": "llm_fuel_cls", "label": "Fuel Classification","col": "llm_fuel_classification", "is_array": True,  "terms": None},
        {"id": "llm_ml_role",  "label": "ML Role",            "col": "llm_ml_role",             "is_array": True,  "terms": None},
        {"id": "llm_ml_algo",  "label": "ML Algorithm",       "col": "llm_ml_algorithm",        "is_array": True,  "terms": None},
        {"id": "llm_scope",    "label": "Value Chain",        "col": "llm_scope",               "is_array": True,  "terms": None},
    ]
    return axes


def _cooc_query_sql(a: dict, b: dict) -> str:
    """Build a co-occurrence SQL between two axis defs, mirroring the JS logic."""
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


def _render_cooc_png(rows: list, title: str) -> bytes:
    """Render a co-occurrence heatmap PNG from rows [(a, b, count), ...]."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    if not rows:
        return b""
    tot_a: dict[str, int] = {}
    tot_b: dict[str, int] = {}
    for a_val, b_val, cnt in rows:
        tot_a[a_val] = tot_a.get(a_val, 0) + cnt
        tot_b[b_val] = tot_b.get(b_val, 0) + cnt
    all_a = sorted(tot_a, key=lambda k: -tot_a[k])
    all_b = sorted(tot_b, key=lambda k: -tot_b[k])
    mat = {}
    for a_val, b_val, cnt in rows:
        mat[(a_val, b_val)] = cnt
    data = np.zeros((len(all_a), len(all_b)))
    for i, a_val in enumerate(all_a):
        for j, b_val in enumerate(all_b):
            data[i, j] = mat.get((a_val, b_val), 0)
    fig, ax = plt.subplots(figsize=(max(6, len(all_b) * 0.6), max(4, len(all_a) * 0.45)))
    if data.max() > 0:
        im = ax.imshow(data, aspect="auto", cmap="Blues", interpolation="nearest")
        plt.colorbar(im, ax=ax, shrink=0.7, label="Paper count")
    ax.set_xticks(range(len(all_b)))
    ax.set_xticklabels(all_b, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(all_a)))
    ax.set_yticklabels(all_a, fontsize=7)
    for i in range(len(all_a)):
        for j in range(len(all_b)):
            v = int(data[i, j])
            if v > 0:
                color = "white" if v > data.max() * 0.55 else "black"
                ax.text(j, i, str(v), ha="center", va="center", fontsize=6, color=color)
    ax.set_title(title, fontsize=10, pad=10)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _export_cooccurrence_data(
    zf: zipfile.ZipFile, prefix: str, db_path: Path, pipeline_config: dict,
) -> list[str]:
    """Generate all meaningful Terms Co-occurrence CSVs + PNGs into the ZIP.

    Returns list of written file base names (without extension)."""
    axes = _cooc_axes_from_config(pipeline_config)
    written: list[str] = []
    if len(axes) < 2:
        return written

    # Generate combos of subgroup-level axes (first 10 axes max for sanity)
    combos = []
    for i, a in enumerate(axes[:10]):
        for j, b in enumerate(axes[:10]):
            if i != j:
                combos.append((a, b))

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA query_only = ON")
        for ax_a, ax_b in combos:
            sql = _cooc_query_sql(ax_a, ax_b)
            try:
                cur = conn.execute(sql)
                raw = cur.fetchall()
            except Exception:
                continue
            rows = [(r[0], r[1], r[2]) for r in raw if r[0] and r[1] and r[2] and r[2] > 0]
            if not rows:
                continue
            safe_name = f"{ax_a['id']}_x_{ax_b['id']}"
            title = f"{ax_a['label']} × {ax_b['label']}"
            # CSV
            csv_buf = io.StringIO()
            w = csv.writer(csv_buf)
            w.writerow([ax_a["label"], ax_b["label"], "count"])
            w.writerows(rows)
            zf.writestr(f"{prefix}charts/cooccurrence/{safe_name}.csv", csv_buf.getvalue())
            # PNG
            try:
                png = _render_cooc_png(rows, title)
                if png:
                    zf.writestr(f"{prefix}charts/cooccurrence/{safe_name}.png", _png_add_watermark(png))
            except Exception:
                pass
            written.append(safe_name)

    return written


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
    """Build and stream a ZIP with charts, data, logs, and metadata.

    Query params:
      db_format = csv | sqlite | both | csv_chunks_N   (default: csv)
        csv            — all tables as individual CSV files
        sqlite         — copy of the raw SQLite database
        both           — CSV + SQLite
        csv_chunks_N   — each table split into N-MB CSV chunks (e.g. csv_chunks_50)

    ZIP structure:
      README.md                       overview + chart index + SQL reference
      keywords.json                   keyword configuration
      pipeline_state.json             pipeline state and skip statistics
      pipeline_metadata.json          full pipeline configuration
      charts/                         all chart images (analysis + LLM)
      chart_queries.sql               all chart SQL queries
      data/                           record data (format per db_format)
      logs/                           pipeline log files + skip stats summary
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

    all_charts     = list(_CHART_DEFINITIONS.values())
    non_llm_charts = [c for c in all_charts if not c.llm_only]
    llm_charts     = [c for c in all_charts if c.llm_only]

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

    # ── Chart descriptions for README ─────────────────────────────────────────
    _CHART_DESC: dict[str, str] = {
        "publications_by_year":          "Distribution of matched papers by publication year. Papers from 2000 or earlier are grouped together.",
        "fuel_subgroup_pie":             "Share of papers touching each fuel subgroup (papers can appear in multiple subgroups).",
        "impact_subgroup_pie":           "Share of papers touching each impact subgroup.",
        "top_fuel_terms":                "Top keyword terms matched in the fuel category, stacked by subgroup.",
        "top_impact_terms":              "Top keyword terms matched in the impact category, stacked by subgroup.",
        "fuel_impact_cooccurrence":      "3D co-occurrence matrix showing how fuel and impact subgroups co-occur within the same paper.",
        "top_countries":                 "Countries contributing the most papers (counted by author affiliation).",
        "top_journals":                  "Journals / conference proceedings publishing the most papers in this corpus.",
        "oa_status":                     "Open access status breakdown (gold, green, hybrid, bronze, closed).",
        "work_types":                    "Distribution of document types (article, conference paper, review, etc.).",
        "top_institutions":              "Institutions with the most affiliated authors in the corpus.",
        "llm_scope":                     "(LLM) Fuel classification tags assigned by the model to each paper.",
        "llm_ml_role":                   "(LLM) Functional role of ML/AI in each paper (predictive, optimisation, etc.).",
        "llm_ml_algorithm":              "(LLM) Algorithm families identified (neural network, gradient boosting, etc.).",
        "llm_ml_usage":                  "(LLM) Fraction of papers that use ML/AI vs. do not.",
        "fuel_impact_model_cooccurrence":"(LLM) Three-way co-occurrence of fuel subgroup, impact subgroup, and model type.",
        "fuel_subgroup_ml_cooccurrence": "(LLM) Fuel × impact subgroup co-occurrence restricted to ML/AI papers only.",
        "fuelclass_x_mlrole":            "(LLM) Heatmap of fuel classification vs. ML functional role.",
        "mlrole_x_algorithm":            "(LLM) Heatmap of ML functional role vs. algorithm family.",
        "fuelclass_x_algorithm":         "(LLM) Heatmap of fuel classification vs. algorithm family.",
        "fuelclass_x_datasource":        "(LLM) Heatmap of fuel classification vs. ML data source type.",
        "llm_ml_datasource":             "(LLM) Distribution of data source types across ML/AI papers.",
        "terms_cooccurrence":            "Heatmap of first fuel subgroup terms × first impact subgroup terms co-occurrence.",
        "llm_value_chain":               "(LLM) Value-chain position of each paper (feedstock, processing, combustion, emissions, etc.).",
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # ── 1. Root-level config files ────────────────────────────────────────
        if pipeline_dir:
            kw = pipeline_dir / "keywords.json"
            if kw.exists():
                zf.write(str(kw), f"{prefix}keywords.json")
            sp = pipeline_dir / "pipeline_state.json"
            if sp.exists():
                zf.write(str(sp), f"{prefix}pipeline_state.json")

        # ── 2. Batch state ────────────────────────────────────────────────────
        if pipeline_dir:
            bs = pipeline_dir / "batch_runs" / "state.json"
            if bs.exists():
                zf.write(str(bs), f"{prefix}batch_state.json")

        # ── 3. All charts in flat charts/ folder ──────────────────────────────
        charts_written_ids: list[str] = []
        if db_path and db_path.exists():
            for spec in all_charts:
                try:
                    png = _chart_gen.generate(db_path, spec.id, pipeline_config, top_n=20)
                    zf.writestr(f"{prefix}charts/{spec.id}.png", _png_add_watermark(png))
                    charts_written_ids.append(spec.id)
                except Exception:
                    pass

        # ── 4. Chart SQL (all charts) ─────────────────────────────────────────
        sql_lines: list[str] = []
        for spec in all_charts:
            sql_lines.append(f"-- {spec.title} ({spec.id})")
            if spec.llm_only:
                sql_lines.append("-- NOTE: requires LLM classification to have been run")
            sql_lines.append(spec.format_sql(top_n=20))
            sql_lines.append("")
        zf.writestr(f"{prefix}chart_queries.sql", "\n".join(sql_lines))

        # ── 4b. Terms Co-occurrence data (all possible axis combos) ───────────
        if db_path and db_path.exists():
            _cooc_written = _export_cooccurrence_data(
                zf, prefix, db_path, pipeline_config,
            )

        # ── 5. Database export ────────────────────────────────────────────────
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
                assert chunk_mb is not None
                with sqlite3.connect(db_path) as conn:
                    for table in tables:
                        chunks = _dump_table_csv_chunks(conn, table, chunk_mb)
                        if len(chunks) == 1:
                            zf.writestr(f"{prefix}data/{table}.csv", chunks[0])
                        else:
                            for i, chunk in enumerate(chunks):
                                zf.writestr(f"{prefix}data/{table}_part{i+1:03d}.csv", chunk)

        # ── 6. Logs (pipeline logs + batch logs) ──────────────────────────────
        logs_written: list[str] = []
        if pipeline_dir and pipeline_dir.exists():
            for log_file in sorted(pipeline_dir.glob("*.log")):
                zf.write(str(log_file), f"{prefix}logs/{log_file.name}")
                logs_written.append(log_file.name)
            for log_file in sorted(pipeline_dir.rglob("**/*.log")):
                if log_file.parent == pipeline_dir:
                    continue
                rel = log_file.relative_to(pipeline_dir)
                zf.write(str(log_file), f"{prefix}logs/{str(rel).replace(chr(92), '/')}")
                logs_written.append(str(rel))
            # Include batch errors/output JSONL if present
            batch_dir = pipeline_dir / "batch_runs"
            if batch_dir.exists():
                for jl in sorted(batch_dir.glob("*.jsonl")):
                    if jl.stat().st_size <= 50 * 1024 * 1024:  # skip files > 50 MB
                        zf.write(str(jl), f"{prefix}logs/batch/{jl.name}")
                        logs_written.append(f"batch/{jl.name}")

        # ── 7. Skip-stats summary ─────────────────────────────────────────────
        state_path = pipeline_dir / "pipeline_state.json" if pipeline_dir else None
        if state_path and state_path.exists():
            try:
                _state = json.loads(state_path.read_text(encoding="utf-8"))
                _skip = _state.get("skip_stats", {})
                _seen = _state.get("total_records_seen", 0)
                _ins  = _state.get("total_records_inserted", 0)
                _ferr = _state.get("file_errors", 0)
                _rerr = _state.get("record_errors", 0)
                skip_lines = [
                    "Screening funnel / skip-reason summary",
                    "=" * 40,
                    f"Records scanned  : {_seen:,}",
                ]
                _SKIP_ORDER = ["not_english", "no_doi", "no_abstract", "no_keywords",
                               "empty_abstract", "json_error", "empty_line", "no_id",
                               "processing_error"]
                _SKIP_LABEL = {
                    "not_english": "non-English (pre-screened)",
                    "no_doi": "no DOI",
                    "no_abstract": "no abstract",
                    "no_keywords": "no matching keywords",
                    "empty_abstract": "empty abstract",
                    "json_error": "JSON parse error",
                    "empty_line": "empty/blank line",
                    "no_id": "no OpenAlex ID",
                    "processing_error": "processing error",
                }
                _accounted = sum(_skip.values())
                for k in _SKIP_ORDER:
                    if k in _skip:
                        skip_lines.append(f"  ↳ {_SKIP_LABEL.get(k, k):<32}: {_skip[k]:>12,}")
                for k, v in sorted(_skip.items(), key=lambda x: -x[1]):
                    if k not in _SKIP_ORDER:
                        skip_lines.append(f"  ↳ {k:<32}: {v:>12,}")
                _unaccounted = _seen - _accounted - _ins if _seen > 0 else 0
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

        # ── 8. Pipeline metadata JSON ─────────────────────────────────────────
        meta = {k: v for k, v in pipeline.items() if k != "config"}
        meta["config"] = pipeline_config
        zf.writestr(
            f"{prefix}pipeline_metadata.json",
            json.dumps(meta, indent=2, default=str),
        )

        # ── 9. README.md ──────────────────────────────────────────────────────
        db_size_str = (
            f"{round(db_path.stat().st_size / 1024**2, 1)} MB"
            if db_path and db_path.exists() else "n/a"
        )
        db_format_desc = (
            "SQLite + CSV"              if db_format == "both"
            else f"CSV chunks ({chunk_mb} MB)" if db_format == "csv_chunks"
            else db_format.upper()
        )
        table_lines_md = "\n".join(
            f"- `{tbl}`: {table_row_counts.get(tbl, 0):,} rows" for tbl in tables
        ) or "*(no tables yet)*"

        # Build skip stats block for README
        readme_skip = ""
        if state_path and state_path.exists():
            try:
                _st2 = json.loads(state_path.read_text(encoding="utf-8"))
                _sk2 = _st2.get("skip_stats", {})
                _s2  = _st2.get("total_records_seen", 0)
                _i2  = _st2.get("total_records_inserted", 0)
                _acc2 = sum(_sk2.values())
                _unc2 = _s2 - _acc2 - _i2 if _s2 > 0 else 0
                _LABEL2 = {
                    "not_english": "Non-English", "no_doi": "No DOI",
                    "no_abstract": "No abstract", "no_keywords": "No matching keywords",
                    "empty_abstract": "Empty abstract", "json_error": "JSON parse error",
                    "empty_line": "Blank line", "no_id": "No OpenAlex ID",
                    "processing_error": "Processing error",
                }
                skip_rows = [f"| Records scanned | {_s2:,} |"]
                for k in ["not_english", "no_doi", "no_abstract", "no_keywords",
                          "empty_abstract", "json_error", "empty_line", "no_id", "processing_error"]:
                    if k in _sk2:
                        skip_rows.append(f"| ↳ {_LABEL2.get(k, k)} | {_sk2[k]:,} |")
                for k, v in sorted(_sk2.items(), key=lambda x: -x[1]):
                    if k not in _LABEL2:
                        skip_rows.append(f"| ↳ {k} | {v:,} |")
                if _unc2 > 0:
                    skip_rows.append(f"| ↳ Unaccounted (not tracked) | {_unc2:,} |")
                skip_rows.append(f"| **Saved (matched)** | **{_i2:,}** |")
                _rate = f"{_i2/_s2*100:.4f}%" if _s2 else "n/a"
                skip_rows.append(f"| Match rate | {_rate} |")
                readme_skip = (
                    "## Screening Funnel\n\n"
                    "| Stage | Count |\n|---|---|\n"
                    + "\n".join(skip_rows) + "\n\n"
                    + "> See `logs/skip_stats_summary.txt` for the full breakdown.\n"
                )
            except Exception:
                readme_skip = ""

        # Chart index section
        chart_section_lines = ["## Charts\n", f"All charts are in the `charts/` folder.\n"]
        for spec in all_charts:
            in_zip = spec.id in charts_written_ids
            tag = "" if in_zip else " *(no data)*"
            desc = _CHART_DESC.get(spec.id, "")
            chart_section_lines.append(f"### {spec.title}{tag}\n")
            chart_section_lines.append(f"**File:** `charts/{spec.id}.png`  ")
            if desc:
                chart_section_lines.append(f"**Description:** {desc}\n")
            chart_section_lines.append(f"```sql\n{spec.format_sql(top_n=20)}\n```\n")
        chart_section = "\n".join(chart_section_lines)

        readme = (
            f"# Pipeline Export: {pipeline.get('name', f'Pipeline {pipeline_id}')}\n\n"
            f"**Exported:** {ts} UTC  \n"
            f"**Records:** {record_count:,}  \n"
            f"**DB size:** {db_size_str}  \n"
            f"**Format:** {db_format_desc}  \n\n"
            "---\n\n"
            + readme_skip +
            "## Files\n\n"
            "| File / Folder | Description |\n|---|---|\n"
            "| `keywords.json` | Keyword groups and terms used to screen OpenAlex records |\n"
            "| `pipeline_state.json` | Pipeline state: skip statistics, record counts, timestamps |\n"
            "| `batch_state.json` | LLM batch API state (if LLM classification was run) |\n"
            "| `pipeline_metadata.json` | Full pipeline configuration |\n"
            "| `chart_queries.sql` | All SQL queries used to generate the charts |\n"
            f"| `charts/` | All chart images ({len(charts_written_ids)} PNG files) |\n"
            f"| `data/` | Database export ({db_format_desc}) |\n"
            "| `logs/` | Pipeline log files and skip-stats summary |\n\n"
            "### Database tables\n\n"
            + table_lines_md + "\n\n"
            "---\n\n"
            + chart_section
        )
        zf.writestr(f"{prefix}README.md", readme)

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
