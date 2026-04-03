"""
#!/usr/bin/env python3
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

# Shortened label maps shared across all chart functions
_SHORT_SCOPE: dict[str, str] = {
    "Engineering Design Optimization – Emissions Reduction":      "Design Opt.–Emissions",
    "Engineering Design Optimization – Performance Enhancement":  "Design Opt.–Performance",
    "Fuel Property Prediction from molecular composition derived data": "Fuel Prop. Prediction",
    "Life Cycle Assessment (LCA) – Environmental Impact Analysis": "LCA",
    "Techno-Economic Assessment (TEA) – Economic Feasibility Analysis": "TEA",
    "Discovery of Novel Molecules for Green Energy Applications":  "Novel Molecules",
    "fuel production from sustainable feedstocks":                "Sustainable Production",
    "Fuel Blend and Additive Research":                           "Blend & Additives",
    "Regulatory Research and Analysis":                           "Regulatory",
}
_SHORT_ML: dict[str, str] = {
    # v3 ml_role codes → short display labels
    "property_prediction":  "Property Pred.",
    "process_optimization": "Process Opt.",
    "surrogate_modeling":   "Surrogate Model",
    "soft_sensor":          "Soft Sensor",
    "pattern_discovery":    "Pattern Discovery",
    "image_analysis":       "Image Analysis",
    "signal_spectral":      "Spectral/Signal",
    "molecular_design":     "Molecular Design",
    "control_automation":   "Control/Automation",
    "text_mining":          "Text Mining",
    "emission_modeling":    "Emission Modeling",
    "lca_tea_integration":  "LCA/TEA ML",
    "anomaly_detection":    "Anomaly Detection",
    # v3 ml_algorithm codes → short display labels
    "ann_mlp":              "ANN/MLP",
    "cnn":                  "CNN",
    "rnn_lstm":             "RNN/LSTM",
    "transformer":          "Transformer",
    "gan_vae":              "GAN/VAE",
    "graph_nn":             "Graph NN",
    "physics_informed_nn":  "PINN",
    "random_forest":        "Random Forest",
    "gradient_boosting":    "Grad. Boosting",
    "svm":                  "SVM",
    "decision_tree":        "Decision Tree",
    "knn":                  "kNN",
    "gaussian_process":     "GP/Kriging",
    "bayesian_network":     "Bayesian Net",
    "pls_pcr":              "PLS/PCR",
    "linear_regularized":   "LASSO/Ridge",
    "clustering":           "Clustering",
    "dim_reduction":        "Dim. Reduction",
    "evolutionary":         "Evolutionary",
    "reinforcement":        "Reinforcement",
    "naive_bayes":          "Naive Bayes",
    "other_ml":             "Other ML",
    # v2 legacy labels (kept for backward compat)
    "Classical Machine Learning (SVM, Random Forest, Gradient Boosting)": "Classical ML",
    "Deep Learning (Neural Networks)":                                     "Deep Learning",
    "Probabilistic Models (Bayesian Networks, HMM)":                      "Probabilistic",
    "Evolutionary / Heuristic Methods (Genetic Algorithms)":              "Evolutionary",
}


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
    # v3: use ml_role; fall back to v2 ml_paradigm for older records
    role = _json_col(r, "llm_ml_role")
    if role:
        return not all(v.strip().lower() in ("none", "") for v in role)
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
        colors=PALETTE[:len(labels)], startangle=90, pctdistance=0.78,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white", "width": 0.5},
    )
    for t in autotexts:
        t.set_fontsize(9)
    ax.text(0, 0, f"{sum(values):,}\npapers", ha="center", va="center",
            fontsize=11, fontweight="bold", color=TEXT_COLOR)
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


def _charts_per_subgroup(
    records: list[dict],
    subgroups: list[dict],
    matched_col: str,
    chart_fn,        # callable(sg_name: str, sg_recs: list[dict]) -> str
    min_n: int = 3,
) -> list[tuple[str, str]]:
    """Iterate subgroups, call chart_fn for each with enough records, collect (name, b64) pairs."""
    grouped = _group_by_subgroup(records, subgroups, matched_col)
    out = []
    for sg_name, sg_recs in grouped.items():
        if len(sg_recs) < min_n:
            continue
        img = chart_fn(sg_name, sg_recs)
        if img:
            out.append((sg_name, img))
    return out


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
# Chart generators — per-subgroup breakdowns
# ═══════════════════════════════════════════════════════════════════════════

def chart_publications_per_subgroup(
    records: list[dict],
    subgroups: list[dict],
    matched_col: str,
) -> list[tuple[str, str]]:
    """Publication-by-year bar chart for each fuel sub-group (small multiples)."""
    return _charts_per_subgroup(
        records, subgroups, matched_col,
        lambda sg_name, sg_recs: chart_publications_by_year(sg_recs),
    )


def chart_yearly_trends_per_subgroup(
    records: list[dict],
    subgroups: list[dict],
    matched_col: str,
    group1_label: str = "Fuel terms",
) -> list[tuple[str, str]]:
    """Stacked fuel-term trends for each sub-group (small multiples)."""
    return _charts_per_subgroup(
        records, subgroups, matched_col,
        lambda sg_name, sg_recs: chart_yearly_fuel_trends(
            sg_recs, n=5, group1_label=group1_label
        ),
    )


def chart_impact_profile_per_subgroup(
    records: list[dict],
    fuel_subgroups: list[dict],
    impact_subgroups: list[dict],
    fuel_col: str = "matched_fuel_terms",
    impact_col: str = "matched_impact_terms",
) -> str:
    """100%-stacked horizontal bar: which impact sub-groups dominate each fuel sub-group."""
    if not fuel_subgroups or not impact_subgroups:
        return ""
    impact_t2sg = _build_term_to_subgroup(impact_subgroups)
    impact_sg_names = [sg["name"] for sg in impact_subgroups]
    fuel_grouped = _group_by_subgroup(records, fuel_subgroups, fuel_col)
    if not fuel_grouped:
        return ""

    fuel_sg_names = list(fuel_grouped.keys())
    matrix = np.zeros((len(fuel_sg_names), len(impact_sg_names)), dtype=float)
    for fi, fsn in enumerate(fuel_sg_names):
        for rec in fuel_grouped[fsn]:
            for term in _json_col(rec, impact_col):
                isgs = impact_t2sg.get(term.lower())
                if isgs and isgs in impact_sg_names:
                    matrix[fi, impact_sg_names.index(isgs)] += 1
        total = matrix[fi].sum()
        if total > 0:
            matrix[fi] /= total

    fig, ax = plt.subplots(figsize=(10, max(3.5, len(fuel_sg_names) * 0.55)))
    colors = PALETTE[:len(impact_sg_names)]
    lefts = np.zeros(len(fuel_sg_names))
    ys = range(len(fuel_sg_names))
    for ii, isgn in enumerate(impact_sg_names):
        vals = matrix[:, ii]
        ax.barh(ys, vals * 100, left=lefts * 100, color=colors[ii % len(colors)],
                height=0.65, label=isgn, zorder=3)
        lefts += vals
    ax.set_yticks(ys)
    ax.set_yticklabels(fuel_sg_names, fontsize=9)
    ax.set_xlabel("Share of matched impact terms (%)")
    ax.set_title("Impact term profile per fuel sub-group")
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.legend(fontsize=8, loc="lower right", ncol=2)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_impact_terms_per_subgroup(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
    n: int = 10,
) -> list[tuple[str, str]]:
    """Top impact terms for each fuel sub-group (small multiples)."""
    return _charts_per_subgroup(
        records, fuel_subgroups, matched_col,
        lambda sg_name, sg_recs: chart_top_terms(
            _count_json_col(sg_recs, "matched_impact_terms"),
            f"Top impact terms — {sg_name}", n=n, color=PALETTE[3],
        ),
    )


def chart_countries_per_subgroup(
    records: list[dict],
    subgroups: list[dict],
    matched_col: str,
    n: int = 15,
) -> list[tuple[str, str]]:
    """Top countries for each fuel sub-group (small multiples)."""
    return _charts_per_subgroup(
        records, subgroups, matched_col,
        lambda sg_name, sg_recs: chart_countries(sg_recs, n=n),
    )


def chart_journals_comparison(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
    top_j: int = 5,
) -> str:
    """Grouped horizontal bar: top journals for each fuel sub-group side-by-side."""
    grouped = _group_by_subgroup(records, fuel_subgroups, matched_col)
    if not grouped:
        return ""

    sg_names = list(grouped.keys())
    sg_top: dict[str, list[str]] = {}
    for sgn, recs in grouped.items():
        jc = Counter(r.get("primary_location_source_display_name")
                     for r in recs if r.get("primary_location_source_display_name"))
        sg_top[sgn] = [j for j, _ in jc.most_common(top_j)]

    # Collect unique journals (respect top_j limit; fall back to 3 if too many)
    seen: list[str] = []
    for jlist in sg_top.values():
        for j in jlist:
            if j not in seen:
                seen.append(j)
    if len(seen) > 25:
        top_j = 3
        sg_top = {}
        for sgn, recs in grouped.items():
            jc = Counter(r.get("primary_location_source_display_name")
                         for r in recs if r.get("primary_location_source_display_name"))
            sg_top[sgn] = [j for j, _ in jc.most_common(top_j)]
        seen = []
        for jlist in sg_top.values():
            for j in jlist:
                if j not in seen:
                    seen.append(j)
    if not seen:
        return ""

    # Shorten journal names
    def _shorten(name: str, max_len: int = 35) -> str:
        return name if len(name) <= max_len else name[:max_len - 1] + "…"

    short_names = [_shorten(j) for j in seen]
    x = np.arange(len(seen))
    width = 0.8 / len(sg_names)
    fig, ax = plt.subplots(figsize=(max(10, len(seen) * 0.9), max(4, len(sg_names) * 0.6)))
    for i, sgn in enumerate(sg_names):
        jc = Counter(r.get("primary_location_source_display_name")
                     for r in grouped[sgn] if r.get("primary_location_source_display_name"))
        vals = [jc.get(j, 0) for j in seen]
        ax.bar(x + i * width, vals, width=width * 0.9, label=sgn,
               color=PALETTE[i % len(PALETTE)], zorder=3)
    ax.set_xticks(x + width * (len(sg_names) - 1) / 2)
    ax.set_xticklabels(short_names, rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("Papers")
    ax.set_title("Top journals by fuel sub-group")
    ax.legend(fontsize=8, loc="upper right")
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_oa_per_subgroup_stacked(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """100%-stacked horizontal bar: OA status distribution per fuel sub-group."""
    grouped = _group_by_subgroup(records, fuel_subgroups, matched_col)
    if not grouped:
        return ""
    oa_color = {
        "gold": PALETTE[2], "green": PALETTE[1], "bronze": PALETTE[6],
        "closed": PALETTE[3], "hybrid": PALETTE[4], "unknown": PALETTE[7],
    }
    all_statuses: list[str] = []
    sg_names = list(grouped.keys())
    counts: dict[str, Counter] = {}
    for sgn, recs in grouped.items():
        c = Counter((r.get("open_access_oa_status") or "unknown").lower() for r in recs)
        counts[sgn] = c
        for s in c:
            if s not in all_statuses:
                all_statuses.append(s)
    ordered = [s for s in oa_color if s in all_statuses] + [
        s for s in all_statuses if s not in oa_color]

    fig, ax = plt.subplots(figsize=(9, max(3, len(sg_names) * 0.55)))
    lefts = np.zeros(len(sg_names))
    for status in ordered:
        vals = np.array([counts[sg].get(status, 0) for sg in sg_names], dtype=float)
        totals = np.array([sum(counts[sg].values()) for sg in sg_names], dtype=float)
        pcts = np.where(totals > 0, vals / totals * 100, 0)
        color = oa_color.get(status, PALETTE[8])
        ax.barh(sg_names, pcts, left=lefts, color=color, height=0.65,
                label=status.capitalize(), zorder=3)
        lefts += pcts
    ax.set_xlabel("Share (%)")
    ax.set_title("Open access status by fuel sub-group")
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.legend(fontsize=8, loc="lower right", ncol=3)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_work_types_per_subgroup_stacked(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """100%-stacked horizontal bar: work type distribution per fuel sub-group."""
    grouped = _group_by_subgroup(records, fuel_subgroups, matched_col)
    if not grouped:
        return ""
    sg_names = list(grouped.keys())
    all_types: list[str] = []
    counts: dict[str, Counter] = {}
    for sgn, recs in grouped.items():
        c = Counter((r.get("type") or "unknown") for r in recs)
        counts[sgn] = c
        for t in c:
            if t not in all_types:
                all_types.append(t)
    # Sort by total occurrence descending
    type_totals = Counter({t: sum(counts[sg].get(t, 0) for sg in sg_names) for t in all_types})
    ordered = [t for t, _ in type_totals.most_common()]

    fig, ax = plt.subplots(figsize=(9, max(3, len(sg_names) * 0.55)))
    lefts = np.zeros(len(sg_names))
    for i, wtype in enumerate(ordered):
        vals = np.array([counts[sg].get(wtype, 0) for sg in sg_names], dtype=float)
        totals = np.array([sum(counts[sg].values()) for sg in sg_names], dtype=float)
        pcts = np.where(totals > 0, vals / totals * 100, 0)
        ax.barh(sg_names, pcts, left=lefts, color=PALETTE[i % len(PALETTE)],
                height=0.65, label=wtype, zorder=3)
        lefts += pcts
    ax.set_xlabel("Share (%)")
    ax.set_title("Work type distribution by fuel sub-group")
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.legend(fontsize=8, loc="lower right", ncol=2)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_fwci_per_subgroup(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """Horizontal box-plot comparing FWCI distributions across fuel sub-groups."""
    grouped = _group_by_subgroup(records, fuel_subgroups, matched_col)
    sg_data: list[tuple[str, list[float]]] = []
    for sgn, recs in grouped.items():
        vals_raw = [r["fwci"] for r in recs if r.get("fwci") is not None]
        if len(vals_raw) < 5:
            continue
        # Clip at per-subgroup IQR fence
        arr = np.array(vals_raw)
        q25, q75 = float(np.percentile(arr, 25)), float(np.percentile(arr, 75))
        fence = q75 + 1.5 * (q75 - q25)
        clipped = [v for v in vals_raw if v <= fence]
        if clipped:
            sg_data.append((sgn, clipped))
    if not sg_data:
        return ""
    # Sort by median descending
    sg_data.sort(key=lambda x: -float(np.median(x[1])))
    sg_names = [d[0] for d in sg_data]
    values   = [d[1] for d in sg_data]

    fig, ax = plt.subplots(figsize=(9, max(3.5, len(sg_data) * 0.65)))
    bp = ax.boxplot(values, vert=False, patch_artist=True, showfliers=False,
                    medianprops={"color": TEXT_COLOR, "linewidth": 2})
    for patch, color in zip(bp["boxes"], [PALETTE[i % len(PALETTE)] for i in range(len(sg_data))]):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_yticks(range(1, len(sg_names) + 1))
    ax.set_yticklabels(sg_names, fontsize=9)
    ax.axvline(1.0, color=PALETTE[3], linestyle="--", linewidth=1.2,
               label="World avg (1.0)")
    ax.set_xlabel("FWCI  (each sub-group clipped at its IQR fence)")
    ax.set_title("Citation impact (FWCI) by fuel sub-group")
    ax.legend(fontsize=9)
    fig.tight_layout()
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
    renamed = Counter({_SHORT_SCOPE.get(k, k): v for k, v in c.items()})
    return chart_top_terms(renamed, "Research scope (LLM classification)", n=20, color=PALETTE[0])


def chart_ml_role(llm_recs: list[dict]) -> str:
    """Bar chart of ML/AI functional roles (None excluded)."""
    c = Counter()
    for r in llm_recs:
        for v in _json_col(r, "llm_ml_role"):
            if v and v.lower() != "none":
                c[_SHORT_ML.get(v, v)] += 1
    if not c:
        return ""
    return chart_top_terms(c, "ML/AI functional role (papers using ML/AI only)",
                           n=13, color=PALETTE[1])


def chart_ml_algorithm(llm_recs: list[dict]) -> str:
    """Bar chart of ML model algorithm families."""
    c = Counter()
    for r in llm_recs:
        for v in _json_col(r, "llm_ml_algorithm"):
            if v and v.lower() not in ("none", "other_ml"):
                c[_SHORT_ML.get(v, v)] += 1
    if not c:
        return ""
    return chart_top_terms(c, "ML/AI algorithm family", n=15, color=PALETTE[2])


def chart_ml_strategy(llm_recs: list[dict]) -> str:
    """Bar chart of cross-cutting ML methodological strategies."""
    c = Counter()
    for r in llm_recs:
        for v in _json_col(r, "llm_ml_strategy"):
            if v and v.lower() != "none":
                c[v] += 1
    if not c:
        return ""
    return chart_top_terms(c, "ML/AI methodological strategies", n=8, color=PALETTE[4])


def chart_ml_data_source(llm_recs: list[dict]) -> str:
    """Pie chart of ML training data sources."""
    c = Counter()
    for r in llm_recs:
        v = (r.get("llm_ml_data_source") or "").strip().lower()
        if v and v not in ("none", ""):
            c[v] += 1
    if not c:
        return ""
    labels = list(c.keys())
    values = list(c.values())
    fig, ax = plt.subplots(figsize=(7, 4.5))
    wedges, _, auto = ax.pie(
        values, labels=None, autopct="%1.1f%%",
        colors=PALETTE[:len(labels)], startangle=90, pctdistance=0.78,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white", "width": 0.5},
    )
    for t in auto:
        t.set_fontsize(9)
    ax.legend(wedges, [f"{l}  ({v:,})" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=9)
    ax.set_title("ML/AI training data source (ML-using papers)")
    fig.tight_layout()
    return _fig_to_base64(fig)


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
    """Heatmap: research scope × ML role (headline figure)."""
    ml_recs = [r for r in llm_recs if _uses_ml(r)]
    scope_c  = Counter()
    role_c   = Counter()
    pairs: Counter = Counter()
    for r in ml_recs:
        scopes = [_SHORT_SCOPE.get(v, v) for v in _json_col(r, "llm_fuel_classification")
                  if v and v.lower() not in ("none", "other")]
        roles  = [_SHORT_ML.get(v, v) for v in _json_col(r, "llm_ml_role")
                  if v and v.lower() != "none"]
        for s in scopes:
            scope_c[s] += 1
        for ro in roles:
            role_c[ro] += 1
        for s in scopes:
            for ro in roles:
                pairs[(s, ro)] += 1

    top_scopes = [s for s, _ in scope_c.most_common(10)]
    top_roles  = [r for r, _ in role_c.most_common(12)]
    if not top_scopes or not top_roles:
        return ""

    matrix = np.zeros((len(top_scopes), len(top_roles)), dtype=int)
    for (s, ro), cnt in pairs.items():
        if s in top_scopes and ro in top_roles:
            matrix[top_scopes.index(s)][top_roles.index(ro)] = cnt

    fig, ax = plt.subplots(figsize=(max(8, len(top_roles) * 1.4),
                                    max(4, len(top_scopes) * 0.6)))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(top_roles)))
    ax.set_xticklabels(top_roles, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(len(top_scopes)))
    ax.set_yticklabels(top_scopes, fontsize=9)
    ax.set_title("Research scope × ML/AI role  (papers using ML/AI only)")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = matrix.max() or 1
    for i in range(len(top_scopes)):
        for j in range(len(top_roles)):
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
# Chart generators — new (improvements batch)
# ═══════════════════════════════════════════════════════════════════════════

def chart_filtering_funnel(state: dict) -> str:
    """Horizontal waterfall showing records at each filter stage."""
    seen = state.get("total_records_seen", 0)
    ins  = state.get("total_records_inserted", 0)
    if not seen:
        return ""
    intermediates = state.get("filter_steps", {})
    if intermediates:
        steps = list(intermediates.items())
    else:
        steps = [("Records scanned", seen), ("Passed filter", ins)]
    labels = [s[0] for s in steps]
    values = [s[1] for s in steps]
    fig, ax = plt.subplots(figsize=(9, max(3, len(steps) * 0.65)))
    colors = [PALETTE[1] if i == len(steps) - 1 else PALETTE[0]
              for i in range(len(steps))]
    bars = ax.barh(labels, values, color=colors, height=0.55, zorder=3)
    ax.set_xlabel("Records")
    ax.set_title("Filtering pipeline funnel")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() * 1.01, bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=9, color=MUTED_COLOR)
    ax.invert_yaxis()
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_fwci_by_ml(records: list[dict], llm_recs: list[dict]) -> str:
    """Box-plot: FWCI for ML-using vs non-ML vs unclassified papers."""
    llm_set = {id(r) for r in llm_recs}
    ml_set  = {id(r) for r in llm_recs if _uses_ml(r)}
    ml_vals, non_ml_vals, unclass = [], [], []
    for r in records:
        v = r.get("fwci")
        if v is None:
            continue
        rid = id(r)
        if rid in ml_set:
            ml_vals.append(v)
        elif rid in llm_set:
            non_ml_vals.append(v)
        else:
            unclass.append(v)
    groups = [(vals, label) for vals, label in [
        (ml_vals,    "Uses ML/AI"),
        (non_ml_vals, "No ML/AI (classified)"),
        (unclass,    "Unclassified"),
    ] if len(vals) >= 5]
    if not groups:
        return ""

    def _clip(vals: list[float]) -> list[float]:
        arr = np.array(vals)
        q25, q75 = float(np.percentile(arr, 25)), float(np.percentile(arr, 75))
        fence = q75 + 1.5 * (q75 - q25)
        return [v for v in vals if v <= fence]

    data   = [_clip(g[0]) for g in groups]
    labels = [g[1] for g in groups]
    fig, ax = plt.subplots(figsize=(8, 3.5))
    bp = ax.boxplot(data, vert=False, patch_artist=True, showfliers=False,
                    medianprops={"color": TEXT_COLOR, "linewidth": 2})
    for patch, color in zip(bp["boxes"], PALETTE):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_yticks(range(1, len(labels) + 1))
    ax.set_yticklabels(labels, fontsize=10)
    ax.axvline(1.0, color=PALETTE[3], linestyle="--", linewidth=1.2, label="World avg (1.0)")
    ax.set_xlabel("FWCI (each group clipped at IQR fence)")
    ax.set_title("Citation impact: ML/AI users vs. non-users")
    ax.legend(fontsize=9)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_oa_trend(records: list[dict]) -> str:
    """Line chart: % open access papers per year."""
    oa_statuses = {"gold", "green", "hybrid"}
    by_year: dict[int, list] = {}
    for r in records:
        yr = r.get("publication_year")
        if not yr or yr <= 2000:
            continue
        by_year.setdefault(yr, []).append(
            (r.get("open_access_oa_status") or "").lower() in oa_statuses)
    years = sorted(by_year)
    if len(years) < 3:
        return ""
    pcts = [sum(by_year[y]) / len(by_year[y]) * 100 for y in years]
    fig, ax = plt.subplots(figsize=(max(9, len(years) * 0.45), 4))
    ax.plot([str(y) for y in years], pcts, color=PALETTE[1],
            marker="o", linewidth=2, markersize=5, zorder=3)
    ax.fill_between(range(len(years)), pcts, alpha=0.15, color=PALETTE[1])
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) for y in years], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("% open access")
    ax.set_title("Open access rate over time (gold + green + hybrid)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.set_ylim(0, 105)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_scope_over_time(llm_recs: list[dict]) -> str:
    """Stacked bar: LLM research scope categories by year."""
    by_year: dict[int, Counter] = {}
    for r in llm_recs:
        yr = r.get("publication_year")
        if not yr or yr <= 2000:
            continue
        by_year.setdefault(yr, Counter())
        for v in _json_col(r, "llm_fuel_classification"):
            if v and v.lower() not in ("none", "other"):
                by_year[yr][_SHORT_SCOPE.get(v, v)] += 1
    years = sorted(by_year)
    if len(years) < 2:
        return ""
    all_scopes_c: Counter = Counter()
    for c in by_year.values():
        all_scopes_c.update(c)
    top_scopes = [s for s, _ in all_scopes_c.most_common(8)]
    fig, ax = plt.subplots(figsize=(max(10, len(years) * 0.5), 5))
    bottom = [0] * len(years)
    for i, scope in enumerate(top_scopes):
        vals = [by_year[y].get(scope, 0) for y in years]
        ax.bar(range(len(years)), vals, bottom=bottom,
               label=scope, color=PALETTE[i % len(PALETTE)], width=0.75, zorder=3)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) for y in years], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Papers")
    ax.set_title("Research scope trends over time (LLM classified)")
    ax.legend(fontsize=8, loc="upper left", ncol=2)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_countries_over_time(records: list[dict], n: int = 5) -> str:
    """Multi-line chart: paper count per year for the top-N countries."""
    country_total = _count_json_col(records, "authors_countries")
    top_countries = [c for c, _ in country_total.most_common(n)]
    if not top_countries:
        return ""
    by_year: dict[int, Counter] = {}
    for r in records:
        yr = r.get("publication_year")
        if not yr or yr <= 2000:
            continue
        by_year.setdefault(yr, Counter())
        for c in _json_col(r, "authors_countries"):
            if c in top_countries:
                by_year[yr][c] += 1
    years = sorted(by_year)
    if len(years) < 2:
        return ""
    fig, ax = plt.subplots(figsize=(max(10, len(years) * 0.45), 4.5))
    for i, country in enumerate(top_countries):
        vals = [by_year[y].get(country, 0) for y in years]
        ax.plot(range(len(years)), vals, label=_country_display(country),
                color=PALETTE[i], marker="o", linewidth=1.8, markersize=4, zorder=3)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) for y in years], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Papers")
    ax.set_title(f"Top {n} countries — papers over time")
    ax.legend(fontsize=9, loc="upper left")
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_institution_subgroup_heatmap(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
    top_n_inst: int = 15,
) -> str:
    """Heatmap: top institutions (rows) × fuel sub-groups (cols)."""
    inst_total = _count_json_col(records, "authors_institutions")
    top_insts  = [inst for inst, _ in inst_total.most_common(top_n_inst)]
    if not top_insts:
        return ""
    grouped  = _group_by_subgroup(records, fuel_subgroups, matched_col)
    sg_names = list(grouped.keys())
    if not sg_names:
        return ""
    matrix = np.zeros((len(top_insts), len(sg_names)), dtype=int)
    for ci, sgn in enumerate(sg_names):
        cnt = _count_json_col(grouped[sgn], "authors_institutions")
        for ri, inst in enumerate(top_insts):
            matrix[ri, ci] = cnt.get(inst, 0)
    short_insts = [i if len(i) <= 30 else i[:29] + "\u2026" for i in top_insts]
    fig, ax = plt.subplots(figsize=(max(8, len(sg_names) * 1.4),
                                    max(5, len(top_insts) * 0.5)))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(sg_names)))
    ax.set_xticklabels(sg_names, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(top_insts)))
    ax.set_yticklabels(short_insts, fontsize=8)
    ax.set_title("Top institutions \u00d7 fuel sub-group")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = matrix.max() or 1
    for ri in range(len(top_insts)):
        for ci in range(len(sg_names)):
            v = int(matrix[ri, ci])
            if v > 0:
                tc = "white" if v > mx * 0.6 else TEXT_COLOR
                ax.text(ci, ri, str(v), ha="center", va="center", fontsize=7, color=tc)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_cooccurrence_heatmap_normalised(
    records: list[dict],
    n_fuel: int = 12,
    n_impact: int = 12,
    group1_label: str = "Fuel",
    group2_label: str = "Impact",
) -> str:
    """Row-normalised co-occurrence heatmap: % of fuel-term papers that also match each impact term."""
    fuel_c   = _count_json_col(records, "matched_fuel_terms")
    impact_c = _count_json_col(records, "matched_impact_terms")
    top_fuel   = [t for t, _ in fuel_c.most_common(n_fuel)]
    top_impact = [t for t, _ in impact_c.most_common(n_impact)]
    if not top_fuel or not top_impact:
        return ""
    matrix = [[0] * len(top_impact) for _ in range(len(top_fuel))]
    for r in records:
        fts = set(_json_col(r, "matched_fuel_terms"))
        its = set(_json_col(r, "matched_impact_terms"))
        for fi, f in enumerate(top_fuel):
            if f in fts:
                for ii, im in enumerate(top_impact):
                    if im in its:
                        matrix[fi][ii] += 1
    # Row-normalise by fuel term total paper count
    norm = np.zeros((len(top_fuel), len(top_impact)), dtype=float)
    for fi, f in enumerate(top_fuel):
        row_total = fuel_c[f]
        if row_total > 0:
            norm[fi] = [v / row_total * 100 for v in matrix[fi]]
    fig, ax = plt.subplots(figsize=(max(8, n_impact * 0.7), max(5, n_fuel * 0.45)))
    im = ax.imshow(norm, cmap="Blues", aspect="auto", vmin=0, vmax=100)
    ax.set_xticks(range(len(top_impact)))
    ax.set_xticklabels(top_impact, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(top_fuel)))
    ax.set_yticklabels(top_fuel, fontsize=9)
    ax.set_title(f"{group1_label} \u00d7 {group2_label} co-occurrence (% of {group1_label.lower()} papers)")
    fig.colorbar(im, ax=ax, label="% of fuel-term papers", shrink=0.8,
                 format=mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    for fi in range(len(top_fuel)):
        for ii in range(len(top_impact)):
            v = norm[fi, ii]
            if v > 0:
                tc = "white" if v > 60 else TEXT_COLOR
                ax.text(ii, fi, f"{v:.0f}%", ha="center", va="center", fontsize=7, color=tc)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_subgroup_cooccurrence(
    records: list[dict],
    fuel_subgroups: list[dict],
    impact_subgroups: list[dict],
    fuel_col: str = "matched_fuel_terms",
    impact_col: str = "matched_impact_terms",
) -> str:
    """Heatmap: fuel sub-group (rows) × impact sub-group (cols) — raw paper counts."""
    fuel_t2sg   = _build_term_to_subgroup(fuel_subgroups)
    impact_t2sg = _build_term_to_subgroup(impact_subgroups)
    fuel_sg_names   = [sg["name"] for sg in fuel_subgroups   if not sg.get("is_others")]
    impact_sg_names = [sg["name"] for sg in impact_subgroups if not sg.get("is_others")]
    if not fuel_sg_names or not impact_sg_names:
        return ""
    matrix = np.zeros((len(fuel_sg_names), len(impact_sg_names)), dtype=int)
    for rec in records:
        fuel_sgs   = {fuel_t2sg[t.lower()] for t in _json_col(rec, fuel_col)
                      if t.lower() in fuel_t2sg}
        impact_sgs = {impact_t2sg[t.lower()] for t in _json_col(rec, impact_col)
                      if t.lower() in impact_t2sg}
        for fi, fsn in enumerate(fuel_sg_names):
            if fsn in fuel_sgs:
                for ii, isn in enumerate(impact_sg_names):
                    if isn in impact_sgs:
                        matrix[fi, ii] += 1
    fig, ax = plt.subplots(
        figsize=(max(8, len(impact_sg_names) * 1.3), max(4, len(fuel_sg_names) * 0.6)))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(impact_sg_names)))
    ax.set_xticklabels(impact_sg_names, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(fuel_sg_names)))
    ax.set_yticklabels(fuel_sg_names, fontsize=9)
    ax.set_title("Fuel sub-group \u00d7 impact sub-group co-occurrence")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = matrix.max() or 1
    for fi in range(len(fuel_sg_names)):
        for ii in range(len(impact_sg_names)):
            v = int(matrix[fi, ii])
            if v > 0:
                tc = "white" if v > mx * 0.6 else TEXT_COLOR
                ax.text(ii, fi, str(v), ha="center", va="center",
                        fontsize=8, color=tc, fontweight="bold")
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_3d_fuel_impact_ml(
    llm_recs: list[dict],
    fuel_subgroups: list[dict],
    fuel_col: str = "matched_fuel_terms",
    top_ml: int = 4,
) -> str:
    """3D bar: fuel sub-groups (X) × ML technique (colour groups), Z = paper count."""
    if not fuel_subgroups:
        return ""
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    fuel_t2sg = _build_term_to_subgroup(fuel_subgroups)
    ml_recs   = [r for r in llm_recs if _uses_ml(r)]
    if not ml_recs:
        return ""
    counts: Counter = Counter()
    for rec in ml_recs:
        fuel_sgs  = {fuel_t2sg[t.lower()] for t in _json_col(rec, fuel_col)
                     if t.lower() in fuel_t2sg}
        roles = [_SHORT_ML.get(v, v) for v in _json_col(rec, "llm_ml_role")
                 if v and v.lower() != "none"]
        for fsn in fuel_sgs:
            for ml in set(roles):
                counts[(fsn, ml)] += 1
    fuel_sg_names = [sg["name"] for sg in fuel_subgroups if not sg.get("is_others")]
    ml_totals: Counter = Counter({ml: 0 for _, ml in counts})
    for (_, ml), cnt in counts.items():
        ml_totals[ml] += cnt
    top_ml_names = [m for m, _ in ml_totals.most_common(top_ml)]
    if not fuel_sg_names or not top_ml_names:
        return ""
    fig = plt.figure(figsize=(12, 7))
    ax  = fig.add_subplot(111, projection="3d")
    n_ml  = len(top_ml_names)
    x_base = np.arange(len(fuel_sg_names))
    width = 0.6 / n_ml
    for mi, ml_name in enumerate(top_ml_names):
        vals = [counts.get((fsn, ml_name), 0) for fsn in fuel_sg_names]
        xs  = x_base + mi * width
        dz  = np.array(vals, dtype=float)
        ax.bar3d(xs, np.zeros(len(fuel_sg_names)) + mi * 0.01,
                 np.zeros(len(fuel_sg_names)),
                 np.full(len(fuel_sg_names), width * 0.85),
                 np.full(len(fuel_sg_names), width * 0.85),
                 dz, color=PALETTE[mi % len(PALETTE)], alpha=0.8,
                 label=ml_name, zsort="average")
    ax.set_xticks(x_base + width * (n_ml - 1) / 2)
    short_names = [n if len(n) <= 16 else n[:15] + "\u2026" for n in fuel_sg_names]
    ax.set_xticklabels(short_names, rotation=15, ha="right", fontsize=7)
    ax.set_yticks([])
    ax.set_zlabel("Papers")
    ax.set_title("Fuel sub-group \u00d7 ML role (3D)", pad=15)
    ax.legend(top_ml_names, loc="upper left", fontsize=8)
    fig.tight_layout()
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
details.outliers, details.top-papers {
  margin: 12px 0 24px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
details.outliers summary, details.top-papers summary {
  padding: 10px 16px; background: var(--surface); cursor: pointer;
  font-size: 14px; font-weight: 600; list-style: none; user-select: none;
}
details.outliers summary { color: var(--amber); }
details.top-papers summary { color: var(--accent); }
details.outliers summary::before, details.top-papers summary::before { content: "▶  "; font-size: 11px; }
details.outliers[open] summary::before, details.top-papers[open] summary::before { content: "▼  "; }
details.outliers table, details.top-papers table { margin: 0; font-size: 13px; }
details.outliers td, details.outliers th,
details.top-papers td, details.top-papers th { padding: 6px 12px; }
details.outliers .fwci-val { font-weight: 700; color: var(--amber); }
.subgroup-section { margin-top: 28px; }
details.subgroup-breakdown {
  margin: 16px 0 28px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
details.subgroup-breakdown > summary {
  padding: 10px 16px; background: var(--surface); cursor: pointer;
  font-size: 14px; font-weight: 600; list-style: none; user-select: none; color: var(--accent);
}
details.subgroup-breakdown > summary::before { content: "▶  "; font-size: 11px; }
details.subgroup-breakdown[open] > summary::before { content: "▼  "; }
details.subgroup-breakdown > .subgroup-inner { padding: 12px 16px; }
/* TOC */
nav.toc {
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 20px; margin: 20px 0 36px; font-size: 14px;
}
nav.toc ol { margin: 6px 0 0 20px; padding: 0; }
nav.toc li { margin-bottom: 3px; }
/* Modal */
.modal-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4);
  z-index: 1000; align-items: center; justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal-box {
  background: #fff; border-radius: 12px; padding: 24px 28px;
  max-width: 880px; width: 92vw; max-height: 80vh; overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.25);
}
.modal-close {
  float: right; cursor: pointer; font-size: 18px; color: var(--muted);
  background: none; border: none; padding: 0; line-height: 1;
}
.kpi .value[data-modal-id] { cursor: pointer; text-decoration: underline dotted; }
.sg-row[data-modal-id] { cursor: pointer; }
.sg-row[data-modal-id]:hover td { background: #EFF6FF; }
/* Print */
@media print {
  .modal-overlay { display: none !important; }
  details.subgroup-breakdown, details.outliers, details.top-papers { display: block; }
  details > summary { display: none; }
  details > .subgroup-inner, details > * { display: block !important; }
  body { max-width: 100%; padding: 0; }
  .note.amber, .insight { border: 1px solid #ccc; }
}
"""

_MODAL_JS = """
<script>
(function(){
  const overlay = document.getElementById('rec-modal');
  if (!overlay) return;
  document.querySelectorAll('[data-modal-id]').forEach(function(el){
    el.addEventListener('click', function(){
      var src = document.getElementById(el.dataset.modalId);
      document.getElementById('rec-modal-body').innerHTML = src ? src.innerHTML : '';
      overlay.classList.add('open');
    });
  });
  overlay.addEventListener('click', function(e){ if(e.target===overlay) overlay.classList.remove('open'); });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') overlay.classList.remove('open'); });
})();
</script>
"""

_MODAL_HTML = """
<div id="rec-modal" class="modal-overlay" role="dialog" aria-modal="true">
  <div class="modal-box">
    <button class="modal-close" onclick="document.getElementById('rec-modal').classList.remove('open')" aria-label="Close">\u00d7</button>
    <div id="rec-modal-body"></div>
  </div>
</div>
"""


def _kpi(label: str, value: str, detail: str = "", cls: str = "",
         modal_id: str = "") -> str:
    det = f'<div class="detail">{detail}</div>' if detail else ""
    modal_attr = f' data-modal-id="{modal_id}"' if modal_id else ""
    return (f'<div class="kpi {cls}">'
            f'<div class="label">{label}</div>'
            f'<div class="value"{modal_attr}>{value}</div>'
            f'{det}</div>')


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


def _toc(sections: list[tuple[str, str]]) -> str:
    """Generate a nav TOC block. sections = [(anchor_id, label), ...]"""
    links = "".join(
        f'<li><a href="#{aid}" style="color:var(--accent);text-decoration:none;">{label}</a></li>'
        for aid, label in sections
    )
    return (f'<nav class="toc"><strong style="font-size:14px">Contents</strong>'
            f'<ol>{links}</ol></nav>')


def _subgroup_kpi_block(
    records: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str = "matched_fuel_terms",
) -> str:
    """Compact sub-group distribution table shown below the main KPI grid."""
    if not fuel_subgroups:
        return ""
    grouped = _group_by_subgroup(records, fuel_subgroups, matched_col)
    total = len(records)
    rows = ""
    for idx, (sg_name, sg_recs) in enumerate(grouped.items()):
        n_sg = len(sg_recs)
        pct  = n_sg / total * 100 if total else 0
        bar_w = max(4, int(pct * 0.9))
        bar = (f'<div style="width:90px;background:#E5E7EB;border-radius:4px;height:6px;display:inline-block;">'
               f'<div style="width:{bar_w}%;background:var(--accent);border-radius:4px;height:6px;"></div>'
               f'</div>')
        mid = f"modal-sg-{idx}"
        rows += (
            f'<tr class="sg-row" data-modal-id="{mid}">'
            f"<td style='font-size:13px'><strong>{sg_name}</strong></td>"
            f"<td style='text-align:right;font-size:13px'>{n_sg:,}</td>"
            f"<td style='text-align:right;font-size:12px;color:var(--muted)'>{pct:.1f}%</td>"
            f"<td style='padding-left:8px'>{bar}</td></tr>"
            f'<div id="{mid}" style="display:none">'
            f'{_records_mini_table(sg_recs, f"Top papers — {sg_name}")}'
            f'</div>'
        )
    return f"""
<details class="subgroup-breakdown" open>
  <summary>Sub-group distribution</summary>
  <div class="subgroup-inner">
    <table style="margin:0">
      <thead><tr>
        <th>Sub-group</th>
        <th style="text-align:right">Papers</th>
        <th style="text-align:right">Share</th>
        <th></th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</details>"""


def _records_mini_table(records: list[dict], title: str, n: int = 20) -> str:
    """Records table for modal display, sorted by FWCI descending."""
    top = sorted(records, key=lambda r: -(r.get("fwci") or 0))[:n]
    rows = ""
    for r in top:
        title_str = (r.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")[:120]
        doi = (r.get("doi") or "").strip()
        linked = (f'<a href="{doi}" target="_blank" rel="noopener" '
                  f'style="color:var(--accent);text-decoration:none;">{title_str}</a>'
                  if doi else title_str)
        fwci_str = f'{r["fwci"]:.2f}' if r.get("fwci") is not None else "—"
        rows += (f"<tr><td>{linked}</td>"
                 f"<td style='text-align:center;white-space:nowrap'>{r.get('publication_year') or '—'}</td>"
                 f"<td style='text-align:center;white-space:nowrap'>{fwci_str}</td></tr>")
    return (f'<h3 style="margin-bottom:12px">{title}</h3>'
            f'<table><thead><tr><th>Title</th><th style="text-align:center">Year</th>'
            f'<th style="text-align:center">FWCI</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def _data_quality_note(records: list[dict], llm_recs: list[dict], n: int) -> str:
    """Collapsible coverage statistics block for the Summary section."""
    if n == 0:
        return ""
    pct_fwci    = sum(1 for r in records if r.get("fwci") is not None) / n * 100
    pct_country = sum(1 for r in records if _json_col(r, "authors_countries")) / n * 100
    pct_inst    = sum(1 for r in records if _json_col(r, "authors_institutions")) / n * 100
    pct_llm     = len(llm_recs) / n * 100 if n else 0
    return f"""
<details class="outliers" style="margin-top:20px">
  <summary style="color:var(--muted)">Data coverage</summary>
  <div style="padding:12px 16px;font-size:13px">
    <ul style="margin-left:16px">
      <li>FWCI available: <strong>{pct_fwci:.0f}%</strong> of papers</li>
      <li>Country data: <strong>{pct_country:.0f}%</strong></li>
      <li>Institution data: <strong>{pct_inst:.0f}%</strong></li>
      <li>LLM-classified: <strong>{pct_llm:.0f}%</strong></li>
    </ul>
  </div>
</details>"""


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


def _per_subgroup_html(
    charts: list[tuple[str, str]],
    heading: str = "By fuel sub-group",
    two_col: bool = False,
) -> str:
    """Render a list of (sg_name, b64_png) tuples as a collapsible <details> section."""
    if not charts:
        return ""
    inner = ""
    for sg_name, b64 in charts:
        inner += (
            f'<div class="subgroup-section">'
            f'<h3>{sg_name}</h3>'
            f'{_chart(b64, sg_name)}'
            f'</div>\n'
        )
    grid = f'<div class="two-col">{inner}</div>' if two_col else inner
    return (
        f'<details class="subgroup-breakdown" open>'
        f'<summary>{heading}</summary>'
        f'<div class="subgroup-inner">{grid}</div>'
        f'</details>'
    )


def _top_papers_table(
    records: list[dict],
    summary_label: str = "Top papers by FWCI",
    n: int = 10,
) -> str:
    """Collapsible table of top-N papers by FWCI with DOI links."""
    scored = [(r.get("fwci") or 0, r) for r in records if r.get("fwci")]
    scored.sort(key=lambda x: -x[0])
    top = [r for _, r in scored[:n]]
    if not top:
        return ""
    rows_html = ""
    for i, r in enumerate(top):
        fwci  = r.get("fwci") or 0
        title = (r.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        doi   = (r.get("doi") or "").strip()
        year  = r.get("publication_year") or "—"
        insts = _json_col(r, "authors_institutions")
        inst_str = "; ".join(insts[:2]) + ("\u2026" if len(insts) > 2 else "")
        fuel_terms   = ", ".join(_json_col(r, "matched_fuel_terms")[:3])
        impact_terms = ", ".join(_json_col(r, "matched_impact_terms")[:3])
        linked = (
            f'<a href="{doi}" target="_blank" rel="noopener" '
            f'style="color:var(--accent);text-decoration:none;">{title}</a>'
            if doi else title
        )
        rows_html += (
            f"<tr><td style='text-align:center;color:var(--muted);font-size:12px'>{i+1}</td>"
            f"<td>{linked}</td>"
            f"<td style='text-align:center'>{year}</td>"
            f'<td style="text-align:center"><span style="font-weight:700;color:{"var(--green)" if fwci >= 1 else "var(--muted)"}">{fwci:.2f}</span></td>'
            f"<td style='font-size:11px;color:var(--muted)'>{inst_str}</td>"
            f"<td style='font-size:11px;color:var(--muted)'>{fuel_terms}</td>"
            f"<td style='font-size:11px;color:var(--muted)'>{impact_terms}</td>"
            f"</tr>"
        )
    total = len([r for r in records if r.get("fwci")])
    return f"""
<details class="top-papers">
  <summary>&#128196; {summary_label} <span style="font-weight:normal;font-size:12px">({min(n, total)} of {total} with FWCI data)</span></summary>
  <table>
    <thead><tr>
      <th style="text-align:center;width:32px">#</th>
      <th>Title</th>
      <th style="text-align:center;width:60px">Year</th>
      <th style="text-align:center;width:70px">FWCI</th>
      <th>Institutions</th>
      <th>Fuel terms</th>
      <th>Impact terms</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</details>
"""


def _ml_by_subgroup_table(
    llm_recs: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """HTML table summarising ML/AI adoption and top scope per fuel sub-group."""
    grouped = _group_by_subgroup(llm_recs, fuel_subgroups, matched_col)
    if not grouped:
        return ""

    rows_html = ""
    for sg_name, sg_recs in grouped.items():
        n_total = len(sg_recs)
        n_ml = sum(1 for r in sg_recs if _uses_ml(r))
        rate = n_ml / n_total * 100 if n_total else 0
        bar_w = max(4, int(rate * 0.9))
        bar_html = (
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'<div style="width:90px;background:#E5E7EB;border-radius:4px;height:8px;">'
            f'<div style="width:{bar_w}%;background:var(--accent);border-radius:4px;height:8px;"></div>'
            f'</div><span style="font-size:12px">{rate:.0f}%</span></div>'
        )
        # Top scope
        scope_c: Counter = Counter()
        method_c: Counter = Counter()
        for r in sg_recs:
            for v in _json_col(r, "llm_fuel_classification"):
                if v and v.lower() not in ("none", "other"):
                    scope_c[_SHORT_SCOPE.get(v, v)] += 1
            for v in _json_col(r, "llm_ml_role"):
                if v and v.lower() not in ("none", "other"):
                    method_c[_SHORT_ML.get(v, v)] += 1
        top_scope = scope_c.most_common(1)[0][0] if scope_c else "—"
        top_role  = method_c.most_common(1)[0][0] if method_c else "—"
        rows_html += (
            f"<tr><td><strong>{sg_name}</strong></td>"
            f"<td style='text-align:center'>{n_total:,}</td>"
            f"<td style='text-align:center'>{n_ml:,}</td>"
            f"<td>{bar_html}</td>"
            f"<td style='font-size:12px'>{top_scope}</td>"
            f"<td style='font-size:12px'>{top_role}</td></tr>"
        )
    return f"""
<table>
  <thead><tr>
    <th>Sub-group</th>
    <th style="text-align:center">Papers</th>
    <th style="text-align:center">Uses ML</th>
    <th>ML rate</th>
    <th>Top scope</th>
    <th>Top ML role</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
"""


def chart_ml_adoption_by_subgroup(
    llm_recs: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """Horizontal bar chart comparing ML adoption rate per fuel sub-group."""
    grouped = _group_by_subgroup(llm_recs, fuel_subgroups, matched_col)
    sg_names, rates, counts = [], [], []
    for sg_name, sg_recs in grouped.items():
        if len(sg_recs) < 3:
            continue
        n_ml = sum(1 for r in sg_recs if _uses_ml(r))
        sg_names.append(sg_name)
        rates.append(n_ml / len(sg_recs) * 100)
        counts.append(len(sg_recs))
    if not sg_names:
        return ""
    fig, ax = plt.subplots(figsize=(9, max(3, len(sg_names) * 0.55)))
    bars = ax.barh(sg_names, rates,
                   color=[PALETTE[i % len(PALETTE)] for i in range(len(sg_names))],
                   height=0.6, zorder=3)
    ax.set_xlabel("% papers using ML/AI")
    ax.set_title("ML/AI adoption rate by fuel sub-group")
    ax.set_xlim(0, 105)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    for bar, rate, cnt in zip(bars, rates, counts):
        ax.text(bar.get_width() + 1.5,
                bar.get_y() + bar.get_height() / 2,
                f"{rate:.0f}%  ({cnt:,} papers)", va="center", fontsize=9, color=MUTED_COLOR)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_scope_per_subgroup(
    llm_recs: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """Heatmap: LLM research-scope categories × fuel sub-groups."""
    grouped = _group_by_subgroup(llm_recs, fuel_subgroups, matched_col)
    sg_names = [sg for sg, recs in grouped.items() if len(recs) >= 3]
    if not sg_names:
        return ""
    scope_c_all: Counter = Counter()
    for sg in sg_names:
        for r in grouped[sg]:
            for v in _json_col(r, "llm_fuel_classification"):
                if v and v.lower() not in ("none", "other"):
                    scope_c_all[_SHORT_SCOPE.get(v, v)] += 1
    top_scopes = [s for s, _ in scope_c_all.most_common(10)]
    if not top_scopes:
        return ""
    matrix = np.zeros((len(sg_names), len(top_scopes)), dtype=int)
    for fi, sgn in enumerate(sg_names):
        for r in grouped[sgn]:
            for v in _json_col(r, "llm_fuel_classification"):
                if v and v.lower() not in ("none", "other"):
                    label = _SHORT_SCOPE.get(v, v)
                    if label in top_scopes:
                        matrix[fi, top_scopes.index(label)] += 1
    fig, ax = plt.subplots(figsize=(max(8, len(top_scopes) * 1.3),
                                    max(3.5, len(sg_names) * 0.65)))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(top_scopes)))
    ax.set_xticklabels(top_scopes, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(sg_names)))
    ax.set_yticklabels(sg_names, fontsize=9)
    ax.set_title("Research scope by fuel sub-group (LLM classified)")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = matrix.max() or 1
    for i in range(len(sg_names)):
        for j in range(len(top_scopes)):
            v = int(matrix[i, j])
            if v > 0:
                tc = "white" if v > mx * 0.6 else TEXT_COLOR
                ax.text(j, i, str(v), ha="center", va="center", fontsize=8,
                        color=tc, fontweight="bold")
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_role_per_subgroup(
    llm_recs: list[dict],
    fuel_subgroups: list[dict],
    matched_col: str,
) -> str:
    """Heatmap: ML functional role × fuel sub-groups (ML-using papers only)."""
    grouped = _group_by_subgroup(llm_recs, fuel_subgroups, matched_col)
    sg_ml: dict[str, list[dict]] = {
        sg: [r for r in recs if _uses_ml(r)]
        for sg, recs in grouped.items()
    }
    sg_names = [sg for sg, recs in sg_ml.items() if len(recs) >= 3]
    if not sg_names:
        return ""
    role_c_all: Counter = Counter()
    for sgn in sg_names:
        for r in sg_ml[sgn]:
            for v in _json_col(r, "llm_ml_role"):
                if v and v.lower() != "none":
                    role_c_all[_SHORT_ML.get(v, v)] += 1
    top_roles = [p for p, _ in role_c_all.most_common(10)]
    if not top_roles:
        return ""
    matrix = np.zeros((len(sg_names), len(top_roles)), dtype=int)
    for fi, sgn in enumerate(sg_names):
        for r in sg_ml[sgn]:
            for v in _json_col(r, "llm_ml_role"):
                if v and v.lower() != "none":
                    label = _SHORT_ML.get(v, v)
                    if label in top_roles:
                        matrix[fi, top_roles.index(label)] += 1
    fig, ax = plt.subplots(figsize=(max(8, len(top_roles) * 1.5),
                                    max(3.5, len(sg_names) * 0.65)))
    im = ax.imshow(matrix, cmap="Purples", aspect="auto")
    ax.set_xticks(range(len(top_roles)))
    ax.set_xticklabels(top_roles, rotation=30, ha="right", fontsize=9)
    ax.set_yticks(range(len(sg_names)))
    ax.set_yticklabels(sg_names, fontsize=9)
    ax.set_title("ML/AI functional role by fuel sub-group (ML-using papers only)")
    fig.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    mx = matrix.max() or 1
    for i in range(len(sg_names)):
        for j in range(len(top_roles)):
            v = int(matrix[i, j])
            if v > 0:
                tc = "white" if v > mx * 0.6 else TEXT_COLOR
                ax.text(j, i, str(v), ha="center", va="center", fontsize=9,
                        color=tc, fontweight="bold")
    fig.tight_layout()
    return _fig_to_base64(fig)


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
    c_scope        = chart_fuel_classification(llm_recs)       if has_llm else ""
    c_ml_adopt     = chart_ml_adoption_rate(llm_recs)          if has_llm else ""
    c_ml_time      = chart_ml_adoption_over_time(llm_recs)     if has_llm else ""
    c_ml_role      = chart_ml_role(llm_recs)                   if has_llm else ""
    c_ml_algo      = chart_ml_algorithm(llm_recs)              if has_llm else ""
    c_ml_strategy  = chart_ml_strategy(llm_recs)               if has_llm else ""
    c_ml_data_src  = chart_ml_data_source(llm_recs)            if has_llm else ""
    c_scope_x_ml   = chart_scope_x_ml(llm_recs)                if has_llm else ""
    c_model_types  = chart_model_types(llm_recs)               if has_llm else ""
    c_ml_fuel      = chart_ml_by_fuel_type(llm_recs)           if has_llm else ""
    c_confidence   = chart_confidence_distribution(llm_recs)   if has_llm else ""
    c_ml_by_sg     = (chart_ml_adoption_by_subgroup(llm_recs, fuel_subgroups, "matched_fuel_terms")
                      if has_llm and fuel_subgroups else "")
    ml_sg_table    = (_ml_by_subgroup_table(llm_recs, fuel_subgroups, "matched_fuel_terms")
                      if has_llm and fuel_subgroups else "")

    # ── Per-subgroup charts ───────────────────────────────────────────────
    c_years_by_sg = (
        chart_publications_per_subgroup(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else []
    )
    c_trends_by_sg = (
        chart_yearly_trends_per_subgroup(records, fuel_subgroups, "matched_fuel_terms",
                                         group1_label)
        if fuel_subgroups else []
    )
    c_impact_profile = (
        chart_impact_profile_per_subgroup(records, fuel_subgroups, impact_subgroups)
        if fuel_subgroups and impact_subgroups else ""
    )
    c_impact_terms_by_sg = (
        chart_impact_terms_per_subgroup(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else []
    )
    c_countries_by_sg = (
        chart_countries_per_subgroup(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else []
    )
    c_journals_comparison = (
        chart_journals_comparison(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else ""
    )
    c_oa_by_sg = (
        chart_oa_per_subgroup_stacked(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else ""
    )
    c_types_by_sg = (
        chart_work_types_per_subgroup_stacked(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else ""
    )
    c_fwci_by_sg = (
        chart_fwci_per_subgroup(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else ""
    )
    c_scope_by_sg = (
        chart_scope_per_subgroup(llm_recs, fuel_subgroups, "matched_fuel_terms")
        if has_llm and fuel_subgroups else ""
    )
    c_role_by_sg = (
        chart_role_per_subgroup(llm_recs, fuel_subgroups, "matched_fuel_terms")
        if has_llm and fuel_subgroups else ""
    )

    # ── New chart variables ───────────────────────────────────────────────
    c_funnel_chart    = chart_filtering_funnel(state) if state else ""
    c_fwci_ml         = chart_fwci_by_ml(records, llm_recs) if has_llm else ""
    c_oa_trend        = chart_oa_trend(records)
    c_scope_time      = chart_scope_over_time(llm_recs) if has_llm else ""
    c_countries_time  = chart_countries_over_time(records)
    c_inst_sg_heatmap = (
        chart_institution_subgroup_heatmap(records, fuel_subgroups, "matched_fuel_terms")
        if fuel_subgroups else ""
    )
    c_heatmap_norm    = chart_cooccurrence_heatmap_normalised(
        records, group1_label=group1_label, group2_label=group2_label)
    c_sg_cooccurrence = (
        chart_subgroup_cooccurrence(records, fuel_subgroups, impact_subgroups)
        if fuel_subgroups and impact_subgroups else ""
    )
    c_3d_fuel_ml      = (
        chart_3d_fuel_impact_ml(llm_recs, fuel_subgroups)
        if has_llm and fuel_subgroups else ""
    )

    # Top-papers tables (by FWCI) — precomputed per subgroup and globally
    top_papers_global = _top_papers_table(records, "Top cited papers (global)", n=10)
    top_papers_by_sg  = []
    if fuel_subgroups:
        sg_grouped = _group_by_subgroup(records, fuel_subgroups, "matched_fuel_terms")
        for sg_name, sg_recs in sg_grouped.items():
            tbl = _top_papers_table(sg_recs, f"Top cited — {sg_name}", n=8)
            if tbl:
                top_papers_by_sg.append((sg_name, tbl))

    print("Building HTML ...")

    # ── KPIs ──────────────────────────────────────────────────────────────
    top5_fuel   = ", ".join(t for t, _ in fuel_counts.most_common(5))
    top5_impact = ", ".join(t for t, _ in impact_counts.most_common(5))
    top3_geo    = ", ".join(_country_display(c) for c, _ in country_counts.most_common(3))
    yr_span     = yr_max - yr_min + 1 if yr_max > yr_min else 1

    # Year-on-year growth for the most recent complete year
    yr_counts  = Counter(r["publication_year"] for r in records
                         if r.get("publication_year") and r["publication_year"] > 2000)
    yoy_detail = f"{yr_min}\u2013{yr_max}"
    if yr_counts.get(yr_max) and yr_counts.get(yr_max - 1):
        yoy = (yr_counts[yr_max] - yr_counts[yr_max - 1]) / yr_counts[yr_max - 1] * 100
        arrow = "\u2191" if yoy >= 0 else "\u2193"
        yoy_detail = f"{yr_min}\u2013{yr_max} \u00b7 {arrow} {abs(yoy):.0f}% vs {yr_max - 1}"

    # Open access percentage
    oa_open = sum(1 for r in records
                  if (r.get("open_access_oa_status") or "").lower()
                  in ("gold", "green", "hybrid"))
    oa_pct  = oa_open / n * 100 if n else 0

    # Pre-build modal data divs for clickable KPIs
    modal_total_papers = (
        f'<div id="modal-total" style="display:none">'
        f'{_records_mini_table(records, "All papers (top by FWCI)")}'
        f'</div>'
    )
    modal_oa_papers = (
        f'<div id="modal-oa" style="display:none">'
        f'{_records_mini_table([r for r in records if (r.get("open_access_oa_status") or "").lower() in ("gold","green","hybrid")], "Open access papers")}'
        f'</div>'
    )
    modal_fwci_papers = ""
    if fwci_median:
        near_med = sorted(records, key=lambda r: abs((r.get("fwci") or 0) - fwci_median))[:20]
        modal_fwci_papers = (
            f'<div id="modal-fwci" style="display:none">'
            f'{_records_mini_table(near_med, f"Papers near median FWCI ({fwci_median:.2f})")}'
            f'</div>'
        )

    kpis = (
        _kpi("Total papers", f"{n:,}", yoy_detail, modal_id="modal-total")
      + _kpi(group1_label, str(len(fuel_counts)))
      + _kpi(group2_label, str(len(impact_counts)))
      + _kpi("Countries", str(len(country_counts)))
      + _kpi("Journals", str(len(journal_counts)))
      + (_kpi("Median FWCI", f"{fwci_median:.2f}", f"mean {fwci_mean:.2f}",
              modal_id="modal-fwci")
         if fwci_median else "")
      + _kpi("Open access", f"{oa_pct:.0f}%", f"{oa_open:,} papers", modal_id="modal-oa")
    )

    llm_kpis = ""
    if has_llm:
        ml_pct  = n_ml  / n_llm * 100
        hyd_pct = n_hydro / n_llm * 100

        # Dominant scope
        scope_c   = _count_json_col(llm_recs, "llm_fuel_classification")
        top_scope = _SHORT_SCOPE.get(*scope_c.most_common(1)[0]) if scope_c else "N/A"

        # Top ML role (excl. None)
        role_c = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_role"):
                if v and v.lower() != "none":
                    role_c[_SHORT_ML.get(v, v)] += 1
        top_role = role_c.most_common(1)[0][0] if role_c else "N/A"

        # Top algorithm
        algo_c = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_algorithm"):
                if v and v.lower() not in ("none", "other_ml"):
                    algo_c[_SHORT_ML.get(v, v)] += 1
        top_algo = algo_c.most_common(1)[0][0] if algo_c else "N/A"

        # Avg tokens
        avg_in  = (sum(r.get("llm_tokens_input", 0)  or 0 for r in llm_recs) / n_llm)
        avg_out = (sum(r.get("llm_tokens_output", 0) or 0 for r in llm_recs) / n_llm)

        modal_ml_papers = (
            f'<div id="modal-ml" style="display:none">'
            f'{_records_mini_table(ml_recs, "ML/AI-using papers (top by FWCI)")}'
            f'</div>'
        )

        llm_kpis = (
            _kpi("LLM-classified", f"{n_llm:,}", f"{n_llm/n*100:.1f}% of corpus")
          + _kpi("Uses ML/AI", f"{n_ml:,}", f"{ml_pct:.1f}% of classified", "green",
                 modal_id="modal-ml")
          + _kpi("Hydrocarbon focus", f"{n_hydro:,}", f"{hyd_pct:.1f}%")
          + _kpi("Dominant scope", top_scope)
          + _kpi("Top ML role", top_role, "", "green")
          + _kpi("Top algorithm", top_algo, "", "green")
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
<h2 id="sec-funnel">1. Filtering funnel</h2>
<p>Pipeline scanned <strong>{n_done:,}</strong> of {n_files:,} partition files
({seen:,} records total), retaining {ins:,} ({hit:.4f}% hit rate).</p>
<div class="kpi-grid">
    {_kpi("Records scanned", f"{seen:,}")}
    {_kpi("Passed filter", f"{ins:,}", f"{hit:.4f}%")}
    {_kpi("Files processed", f"{n_done:,}", f"of {n_files:,}")}
</div>
{_chart(c_funnel_chart, "Filtering funnel")}
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
                    scope_c_named[_SHORT_SCOPE.get(v, v)] += 1
        top3_scopes = ", ".join(s for s, _ in scope_c_named.most_common(3))

        role_c2 = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_role"):
                if v and v.lower() != "none":
                    role_c2[_SHORT_ML.get(v, v)] += 1
        top_role2 = role_c2.most_common(1)[0][0] if role_c2 else "Property Pred."

        algo_c2 = Counter()
        for r in llm_recs:
            for v in _json_col(r, "llm_ml_algorithm"):
                if v and v.lower() not in ("none", "other_ml"):
                    algo_c2[_SHORT_ML.get(v, v)] += 1
        top_algo2 = algo_c2.most_common(1)[0][0] if algo_c2 else "Grad. Boosting"

        llm_section = f"""
<h2>{s+2}. AI / ML in sustainable-fuels research</h2>
<p>
  {n_llm:,} papers in this corpus have been classified by the LLM batch pipeline (prompt v3).
  The analysis below answers <em>how</em> and <em>where</em> machine learning
  is applied in the field — covering research scope, ML functional roles,
  algorithm families, methodological strategies, data sources, and growth trends.
</p>

<div class="kpi-grid">{llm_kpis}</div>

<div class="insight">
  <strong>Key findings:</strong>
  <ul style="margin-top:8px;">
    <li><strong>{ml_pct:.0f}%</strong> of classified papers employ at least one ML/AI method,
        indicating the field is in a rapid AI-adoption phase.</li>
    <li>The dominant research scope is <strong>{top3_scopes}</strong>.</li>
    <li>The most common ML functional role is <strong>{top_role2}</strong>, driven by
        <strong>{top_algo2}</strong> as the leading algorithm family.</li>
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

{"" if not c_scope_time else f"""
<h3>Research scope trends over time</h3>
<p>How the dominant LLM-assigned research scope categories have shifted year-on-year.</p>
{_chart(c_scope_time, "Scope trends")}
"""}

<h3>ML/AI functional role and algorithm family</h3>
<p>
  Among papers that <em>do</em> use ML/AI, the charts below show
  the functional purpose ML serves in the research (role) and the algorithm
  architecture used (algorithm family).  These two dimensions independently
  reveal <em>what ML does</em> vs <em>how it is implemented</em>.
</p>
<div class="two-col">
  <div>{_chart(c_ml_role, "ML roles")}</div>
  <div>{_chart(c_ml_algo, "ML algorithms")}</div>
</div>

{"" if not c_ml_strategy else f"""
<h3>Cross-cutting ML strategies</h3>
<p>Advanced methodological approaches noted as significant contributions: transfer learning,
explainability (SHAP/LIME), physics-hybrid architectures, ensemble stacking, and active learning.</p>
{_chart(c_ml_strategy, "ML strategies")}
"""}

{"" if not c_ml_data_src else f"""
<h3>ML training data sources</h3>
<p>Where the data feeding the ML models originates — distinguishing experiment-driven
from simulation-dependent sub-fields.</p>
{_chart(c_ml_data_src, "ML data sources")}
"""}

<h3>Research scope × ML role heatmap</h3>
<p>
  Headline co-occurrence matrix: which research areas pair with which ML functional roles?
  Bright cells indicate the dominant ML application within each fuel research category.
</p>
{_chart(c_scope_x_ml, "Scope x ML role heatmap")}

<h3>ML/AI adoption by fuel type</h3>
<p>
  For the most common fuel keywords, the bar below shows how many
  papers do or do not employ ML/AI.  The percentage label is the
  ML/AI adoption rate for that fuel type.
</p>
{_chart(c_ml_fuel, "ML by fuel type")}
{"" if not c_ml_by_sg else f'''
<h3>ML/AI adoption by fuel sub-group</h3>
<p>Comparing ML/AI adoption rates across defined fuel sub-groups.
Each bar represents the share of LLM-classified papers in that
sub-group that employ at least one ML/AI method.</p>
{_chart(c_ml_by_sg, "ML adoption by sub-group")}
''' }
{"" if not ml_sg_table else f'''
<h3>ML/AI profile per fuel sub-group</h3>
<p>Summary of classified papers, ML/AI adoption rate, dominant research scope,
and most common ML functional role for each fuel sub-group.</p>
{ml_sg_table}
''' }
{"" if not c_scope_by_sg else f'''
<h3>Research scope by fuel sub-group</h3>
<p>Which LLM-assigned scope categories are most prominent within each fuel sub-group?</p>
{_chart(c_scope_by_sg, "Scope by sub-group")}
'''}
{"" if not c_role_by_sg else f'''
<h3>ML/AI functional role by fuel sub-group</h3>
<p>Among papers that employ ML/AI, the dominant ML functional roles per fuel sub-group.</p>
{_chart(c_role_by_sg, "Role by sub-group")}
'''}

<h3>Computational and analytical model inventory</h3>
<p>
  The LLM was instructed to extract every named model used in each paper
  (ML, CFD, experimental, thermodynamic, etc.).  The chart aggregates
  model <em>types</em> across the classified corpus.
</p>
{_chart(c_model_types, "Model types")}
{"" if not c_3d_fuel_ml else f"""
<h3>Fuel sub-group \u00d7 ML technique (3D)</h3>
<p>Three-dimensional view: X = fuel sub-group, colour groups = ML paradigm, height = paper count.
Shows where each ML technique dominates within each fuel sub-group.</p>
{_chart(c_3d_fuel_ml, "3D fuel vs ML")}
"""}
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
{_subgroup_kpi_block(records, fuel_subgroups)}

{_toc([
    *([("sec-funnel", "Filtering funnel")] if funnel_html else []),
    ("sec-trends",    f"{s}. Publication trends"),
    ("sec-keywords",  f"{s+1}. Keyword analysis"),
    *([("sec-llm",    f"{s+2}. AI / ML analysis")] if has_llm else []),
    ("sec-geo",       f"{s + (3 if has_llm else 2)}. Geographic distribution"),
    ("sec-journals",  f"{s + (4 if has_llm else 3)}. Journals and sources"),
    ("sec-oa",        f"{s + (5 if has_llm else 4)}. Open access and work types"),
    ("sec-citation",  f"{s + (6 if has_llm else 5)}. Citation impact"),
    ("sec-inst",      f"{s + (7 if has_llm else 6)}. Top institutions"),
    ("sec-summary",   f"{s + (8 if has_llm else 7)}. Summary"),
])}

{funnel_html}

<h2 id="sec-trends">{s}. Publication trends</h2>
<p>Annual volume of matching publications across the {yr_span}-year span.</p>
{_chart(c_years, "Publications by year")}

<h3>{group1_label} trends over time</h3>
<p>Stacked view showing the shifting research focus over time across {group1_label.lower()}.</p>
{_chart(c_trends, f"{group1_label} trends")}
{_per_subgroup_html(c_years_by_sg, "Annual publication volume by fuel sub-group", two_col=True)}
{_per_subgroup_html(c_trends_by_sg, f"{group1_label} trends by fuel sub-group", two_col=True)}

<h2 id="sec-keywords">{s+1}. Keyword analysis</h2>

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

<h3>{group1_label} &times; {group2_label} co-occurrence (row-normalised)</h3>
<p>Same data expressed as the % of papers for each {group1_label.lower()} term that also match each {group2_label.lower()} term — removes the effect of dominant terms.</p>
{_chart(c_heatmap_norm, "Normalised co-occurrence")}

{"" if not c_sg_cooccurrence else f'''
<h3>Fuel sub-group &times; impact sub-group co-occurrence</h3>
<p>How many papers sit at the intersection of each fuel sub-group and each impact sub-group.</p>
{_chart(c_sg_cooccurrence, "Sub-group co-occurrence")}
'''}
{"" if not c_impact_profile else f'''
<h3>Impact term profile by fuel sub-group</h3>
<p>Share of papers in each fuel sub-group matched to each {group2_label} sub-group
(papers may count in multiple categories).</p>
{_chart(c_impact_profile, "Impact profile per fuel sub-group")}
'''}
{_per_subgroup_html(c_impact_terms_by_sg, f"Top {group2_label} per fuel sub-group", two_col=True)}

{llm_section}

<h2 id="sec-geo">{s + (3 if has_llm else 2)}. Geographic distribution</h2>
<p>Author country affiliations (one paper may count across multiple countries).</p>
<div class="two-col">
  <div>{_chart(c_countries, "Top countries")}</div>
  <div>{top_countries_table}</div>
</div>
{_chart(c_countries_time, "Country trends over time")}
{_per_subgroup_html(c_countries_by_sg, "Geographic distribution by fuel sub-group", two_col=True)}

<h2 id="sec-journals">{s + (4 if has_llm else 3)}. Journals and sources</h2>
<div class="two-col">
  <div>{_chart(c_journals, "Top journals")}</div>
  <div>{top_journals_table}</div>
</div>
{"" if not c_journals_comparison else f'''
<h3>Journal affinity by fuel sub-group</h3>
<p>Top journals for each fuel sub-group compared in a single view.</p>
{_chart(c_journals_comparison, "Journal affinity by sub-group")}
'''}

<h2 id="sec-oa">{s + (5 if has_llm else 4)}. Open access and work types</h2>
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
{"" if not c_oa_by_sg else f'''
<h3>Open access status by fuel sub-group</h3>
{_chart(c_oa_by_sg, "OA by sub-group")}
'''}
{"" if not c_types_by_sg else f'''
<h3>Document types by fuel sub-group</h3>
{_chart(c_types_by_sg, "Work types by sub-group")}
'''}
<h3>Open access rate over time</h3>
{_chart(c_oa_trend, "OA trend")}

<h2 id="sec-citation">{s + (6 if has_llm else 5)}. Citation impact</h2>
<p>{fwci_note}</p>
{_chart(c_fwci, "FWCI distribution")}
{_fwci_outlier_html(fwci_outliers)}
{"" if not c_fwci_ml else f'''
<h3>Citation impact: ML/AI users vs. non-users</h3>
<p>Do papers that employ machine learning methods attract more citations? Dashed line = world average FWCI 1.0.</p>
{_chart(c_fwci_ml, "FWCI by ML usage")}
'''}
{"" if not c_fwci_by_sg else f'''
<h3>FWCI distribution by fuel sub-group</h3>
<p>Box-plot comparison across fuel sub-groups. Dashed line = world average (FWCI 1.0).
Each distribution is independently clipped at its own IQR-based fence.</p>
{_chart(c_fwci_by_sg, "FWCI comparison by sub-group")}
'''}
{top_papers_global}
{"".join(f'<div class="subgroup-section"><h3>Top papers \u2014 {sg_name}</h3>{tbl}</div>' for sg_name, tbl in top_papers_by_sg)}

<h2 id="sec-inst">{s + (7 if has_llm else 6)}. Top institutions</h2>
{_chart(c_inst, "Top institutions")}
{"" if not c_inst_sg_heatmap else f'''
<h3>Institution specialisation by fuel sub-group</h3>
<p>Which institutions publish most in each fuel sub-group? Rows = top institutions, columns = sub-groups.</p>
{_chart(c_inst_sg_heatmap, "Institution sub-group heatmap")}
'''}
{_per_subgroup_html(c_inst_by_subgroup, "Institutions by fuel sub-group", two_col=True)}

<h2 id="sec-summary">{s + (8 if has_llm else 7)}. Summary</h2>
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
  <code>llm_ml_role</code>, <code>llm_ml_algorithm</code>, <code>llm_ml_strategy</code>,
  <code>llm_ml_data_source</code>, and <code>llm_models</code> columns for programmatic querying.
</div>

{_data_quality_note(records, llm_recs, n)}

<div class="footer">
  Generated by <code>generate_report.py</code> &middot; {now} &middot;
  DB: <code>{db_path.name}</code> ({n:,} records
  {f", {n_llm:,} LLM-classified" if has_llm else ""}).
</div>

{modal_total_papers}
{modal_oa_papers}
{modal_fwci_papers}
{modal_ml_papers if has_llm else ""}
{_MODAL_HTML}
{_MODAL_JS}

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
