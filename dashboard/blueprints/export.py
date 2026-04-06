"""Export blueprint — /api/export/*

Endpoints for data export:
  - Co-occurrence matrices as CSV
  - PRISMA flow diagram data
  - Article data package (ZIP)
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from blueprints.auth import require_auth, get_current_user_id
from models.pipeline import get_pipeline

export_bp = Blueprint("export", __name__)


def _pipeline_db(pipeline_id: int, uid: int) -> tuple[Path | None, dict | None, str | None]:
    pipeline = get_pipeline(pipeline_id, uid)
    if not pipeline:
        return None, None, "pipeline not found"
    p = pipeline.get("output_db_path")
    if not p:
        return None, pipeline, "pipeline has no database yet"
    path = Path(p)
    if not path.exists():
        return None, pipeline, "database file not found"
    return path, pipeline, None


def _pivot_csv(rows: list[tuple], x_label: str, y_label: str) -> str:
    """Convert (x, y, count) rows into a pivot table CSV with row/column totals."""
    data: dict[str, dict[str, int]] = {}
    all_y: set[str] = set()
    for x, y, cnt in rows:
        if x not in data:
            data[x] = {}
        data[x][y] = data[x].get(y, 0) + int(cnt)
        all_y.add(y)

    sorted_x = sorted(data.keys())
    sorted_y = sorted(all_y)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{x_label} \\ {y_label}"] + sorted_y + ["Total"])

    col_totals = {y: 0 for y in sorted_y}
    grand = 0
    for x in sorted_x:
        row_total = 0
        row = [x]
        for y in sorted_y:
            v = data[x].get(y, 0)
            row.append(v)
            row_total += v
            col_totals[y] += v
        row.append(row_total)
        grand += row_total
        w.writerow(row)

    w.writerow(["Total"] + [col_totals[y] for y in sorted_y] + [grand])
    return buf.getvalue()


# ── Co-occurrence CSVs ────────────────────────────────────────────────────────

@export_bp.route("/api/export/cooccurrence/<int:pipeline_id>")
def api_export_cooccurrence(pipeline_id: int):
    """Export specific co-occurrence matrix as CSV pivot table.

    Query params: ?matrix=fuelclass_x_mlrole (or mlrole_x_algorithm, fuelclass_x_algorithm, fuelclass_x_datasource)
    """
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, pipeline, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    matrix_id = request.args.get("matrix", "fuelclass_x_mlrole")

    MATRIX_QUERIES = {
        "fuelclass_x_mlrole": {
            "sql": """SELECT f.value AS x, r.value AS y, COUNT(DISTINCT openalex_id) AS count
                      FROM filtered_records, json_each(llm_fuel_classification) f, json_each(llm_ml_role) r
                      WHERE LOWER(f.value) NOT IN ('none','') AND f.value IS NOT NULL
                        AND LOWER(r.value) NOT IN ('none','') AND r.value IS NOT NULL
                      GROUP BY f.value, r.value""",
            "x_label": "Fuel Classification", "y_label": "ML Role",
        },
        "mlrole_x_algorithm": {
            "sql": """SELECT r.value AS x, a.value AS y, COUNT(DISTINCT openalex_id) AS count
                      FROM filtered_records, json_each(llm_ml_role) r, json_each(llm_ml_algorithm) a
                      WHERE LOWER(r.value) NOT IN ('none','') AND r.value IS NOT NULL
                        AND LOWER(a.value) NOT IN ('none','other_ml','') AND a.value IS NOT NULL
                      GROUP BY r.value, a.value""",
            "x_label": "ML Role", "y_label": "ML Algorithm",
        },
        "fuelclass_x_algorithm": {
            "sql": """SELECT f.value AS x, a.value AS y, COUNT(DISTINCT openalex_id) AS count
                      FROM filtered_records, json_each(llm_fuel_classification) f, json_each(llm_ml_algorithm) a
                      WHERE LOWER(f.value) NOT IN ('none','') AND f.value IS NOT NULL
                        AND LOWER(a.value) NOT IN ('none','other_ml','') AND a.value IS NOT NULL
                      GROUP BY f.value, a.value""",
            "x_label": "Fuel Classification", "y_label": "ML Algorithm",
        },
        "fuelclass_x_datasource": {
            "sql": """SELECT f.value AS x,
                        CASE WHEN llm_ml_data_source IS NULL OR llm_ml_data_source IN ('','none','null')
                             THEN 'none' ELSE llm_ml_data_source END AS y,
                        COUNT(DISTINCT openalex_id) AS count
                      FROM filtered_records, json_each(llm_fuel_classification) f
                      WHERE LOWER(f.value) NOT IN ('none','') AND f.value IS NOT NULL
                        AND llm_ml_data_source IS NOT NULL
                        AND LOWER(llm_ml_data_source) NOT IN ('none','','null')
                      GROUP BY f.value, y""",
            "x_label": "Fuel Classification", "y_label": "ML Data Source",
        },
    }

    if matrix_id not in MATRIX_QUERIES:
        return jsonify({"error": f"unknown matrix '{matrix_id}'"}), 400

    spec = MATRIX_QUERIES[matrix_id]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(spec["sql"]).fetchall()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500
    conn.close()

    csv_str = _pivot_csv(rows, spec["x_label"], spec["y_label"])
    return Response(
        csv_str, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={matrix_id}.csv"},
    )


# ── PRISMA flow diagram data ─────────────────────────────────────────────────

@export_bp.route("/api/export/prisma/<int:pipeline_id>")
def api_export_prisma(pipeline_id: int):
    """Return PRISMA flow diagram counts as JSON."""
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, pipeline, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}

    total = conn.execute("SELECT COUNT(*) FROM filtered_records").fetchone()[0]

    counts = {"total_filtered_records": total}

    if "llm_is_hydrocarbon" in cols:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM filtered_records WHERE llm_is_hydrocarbon = 1 OR llm_is_hydrocarbon = 1.0"
        ).fetchone()
        counts["hydrocarbon_relevant"] = row["cnt"]
        counts["excluded_not_hydrocarbon"] = total - row["cnt"]

    if "llm_ml_role" in cols:
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM filtered_records
            WHERE llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','')
              AND LOWER(llm_ml_role) NOT LIKE '%"none"%'
        """).fetchone()
        counts["ml_positive"] = row["cnt"]

    # Load pipeline state for search-stage counts if available
    if pipeline:
        state_path = pipeline.get("state_file_path")
        if state_path and Path(state_path).exists():
            try:
                state = json.loads(Path(state_path).read_text(encoding="utf-8"))
                counts["total_scanned"] = state.get("total_works", 0)
                counts["keyword_matched"] = state.get("keyword_matched", 0)
                counts["excluded_non_english"] = state.get("excluded_non_english", 0)
                counts["excluded_no_abstract"] = state.get("excluded_no_abstract", 0)
                counts["excluded_retracted"] = state.get("excluded_retracted", 0)
            except Exception:
                pass

    conn.close()
    return jsonify(counts)


# ── Article data package (ZIP) ────────────────────────────────────────────────

@export_bp.route("/api/export/article-package/<int:pipeline_id>")
def api_export_article_package(pipeline_id: int):
    """Generate a ZIP containing all article data: CSVs, metrics JSON, exemplars."""
    auth = require_auth()
    if auth:
        return auth
    uid = get_current_user_id()
    db_path, pipeline, err = _pipeline_db(pipeline_id, uid)
    if err:
        return jsonify({"error": err}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = "article_data/"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        zf.writestr(f"{prefix}README.txt", f"Article Data Package\nGenerated: {ts}\nPipeline: {pipeline.get('name', '')}\n")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        # 1. Co-occurrence CSVs
        MATRICES = {
            "fuelclass_x_mlrole": {
                "sql": """SELECT f.value, r.value, COUNT(DISTINCT openalex_id)
                          FROM filtered_records, json_each(llm_fuel_classification) f, json_each(llm_ml_role) r
                          WHERE LOWER(f.value) NOT IN ('none','') AND LOWER(r.value) NOT IN ('none','')
                          GROUP BY f.value, r.value""",
                "x": "Fuel Classification", "y": "ML Role",
            },
            "mlrole_x_algorithm": {
                "sql": """SELECT r.value, a.value, COUNT(DISTINCT openalex_id)
                          FROM filtered_records, json_each(llm_ml_role) r, json_each(llm_ml_algorithm) a
                          WHERE LOWER(r.value) NOT IN ('none','') AND LOWER(a.value) NOT IN ('none','other_ml','')
                          GROUP BY r.value, a.value""",
                "x": "ML Role", "y": "ML Algorithm",
            },
            "fuelclass_x_algorithm": {
                "sql": """SELECT f.value, a.value, COUNT(DISTINCT openalex_id)
                          FROM filtered_records, json_each(llm_fuel_classification) f, json_each(llm_ml_algorithm) a
                          WHERE LOWER(f.value) NOT IN ('none','') AND LOWER(a.value) NOT IN ('none','other_ml','')
                          GROUP BY f.value, a.value""",
                "x": "Fuel Classification", "y": "ML Algorithm",
            },
            "fuelclass_x_datasource": {
                "sql": """SELECT f.value, llm_ml_data_source, COUNT(DISTINCT openalex_id)
                          FROM filtered_records, json_each(llm_fuel_classification) f
                          WHERE LOWER(f.value) NOT IN ('none','')
                            AND llm_ml_data_source IS NOT NULL AND LOWER(llm_ml_data_source) NOT IN ('none','','null')
                          GROUP BY f.value, llm_ml_data_source""",
                "x": "Fuel Classification", "y": "ML Data Source",
            },
        }
        for name, spec in MATRICES.items():
            try:
                rows = conn.execute(spec["sql"]).fetchall()
                csv_str = _pivot_csv(rows, spec["x"], spec["y"])
                zf.writestr(f"{prefix}cooccurrence/{name}.csv", csv_str)
            except Exception:
                pass

        conn.close()

        # 2. PRISMA flow JSON
        try:
            from core.orthogonality import data_quality_audit
            audit = data_quality_audit(db_path)
            zf.writestr(f"{prefix}data_quality_audit.json", json.dumps(audit, indent=2))
        except Exception:
            pass

        # 3. Orthogonality metrics
        try:
            from core.orthogonality import cramers_v_matrix, conditional_entropy_analysis, pmi_analysis
            ortho = {
                "cramers_v": cramers_v_matrix(db_path, ml_only=True),
                "entropy": conditional_entropy_analysis(db_path, ml_only=True),
                "pmi": pmi_analysis(db_path, ml_only=True),
            }
            zf.writestr(f"{prefix}orthogonality_metrics.json", json.dumps(ortho, indent=2, default=str))
        except Exception:
            pass

        # 4. Exemplar papers CSV
        try:
            from core.orthogonality import exemplar_papers
            ex = exemplar_papers(db_path)
            if ex["exemplars"]:
                csv_buf = io.StringIO()
                w = csv.DictWriter(csv_buf, fieldnames=["fuel_class", "ml_role", "cell_count", "title", "doi", "fwci", "id"])
                w.writeheader()
                w.writerows(ex["exemplars"])
                zf.writestr(f"{prefix}exemplar_papers.csv", csv_buf.getvalue())
        except Exception:
            pass

    buf.seek(0)
    return Response(
        buf.getvalue(), mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=article_data_pipeline_{pipeline_id}.zip"},
    )
