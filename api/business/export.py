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
from api.business.dynamic_chart import build_dynamic_sql
from api.business.export_charts import (
    is_mpl_available,
    render_dynamic_chart_png,
    render_preset_chart_png,
    rows_to_csv_text,
)
from api.business.ortho_presets import ORTHO_PRESET_ROWS, compute_ortho_preset_overview
from api.business.orthogonality import contingency_from_labeled_counts
from api.business.qa import compute_irr
from api.business.subset_sql import normalize_subset_inner
from api.db.pipelines import get_subset, list_saved_charts, list_subsets
from api.db.research import count_subset_rows, get_db_stats, get_schema, run_query


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


_CSV_EXCLUDE_COLUMNS = frozenset({
    "llm_result_raw",
    "llm_batch_id",
    "llm_tokens_input",
    "llm_tokens_output",
    "llm_tokens_reasoning",
})


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

def _dump_table_csv(
    conn: sqlite3.Connection,
    table: str,
    exclude_cols: frozenset[str] | None = None,
) -> str:
    try:
        cur  = conn.execute(f"SELECT * FROM [{table}]")  # noqa: S608
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception:
        return ""
    if exclude_cols:
        keep = [i for i, c in enumerate(cols) if c not in exclude_cols]
        cols = [cols[i] for i in keep]
        rows = [tuple(row[i] for i in keep) for row in rows]
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue()


def _dump_table_csv_chunks(
    conn: sqlite3.Connection,
    table: str,
    chunk_mb: int,
    exclude_cols: frozenset[str] | None = None,
) -> list[str]:
    try:
        cur  = conn.execute(f"SELECT * FROM [{table}]")  # noqa: S608
        cols = [d[0] for d in cur.description]
    except Exception:
        return []
    keep: list[int] | None = None
    if exclude_cols:
        keep = [i for i, c in enumerate(cols) if c not in exclude_cols]
        cols = [cols[i] for i in keep]
    header_buf = io.StringIO()
    csv.writer(header_buf).writerow(cols)
    header = header_buf.getvalue()
    target  = chunk_mb * 1024 * 1024
    chunks: list[str] = []
    buf = io.StringIO()
    buf.write(header)
    w   = csv.writer(buf)
    for row in cur:
        w.writerow(tuple(row[i] for i in keep) if keep else row)
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


_METADATA_AXIS_IDS = frozenset({"countries", "institutions", "work_type", "oa_status", "pub_year"})
_LLM_AXIS_IDS = frozenset({"llm_fuel_cls", "llm_ml_role", "llm_ml_algo", "llm_scope"})


def _is_meaningful_cooc_pair(a_id: str, b_id: str) -> bool:
    """Filter co-occurrence pairs to research-relevant combinations only."""
    if a_id in _METADATA_AXIS_IDS and b_id in _METADATA_AXIS_IDS:
        return False
    a_grp = a_id.split("_")[0] if "_" in a_id else a_id
    b_grp = b_id.split("_")[0] if "_" in b_id else b_id
    if {a_grp, b_grp} == {"0", "1"}:
        return True
    if a_id in _LLM_AXIS_IDS and b_id in _LLM_AXIS_IDS:
        return True
    if (a_grp in ("0", "1") and b_id in _LLM_AXIS_IDS) or (
        b_grp in ("0", "1") and a_id in _LLM_AXIS_IDS
    ):
        return True
    return False


def _export_cooccurrence_data(
    zf: zipfile.ZipFile, prefix: str, db_path: Path, pipeline_config: dict,
) -> list[str]:
    axes = _cooc_axes_from_config(pipeline_config)
    written: list[str] = []
    if len(axes) < 2:
        return written
    combos = [
        (a, b)
        for i, a in enumerate(axes)
        for j, b in enumerate(axes)
        if i != j and _is_meaningful_cooc_pair(a["id"], b["id"])
    ]
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


def _export_qa_csvs_and_irr(
    zf: zipfile.ZipFile,
    prefix: str,
    meta_db: sqlite3.Connection,
    pipeline_id: int,
) -> None:
    cur = meta_db.execute(
        """SELECT qr.*, ur.username AS rater_username
           FROM qa_ratings qr JOIN users ur ON ur.id = qr.user_id
           WHERE qr.pipeline_id=? ORDER BY qr.created_at""",
        (pipeline_id,),
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if rows:
        zf.writestr(f"{prefix}qa/qa_ratings.csv", rows_to_csv_text(cols, rows))

    cur = meta_db.execute(
        """SELECT qa.*, ua.username AS annotator_username
           FROM qa_annotations qa JOIN users ua ON ua.id = qa.annotator_id
           WHERE qa.pipeline_id=? ORDER BY qa.created_at""",
        (pipeline_id,),
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if rows:
        zf.writestr(f"{prefix}qa/qa_annotations.csv", rows_to_csv_text(cols, rows))

    irr = compute_irr(meta_db, pipeline_id)
    zf.writestr(f"{prefix}qa/irr_summary.json", json.dumps(irr, indent=2, default=str))


def _export_subsets_snapshot(
    zf: zipfile.ZipFile,
    prefix: str,
    meta_db: sqlite3.Connection,
    pipeline_id: int,
    db_path: Path | None,
) -> None:
    subs = list_subsets(meta_db, pipeline_id)
    dataset_total = None
    if db_path and db_path.exists():
        st = get_db_stats(db_path)
        if st.get("exists") and "error" not in st:
            dataset_total = st.get("record_count")
    enriched = []
    for s in subs:
        row_count = None
        if db_path and db_path.exists():
            r = count_subset_rows(db_path, s["sql_query"])
            row_count = r.get("count") if "error" not in r else None
        enriched.append({**s, "row_count": row_count})
    zf.writestr(
        f"{prefix}subsets/subsets.json",
        json.dumps({"dataset_total": dataset_total, "subsets": enriched}, indent=2, default=str),
    )


def _export_orthogonality(
    zf: zipfile.ZipFile,
    prefix: str,
    db_path: Path,
    subsets: list[dict],
) -> list[str]:
    """Export orthogonality statistics for all preset pairs across subsets.

    Writes summary CSV/JSON and per-pair contingency matrices.
    """
    from api.db.research import fetch_pairwise_contingency

    notes: list[str] = []
    all_rows: list[dict] = []

    scope_list: list[tuple[str | None, str, str | None]] = [
        (None, "full_dataset", None),
    ]
    for s in subsets:
        safe = _safe_filename_part(s.get("name", f"subset_{s['id']}"))
        scope_list.append((s.get("sql_query"), safe, s.get("name")))

    for subset_sql, scope_name, subset_label in scope_list:
        inner_sql = normalize_subset_inner(subset_sql) if subset_sql else None
        try:
            pairs = compute_ortho_preset_overview(db_path, inner_sql)
        except Exception as exc:
            notes.append(f"orthogonality ({scope_name}): {exc}")
            continue
        for p in pairs:
            stats = p.get("stats") or {}
            all_rows.append({
                "scope": subset_label or "Full dataset",
                "pair_id": p["id"],
                "pair_label": p["label"],
                "column_a": p["a"],
                "column_b": p["b"],
                "chi2": stats.get("chi2"),
                "p_value": stats.get("p_value"),
                "cramers_v": stats.get("cramers_v"),
                "cramers_v_ci_low": stats.get("cramers_v_ci_low"),
                "cramers_v_ci_high": stats.get("cramers_v_ci_high"),
                "ci_level": stats.get("cramers_v_ci_level"),
                "n": stats.get("n"),
                "df": stats.get("df"),
                "interpretation": p.get("interpretation") or p.get("error") or "",
            })

            if p.get("error"):
                continue
            raw = fetch_pairwise_contingency(
                db_path, inner_sql, p["a"], p["b"], p["json_x"], p["json_y"],
            )
            data_rows = raw.get("rows") or []
            if not data_rows:
                continue
            rl_set, cl_set, matrix = contingency_from_labeled_counts(data_rows)
            mat_buf = io.StringIO()
            w = csv.writer(mat_buf)
            w.writerow([""] + cl_set)
            for ri, rl in enumerate(rl_set):
                w.writerow([rl] + [int(matrix[ri][ci]) for ci in range(len(cl_set))])
            zf.writestr(
                f"{prefix}orthogonality/{scope_name}/{p['id']}_contingency.csv",
                mat_buf.getvalue(),
            )

    if all_rows:
        csv_cols = [
            "scope", "pair_id", "pair_label", "column_a", "column_b",
            "chi2", "p_value", "cramers_v", "cramers_v_ci_low", "cramers_v_ci_high",
            "ci_level", "n", "df", "interpretation",
        ]
        csv_buf = io.StringIO()
        w = csv.DictWriter(csv_buf, fieldnames=csv_cols)
        w.writeheader()
        w.writerows(all_rows)
        zf.writestr(f"{prefix}orthogonality/summary.csv", csv_buf.getvalue())
        zf.writestr(
            f"{prefix}orthogonality/summary.json",
            json.dumps(all_rows, indent=2, default=str),
        )
    return notes


def _export_preset_charts_csv_png(
    zf: zipfile.ZipFile,
    prefix: str,
    db_path: Path,
) -> list[str]:
    notes: list[str] = []
    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_llm_columns(conn)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    mpl_ok = is_mpl_available()
    if not mpl_ok:
        notes.append("PNG export unavailable: matplotlib not installed or failed to initialize")
    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_llm_columns(conn)
            for spec in _CHART_DEFINITIONS.values():
                sql = spec.format_sql(top_n=20, cutoff_year=2000)
                try:
                    cur = conn.execute(sql)
                    cols = [d[0] for d in cur.description] if cur.description else []
                    rows = cur.fetchall()
                except Exception as exc:
                    notes.append(f"{spec.id}: {exc}")
                    continue
                zf.writestr(f"{prefix}charts/data/{spec.id}.csv", rows_to_csv_text(cols, rows))
                if not rows:
                    notes.append(f"{spec.id}: PNG skipped (query returned no data)")
                    continue
                if not mpl_ok:
                    continue
                png = render_preset_chart_png(spec, cols, rows)
                if png:
                    zf.writestr(f"{prefix}charts/png/{spec.id}.png", _png_add_watermark(png))
                else:
                    notes.append(f"{spec.id}: PNG rendering failed (unsupported renderer or data shape)")
    except Exception as exc:
        notes.append(f"preset charts: {exc}")
    return notes


def _safe_filename_part(name: str, max_len: int = 48) -> str:
    s = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "chart"))
    return s[:max_len] or "chart"


def _export_saved_charts_bundle(
    zf: zipfile.ZipFile,
    prefix: str,
    db_path: Path,
    meta_db: sqlite3.Connection,
    pipeline: dict,
) -> list[str]:
    notes: list[str] = []
    pid = pipeline["id"]
    uid = pipeline["user_id"]
    for sc in list_saved_charts(meta_db, pid, uid):
        cfg = json.loads(sc.get("config_json") or "{}")
        if cfg.get("kind") != "dynamic":
            continue
        safe = _safe_filename_part(sc.get("name", "chart"))
        folder = f"{prefix}charts/saved/{sc['id']}_{safe}/"
        zf.writestr(
            folder + "config.json",
            json.dumps({"id": sc["id"], "name": sc["name"], "config": cfg}, indent=2, ensure_ascii=False),
        )
        subset_inner = None
        sid = cfg.get("subset_id")
        if sid is not None:
            sub = get_subset(meta_db, pid, int(sid))
            if sub:
                subset_inner = normalize_subset_inner(sub["sql_query"])
            else:
                all_subs = list_subsets(meta_db, pid)
                matched = next(
                    (s for s in all_subs if s.get("preset_key") and str(s["id"]) == str(sid)),
                    None,
                )
                if not matched:
                    matched = next((s for s in all_subs), None)
                if matched:
                    notes.append(
                        f"saved chart {sc['id']}: subset {sid} not found, "
                        f"falling back to '{matched.get('name', matched['id'])}'"
                    )
                    subset_inner = normalize_subset_inner(matched["sql_query"])
                else:
                    notes.append(
                        f"saved chart {sc['id']}: subset {sid} not found, "
                        f"running against full dataset"
                    )
        schema = get_schema(db_path)
        allowed = {c["name"] for c in schema}
        try:
            sql, renderer = build_dynamic_sql(
                chart_type=cfg["chart_type"],
                column_x=cfg["column_x"],
                column_y=cfg.get("column_y"),
                stack_column=cfg.get("stack_column"),
                json_x=bool(cfg.get("json_x")),
                json_y=bool(cfg.get("json_y")),
                top_n=int(cfg.get("top_n") or 20),
                allowed_cols=allowed,
            )
        except Exception as exc:
            notes.append(f"saved chart {sc['id']}: {exc}")
            continue
        rq = run_query(db_path, sql, max_rows=50_000, subset_inner_sql=subset_inner)
        if rq.get("error"):
            notes.append(f"saved chart {sc['id']}: {rq['error']}")
            continue
        cols = rq.get("columns") or []
        data_rows = [tuple(r) for r in (rq.get("rows") or [])]
        zf.writestr(folder + "data.csv", rows_to_csv_text(cols, data_rows))
        png = render_dynamic_chart_png(renderer, cols, data_rows, sc.get("name") or "Chart")
        if png:
            zf.writestr(folder + "chart.png", _png_add_watermark(png))
    return notes


# ── Main build function ───────────────────────────────────────────────────────

def build_export_zip(
    pipeline: dict,
    db_format: str = "csv",
    chunk_mb: int | None = 50,
    meta_db: sqlite3.Connection | None = None,
) -> bytes:
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
                try:
                    raw_state = json.loads(sp.read_text(encoding="utf-8"))
                    all_files = raw_state.get("all_files") or []
                    raw_state["all_files_count"] = len(all_files)
                    if all_files:
                        raw_state["all_files_first"] = all_files[0]
                        raw_state["all_files_last"] = all_files[-1]
                    raw_state.pop("all_files", None)
                    zf.writestr(
                        f"{prefix}pipeline_state.json",
                        json.dumps(raw_state, indent=2, default=str),
                    )
                except Exception:
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

        export_notes: list[str] = []

        # 3a. QA / IRR / subsets / preset & saved chart data + PNGs
        if meta_db is not None:
            try:
                _export_qa_csvs_and_irr(zf, prefix, meta_db, pipeline_id)
            except Exception as exc:
                export_notes.append(f"qa export: {exc}")
            try:
                _export_subsets_snapshot(zf, prefix, meta_db, pipeline_id, db_path)
            except Exception as exc:
                export_notes.append(f"subsets: {exc}")
        if db_path and db_path.exists():
            export_notes.extend(_export_preset_charts_csv_png(zf, prefix, db_path))
            if meta_db is not None:
                export_notes.extend(_export_saved_charts_bundle(zf, prefix, db_path, meta_db, pipeline))

        # 3b. Orthogonality analysis
        exported_subsets: list[dict] = []
        if db_path and db_path.exists():
            if meta_db is not None:
                exported_subsets = list_subsets(meta_db, pipeline_id)
            try:
                export_notes.extend(
                    _export_orthogonality(zf, prefix, db_path, exported_subsets)
                )
            except Exception as exc:
                export_notes.append(f"orthogonality export: {exc}")

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
                        excl = _CSV_EXCLUDE_COLUMNS if table == "filtered_records" else None
                        csv_text = _dump_table_csv(conn, table, exclude_cols=excl)
                        if csv_text:
                            zf.writestr(f"{prefix}data/{table}.csv", csv_text)

            if db_format == "csv_chunks":
                use_mb_chunks = chunk_mb is not None and chunk_mb > 0
                with sqlite3.connect(db_path) as conn:
                    for table in tables:
                        excl = _CSV_EXCLUDE_COLUMNS if table == "filtered_records" else None
                        if use_mb_chunks:
                            chunks = _dump_table_csv_chunks(conn, table, int(chunk_mb), exclude_cols=excl)
                            if len(chunks) == 1:
                                zf.writestr(f"{prefix}data/{table}.csv", chunks[0])
                            else:
                                for i, chunk in enumerate(chunks):
                                    zf.writestr(f"{prefix}data/{table}_part{i+1:03d}.csv", chunk)
                        else:
                            csv_text = _dump_table_csv(conn, table, exclude_cols=excl)
                            if csv_text:
                                zf.writestr(f"{prefix}data/{table}.csv", csv_text)

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

        # 8. Export manifest (structured, machine-readable)
        db_size_str = (
            f"{round(db_path.stat().st_size / 1024**2, 1)} MB"
            if db_path and db_path.exists() else "n/a"
        )
        if db_format == "both":
            db_format_desc = "SQLite + CSV"
        elif db_format == "csv_chunks":
            if chunk_mb is not None and chunk_mb > 0:
                db_format_desc = f"CSV chunks ({chunk_mb} MB target per part)"
            else:
                db_format_desc = "CSV (single file per table)"
        else:
            db_format_desc = db_format.upper()

        manifest = {
            "export_version": "1.1.0",
            "timestamp_utc": ts,
            "pipeline": {
                "id": pipeline_id,
                "name": pipeline.get("name"),
            },
            "record_count": record_count,
            "db_size": db_size_str,
            "db_format": db_format_desc,
            "tables": {tbl: table_row_counts.get(tbl, 0) for tbl in tables},
            "subsets": [
                {"id": s["id"], "name": s.get("name"), "row_count": s.get("row_count")}
                for s in exported_subsets
            ] if exported_subsets else [],
            "artifacts": {
                "orthogonality": {
                    "path": "orthogonality/",
                    "description": "Chi-squared / Cramer's V for preset dimension pairs",
                    "preset_pairs": [p["id"] for p in ORTHO_PRESET_ROWS],
                },
                "preset_charts": {
                    "csv_dir": "charts/data/",
                    "png_dir": "charts/png/",
                    "count": len(_CHART_DEFINITIONS),
                },
                "cooccurrence": {
                    "dir": "charts/cooccurrence/",
                    "description": "Curated co-occurrence CSV matrices",
                },
                "qa": {
                    "dir": "qa/",
                    "description": "QA ratings, annotations, IRR summary",
                },
            },
            "csv_excluded_columns": sorted(_CSV_EXCLUDE_COLUMNS),
            "warnings": export_notes,
        }
        zf.writestr(
            f"{prefix}export_manifest.json",
            json.dumps(manifest, indent=2, default=str),
        )

        if export_notes:
            zf.writestr(
                f"{prefix}export_notes.txt",
                "Non-fatal export notes (charts or QA may be partial):\n\n"
                + "\n".join(export_notes),
            )

        # 9. README
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
            "| `pipeline_state.json` | Pipeline state: skip statistics, record counts (trimmed) |\n"
            "| `batch_state.json` | LLM batch API state |\n"
            "| `pipeline_metadata.json` | Full pipeline configuration |\n"
            "| `export_manifest.json` | Structured export manifest with all artifact metadata |\n"
            "| `chart_queries.sql` | All SQL queries for charts |\n"
            "| `charts/data/` | Tabular CSV per preset chart (same data as plots) |\n"
            "| `charts/png/` | Matplotlib PNG exports for preset charts |\n"
            "| `charts/saved/` | Saved dynamic charts: `config.json`, `data.csv`, `chart.png` |\n"
            "| `charts/cooccurrence/` | Co-occurrence CSV exports (curated pairs) |\n"
            "| `orthogonality/` | Chi-squared / Cramer's V summary + contingency matrices |\n"
            "| `qa/` | QA ratings, annotations, `irr_summary.json` |\n"
            "| `subsets/subsets.json` | Subset definitions and live row counts |\n"
            f"| `data/` | Database export ({db_format_desc}) |\n"
            "| `logs/` | Pipeline log files and skip-stats summary |\n"
            "| `export_notes.txt` | Optional warnings if some chart queries failed |\n\n"
            "### Database tables\n\n"
            + table_lines_md
            + "\n\n### CSV column exclusions\n\n"
            "The following columns are excluded from CSV exports (present in SQLite only): "
            + ", ".join(f"`{c}`" for c in sorted(_CSV_EXCLUDE_COLUMNS))
        )
        zf.writestr(f"{prefix}README.md", readme)

    buf.seek(0)
    return buf.read()
