#!/usr/bin/env python3
"""
generate_report.py
==================
Generate a self-contained HTML report from the filtered OpenAlex SQLite DB.

Sections
--------
  1. (opt) Filtering funnel
  2. Publication trends
  3. Keyword analysis
  4. AI / ML in sustainable-fuels research   ← new (requires LLM batch results)
  5. Geographic distribution
  6. Journals and sources
  7. Open access & work types
  8. Citation impact
  9. Top institutions
  10. Summary

Usage
-----
    python generate_report.py
    python generate_report.py --db results.sqlite --output report.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sqlite3
import textwrap
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# ═══════════════════════════════════════════════════════════════════════════
# Style
# ═══════════════════════════════════════════════════════════════════════════
PALETTE = [
    "#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED",
    "#0891B2", "#65A30D", "#C026D3", "#EA580C", "#4F46E5",
    "#0D9488", "#CA8A04", "#BE185D", "#6D28D9", "#0284C7",
]
BG_COLOR   = "#FAFAFA"
GRID_COLOR = "#E5E7EB"
TEXT_COLOR = "#1F2937"
MUTED_COLOR= "#6B7280"

plt.rcParams.update({
    "figure.facecolor": BG_COLOR,
    "axes.facecolor": "#FFFFFF",
    "axes.edgecolor": GRID_COLOR,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": GRID_COLOR,
    "grid.linewidth": 0.5,
    "text.color": TEXT_COLOR,
    "axes.labelcolor": TEXT_COLOR,
    "xtick.color": MUTED_COLOR,
    "ytick.color": MUTED_COLOR,
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
})


def _fig_to_base64(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _img_tag(b64: str, alt: str = "") -> str:
    return (
        f'<img src="data:image/png;base64,{b64}" '
        f'alt="{alt}" style="max-width:100%;height:auto;">'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════════
def load_data(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM filtered_records").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_pipeline_state(state_path: Path) -> dict | None:
    if state_path and state_path.exists():
        return json.loads(state_path.read_text())
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Analysis helpers
# ═══════════════════════════════════════════════════════════════════════════
def _json_col(row: dict, col: str) -> list:
    val = row.get(col)
    if not val:
        return []
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _count_json_col(records: list[dict], col: str) -> Counter:
    c = Counter()
    for r in records:
        for v in _json_col(r, col):
            c[v] += 1
    return c


PRE2000_LABEL = "Unknown / ≤ 2000"

# ISO-3166 alpha-2 → full name for common research countries
_ISO2_NAMES = {
    "CN":"China","US":"United States","GB":"United Kingdom","DE":"Germany",
    "JP":"Japan","IN":"India","KR":"South Korea","AU":"Australia","CA":"Canada",
    "FR":"France","IT":"Italy","BR":"Brazil","ES":"Spain","NL":"Netherlands",
    "SE":"Sweden","CH":"Switzerland","PL":"Poland","RU":"Russia","IR":"Iran",
    "SA":"Saudi Arabia","NG":"Nigeria","ZA":"South Africa","MX":"Mexico",
    "PT":"Portugal","BE":"Belgium","AT":"Austria","DK":"Denmark","NO":"Norway",
    "FI":"Finland","TR":"Turkey","EG":"Egypt","PK":"Pakistan","MY":"Malaysia",
    "TH":"Thailand","ID":"Indonesia","PH":"Philippines","TW":"Taiwan",
    "SG":"Singapore","HK":"Hong Kong","NZ":"New Zealand","CL":"Chile",
    "AR":"Argentina","CO":"Colombia","PE":"Peru","CZ":"Czech Republic",
    "HU":"Hungary","RO":"Romania","UA":"Ukraine","GR":"Greece","IL":"Israel",
    "AE":"United Arab Emirates","QA":"Qatar","KW":"Kuwait","IQ":"Iraq",
    "BD":"Bangladesh","LK":"Sri Lanka","NP":"Nepal","ET":"Ethiopia",
    "KE":"Kenya","GH":"Ghana","CM":"Cameroon","VN":"Vietnam","TN":"Tunisia",
    "MA":"Morocco","DZ":"Algeria","RS":"Serbia","HR":"Croatia","BG":"Bulgaria",
    "SK":"Slovakia","SI":"Slovenia","LT":"Lithuania","LV":"Latvia","EE":"Estonia",
    "IS":"Iceland","IE":"Ireland","LU":"Luxembourg","CY":"Cyprus","MT":"Malta",
    "BA":"Bosnia and Herzegovina","MK":"North Macedonia","AL":"Albania",
    "BY":"Belarus","MD":"Moldova","GE":"Georgia","AM":"Armenia","AZ":"Azerbaijan",
    "KZ":"Kazakhstan","UZ":"Uzbekistan","TM":"Turkmenistan","TJ":"Tajikistan",
    "KG":"Kyrgyzstan","MN":"Mongolia","MM":"Myanmar","KH":"Cambodia","LA":"Laos",
    "NE":"Niger","CI":"Ivory Coast","SN":"Senegal","TZ":"Tanzania","UG":"Uganda",
}


def _country_display(code: str) -> str:
    """Return full name for an ISO-3166 alpha-2 code, falling back to the code itself."""
    return _ISO2_NAMES.get((code or "").upper().strip(), code)


def _year_range(records: list[dict]) -> tuple[int, int]:
    years = [r["publication_year"] for r in records
             if r.get("publication_year") and r["publication_year"] > 2000]
    return (min(years), max(years)) if years else (0, 0)


def _uses_ml(r: dict) -> bool:
    """True when the paper actually employs an ML/AI method."""
    paradigm = _json_col(r, "llm_ml_paradigm")
    if not paradigm:
        return False
    return not all(p.strip().lower() in ("none", "") for p in paradigm)


# ═══════════════════════════════════════════════════════════════════════════
# Chart generators — existing
# ═══════════════════════════════════════════════════════════════════════════
def chart_publications_by_year(records: list[dict]) -> str:
    pre = sum(1 for r in records
              if not r.get("publication_year") or r["publication_year"] <= 2000)
    modern = [r["publication_year"] for r in records
              if r.get("publication_year") and r["publication_year"] > 2000]
    if not modern and pre == 0:
        return ""
    c = Counter(modern)
    xs = list(range(min(c) if c else 2001, (max(c) if c else 2001) + 1))
    ys = [c.get(x, 0) for x in xs]
    labels = [PRE2000_LABEL] + [str(x) for x in xs]
    values = [pre] + ys
    colors = [PALETTE[3]] + [PALETTE[0]] * len(ys)
    fig, ax = plt.subplots(figsize=(max(10, len(labels) * 0.45), 4.5))
    ax.bar(range(len(labels)), values, color=colors, width=0.75, zorder=3)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Publication year"); ax.set_ylabel("Papers")
    ax.set_title("Publications by year")
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    if c:
        pk = max(c, key=c.get); pi = xs.index(pk) + 1
        ax.annotate(str(c[pk]), (pi, c[pk]), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=9,
                    color=PALETTE[0], fontweight="bold")
    return _fig_to_base64(fig)


def chart_top_terms(counter: Counter, title: str, n: int = 15,
                    color: str = PALETTE[0]) -> str:
    if not counter:
        return ""
    top = counter.most_common(n)
    labels = [t for t, _ in reversed(top)]
    values = [v for _, v in reversed(top)]
    fig, ax = plt.subplots(figsize=(9, max(3.5, n * 0.35)))
    bars = ax.barh(labels, values, color=color, height=0.65, zorder=3)
    ax.set_xlabel("Papers"); ax.set_title(title)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.015,
                bar.get_y() + bar.get_height() / 2,
                str(val), va="center", fontsize=9, color=MUTED_COLOR)
    return _fig_to_base64(fig)


def chart_cooccurrence_heatmap(records: list[dict],
                                n_fuel: int = 12, n_impact: int = 12,
                                group1_label: str = "Fuel", group2_label: str = "Impact") -> str:
    fuel_c   = _count_json_col(records, "matched_fuel_terms")
    impact_c = _count_json_col(records, "matched_impact_terms")
    top_fuel   = [t for t, _ in fuel_c.most_common(n_fuel)]
    top_impact = [t for t, _ in impact_c.most_common(n_impact)]
    if not top_fuel or not top_impact:
        return ""
    matrix = [[0]*len(top_impact) for _ in range(len(top_fuel))]
    for r in records:
        fts = set(_json_col(r, "matched_fuel_terms"))
        its = set(_json_col(r, "matched_impact_terms"))
        for fi, f in enumerate(top_fuel):
            if f in fts:
                for ii, im in enumerate(top_impact):
                    if im in its:
                        matrix[fi][ii] += 1
    fig, ax = plt.subplots(figsize=(max(8, n_impact * 0.7), max(5, n_fuel * 0.45)))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(top_impact)))
    ax.set_xticklabels(top_impact, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(top_fuel)))
    ax.set_yticklabels(top_fuel, fontsize=9)
    ax.set_title(f"{group1_label} \u00d7 {group2_label} term co-occurrence")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = max(max(row) for row in matrix) if any(matrix) else 1
    for fi in range(len(top_fuel)):
        for ii in range(len(top_impact)):
            val = matrix[fi][ii]
            if val > 0:
                tc = "white" if val > mx * 0.6 else TEXT_COLOR
                ax.text(ii, fi, str(val), ha="center", va="center",
                        fontsize=8, color=tc)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_countries(records: list[dict], n: int = 20) -> str:
    raw = _count_json_col(records, "authors_countries")
    named = Counter({_country_display(c): v for c, v in raw.items()})
    return chart_top_terms(named, "Top contributing countries", n=n, color=PALETTE[1])


def chart_journals(records: list[dict], n: int = 15) -> str:
    c = Counter(r.get("primary_location_source_display_name")
                for r in records if r.get("primary_location_source_display_name"))
    return chart_top_terms(c, "Top journals / sources", n=n, color=PALETTE[2])


def chart_oa_status(records: list[dict]) -> str:
    c = Counter((r.get("open_access_oa_status") or "unknown") for r in records)
    if not c:
        return ""
    labels, values = list(c.keys()), list(c.values())
    fig, ax = plt.subplots(figsize=(6, 4))
    wedges, _, autotexts = ax.pie(
        values, labels=None, autopct="%1.1f%%",
        colors=PALETTE[:len(labels)], startangle=90, pctdistance=0.75,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white"},
    )
    for t in autotexts:
        t.set_fontsize(9)
    ax.legend(wedges, [f"{l}  ({v:,})" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=9)
    ax.set_title("Open access status")
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_work_types(records: list[dict]) -> str:
    c = Counter((r.get("type") or "unknown") for r in records)
    return chart_top_terms(c, "Work types", n=10, color=PALETTE[4])


# ═══════════════════════════════════════════════════════════════════════════
# Subgroup helpers
# ═══════════════════════════════════════════════════════════════════════════
def _get_subgroups(keyword_config: dict | None, group_idx: int) -> list[dict]:
    """Return the subgroup list for group at group_idx, or empty list."""
    if not keyword_config:
        return []
    grps = keyword_config.get("groups", [])
    if group_idx >= len(grps):
        return []
    return grps[group_idx].get("subgroups", [])


def _build_term_to_subgroup(subgroups: list[dict]) -> dict[str, str]:
    """Map each term (lowercased) → subgroup name, skipping the 'others' catch-all."""
    mapping: dict[str, str] = {}
    for sg in subgroups:
        if sg.get("is_others"):
            continue
        for t in sg.get("terms", []):
            mapping[t.lower()] = sg["name"]
    return mapping


def _others_name(subgroups: list[dict]) -> str:
    for sg in subgroups:
        if sg.get("is_others"):
            return sg["name"]
    return "Others"


def _group_by_subgroup(
    records: list[dict],
    subgroups: list[dict],
    matched_col: str,
) -> dict[str, list[dict]]:
    """Partition records by subgroup — a record may appear in multiple subgroups."""
    if not subgroups:
        return {}
    term_to_sg = _build_term_to_subgroup(subgroups)
    others = _others_name(subgroups)
    grouped: dict[str, list[dict]] = {sg["name"]: [] for sg in subgroups}
    if others not in grouped:
        grouped[others] = []

    for rec in records:
        matched = {t.lower() for t in _json_col(rec, matched_col)}
        matched_sgs: set[str] = set()
        for t in matched:
            if t in term_to_sg:
                matched_sgs.add(term_to_sg[t])
        if not matched_sgs:
            matched_sgs.add(others)
        for sg_name in matched_sgs:
            if sg_name in grouped:
                grouped[sg_name].append(rec)

    return {k: v for k, v in grouped.items() if v}


def chart_fwci(records: list[dict]) -> tuple[str, list[dict]]:
    """Return (base64_img, outlier_records).

    The histogram is clipped at the IQR-based upper fence so that a handful
    of extreme papers do not compress the bulk of the distribution into
    an unreadable sliver.  Outlier records are returned separately so the
    caller can render a drill-down table.
    """
    vals_recs = [(r["fwci"], r) for r in records if r.get("fwci") is not None]
    if len(vals_recs) < 5:
        return "", []
    vals = [v for v, _ in vals_recs]
    q25, q75 = float(np.percentile(vals, 25)), float(np.percentile(vals, 75))
    iqr = q75 - q25
    fence = q75 + 1.5 * iqr
    med = float(np.median(vals))

    normal_vals = [v for v in vals if v <= fence]
    outlier_recs = [r for v, r in vals_recs if v > fence]

    fig, ax = plt.subplots(figsize=(9, 4))
    bins = min(40, max(10, len(set(round(v, 1) for v in normal_vals))))
    ax.hist(normal_vals, bins=bins, color=PALETTE[0], edgecolor="white", linewidth=0.5, zorder=3)
    ax.axvline(1.0, color=PALETTE[3], linestyle="--", linewidth=1.2, label="World average (1.0)")
    ax.axvline(med, color=PALETTE[1], linestyle="--", linewidth=1.2, label=f"Median ({med:.2f})")
    xlabel = "FWCI"
    if outlier_recs:
        xlabel += f"  (axis clipped at {fence:.1f} — {len(outlier_recs)} outlier{'s' if len(outlier_recs) != 1 else ''} not shown)"
    ax.set_xlabel(xlabel); ax.set_ylabel("Papers")
    ax.set_title("Citation impact distribution")
    ax.legend(fontsize=9)
    return _fig_to_base64(fig), outlier_recs


def chart_institutions(records: list[dict], n: int = 20) -> str:
    return chart_top_terms(_count_json_col(records, "authors_institutions"),
                           "Top institutions", n=n, color=PALETTE[5])


def chart_institutions_per_subgroup(
    records: list[dict],
    subgroups: list[dict],
    matched_col: str,
    n: int = 15,
) -> list[tuple[str, str]]:
    """Return [(sg_name, base64_img), ...] — one chart per non-empty subgroup."""
    grouped = _group_by_subgroup(records, subgroups, matched_col)
    charts = []
    for sg_name, sg_recs in grouped.items():
        if len(sg_recs) < 3:
            continue
        img = chart_top_terms(
            _count_json_col(sg_recs, "authors_institutions"),
            f"Top institutions — {sg_name}",
            n=n,
            color=PALETTE[5],
        )
        if img:
            charts.append((sg_name, img))
    return charts


def chart_yearly_fuel_trends(records: list[dict], n: int = 6,
                              group1_label: str = "Fuel terms") -> str:
    fuel_c   = _count_json_col(records, "matched_fuel_terms")
    top_fuels = [t for t, _ in fuel_c.most_common(n)]
    if not top_fuels:
        return ""
    year_term: dict = {}
    for r in records:
        yr  = r.get("publication_year")
        key = PRE2000_LABEL if (not yr or yr <= 2000) else yr
        year_term.setdefault(key, Counter())
        for f in _json_col(r, "matched_fuel_terms"):
            if f in top_fuels:
                year_term[key][f] += 1
    num_years = sorted(k for k in year_term if isinstance(k, int))
    keys = ([PRE2000_LABEL] if PRE2000_LABEL in year_term else []) + num_years
    series = {f: [year_term.get(k, Counter()).get(f, 0) for k in keys] for f in top_fuels}
    fig, ax = plt.subplots(figsize=(max(10, len(keys) * 0.45), 4.5))
    bottom = [0] * len(keys)
    for i, fuel in enumerate(top_fuels):
        ax.bar(range(len(keys)), series[fuel], bottom=bottom, label=fuel,
               color=PALETTE[i % len(PALETTE)], width=0.75, zorder=3)
        bottom = [b + s for b, s in zip(bottom, series[fuel])]
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels([str(k) for k in keys], rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Year"); ax.set_ylabel("Papers")
    ax.set_title(f"{group1_label} trends over time")
    ax.legend(fontsize=9, loc="upper left", ncol=2)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    return _fig_to_base64(fig)


# ═══════════════════════════════════════════════════════════════════════════
# Chart generators — LLM / ML-AI analysis (new)
# ═══════════════════════════════════════════════════════════════════════════

def chart_fuel_classification(llm_recs: list[dict]) -> str:
    """Bar chart of LLM-assigned research scope categories."""
    c = Counter()
    for r in llm_recs:
        for v in _json_col(r, "llm_fuel_classification"):
            if v and v.lower() not in ("none", "other"):
                c[v] += 1
    if not c:
        return ""
    # Shorten long labels
    SHORT = {
        "Engineering Design Optimization – Emissions Reduction":     "Design Opt. – Emissions",
        "Engineering Design Optimization – Performance Enhancement":  "Design Opt. – Performance",
        "Fuel Property Prediction from molecular composition derived data": "Fuel Property Prediction",
        "Life Cycle Assessment (LCA) – Environmental Impact Analysis": "LCA – Environmental",
        "Techno-Economic Assessment (TEA) – Economic Feasibility Analysis": "TEA – Economic",
        "Discovery of Novel Molecules for Green Energy Applications":  "Novel Molecule Discovery",
        "fuel production from sustainable feedstocks":                "Sustainable Production",
        "Fuel Blend and Additive Research":                           "Blend & Additive Research",
        "Regulatory Research and Analysis":                           "Regulatory",
    }
    renamed = Counter({SHORT.get(k, k): v for k, v in c.items()})
    return chart_top_terms(renamed, "Research scope (LLM classification)", n=20, color=PALETTE[0])


def chart_ml_paradigm(llm_recs: list[dict]) -> str:
    """Bar chart of ML/AI learning paradigms (None excluded for the ML-specific chart)."""
    c = Counter()
    for r in llm_recs:
        for v in _json_col(r, "llm_ml_paradigm"):
            if v and v.lower() != "none":
                c[v] += 1
    return chart_top_terms(c, "ML/AI learning paradigms (papers using ML/AI only)",
                           n=10, color=PALETTE[1])


def chart_ml_method(llm_recs: list[dict]) -> str:
    """Bar chart of ML model classes / methods."""
    c = Counter()
    for r in llm_recs:
        for v in _json_col(r, "llm_ml_method"):
            if v and v.lower() not in ("none", "other"):
                c[v] += 1
    SHORT = {
        "Classical Machine Learning (SVM, Random Forest, Gradient Boosting)": "Classical ML",
        "Deep Learning (Neural Networks)":                                     "Deep Learning",
        "Probabilistic Models (Bayesian Networks, HMM)":                      "Probabilistic",
        "Evolutionary / Heuristic Methods (Genetic Algorithms)":              "Evolutionary / Heuristic",
    }
    renamed = Counter({SHORT.get(k, k): v for k, v in c.items()})
    return chart_top_terms(renamed, "ML/AI model class / method", n=10, color=PALETTE[2])


def chart_ml_adoption_rate(llm_recs: list[dict]) -> str:
    """Pie showing ML-using vs non-ML papers."""
    uses = sum(1 for r in llm_recs if _uses_ml(r))
    nouse = len(llm_recs) - uses
    if uses == 0:
        return ""
    labels = [f"Uses ML/AI  ({uses:,})", f"No ML/AI  ({nouse:,})"]
    values = [uses, nouse]
    fig, ax = plt.subplots(figsize=(6, 4))
    wedges, _, auto = ax.pie(
        values, labels=None, autopct="%1.1f%%",
        colors=[PALETTE[0], PALETTE[7]],
        startangle=90, pctdistance=0.72,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white"},
    )
    for t in auto:
        t.set_fontsize(10)
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1, 0.5), fontsize=10)
    ax.set_title("ML/AI adoption in the corpus")
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_ml_adoption_over_time(llm_recs: list[dict]) -> str:
    """Stacked bar: ML-using vs non-ML papers per year (modern years only)."""
    ml_yr:  Counter = Counter()
    non_yr: Counter = Counter()
    for r in llm_recs:
        yr = r.get("publication_year")
        if not yr or yr <= 2000:
            continue
        if _uses_ml(r):
            ml_yr[yr] += 1
        else:
            non_yr[yr] += 1
    all_years = sorted(set(ml_yr) | set(non_yr))
    if len(all_years) < 2:
        return ""
    ml_vals  = [ml_yr.get(y, 0)  for y in all_years]
    non_vals = [non_yr.get(y, 0) for y in all_years]
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.5))

    # Left: stacked bar
    ax = axes[0]
    xs = range(len(all_years))
    ax.bar(xs, non_vals, label="No ML/AI", color=PALETTE[7], width=0.75, zorder=3)
    ax.bar(xs, ml_vals, bottom=non_vals, label="Uses ML/AI", color=PALETTE[0], width=0.75, zorder=3)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(y) for y in all_years], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Papers"); ax.set_title("ML/AI adoption over time (count)")
    ax.legend(fontsize=9)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    # Right: ML% line
    ax2 = axes[1]
    totals = [m + n for m, n in zip(ml_vals, non_vals)]
    pct = [m / t * 100 if t else 0 for m, t in zip(ml_vals, totals)]
    ax2.plot([str(y) for y in all_years], pct, color=PALETTE[0],
             marker="o", linewidth=2, markersize=5, zorder=3)
    ax2.fill_between(range(len(all_years)), pct, alpha=0.15, color=PALETTE[0])
    ax2.set_xticks(range(len(all_years)))
    ax2.set_xticklabels([str(y) for y in all_years], rotation=45, ha="right", fontsize=9)
    ax2.set_ylabel("% papers using ML/AI")
    ax2.set_title("ML/AI share of publications over time (%)")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax2.set_ylim(0, 100)

    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_scope_x_ml(llm_recs: list[dict]) -> str:
    """Heatmap: research scope × ML paradigm."""
    SHORT_SCOPE = {
        "Engineering Design Optimization – Emissions Reduction":     "Design Opt. – Emissions",
        "Engineering Design Optimization – Performance Enhancement":  "Design Opt. – Performance",
        "Fuel Property Prediction from molecular composition derived data": "Fuel Property Pred.",
        "Life Cycle Assessment (LCA) – Environmental Impact Analysis": "LCA",
        "Techno-Economic Assessment (TEA) – Economic Feasibility Analysis": "TEA",
        "Discovery of Novel Molecules for Green Energy Applications":  "Novel Molecule Discovery",
        "fuel production from sustainable feedstocks":                "Sustainable Production",
        "Fuel Blend and Additive Research":                           "Blend & Additive",
        "Regulatory Research and Analysis":                           "Regulatory",
    }
    SHORT_ML = {
        "Classical Machine Learning (SVM, Random Forest, Gradient Boosting)": "Classical ML",
        "Deep Learning (Neural Networks)":                                     "Deep Learning",
        "Probabilistic Models (Bayesian Networks, HMM)":                      "Probabilistic",
        "Evolutionary / Heuristic Methods (Genetic Algorithms)":              "Evolutionary",
    }
    ml_recs = [r for r in llm_recs if _uses_ml(r)]
    scope_c  = Counter()
    paradigm_c = Counter()
    pairs: Counter = Counter()
    for r in ml_recs:
        scopes    = [SHORT_SCOPE.get(v, v) for v in _json_col(r, "llm_fuel_classification")
                     if v and v.lower() not in ("none", "other")]
        paradigms = [SHORT_ML.get(v, v) for v in _json_col(r, "llm_ml_paradigm")
                     if v and v.lower() != "none"]
        for s in scopes:
            scope_c[s] += 1
        for p in paradigms:
            paradigm_c[p] += 1
        for s in scopes:
            for p in paradigms:
                pairs[(s, p)] += 1

    top_scopes    = [s for s, _ in scope_c.most_common(10)]
    top_paradigms = [p for p, _ in paradigm_c.most_common()]
    if not top_scopes or not top_paradigms:
        return ""

    matrix = np.zeros((len(top_scopes), len(top_paradigms)), dtype=int)
    for (s, p), cnt in pairs.items():
        if s in top_scopes and p in top_paradigms:
            matrix[top_scopes.index(s)][top_paradigms.index(p)] = cnt

    fig, ax = plt.subplots(figsize=(max(7, len(top_paradigms) * 1.6),
                                    max(4, len(top_scopes) * 0.55)))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(top_paradigms)))
    ax.set_xticklabels(top_paradigms, rotation=30, ha="right", fontsize=9)
    ax.set_yticks(range(len(top_scopes)))
    ax.set_yticklabels(top_scopes, fontsize=9)
    ax.set_title("Research scope × ML/AI paradigm  (papers using ML/AI only)")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = matrix.max() or 1
    for i in range(len(top_scopes)):
        for j in range(len(top_paradigms)):
            v = int(matrix[i, j])
            if v > 0:
                tc = "white" if v > mx * 0.6 else TEXT_COLOR
                ax.text(j, i, str(v), ha="center", va="center",
                        fontsize=9, color=tc, fontweight="bold")
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_model_types(llm_recs: list[dict]) -> str:
    """Bar chart of computational model types extracted from papers."""
    type_c: Counter = Counter()
    for r in llm_recs:
        for m in _json_col(r, "llm_models"):
            if isinstance(m, dict):
                t = (m.get("type") or "").strip()
                if t:
                    # Normalise common abbreviations
                    tl = t.lower()
                    if any(x in tl for x in ("cfd", "computational fluid")):
                        t = "CFD"
                    elif any(x in tl for x in ("ml", "machine learning", "deep learning",
                                                "neural", "random forest", "svm",
                                                "gradient boost", "regression")):
                        t = "ML/AI"
                    elif any(x in tl for x in ("experiment", "bench", "dyno", "test rig")):
                        t = "Experimental"
                    elif any(x in tl for x in ("thermo", "kinetic", "mechanism")):
                        t = "Thermodynamic / Kinetic"
                    elif any(x in tl for x in ("lca", "life cycle")):
                        t = "LCA"
                    elif any(x in tl for x in ("tea", "techno-economic", "economic")):
                        t = "TEA / Economic"
                    elif any(x in tl for x in ("optimis", "optim", "genetic", "heuristic")):
                        t = "Optimisation"
                    type_c[t] += 1
    return chart_top_terms(type_c, "Computational / analytical model types (from papers)", n=15,
                           color=PALETTE[3])


def chart_ml_by_fuel_type(llm_recs: list[dict]) -> str:
    """Horizontal stacked bar: for each matched fuel term, ML vs non-ML paper count."""
    fuel_ml:  Counter = Counter()
    fuel_tot: Counter = Counter()
    for r in llm_recs:
        ml = _uses_ml(r)
        for fuel in _json_col(r, "matched_fuel_terms"):
            fuel_tot[fuel] += 1
            if ml:
                fuel_ml[fuel] += 1
    top_fuels = [f for f, _ in fuel_tot.most_common(15)]
    if not top_fuels:
        return ""
    ml_vals  = [fuel_ml.get(f, 0)              for f in top_fuels]
    non_vals = [fuel_tot[f] - fuel_ml.get(f, 0) for f in top_fuels]
    fig, ax = plt.subplots(figsize=(10, max(4, len(top_fuels) * 0.45)))
    ys = range(len(top_fuels))
    ax.barh(ys, non_vals, label="No ML/AI", color=PALETTE[7], height=0.65, zorder=3)
    ax.barh(ys, ml_vals, left=non_vals, label="Uses ML/AI", color=PALETTE[0], height=0.65, zorder=3)
    ax.set_yticks(ys)
    ax.set_yticklabels(top_fuels, fontsize=9)
    ax.set_xlabel("Papers")
    ax.set_title("ML/AI usage by fuel type")
    ax.legend(fontsize=9)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    # Annotate % ML
    for i, (ml, tot) in enumerate(zip(ml_vals, [m+n for m, n in zip(ml_vals, non_vals)])):
        if tot > 0:
            pct = ml / tot * 100
            ax.text(tot + 2, i, f"{pct:.0f}%", va="center", fontsize=8, color=PALETTE[0])
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_confidence_distribution(llm_recs: list[dict]) -> str:
    """Bar chart of LLM classification confidence."""
    c = Counter()
    for r in llm_recs:
        conf = (r.get("llm_confidence") or "").strip()
        if conf:
            c[conf] += 1
    if not c:
        return ""
    order = ["High", "Medium", "Low"]
    labels = [k for k in order if k in c] + [k for k in c if k not in order]
    values = [c[k] for k in labels]
    colors = [PALETTE[1], PALETTE[2], PALETTE[3]] + PALETTE[4:]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.bar(labels, values, color=colors[:len(labels)], zorder=3)
    ax.set_ylabel("Papers"); ax.set_title("LLM classification confidence")
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    for i, v in enumerate(values):
        ax.text(i, v + max(values)*0.02, str(v), ha="center", fontsize=10, fontweight="bold")
    return _fig_to_base64(fig)


# ═══════════════════════════════════════════════════════════════════════════
# HTML helpers
# ═══════════════════════════════════════════════════════════════════════════
_CSS = """
:root {
  --text: #1F2937; --muted: #6B7280; --bg: #FFFFFF;
  --accent: #2563EB; --border: #E5E7EB; --surface: #F9FAFB;
  --green: #059669; --amber: #D97706;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--text); background: var(--bg); line-height: 1.7;
  max-width: 1020px; margin: 0 auto; padding: 40px 24px 80px;
}
h1 { font-size: 28px; margin-bottom: 8px; }
h2 {
  font-size: 20px; margin: 52px 0 12px; padding-bottom: 8px;
  border-bottom: 2px solid var(--border);
}
h3 { font-size: 16px; margin: 24px 0 8px; color: var(--muted); }
p, li { font-size: 15px; margin-bottom: 12px; }
.subtitle { color: var(--muted); font-size: 15px; margin-bottom: 32px; }
.kpi-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 16px; margin: 24px 0 32px;
}
.kpi {
  background: var(--surface); border-radius: 10px; padding: 20px;
  border: 1px solid var(--border);
}
.kpi .label  { font-size: 12px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.5px; margin-bottom: 4px; }
.kpi .value  { font-size: 26px; font-weight: 700; color: var(--accent); }
.kpi .detail { font-size: 12px; color: var(--muted); margin-top: 2px; }
.kpi.green .value  { color: var(--green); }
.kpi.amber .value  { color: var(--amber); }
.chart-container { margin: 20px 0 36px; text-align: center; }
.chart-container img { border-radius: 8px; border: 1px solid var(--border); }
table {
  width: 100%; border-collapse: collapse; margin: 16px 0 24px; font-size: 14px;
}
th { text-align: left; padding: 10px 12px; background: var(--surface);
  border-bottom: 2px solid var(--border); font-weight: 600; }
td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
tr:hover td { background: var(--surface); }
.note {
  background: var(--surface); border-left: 3px solid var(--accent);
  padding: 12px 16px; margin: 16px 0; border-radius: 0 6px 6px 0; font-size: 14px;
}
.note.green { border-left-color: var(--green); }
.note.amber { border-left-color: var(--amber); }
.insight {
  background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 8px;
  padding: 16px 20px; margin: 20px 0; font-size: 14px;
}
.insight strong { color: var(--accent); }
.footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border);
  font-size: 13px; color: var(--muted); }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; }
@media (max-width: 700px) {
  .two-col, .three-col { grid-template-columns: 1fr; }
}
details.outliers { margin: 12px 0 24px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
details.outliers summary {
  padding: 10px 16px; background: var(--surface); cursor: pointer;
  font-size: 14px; font-weight: 600; color: var(--amber); list-style: none;
}
details.outliers summary::before { content: "▶ "; font-size: 11px; }
details.outliers[open] summary::before { content: "▼ "; }
details.outliers table { margin: 0; font-size: 13px; }
details.outliers td, details.outliers th { padding: 6px 12px; }
details.outliers .fwci-val { font-weight: 700; color: var(--amber); }
.subgroup-section { margin-top: 28px; }
"""


def _kpi(label: str, value: str, detail: str = "", cls: str = "") -> str:
    det = f'<div class="detail">{detail}</div>' if detail else ""
    return f'<div class="kpi {cls}"><div class="label">{label}</div><div class="value">{value}</div>{det}</div>'


def _chart(b64: str, alt: str = "") -> str:
    if not b64:
        return '<p style="color:var(--muted);font-style:italic;">Insufficient data.</p>'
    return f'<div class="chart-container">{_img_tag(b64, alt)}</div>'


def _table(headers: list[str], rows: list[list],
           align: list[str] | None = None) -> str:
    if not rows:
        return ""
    if align is None:
        align = ["left"] * len(headers)
    html = "<table><thead><tr>"
    for h, a in zip(headers, align):
        html += f'<th style="text-align:{a}">{h}</th>'
    html += "</tr></thead><tbody>"
    for row in rows:
        html += "<tr>"
        for cell, a in zip(row, align):
            html += f'<td style="text-align:{a}">{cell}</td>'
        html += "</tr>"
    html += "</tbody></table>"
    return html


def _fwci_outlier_html(outlier_recs: list[dict]) -> str:
    """Collapsible <details> block listing papers with FWCI above the IQR fence."""
    if not outlier_recs:
        return ""
    rows_html = ""
    for r in sorted(outlier_recs, key=lambda x: -(x.get("fwci") or 0)):
        fwci_val = r.get("fwci") or 0
        title    = (r.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        doi      = (r.get("doi") or "").strip()
        year     = r.get("publication_year") or "—"
        link     = (
            f'<a href="{doi}" target="_blank" rel="noopener" '
            f'style="color:var(--accent);text-decoration:none;">{title}</a>'
            if doi else title
        )
        rows_html += (
            f"<tr><td>{link}</td>"
            f'<td class="fwci-val">{fwci_val:.2f}</td>'
            f"<td>{year}</td></tr>"
        )
    return f"""
<details class="outliers">
  <summary>&#9888; {len(outlier_recs)} high-impact outlier paper{'s' if len(outlier_recs) != 1 else ''} (FWCI above IQR fence)</summary>
  <table>
    <thead><tr><th>Title</th><th>FWCI</th><th>Year</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</details>
"""


def _subgroup_inst_html(charts: list[tuple[str, str]]) -> str:
    """Render per-subgroup institution charts as stacked sections."""
    if not charts:
        return ""
    parts = []
    for sg_name, b64 in charts:
        parts.append(
            f'<div class="subgroup-section">'
            f'<h3>{sg_name}</h3>'
            f'{_chart(b64, f"Top institutions — {sg_name}")}'
            f"</div>"
        )
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Main report builder
# ═══════════════════════════════════════════════════════════════════════════
def generate_report(db_path: Path, state_path: Path | None,
                    output_path: Path,
                    keyword_config: dict | None = None,
                    top_n: int = 20,
                    include_group1: list | None = None,
                    include_group2: list | None = None) -> None:
    # Resolve group labels from keyword config (fall back to defaults)
    if keyword_config:
        grps = keyword_config.get("groups", [])
        group1_label = grps[0]["name"] if len(grps) > 0 else keyword_config.get("group1_label", "Fuel terms")
        group2_label = grps[1]["name"] if len(grps) > 1 else keyword_config.get("group2_label", "Impact terms")
    else:
        group1_label = "Fuel terms"
        group2_label = "Impact terms"

    print(f"Loading data from {db_path} ...")
    records = load_data(db_path)
    n = len(records)
    if n == 0:
        print("No records. Nothing to report.")
        return

    # ── LLM-processed subset ──────────────────────────────────────────────
    llm_recs = [r for r in records if r.get("llm_is_hydrocarbon") is not None]
    ml_recs  = [r for r in llm_recs if _uses_ml(r)]
    n_llm    = len(llm_recs)
    n_ml     = len(ml_recs)
    n_hydro  = sum(1 for r in llm_recs if r.get("llm_is_hydrocarbon") == 1)
    has_llm  = n_llm > 0

    state = load_pipeline_state(state_path) if state_path else None

    print(f"  Total: {n:,}  |  LLM-processed: {n_llm:,}  |  Uses ML/AI: {n_ml:,}")
    print("Generating charts ...")

    # ── Standard corpus stats ─────────────────────────────────────────────
    yr_min, yr_max  = _year_range(records)
    fuel_counts     = _count_json_col(records, "matched_fuel_terms")
    impact_counts   = _count_json_col(records, "matched_impact_terms")
    country_counts  = _count_json_col(records, "authors_countries")
    inst_counts     = _count_json_col(records, "authors_institutions")
    journal_counts  = Counter(r.get("primary_location_source_display_name")
                              for r in records if r.get("primary_location_source_display_name"))
    fwci_vals       = [r["fwci"] for r in records if r.get("fwci") is not None]
    fwci_median     = sorted(fwci_vals)[len(fwci_vals)//2] if fwci_vals else None
    fwci_mean       = sum(fwci_vals)/len(fwci_vals) if fwci_vals else None

    # Apply per-graph term filters (subset the counters if include lists provided)
    if include_group1:
        inc1_set = {t.lower() for t in include_group1}
        fuel_counts = Counter({t: c for t, c in fuel_counts.items() if t.lower() in inc1_set})
    if include_group2:
        inc2_set = {t.lower() for t in include_group2}
        impact_counts = Counter({t: c for t, c in impact_counts.items() if t.lower() in inc2_set})

    # Subgroup definitions from keyword config
    fuel_subgroups   = _get_subgroups(keyword_config, 0)
    impact_subgroups = _get_subgroups(keyword_config, 1)

    c_years    = chart_publications_by_year(records)
    c_fuel     = chart_top_terms(fuel_counts, f"Top {group1_label}", top_n, PALETTE[0])
    c_impact   = chart_top_terms(impact_counts, f"Top {group2_label}", top_n, PALETTE[3])
    c_heatmap  = chart_cooccurrence_heatmap(records, group1_label=group1_label, group2_label=group2_label)
    c_countries= chart_countries(records)
    c_journals = chart_journals(records)
    c_oa       = chart_oa_status(records)
    c_types    = chart_work_types(records)
    c_fwci, fwci_outliers = chart_fwci(records)
    c_inst     = chart_institutions(records)
    c_inst_by_subgroup = (
        chart_institutions_per_subgroup(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else []
    )
    c_trends   = chart_yearly_fuel_trends(records, group1_label=group1_label)

    # ── LLM / ML charts ───────────────────────────────────────────────────
    c_scope        = chart_fuel_classification(llm_recs) if has_llm else ""
    c_ml_adopt     = chart_ml_adoption_rate(llm_recs)    if has_llm else ""
    c_ml_time      = chart_ml_adoption_over_time(llm_recs) if has_llm else ""
    c_paradigm     = chart_ml_paradigm(llm_recs)         if has_llm else ""
    c_method       = chart_ml_method(llm_recs)           if has_llm else ""
    c_scope_x_ml   = chart_scope_x_ml(llm_recs)          if has_llm else ""
    c_model_types  = chart_model_types(llm_recs)          if has_llm else ""
    c_ml_fuel      = chart_ml_by_fuel_type(llm_recs)     if has_llm else ""
    c_confidence   = chart_confidence_distribution(llm_recs) if has_llm else ""

    print("Building HTML ...")

    # ── KPIs ──────────────────────────────────────────────────────────────
    top5_fuel   = ", ".join(t for t, _ in fuel_counts.most_common(5))
    top5_impact = ", ".join(t for t, _ in impact_counts.most_common(5))
    # Update KPI labels to use custom group names
    top3_geo    = ", ".join(_country_display(c) for c, _ in country_counts.most_common(3))
    yr_span     = yr_max - yr_min + 1 if yr_max > yr_min else 1

    kpis = (
        _kpi("Total papers", f"{n:,}", f"{yr_min}–{yr_max}")
      + _kpi(group1_label, str(len(fuel_counts)))
      + _kpi(group2_label, str(len(impact_counts)))
      + _kpi("Countries", str(len(country_counts)))
      + _kpi("Journals", str(len(journal_counts)))
      + (_kpi("Median FWCI", f"{fwci_median:.2f}", f"mean {fwci_mean:.2f}")
         if fwci_median else "")
    )

    llm_kpis = ""
    if has_llm:
        ml_pct  = n_ml  / n_llm * 100
        hyd_pct = n_hydro / n_llm * 100

        # Dominant scope
        scope_c   = _count_json_col(llm_recs, "llm_fuel_classification")
        SHORT_SCOPE = {
            "Engineering Design Optimization – Emissions Reduction":     "Design Opt.–Emissions",
            "Engineering Design Optimization – Performance Enhancement":  "Design Opt.–Performance",
            "Fuel Property Prediction from molecular composition derived data": "Fuel Prop. Prediction",
            "Life Cycle Assessment (LCA) – Environmental Impact Analysis": "LCA",
            "Techno-Economic Assessment (TEA) – Economic Feasibility Analysis": "TEA",
            "Discovery of Novel Molecules for Green Energy Applications":  "Novel Molecules",
            "fuel production from sustainable feedstocks":                "Sustainable Production",
            "Fuel Blend and Additive Research":                           "Blend & Additives",
        }
        top_scope = SHORT_SCOPE.get(*scope_c.most_common(1)[0]) if scope_c else "N/A"

        # Top paradigm (excl. None)
        par_c = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_paradigm"):
                if v and v.lower() != "none":
                    par_c[v] += 1
        top_par = par_c.most_common(1)[0][0] if par_c else "N/A"

        # Avg tokens
        avg_in  = (sum(r.get("llm_tokens_input", 0)  or 0 for r in llm_recs) / n_llm)
        avg_out = (sum(r.get("llm_tokens_output", 0) or 0 for r in llm_recs) / n_llm)

        llm_kpis = (
            _kpi("LLM-classified", f"{n_llm:,}", f"{n_llm/n*100:.1f}% of corpus")
          + _kpi("Uses ML/AI", f"{n_ml:,}", f"{ml_pct:.1f}% of classified", "green")
          + _kpi("Hydrocarbon focus", f"{n_hydro:,}", f"{hyd_pct:.1f}%")
          + _kpi("Dominant scope", top_scope)
          + _kpi("Top ML paradigm", top_par, "", "green")
          + _kpi("Avg tokens / paper", f"{avg_in:.0f} in", f"{avg_out:.0f} out")
        )

    # ── Funnel section ─────────────────────────────────────────────────────
    funnel_html = ""
    if state and state.get("total_records_seen"):
        seen = state["total_records_seen"]
        ins  = state["total_records_inserted"]
        hit  = ins / seen * 100 if seen else 0
        n_done = len(state.get("processed_files", []))
        n_files = len(state.get("all_files", []))
        funnel_html = f"""
<h2>1. Filtering funnel</h2>
<p>Pipeline scanned <strong>{n_done:,}</strong> of {n_files:,} partition files
({seen:,} records total), retaining {ins:,} ({hit:.4f}% hit rate).</p>
<div class="kpi-grid">
    {_kpi("Records scanned", f"{seen:,}")}
    {_kpi("Passed filter", f"{ins:,}", f"{hit:.4f}%")}
    {_kpi("Files processed", f"{n_done:,}", f"of {n_files:,}")}
</div>
<div class="note">Inclusion criteria: DOI present · open access · English ·
abstract present · ≥ 1 fuel term AND ≥ 1 impact term in title/abstract.</div>
"""

    s = 2 if funnel_html else 1   # section counter offset

    # ── LLM section ────────────────────────────────────────────────────────
    llm_section = ""
    if has_llm:
        ml_pct = n_ml / n_llm * 100

        # Insight bullet construction
        scope_c_named = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_fuel_classification"):
                if v and v.lower() not in ("none", "other"):
                    scope_c_named[SHORT_SCOPE.get(v, v)] += 1
        top3_scopes = ", ".join(s for s, _ in scope_c_named.most_common(3))

        par_c2 = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_paradigm"):
                if v and v.lower() != "none":
                    par_c2[v] += 1
        top_par2 = par_c2.most_common(1)[0][0] if par_c2 else "Supervised Learning"

        method_c = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_method"):
                if v and v.lower() not in ("none", "other"):
                    method_c[v] += 1
        SHORT_M = {
            "Classical Machine Learning (SVM, Random Forest, Gradient Boosting)": "Classical ML",
            "Deep Learning (Neural Networks)": "Deep Learning",
            "Evolutionary / Heuristic Methods (Genetic Algorithms)": "Evolutionary methods",
        }
        top_method = SHORT_M.get(method_c.most_common(1)[0][0], method_c.most_common(1)[0][0]) \
                     if method_c else "Classical ML"

        llm_section = f"""
<h2>{s+2}. AI / ML in sustainable-fuels research</h2>
<p>
  {n_llm:,} papers in this corpus have been classified by the LLM batch pipeline.
  The analysis below answers <em>how</em> and <em>where</em> machine learning
  is applied in the field — covering research scope, learning paradigms,
  model classes, and growth trends.
</p>

<div class="kpi-grid">{llm_kpis}</div>

<div class="insight">
  <strong>Key findings:</strong>
  <ul style="margin-top:8px;">
    <li><strong>{ml_pct:.0f}%</strong> of classified papers employ at least one ML/AI method,
        indicating the field is in a rapid AI-adoption phase.</li>
    <li>The dominant research scope is <strong>{top3_scopes}</strong>.</li>
    <li><strong>{top_par2}</strong> is the most common ML paradigm, with
        <strong>{top_method}</strong> the most frequent model class.</li>
    <li>Papers span CFD simulations, experimental studies, and data-driven
        property prediction — often combining multiple modelling approaches.</li>
  </ul>
</div>

<h3>Research scope distribution</h3>
<p>
  Each paper may fall into one or more research categories. Engineering design
  optimisation and fuel property prediction are the most active areas where
  ML/AI techniques are being applied.
</p>
{_chart(c_scope, "Research scope")}

<h3>ML/AI adoption rate</h3>
<p>
  What fraction of sustainable-fuels papers use any ML/AI method?
  The pie shows the overall split; the time-series tracks how this share
  has changed year-on-year.
</p>
<div class="two-col">
  <div>{_chart(c_ml_adopt, "ML adoption")}</div>
  <div>{_chart(c_confidence, "Confidence")}</div>
</div>
{_chart(c_ml_time, "ML adoption over time")}

<h3>Learning paradigm and model class</h3>
<p>
  Among papers that <em>do</em> use ML/AI, the breakdown below shows
  which paradigms and model families are most prevalent.
  Supervised learning dominates — driven by property-prediction and
  regression tasks — while deep learning is gaining ground for
  combustion-image and time-series applications.
</p>
<div class="two-col">
  <div>{_chart(c_paradigm, "ML paradigms")}</div>
  <div>{_chart(c_method, "ML methods")}</div>
</div>

<h3>Research scope × ML paradigm heatmap</h3>
<p>
  Which research areas pair with which ML approaches?
  Bright cells indicate strong usage of a paradigm within a scope.
</p>
{_chart(c_scope_x_ml, "Scope x ML heatmap")}

<h3>ML/AI adoption by fuel type</h3>
<p>
  For the most common fuel keywords, the bar below shows how many
  papers do or do not employ ML/AI.  The percentage label is the
  ML/AI adoption rate for that fuel type.
</p>
{_chart(c_ml_fuel, "ML by fuel type")}

<h3>Computational and analytical model inventory</h3>
<p>
  The LLM was instructed to extract every named model used in each paper
  (ML, CFD, experimental, thermodynamic, etc.).  The chart aggregates
  model <em>types</em> across the classified corpus.
</p>
{_chart(c_model_types, "Model types")}
"""

    # ── Tables ─────────────────────────────────────────────────────────────
    top_fuel_table = _table(
        ["Rank", f"{group1_label} term", "Papers", "% corpus"],
        [[str(i+1), t, f"{c:,}", f"{c/n*100:.1f}%"]
         for i, (t, c) in enumerate(fuel_counts.most_common(15))],
        ["center", "left", "right", "right"],
    )
    top_impact_table = _table(
        ["Rank", f"{group2_label} term", "Papers", "% corpus"],
        [[str(i+1), t, f"{c:,}", f"{c/n*100:.1f}%"]
         for i, (t, c) in enumerate(impact_counts.most_common(15))],
        ["center", "left", "right", "right"],
    )
    top_journals_table = _table(
        ["Rank", "Journal / source", "Papers", "% corpus"],
        [[str(i+1), j, f"{cnt:,}", f"{cnt/n*100:.1f}%"]
         for i, (j, cnt) in enumerate(journal_counts.most_common(10))],
        ["center", "left", "right", "right"],
    )
    top_countries_table = _table(
        ["Rank", "Country", "Papers", "% corpus"],
        [[str(i+1),
          f'<a href="https://en.wikipedia.org/wiki/{_country_display(c).replace(" ", "_")}" '
          f'target="_blank" style="color:var(--accent);text-decoration:none;">'
          f'{_country_display(c)}</a>',
          f"{cnt:,}", f"{cnt/n*100:.1f}%"]
         for i, (c, cnt) in enumerate(country_counts.most_common(15))],
        ["center", "left", "right", "right"],
    )

    # ── FWCI note ─────────────────────────────────────────────────────────
    fwci_note = (
        f'Median FWCI <strong>{fwci_median:.2f}</strong> — the typical paper '
        f'{"outperforms" if fwci_median > 1 else "is near"} the world average.'
        if fwci_median else "FWCI data unavailable for most records."
    )
    fwci_li = (
        f"<li><strong>Citation quality:</strong> median FWCI {fwci_median:.2f} "
        f'— field {"punches above" if fwci_median > 1 else "at"} world average.</li>'
        if fwci_median else ""
    )

    ml_summary_li = ""
    if has_llm:
        ml_pct = n_ml / n_llm * 100
        ml_summary_li = (
            f"<li><strong>AI/ML adoption:</strong> {ml_pct:.0f}% of classified papers "
            f"employ ML/AI — predominantly supervised learning for property prediction "
            f"and emissions modelling.</li>"
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ══════════════════════════════════════════════════════════════════════
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenAlex Sustainable Fuels — Research Landscape Report</title>
<style>{_CSS}</style>
</head>
<body>

<h1>Sustainable Hydrocarbon Fuels Research — Landscape Report</h1>
<p class="subtitle">
  {n:,} papers &middot; {yr_min}–{yr_max} &middot; generated {now}
</p>

<p>
  Corpus of <strong>{n:,}</strong> open-access, English-language papers from
  <a href="https://openalex.org/">OpenAlex</a> covering sustainable fuels
  (biofuels, SAF, HVO, FT fuels, conventional hydrocarbon fuels and related
  feedstocks) intersected with environmental or performance impact topics
  (emissions, efficiency, combustion properties, lifecycle analysis).
  {f"A sub-sample of <strong>{n_llm:,}</strong> papers has been further classified by a large language model (LLM) to characterise research scope and ML/AI usage." if has_llm else ""}
</p>

<div class="kpi-grid">{kpis}</div>

{funnel_html}

<h2>{s}. Publication trends</h2>
<p>Annual volume of matching publications across the {yr_span}-year span.</p>
{_chart(c_years, "Publications by year")}

<h3>{group1_label} trends over time</h3>
<p>Stacked view showing the shifting research focus over time across {group1_label.lower()}.</p>
{_chart(c_trends, f"{group1_label} trends")}

<h2>{s+1}. Keyword analysis</h2>

<h3>{group1_label}</h3>
<p>{len(fuel_counts)} distinct {group1_label.lower()}.  A paper may match multiple terms.</p>
<div class="two-col">
  <div>{_chart(c_fuel, f"Top {group1_label}")}</div>
  <div>{top_fuel_table}</div>
</div>

<h3>{group2_label}</h3>
<p>{len(impact_counts)} distinct {group2_label.lower()}.  A paper may match multiple terms.</p>
<div class="two-col">
  <div>{_chart(c_impact, f"Top {group2_label}")}</div>
  <div>{top_impact_table}</div>
</div>

<h3>{group1_label} &times; {group2_label} co-occurrence heatmap</h3>
<p>How often each {group1_label.lower()} term appears together with each {group2_label.lower()} term in the same paper.</p>
{_chart(c_heatmap, "Co-occurrence")}

{llm_section}

<h2>{s + (3 if has_llm else 2)}. Geographic distribution</h2>
<p>Author country affiliations (one paper may count across multiple countries).</p>
<div class="two-col">
  <div>{_chart(c_countries, "Top countries")}</div>
  <div>{top_countries_table}</div>
</div>

<h2>{s + (4 if has_llm else 3)}. Journals and sources</h2>
<div class="two-col">
  <div>{_chart(c_journals, "Top journals")}</div>
  <div>{top_journals_table}</div>
</div>

<h2>{s + (5 if has_llm else 4)}. Open access and work types</h2>
<div class="two-col">
  <div>
    <h3>OA pathway</h3>
    {_chart(c_oa, "OA status")}
  </div>
  <div>
    <h3>Document types</h3>
    {_chart(c_types, "Work types")}
  </div>
</div>

<h2>{s + (6 if has_llm else 5)}. Citation impact</h2>
<p>{fwci_note}</p>
{_chart(c_fwci, "FWCI distribution")}
{_fwci_outlier_html(fwci_outliers)}

<h2>{s + (7 if has_llm else 6)}. Top institutions</h2>
{_chart(c_inst, "Top institutions")}
{_subgroup_inst_html(c_inst_by_subgroup)}

<h2>{s + (8 if has_llm else 7)}. Summary</h2>
<p><strong>{n:,}</strong> open-access English papers, {yr_span} years
({yr_min}–{yr_max}), {len(journal_counts)} journals.</p>
<ul>
  <li><strong>Top {group1_label.lower()}:</strong> {top5_fuel}</li>
  <li><strong>Top {group2_label.lower()}:</strong> {top5_impact}</li>
  <li><strong>Geography:</strong> led by {top3_geo}</li>
  {fwci_li}
  {ml_summary_li}
</ul>

<div class="note">
  <strong>Keyword provenance:</strong> every record stores
  <code>matched_fuel_terms</code>, <code>matched_impact_terms</code> (and their
  stemmed variants) documenting which keywords triggered inclusion.
  LLM-classified records additionally carry <code>llm_fuel_classification</code>,
  <code>llm_ml_paradigm</code>, <code>llm_ml_method</code>, and
  <code>llm_models</code> columns for programmatic querying.
</div>

<div class="footer">
  Generated by <code>generate_report.py</code> · {now} ·
  DB: <code>{db_path.name}</code> ({n:,} records
  {f", {n_llm:,} LLM-classified" if has_llm else ""}).
</div>

</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"Report saved -> {output_path}  ({len(html):,} bytes)")


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate an HTML research-landscape report from the filtered OpenAlex DB."
    )
    p.add_argument("--db", type=Path,
                   default=Path("openalex_works_filtered.sqlite"))
    p.add_argument("--state", type=Path, default=Path("pipeline_state.json"))
    p.add_argument("--output", type=Path, default=Path("report.html"))
    p.add_argument("--keyword-config", type=Path, default=None,
                   help="keywords.json with group labels and term lists")
    p.add_argument("--top-n", type=int, default=20,
                   help="Number of top terms to show in keyword charts (default: 20)")
    p.add_argument("--include-group1", type=str, default=None,
                   help="Comma-separated list of group-1 terms to include in charts (default: all)")
    p.add_argument("--include-group2", type=str, default=None,
                   help="Comma-separated list of group-2 terms to include in charts (default: all)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    kw_cfg = None
    if args.keyword_config and args.keyword_config.exists():
        import json as _json_main
        kw_cfg = _json_main.loads(args.keyword_config.read_text(encoding="utf-8"))
    include1 = [t.strip() for t in args.include_group1.split(",")] if args.include_group1 else None
    include2 = [t.strip() for t in args.include_group2.split(",")] if args.include_group2 else None
    generate_report(
        db_path=args.db,
        state_path=args.state if args.state.exists() else None,
        output_path=args.output,
        keyword_config=kw_cfg,
        top_n=args.top_n,
        include_group1=include1,
        include_group2=include2,
    )
