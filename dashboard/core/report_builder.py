"""core/report_builder.py — Self-contained per-stage HTML report generation.

Each build_*_report() function returns a complete, standalone HTML string with:
  - Embedded CSS (no external dependencies)
  - Base64-embedded chart PNGs where applicable
  - SQL queries for reproducibility
  - Stage-specific data from the pipeline (keywords, stats, log excerpts, etc.)
"""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import chart_gen as _cg

# ---------------------------------------------------------------------------
# Shared HTML template helpers
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px; line-height: 1.6; color: #1a1a2e; background: #fff;
  max-width: 1100px; margin: 0 auto; padding: 2rem 2.5rem;
}
h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: .25rem; color: #0f172a; }
h2 { font-size: 1.15rem; font-weight: 600; margin: 2rem 0 .75rem;
     padding-bottom: .4rem; border-bottom: 2px solid #e2e8f0; color: #1e3a5f; }
h3 { font-size: 1rem; font-weight: 600; margin: 1.25rem 0 .5rem; color: #334155; }
.meta { font-size: .8rem; color: #64748b; margin-bottom: 1.5rem; }
.meta span { margin-right: 1.5rem; }
table { width: 100%; border-collapse: collapse; margin: .75rem 0 1.5rem;
        font-size: .82rem; }
th { background: #f1f5f9; text-align: left; padding: .45rem .75rem;
     border: 1px solid #e2e8f0; font-weight: 600; color: #475569; }
td { padding: .4rem .75rem; border: 1px solid #e2e8f0; vertical-align: top; }
tr:nth-child(even) td { background: #f8fafc; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr));
             gap: 1rem; margin: 1rem 0 1.5rem; }
.stat-card { background: #f1f5f9; border-radius: 8px; padding: 1rem 1.25rem;
             border-left: 4px solid #3b82f6; }
.stat-card .val { font-size: 1.6rem; font-weight: 700; color: #1e3a5f; }
.stat-card .lbl { font-size: .75rem; color: #64748b; margin-top: .2rem; }
.funnel { display: flex; align-items: stretch; gap: 0; margin: 1rem 0 1.5rem; flex-wrap: wrap; }
.funnel-step { flex: 1; min-width: 110px; text-align: center; padding: .75rem .5rem;
               background: #eff6ff; border: 1px solid #bfdbfe;
               position: relative; }
.funnel-step:not(:last-child)::after { content: "→";
  position: absolute; right: -14px; top: 50%; transform: translateY(-50%);
  font-size: 1.1rem; color: #94a3b8; z-index: 1; }
.funnel-step .n { font-size: 1.3rem; font-weight: 700; color: #1d4ed8; }
.funnel-step .l { font-size: .7rem; color: #475569; margin-top: .2rem; }
.chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(460px,1fr));
              gap: 1.5rem; margin: 1rem 0; }
.chart-box { background: #f8fafc; border: 1px solid #e2e8f0;
             border-radius: 8px; overflow: hidden; }
.chart-box .chart-title { font-size: .8rem; font-weight: 600; color: #374151;
                           padding: .6rem .9rem; background: #f1f5f9;
                           border-bottom: 1px solid #e2e8f0; }
.chart-box img { width: 100%; height: auto; display: block; }
.sql-block { font-family: "Cascadia Code","Fira Mono","Courier New",monospace;
             font-size: .75rem; background: #0f172a; color: #e2e8f0;
             padding: 1rem 1.25rem; border-radius: 6px; overflow-x: auto;
             white-space: pre; margin: .5rem 0 1rem; }
.badge { display: inline-block; padding: .15rem .5rem; border-radius: 4px;
         font-size: .7rem; font-weight: 600; }
.badge-blue  { background: #dbeafe; color: #1d4ed8; }
.badge-green { background: #dcfce7; color: #15803d; }
.badge-amber { background: #fef9c3; color: #92400e; }
.skip-bar { display: flex; align-items: center; gap: .5rem; margin: .25rem 0; }
.skip-bar .label { width: 160px; font-size: .78rem; color: #475569;
                   text-align: right; flex-shrink: 0; }
.skip-bar .track { flex: 1; background: #e2e8f0; border-radius: 3px;
                   height: 10px; overflow: hidden; }
.skip-bar .fill  { height: 100%; background: #6366f1; border-radius: 3px; }
.skip-bar .count { width: 80px; font-size: .75rem; color: #64748b; }
.kw-group { margin-bottom: 1.5rem; }
.kw-group h3 { color: #1e40af; }
.sg-name { font-weight: 600; color: #374151; margin: .6rem 0 .3rem; font-size: .83rem; }
.term-list { display: flex; flex-wrap: wrap; gap: .3rem; margin-left: .5rem; }
.term-pill { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 99px;
             padding: .1rem .55rem; font-size: .72rem; color: #1e40af; }
.log-block { font-family: "Cascadia Code","Fira Mono","Courier New",monospace;
             font-size: .72rem; background: #0f172a; color: #94a3b8;
             padding: 1rem 1.25rem; border-radius: 6px; overflow-x: auto;
             white-space: pre-wrap; word-break: break-all; max-height: 500px;
             overflow-y: auto; margin: .5rem 0 1rem; }
.footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e2e8f0;
          font-size: .75rem; color: #94a3b8; }
@media print {
  .log-block, .sql-block { max-height: none; }
  body { padding: 1rem; font-size: 12px; }
}
"""


def _html_page(title: str, stage_label: str, pipeline_name: str, body: str, generated_at: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>{_esc(title)}</h1>
<div class="meta">
  <span><strong>Pipeline:</strong> {_esc(pipeline_name)}</span>
  <span><strong>Stage:</strong> {_esc(stage_label)}</span>
  <span><strong>Generated:</strong> {_esc(generated_at)}</span>
</div>
{body}
<div class="footer">Generated by OA Pipeline Dashboard</div>
</body>
</html>"""


def _esc(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _stat_card(value: str, label: str, color: str = "#3b82f6") -> str:
    return (
        f'<div class="stat-card" style="border-left-color:{color}">'
        f'<div class="val">{_esc(value)}</div>'
        f'<div class="lbl">{_esc(label)}</div></div>'
    )


def _png_to_b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _chart_img(title: str, png: bytes) -> str:
    b64 = _png_to_b64(png)
    return (
        f'<div class="chart-box">'
        f'<div class="chart-title">{_esc(title)}</div>'
        f'<img src="data:image/png;base64,{b64}" alt="{_esc(title)}">'
        f'</div>'
    )


def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _read_log_tail(log_path: Path, lines: int = 200) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        return "\n".join(all_lines[-lines:])
    except Exception:
        return "(log not available)"


# ---------------------------------------------------------------------------
# Stage 1 — Keyword Selection
# ---------------------------------------------------------------------------

def build_keywords_report(pipeline: dict, pipeline_dir: Path | None) -> str:
    """Generate HTML report for the keyword selection stage."""
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cfg = pipeline.get("config") or {}

    # Try to load keywords.json from pipeline dir (richer than config)
    kw_data: dict | None = None
    if pipeline_dir:
        kw_path = pipeline_dir / "keywords.json"
        if kw_path.exists():
            try:
                kw_data = json.loads(kw_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    groups = (kw_data or cfg).get("groups", [])

    body = '<h2>Keyword Configuration</h2>'

    if not groups:
        body += '<p>No keyword groups configured.</p>'
    else:
        # Summary stats
        total_terms = sum(
            len(sg.get("terms", []))
            for g in groups
            for sg in g.get("subgroups", [])
        )
        body += '<div class="stat-grid">'
        body += _stat_card(str(len(groups)), "Top-level groups", "#6366f1")
        body += _stat_card(
            str(sum(len(g.get("subgroups", [])) for g in groups)),
            "Subgroups total", "#0891b2"
        )
        body += _stat_card(str(total_terms), "Total terms", "#059669")
        body += '</div>'

        # Per-group detail
        for group in groups:
            gname = group.get("name", "Group")
            body += f'<div class="kw-group"><h3>{_esc(gname)}</h3>'

            subgroups = group.get("subgroups", [])
            if subgroups:
                body += '<table><thead><tr><th>Subgroup</th><th>Term count</th><th>Terms</th></tr></thead><tbody>'
                for sg in subgroups:
                    terms = sg.get("terms", [])
                    pills = "".join(f'<span class="term-pill">{_esc(t)}</span>' for t in terms)
                    body += (
                        f'<tr><td>{_esc(sg.get("name",""))}</td>'
                        f'<td style="text-align:center">{len(terms)}</td>'
                        f'<td><div class="term-list">{pills}</div></td></tr>'
                    )
                body += '</tbody></table>'
            else:
                flat = group.get("terms", [])
                if flat:
                    pills = "".join(f'<span class="term-pill">{_esc(t)}</span>' for t in flat)
                    body += f'<div class="term-list">{pills}</div>'

            body += '</div>'

    # Match logic note
    body += (
        '<h2>Match Logic</h2>'
        '<p>A record is included when its title or abstract contains at least one term '
        'from <em>each</em> top-level group (AND logic between groups, OR within each group). '
        'Matching uses stemming and unicode normalisation for robustness.</p>'
    )

    # Pipeline config extras
    workers = cfg.get("workers")
    if workers:
        body += f'<p><strong>Workers:</strong> {_esc(str(workers))}</p>'

    return _html_page(
        title="Keyword Selection Report",
        stage_label="① Keyword Selection",
        pipeline_name=pipeline.get("name", ""),
        body=body,
        generated_at=ts,
    )


# ---------------------------------------------------------------------------
# Stage 2 — OpenAlex Search
# ---------------------------------------------------------------------------

def build_search_report(pipeline: dict, pipeline_dir: Path | None) -> str:
    """Generate HTML report for the OpenAlex search / filtering stage."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    state: dict = {}
    if pipeline_dir:
        sp = pipeline_dir / "pipeline_state.json"
        if sp.exists():
            try:
                state = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                pass

    all_files  = len(state.get("all_files", []))
    processed  = len(state.get("processed_files", []))
    failed     = len(state.get("failed_files", []))
    seen       = state.get("total_records_seen", 0)
    inserted   = state.get("total_records_inserted", 0)
    skip_stats = state.get("skip_stats", {})
    started    = state.get("started_at", "—")
    updated    = state.get("last_updated", "—")

    body = '<h2>Search Progress</h2><div class="stat-grid">'
    pct = round(100 * processed / all_files, 1) if all_files else 0
    body += _stat_card(f"{processed:,} / {all_files:,}", f"Files processed ({pct}%)", "#2563eb")
    body += _stat_card(_fmt_int(seen),     "Records scanned",  "#0891b2")
    body += _stat_card(_fmt_int(inserted), "Records matched",  "#059669")
    if seen:
        body += _stat_card(
            f"{inserted / seen * 100:.4f}%", "Match rate", "#d97706"
        )
    if failed:
        body += _stat_card(str(failed), "File failures", "#dc2626")
    body += '</div>'

    if started != "—":
        body += f'<p><strong>Started:</strong> {_esc(str(started))} &nbsp;&nbsp; <strong>Last updated:</strong> {_esc(str(updated))}</p>'

    # Screening funnel
    body += '<h2>Screening Funnel</h2><div class="funnel">'
    total_skipped = sum(skip_stats.values())

    _SKIP_LABELS = {
        "not_english": "not English", "no_doi": "no DOI",
        "no_abstract": "no abstract", "no_keywords": "no match",
        "empty_abstract": "empty abstract", "json_error": "JSON error",
        "not_oa": "not OA", "empty_line": "empty line",
        "no_id": "no ID", "processing_error": "error",
    }
    funnel_steps = [
        (seen + total_skipped, "Total scanned"),
        (seen - skip_stats.get("not_english", 0) - skip_stats.get("not_oa", 0), "Passed pre-screen"),
        (seen, "Had abstract"),
        (inserted, "Keyword match"),
    ]
    for n, lbl in funnel_steps:
        body += (
            f'<div class="funnel-step">'
            f'<div class="n">{_fmt_int(max(0, n))}</div>'
            f'<div class="l">{_esc(lbl)}</div>'
            f'</div>'
        )
    body += '</div>'

    # Skip breakdown
    if skip_stats:
        body += '<h2>Skip Breakdown</h2>'
        max_count = max(skip_stats.values(), default=1)
        for reason, count in sorted(skip_stats.items(), key=lambda kv: kv[1], reverse=True):
            label = _SKIP_LABELS.get(reason, reason)
            pct_r  = count / (total_skipped or 1) * 100
            width  = int(count / max_count * 100)
            body += (
                f'<div class="skip-bar">'
                f'<div class="label">{_esc(label)}</div>'
                f'<div class="track"><div class="fill" style="width:{width}%"></div></div>'
                f'<div class="count">{count:,} ({pct_r:.1f}%)</div>'
                f'</div>'
            )

    # Log excerpt
    if pipeline_dir:
        log_path = pipeline_dir / "pipeline.log"
        if log_path.exists():
            body += '<h2>Pipeline Log (last 200 lines)</h2>'
            body += f'<div class="log-block">{_esc(_read_log_tail(log_path, 200))}</div>'

    return _html_page(
        title="OpenAlex Search Report",
        stage_label="② OpenAlex Search",
        pipeline_name=pipeline.get("name", ""),
        body=body,
        generated_at=ts,
    )


# ---------------------------------------------------------------------------
# Stage 3 & 5 — Analysis and LLM Analysis
# ---------------------------------------------------------------------------

def build_analysis_report(
    pipeline: dict,
    db_path: Path | None,
    pipeline_config: dict,
    chart_gen,
    *,
    llm: bool = False,
    top_n: int = 20,
) -> str:
    """Generate HTML report for the analysis stage (standard or LLM-only charts).

    chart_gen is the dashboard's core.chart_gen module (passed to avoid circular import).
    """
    from core.charts import _CHART_DEFINITIONS

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stage_label = "⑤ LLM Analysis" if llm else "③ Analysis"
    title       = "LLM Analysis Report"  if llm else "Analysis Report"

    charts = [c for c in _CHART_DEFINITIONS.values() if c.llm_only == llm]

    body = ''

    # Record count
    record_count = 0
    if db_path and db_path.exists():
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                row = conn.execute("SELECT COUNT(*) FROM filtered_records").fetchone()
                record_count = row[0] if row else 0
        except Exception:
            pass

    body += '<div class="stat-grid">'
    body += _stat_card(_fmt_int(record_count), "Matched records", "#059669")
    body += _stat_card(str(len(charts)), "Charts", "#6366f1")
    body += '</div>'

    if not db_path or not db_path.exists():
        body += '<p>Database not available — run the search stage first.</p>'
        return _html_page(title=title, stage_label=stage_label,
                          pipeline_name=pipeline.get("name", ""), body=body, generated_at=ts)

    # Charts
    body += '<h2>Charts</h2><div class="chart-grid">'
    for spec in charts:
        try:
            png = chart_gen.generate(db_path, spec.id, pipeline_config, top_n=top_n)
            body += _chart_img(spec.title, png)
        except Exception:
            body += f'<div class="chart-box"><div class="chart-title">{_esc(spec.title)}</div><p style="padding:.75rem;color:#94a3b8;font-size:.8rem">Chart unavailable</p></div>'
    body += '</div>'

    # SQL queries
    body += '<h2>SQL Queries</h2>'
    body += '<p>The following queries were used to generate each chart (top_n={}).'.format(top_n)
    body += ' All queries run against the <code>filtered_records</code> table.</p>'
    for spec in charts:
        sql = spec.format_sql(top_n=top_n)
        body += f'<h3>{_esc(spec.title)}</h3><div class="sql-block">{_esc(sql)}</div>'

    return _html_page(title=title, stage_label=stage_label,
                      pipeline_name=pipeline.get("name", ""), body=body, generated_at=ts)


# ---------------------------------------------------------------------------
# Stage 4 — LLM Classification
# ---------------------------------------------------------------------------

def build_llm_classification_report(pipeline: dict, pipeline_dir: Path | None) -> str:
    """Generate HTML report for the LLM classification stage."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    batch_state: dict = {}
    if pipeline_dir:
        bp = pipeline_dir / "batch_runs" / "state.json"
        if bp.exists():
            try:
                batch_state = json.loads(bp.read_text(encoding="utf-8"))
            except Exception:
                pass

    body = '<h2>LLM Classification Summary</h2>'

    if batch_state:
        total      = batch_state.get("total_records", 0)
        classified = batch_state.get("records_classified", 0)
        batches    = batch_state.get("batches", [])
        model      = batch_state.get("model", "—")
        started    = batch_state.get("started_at", "—")

        body += '<div class="stat-grid">'
        body += _stat_card(_fmt_int(classified), "Records classified", "#059669")
        body += _stat_card(_fmt_int(total),      "Total records",      "#2563eb")
        if total:
            body += _stat_card(f"{classified/total*100:.1f}%", "Completion", "#0891b2")
        body += _stat_card(str(len(batches)), "API batches", "#6366f1")
        body += '</div>'

        body += f'<p><strong>Model:</strong> {_esc(str(model))}</p>'
        body += f'<p><strong>Started:</strong> {_esc(str(started))}</p>'

        if batches:
            body += '<h2>Batch Log</h2>'
            body += '<table><thead><tr><th>#</th><th>Batch ID</th><th>Status</th><th>Records</th><th>Created</th></tr></thead><tbody>'
            for i, b in enumerate(batches, 1):
                status  = b.get("status", "—")
                badge   = "badge-green" if status == "completed" else ("badge-amber" if status in ("in_progress","validating") else "badge-blue")
                body += (
                    f'<tr>'
                    f'<td>{i}</td>'
                    f'<td style="font-family:monospace;font-size:.75rem">{_esc(b.get("batch_id",""))}</td>'
                    f'<td><span class="badge {badge}">{_esc(status)}</span></td>'
                    f'<td>{_fmt_int(b.get("record_count",0))}</td>'
                    f'<td>{_esc(str(b.get("created_at","—")))}</td>'
                    f'</tr>'
                )
            body += '</tbody></table>'
    else:
        body += '<p>No LLM batch state found — classification may not have run yet.</p>'

    # Log excerpt
    if pipeline_dir:
        log_path = pipeline_dir / "pipeline.log"
        if log_path.exists():
            body += '<h2>Pipeline Log (last 100 lines)</h2>'
            body += f'<div class="log-block">{_esc(_read_log_tail(log_path, 100))}</div>'

    return _html_page(
        title="LLM Classification Report",
        stage_label="④ LLM Classification",
        pipeline_name=pipeline.get("name", ""),
        body=body,
        generated_at=ts,
    )


# ---------------------------------------------------------------------------
# Stage 6 — Summary
# ---------------------------------------------------------------------------

def build_summary_report(
    pipeline: dict,
    db_path: Path | None,
    pipeline_dir: Path | None,
    pipeline_config: dict,
    chart_gen,
    top_n: int = 20,
) -> str:
    """Generate the top-level summary HTML report (root of ZIP)."""
    from core.charts import _CHART_DEFINITIONS

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Stats from DB ──
    record_count = 0
    llm_done     = 0
    if db_path and db_path.exists():
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
                if "llm_is_hydrocarbon" in existing_cols:
                    row = conn.execute(
                        "SELECT COUNT(*), SUM(CASE WHEN llm_is_hydrocarbon IS NOT NULL THEN 1 ELSE 0 END) "
                        "FROM filtered_records"
                    ).fetchone()
                    record_count = row[0] or 0
                    llm_done     = row[1] or 0
                else:
                    row = conn.execute("SELECT COUNT(*) FROM filtered_records").fetchone()
                    record_count = row[0] if row else 0
        except Exception:
            pass

    # ── Pipeline search progress ──
    search_progress: dict = {}
    if pipeline_dir:
        sp = pipeline_dir / "pipeline_state.json"
        if sp.exists():
            try:
                ps = json.loads(sp.read_text(encoding="utf-8"))
                search_progress = {
                    "total":     len(ps.get("all_files", [])),
                    "processed": len(ps.get("processed_files", [])),
                    "seen":      ps.get("total_records_seen", 0),
                    "inserted":  ps.get("total_records_inserted", 0),
                }
            except Exception:
                pass

    db_size_mb = 0.0
    if db_path and db_path.exists():
        try:
            db_size_mb = db_path.stat().st_size / (1024 ** 2)
        except OSError:
            pass

    body = '<h2>Pipeline Overview</h2><div class="stat-grid">'
    body += _stat_card(_fmt_int(record_count), "Matched records", "#059669")
    if search_progress:
        body += _stat_card(
            f"{search_progress['processed']:,} / {search_progress['total']:,}",
            "Files processed", "#2563eb"
        )
        body += _stat_card(_fmt_int(search_progress["seen"]), "Records scanned", "#0891b2")
    if llm_done:
        body += _stat_card(_fmt_int(llm_done), "LLM classified", "#7c3aed")
    body += _stat_card(f"{db_size_mb:.1f} MB", "Database size", "#d97706")
    body += '</div>'

    # ── Pipeline metadata table ──
    body += '<h2>Pipeline Metadata</h2><table><tbody>'
    meta_rows = [
        ("Name",         pipeline.get("name", "")),
        ("Pipeline ID",  pipeline.get("id", "")),
        ("Stage",        pipeline.get("current_stage", "")),
        ("Created",      pipeline.get("created_at", "")),
        ("Last updated", pipeline.get("updated_at", "")),
        ("Owner ID",     pipeline.get("user_id", "")),
    ]
    if pipeline.get("forked_from_id"):
        meta_rows.append(("Forked from", pipeline["forked_from_id"]))
    for k, v in meta_rows:
        if v not in (None, "", 0):
            body += f'<tr><th style="width:160px">{_esc(k)}</th><td>{_esc(str(v))}</td></tr>'
    body += '</tbody></table>'

    # ── Key charts (non-LLM only for the summary) ──
    non_llm = [c for c in _CHART_DEFINITIONS.values() if not c.llm_only]
    if db_path and db_path.exists() and non_llm:
        body += '<h2>Key Charts</h2><div class="chart-grid">'
        # Show first 6 non-LLM charts in the summary
        for spec in non_llm[:6]:
            try:
                png = chart_gen.generate(db_path, spec.id, pipeline_config, top_n=top_n)
                body += _chart_img(spec.title, png)
            except Exception:
                pass
        body += '</div>'
        if len(non_llm) > 6:
            body += f'<p>See <strong>stages/3_analysis/report.html</strong> for all {len(non_llm)} analysis charts.</p>'

    # ── ZIP structure note ──
    body += (
        '<h2>Export Contents</h2>'
        '<p>This ZIP is organized as follows:</p>'
        '<table><tbody>'
        '<tr><th>Path</th><th>Contents</th></tr>'
        '<tr><td><code>summary_report.html</code></td><td>This file — pipeline overview and key charts</td></tr>'
        '<tr><td><code>data/</code></td><td>Database export (CSV / SQLite as selected)</td></tr>'
        '<tr><td><code>stages/1_keyword_selection/</code></td><td>Keywords report, keywords.json</td></tr>'
        '<tr><td><code>stages/2_openalex_search/</code></td><td>Search report, pipeline_state.json</td></tr>'
        '<tr><td><code>stages/3_analysis/</code></td><td>Analysis report, chart PNGs, chart_queries.sql</td></tr>'
        '<tr><td><code>stages/4_llm_classification/</code></td><td>LLM classification report, batch_state.json</td></tr>'
        '<tr><td><code>stages/5_llm_analysis/</code></td><td>LLM analysis report, LLM chart PNGs</td></tr>'
        '<tr><td><code>logs/</code></td><td>Pipeline log files</td></tr>'
        '<tr><td><code>pipeline_metadata.json</code></td><td>Full pipeline configuration</td></tr>'
        '<tr><td><code>manifest.txt</code></td><td>File inventory and statistics</td></tr>'
        '</tbody></table>'
    )

    return _html_page(
        title="Pipeline Summary Report",
        stage_label="⑥ Summary",
        pipeline_name=pipeline.get("name", ""),
        body=body,
        generated_at=ts,
    )


# ---------------------------------------------------------------------------
# Orthogonality & Data Quality Report Section
# ---------------------------------------------------------------------------

def build_orthogonality_section(db_path: Path) -> str:
    """Generate HTML section with Cramér's V, entropy, tautological pair warnings, and algorithm diversity."""
    from core.orthogonality import (
        cramers_v_matrix, conditional_entropy_analysis,
        tautological_pairs, pmi_analysis, data_quality_audit,
    )

    body = '<h2>Taxonomy Orthogonality Analysis</h2>'

    # Cramér's V
    try:
        cv = cramers_v_matrix(db_path, ml_only=True)
        dims = cv["dimensions"]
        mat = cv["matrix"]
        body += '<h3>Cramér\'s V Matrix (ML-Positive Papers)</h3>'
        body += '<table><thead><tr><th></th>'
        for d in dims:
            body += f'<th>{_esc(d)}</th>'
        body += '</tr></thead><tbody>'
        for i, d in enumerate(dims):
            body += f'<tr><th>{_esc(d)}</th>'
            for j in range(len(dims)):
                v = mat[i][j]
                color = "background:#fee2e2" if v > 0.3 else ("background:#fef9c3" if v > 0.15 else "")
                style = f' style="{color};text-align:center"' if color else ' style="text-align:center"'
                body += f'<td{style}>{v:.3f}</td>'
            body += '</tr>'
        body += '</tbody></table>'
    except Exception:
        body += '<p>Cramér\'s V calculation unavailable.</p>'

    # Conditional entropy
    try:
        ent = conditional_entropy_analysis(db_path, ml_only=True)
        body += '<h3>Conditional Entropy</h3>'
        body += '<div class="stat-grid">'
        body += _stat_card(f"{ent['h_role']:.2f} bits", "H(ML Role)", "#6366f1")
        body += _stat_card(f"{ent['h_role_given_fuel']:.2f} bits", "H(ML Role | Fuel Class)", "#0891b2")
        body += _stat_card(f"{ent['mi']:.2f} bits", "Mutual Information", "#059669")
        body += _stat_card(f"{ent['uncertainty_reduction_pct']:.1f}%", "Uncertainty Reduction", "#d97706")
        body += '</div>'
        body += f'<p>Knowing the fuel domain reduces role uncertainty by <strong>{ent["uncertainty_reduction_pct"]:.1f}%</strong>.</p>'

        # Per-fuel entropy table
        if ent["per_fuel"]:
            body += '<h3>H(ML Role) per Fuel Classification</h3>'
            body += '<table><thead><tr><th>Fuel Class</th><th>Papers</th><th>H(Role) bits</th><th>Flag</th></tr></thead><tbody>'
            for pf in ent["per_fuel"][:20]:
                flag = '<span class="badge badge-amber">low diversity</span>' if pf["h_role"] < 1.5 else ''
                body += f'<tr><td>{_esc(pf["fuel_class"])}</td><td>{pf["count"]:,}</td><td>{pf["h_role"]:.3f}</td><td>{flag}</td></tr>'
            body += '</tbody></table>'
    except Exception:
        body += '<p>Entropy analysis unavailable.</p>'

    # Tautological pairs
    try:
        tp = tautological_pairs(db_path, ml_only=True)
        flagged = [p for p in tp["pairs"] if p["flagged"]]
        if flagged:
            body += '<h3>Tautological Pair Warnings</h3>'
            body += f'<p>{len(flagged)} fuel×role pairs have &gt;50% overlap:</p>'
            body += '<table><thead><tr><th>Fuel Class</th><th>Dominant Role</th><th>Overlap</th><th>Count</th></tr></thead><tbody>'
            for p in flagged:
                body += f'<tr><td>{_esc(p["fuel_class"])}</td><td>{_esc(p["top_ml_role"])}</td><td>{p["overlap_pct"]}%</td><td>{p["count"]:,}</td></tr>'
            body += '</tbody></table>'
    except Exception:
        pass

    return body


def build_data_quality_section(db_path: Path) -> str:
    """Generate HTML section with data quality audit results."""
    from core.orthogonality import data_quality_audit

    body = '<h2>Data Quality Audit</h2>'

    try:
        audit = data_quality_audit(db_path)
        body += '<div class="stat-grid">'
        body += _stat_card(_fmt_int(audit.get("total_papers", 0)), "Total Papers", "#2563eb")
        body += _stat_card(_fmt_int(audit.get("ml_positive_papers", 0)), "ML-Positive Papers", "#059669")
        body += '</div>'

        issues = []
        role_no_algo = audit.get("role_without_algorithm", 0)
        if role_no_algo:
            issues.append(f'<tr><td>ML role without algorithm</td><td class="text-red">{role_no_algo:,}</td><td>Papers with ml_role ≠ none but ml_algorithm is empty</td></tr>')

        scope_leaks = audit.get("scope_leaks", 0)
        if scope_leaks:
            issues.append(f'<tr><td>Scope leaks</td><td class="text-red">{scope_leaks:,}</td><td>is_hydrocarbon=0 but ml_role ≠ none</td></tr>')

        multi_role = audit.get("multi_role_papers", 0)
        if multi_role:
            issues.append(f'<tr><td>Multi-role papers (3+)</td><td>{multi_role:,}</td><td>Potential over-labelling</td></tr>')

        other_ml = audit.get("other_ml_count", 0)
        if other_ml:
            issues.append(f'<tr><td>"other_ml" algorithm</td><td>{other_ml:,}</td><td>Should be minimised</td></tr>')

        other_fuel = audit.get("other_fuel_count", 0)
        if other_fuel:
            issues.append(f'<tr><td>"other" fuel classification</td><td>{other_fuel:,}</td><td>Review for specificity</td></tr>')

        if issues:
            body += '<h3>Flagged Issues</h3>'
            body += '<table><thead><tr><th>Issue</th><th>Count</th><th>Description</th></tr></thead><tbody>'
            body += ''.join(issues)
            body += '</tbody></table>'
        else:
            body += '<p style="color:#059669">No data quality issues detected.</p>'

        # Confidence distribution
        conf = audit.get("confidence_distribution")
        if conf:
            body += '<h3>Confidence Distribution</h3>'
            body += '<table><thead><tr><th>Level</th><th>All Papers</th><th>ML-Positive</th></tr></thead><tbody>'
            ml_conf = audit.get("ml_positive_confidence", {})
            for level in ["High", "Medium", "Low"]:
                body += f'<tr><td>{level}</td><td>{conf.get(level, 0):,}</td><td>{ml_conf.get(level, 0):,}</td></tr>'
            body += '</tbody></table>'

    except Exception:
        body += '<p>Data quality audit unavailable.</p>'

    return body
