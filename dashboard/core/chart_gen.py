"""chart_gen.py — Server-side chart generation for the dashboard API.

Architecture
------------
1. Theme        — palette, matplotlib rcParams, DPI
2. Country map  — ISO 3166-1 lookup
3. Subgroup     — config extraction, term→subgroup mapping
4. SQL helpers  — _run_sql, _apply_subset_cte, _fetch_sg_counts
5. Figure utils — _to_png, _empty_png
6. Renderers    — pure Figure factories (_vbar, _hbar, _pie,
                  _stacked_hbar, _cooccurrence_heatmap, _cooccurrence_3d)
7. Public API   — generate(db_path, chart_id, ...)
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np

from core.charts import ChartDefinition, get_chart_definition


# ── 1. Theme ──────────────────────────────────────────────────────────────────

PALETTE = [
    "#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED",
    "#0891B2", "#65A30D", "#C026D3", "#EA580C", "#4F46E5",
    "#0D9488", "#CA8A04", "#BE185D", "#6D28D9", "#0284C7",
]
BG_COLOR   = "#FAFAFA"
GRID_COLOR = "#E5E7EB"
TEXT_COLOR = "#1F2937"
MUTED      = "#6B7280"

plt.rcParams.update({
    "figure.facecolor":  BG_COLOR,
    "axes.facecolor":    "#FFFFFF",
    "axes.edgecolor":    GRID_COLOR,
    "axes.grid":         True,
    "axes.grid.axis":    "y",
    "grid.color":        GRID_COLOR,
    "grid.linewidth":    0.5,
    "text.color":        TEXT_COLOR,
    "axes.labelcolor":   TEXT_COLOR,
    "xtick.color":       MUTED,
    "ytick.color":       MUTED,
    "font.family":       "sans-serif",
    "font.size":         11,
    "axes.titlesize":    13,
    "axes.titleweight":  "bold",
    "axes.spines.top":   False,
    "axes.spines.right": False,
})

DPI = 180


# ── 2. Country map ────────────────────────────────────────────────────────────

_COUNTRY_NAMES: dict[str, str] = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AL": "Albania", "AM": "Armenia", "AO": "Angola", "AR": "Argentina",
    "AT": "Austria", "AU": "Australia", "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina", "BD": "Bangladesh", "BE": "Belgium",
    "BF": "Burkina Faso", "BG": "Bulgaria", "BH": "Bahrain",
    "BN": "Brunei", "BO": "Bolivia", "BR": "Brazil", "BY": "Belarus",
    "CA": "Canada", "CD": "DR Congo", "CH": "Switzerland", "CI": "Côte d'Ivoire",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CY": "Cyprus", "CZ": "Czech Republic",
    "DE": "Germany", "DK": "Denmark", "DO": "Dominican Republic", "DZ": "Algeria",
    "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt", "ES": "Spain",
    "ET": "Ethiopia", "FI": "Finland", "FJ": "Fiji", "FR": "France",
    "GB": "United Kingdom", "GE": "Georgia", "GH": "Ghana", "GR": "Greece",
    "GT": "Guatemala", "HK": "Hong Kong", "HN": "Honduras", "HR": "Croatia",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel",
    "IN": "India", "IQ": "Iraq", "IR": "Iran", "IS": "Iceland",
    "IT": "Italy", "JM": "Jamaica", "JO": "Jordan", "JP": "Japan",
    "KE": "Kenya", "KH": "Cambodia", "KR": "South Korea", "KW": "Kuwait",
    "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon", "LI": "Liechtenstein",
    "LK": "Sri Lanka", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia",
    "MA": "Morocco", "MD": "Moldova", "ME": "Montenegro", "MK": "North Macedonia",
    "ML": "Mali", "MM": "Myanmar", "MN": "Mongolia", "MT": "Malta",
    "MX": "Mexico", "MY": "Malaysia", "NG": "Nigeria", "NL": "Netherlands",
    "NO": "Norway", "NP": "Nepal", "NZ": "New Zealand", "OM": "Oman",
    "PA": "Panama", "PE": "Peru", "PG": "Papua New Guinea", "PH": "Philippines",
    "PK": "Pakistan", "PL": "Poland", "PT": "Portugal", "PY": "Paraguay",
    "QA": "Qatar", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
    "SA": "Saudi Arabia", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "SV": "El Salvador", "SY": "Syria", "TH": "Thailand",
    "TN": "Tunisia", "TR": "Turkey", "TW": "Taiwan", "TZ": "Tanzania",
    "UA": "Ukraine", "UG": "Uganda", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VE": "Venezuela", "VN": "Vietnam",
    "XK": "Kosovo", "YE": "Yemen", "ZA": "South Africa",
}


def _country_name(code: str) -> str:
    return _COUNTRY_NAMES.get(code.upper(), code.upper()) if code else code


# ── 3. Subgroup helpers ───────────────────────────────────────────────────────

def _extract_subgroups(config: dict, key: str) -> list[dict]:
    """Extract subgroup list for 'fuel' (index 0) or 'impact' (index 1)."""
    if not config:
        return [{"name": "Others", "terms": [], "is_others": True}]

    groups = config.get("groups", [])
    if not groups:
        terms = config.get("fuel_terms" if key == "fuel" else "impact_terms", [])
        return [{"name": "Others", "terms": terms, "is_others": True}]

    group_idx = 0 if key == "fuel" else 1
    if group_idx >= len(groups):
        return [{"name": "Others", "terms": [], "is_others": True}]

    group = groups[group_idx]
    subgroups = group.get("subgroups", [])
    if not subgroups:
        return [{"name": "Others", "terms": group.get("terms", []), "is_others": True}]

    return subgroups


def _build_term_sg_map(subgroups: list[dict]) -> tuple[dict[str, str], str]:
    """Return ({term_lower: subgroup_name}, others_name) from a subgroup list."""
    term_map: dict[str, str] = {}
    others_name = "Others"
    for sg in subgroups:
        if sg.get("is_others"):
            others_name = sg.get("name", "Others")
            continue
        for t in sg.get("terms", []):
            term_map[t.lower()] = sg["name"]
    return term_map, others_name


# ── 4. SQL helpers ────────────────────────────────────────────────────────────

def _run_sql(db_path: Path, sql: str) -> tuple[list[str], list[tuple]]:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(sql)
        return [d[0] for d in cur.description], cur.fetchall()


def _apply_subset_cte(sql: str, subset_sql: str) -> str:
    """Wrap *sql* in a CTE that shadows filtered_records with *subset_sql*."""
    return f"WITH _fr_subset AS (\n{subset_sql}\n)\n{sql.replace('filtered_records', '_fr_subset')}"


def _fetch_sg_counts(
    db_path: Path,
    subgroups: list[dict],
    subgroup_key: str,
    subset_sql: str | None,
) -> dict[str, int]:
    """
    Return {subgroup_name: distinct_paper_count} using the same dual-condition
    as the records viewer: matched_*_subgroups contains the name OR
    matched_*_terms contains any of the subgroup's terms.
    """
    sg_col    = f"matched_{subgroup_key}_subgroups"
    terms_col = f"matched_{subgroup_key}_terms"

    parts: list[str] = []
    for sg in subgroups:
        if sg.get("is_others"):
            continue
        safe_name  = sg["name"].replace("'", "''")
        sg_terms   = sg.get("terms", [])
        conditions = [
            f"EXISTS (SELECT 1 FROM json_each({sg_col}) WHERE value = '{safe_name}')"
        ]
        if sg_terms:
            quoted = ", ".join("'" + t.replace("'", "''") + "'" for t in sg_terms)
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each({terms_col}) WHERE value IN ({quoted}))"
            )
        where = " OR ".join(conditions)
        parts.append(
            f"SELECT '{safe_name}' AS sg, COUNT(DISTINCT openalex_id) AS cnt "
            f"FROM filtered_records WHERE {where}"
        )

    if parts:
        base_sql = " UNION ALL ".join(parts)
    else:
        base_sql = (
            f"SELECT value AS sg, COUNT(DISTINCT openalex_id) AS cnt "
            f"FROM filtered_records, json_each({sg_col}) "
            f"WHERE {sg_col} IS NOT NULL AND {sg_col} != '[]' GROUP BY value"
        )

    sql = _apply_subset_cte(base_sql, subset_sql) if subset_sql else base_sql
    try:
        _, rows = _run_sql(db_path, sql)
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception:
        return {}


# ── 5. Figure utilities ───────────────────────────────────────────────────────

def _to_png(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _empty_png(title: str) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.text(0.5, 0.5, "No data available", ha="center", va="center",
            fontsize=13, color="#9CA3AF", transform=ax.transAxes)
    ax.set_title(title)
    ax.axis("off")
    return _to_png(fig)


# ── 6. Renderers ──────────────────────────────────────────────────────────────

def _vbar(labels: list[str], values: list[int], title: str) -> plt.Figure:
    n = len(labels)
    fig, ax = plt.subplots(figsize=(max(8, n * 0.5), 5))
    ax.bar(range(n), values, color=PALETTE[0], width=0.72, zorder=3)
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    ax.set_ylabel("Papers")
    ax.set_title(title)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    if values:
        pk = max(range(n), key=lambda i: values[i])
        ax.annotate(str(values[pk]), (pk, values[pk]),
                    textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=9, color=PALETTE[0], fontweight="bold")
    fig.tight_layout()
    return fig


def _hbar(labels: list[str], values: list[int], title: str) -> plt.Figure:
    pairs  = sorted(zip(labels, values), key=lambda p: p[1])
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    fig, ax = plt.subplots(figsize=(9, max(3.5, len(labels) * 0.38)))
    bars = ax.barh(range(len(labels)), values, color=PALETTE[0], height=0.65, zorder=3)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Papers")
    ax.set_title(title)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    mx = max(values) if values else 1
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + mx * 0.012, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", fontsize=9, color=MUTED)
    fig.tight_layout()
    return fig


def _pie(labels: list[str], values: list[int], title: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(7, 4.5))
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
    ax.set_title(title)
    fig.tight_layout()
    return fig


def _stacked_hbar(
    labels: list[str],
    values: list[int],
    title: str,
    subgroups: list[dict],
    sg_counts: dict[str, int],
    terms_per_subgroup: int | None,
) -> plt.Figure | None:
    """
    Stacked horizontal bar chart: major bars = subgroups, segments = terms.
    Segments are shaded within the subgroup's palette colour.

    When *terms_per_subgroup* is set, each subgroup is capped at that many
    term segments; the remainder collapse into an "Others (N)" segment.
    The legend switches from subgroup names to per-term names in that mode.
    """
    import matplotlib.colors as mcolors

    # 1. Default terms_per_subgroup to 5
    if terms_per_subgroup is None:
        terms_per_subgroup = 5

    term_sg_map, others_name = _build_term_sg_map(subgroups)
    label_lower = [l.lower() for l in labels]

    # Group terms by subgroup (SQL order = count desc is preserved)
    sg_items: dict[str, list[tuple[str, int]]] = {}
    assigned: set[str] = set()

    for sg in subgroups:
        if sg.get("is_others"):
            continue
        name  = sg["name"]
        items: list[tuple[str, int]] = []
        for orig, val, low in zip(labels, values, label_lower):
            if term_sg_map.get(low) == name and low not in assigned:
                items.append((orig, val))
                assigned.add(low)
        if items:
            sg_items[name] = items

    others = [(orig, val) for orig, val, low in zip(labels, values, label_lower)
              if low not in assigned]
    if others:
        sg_items[others_name] = others

    if not sg_items:
        return None

    # Cap terms per subgroup and track the collapsed names for the legend
    other_terms_map: dict[str, list[str]] = {}
    capped: dict[str, list[tuple[str, int]]] = {}
    for sg_name, items in sg_items.items():
        if len(items) > terms_per_subgroup:
            rest = items[terms_per_subgroup:]
            other_terms_map[sg_name] = [t for t, _ in rest]
            capped[sg_name] = items[:terms_per_subgroup] + [
                (f"Others ({len(rest)})", sum(c for _, c in rest))
            ]
        else:
            capped[sg_name] = items
    sg_items = capped

    ordered     = sorted(sg_items.items(), key=lambda x: sum(c for _, c in x[1]))
    sg_names    = [name for name, _ in ordered]
    sg_base_hex = {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(sg_names)}
    max_total   = max((sum(c for _, c in items) for _, items in ordered), default=1)

    fig, ax = plt.subplots(figsize=(12, max(4.5, len(ordered) * 0.9 + 1.5)))
    legend_patches: list[mpatches.Patch] = []

    # Threshold: segments narrower than this fraction of max_total get arrow labels
    narrow_threshold = max_total * 0.07

    for sg_i, (sg_name, items) in enumerate(ordered):
        base_rgb = mcolors.hex2color(sg_base_hex[sg_name])
        n_terms  = len(items)
        left     = 0
        # Collect narrow-segment annotations to stagger vertically
        arrow_annotations: list[tuple[float, int, str, tuple]] = []

        for ti, (term, count) in enumerate(items):
            blend = (0.45 * ti / (n_terms - 1)) if n_terms > 1 else 0.0
            color = tuple(c * (1 - blend) + blend for c in base_rgb)
            ax.barh(sg_i, count, left=left, color=color, height=0.6,
                    zorder=3, edgecolor="white", linewidth=0.8)

            if count >= narrow_threshold:
                # Wide segment — place count text inside
                lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
                ax.text(left + count / 2, sg_i, f"{count:,}",
                        ha="center", va="center", fontsize=8, fontweight="bold",
                        color="white" if lum < 0.55 else TEXT_COLOR, clip_on=True)
            else:
                # Narrow segment — queue for arrow annotation
                arrow_annotations.append((left + count / 2, sg_i, f"{count:,}", color))

            if term.startswith("Others ("):
                rest_str = ", ".join(other_terms_map.get(sg_name, []))
                if len(rest_str) > 72:
                    rest_str = rest_str[:69] + "…"
                lbl = f"[{sg_name}] Other terms: {rest_str} ({count:,})"
            else:
                lbl = f"[{sg_name}] {term} ({count:,})"
            legend_patches.append(mpatches.Patch(color=mcolors.to_hex(color), label=lbl))

            left += count

        # Draw staggered arrow annotations for narrow segments
        for ai, (ax_x, ax_y, txt, clr) in enumerate(arrow_annotations):
            # Alternate above/below the bar to avoid overlap
            y_offset = 22 + ai * 14 if ai % 2 == 0 else -(22 + ai * 14)
            ax.annotate(
                txt, xy=(ax_x, ax_y),
                xytext=(0, y_offset), textcoords="offset points",
                fontsize=7, fontweight="bold", color=MUTED,
                ha="center", va="bottom" if y_offset > 0 else "top",
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=0.8),
                zorder=5,
            )

        # Use the accurate distinct-paper count when available
        ax.text(left + max_total * 0.015, sg_i, f"{sg_counts.get(sg_name, left):,}",
                va="center", fontsize=9.5, fontweight="bold", color=MUTED)

    ax.set_yticks(range(len(ordered)))
    ax.set_yticklabels(sg_names, fontsize=10, fontweight="bold")
    ax.set_xlabel("Papers")
    ax.set_title(title, fontsize=14, pad=12)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_xlim(0, max_total * 1.15)
    ax.set_ylim(-0.6, len(ordered) - 0.4)

    if legend_patches:
        n_cols = max(1, len(legend_patches) // 6)
        ax.legend(handles=legend_patches, loc="upper center",
                  bbox_to_anchor=(0.5, -0.08), fontsize=7.5, framealpha=0.9,
                  ncol=n_cols, columnspacing=1.2,
                  title="Segments", title_fontsize=8.5, borderaxespad=0)

    fig.tight_layout(rect=[0, 0.05, 1, 1])
    return fig


def _cooccurrence_3d_bar(
    rows: list[tuple],
    title: str,
) -> plt.Figure | None:
    """
    3D bar chart: fuel subgroup (X) × impact subgroup (Y), bar height = distinct papers.
    *rows* is (openalex_id, fuel_subgroup, impact_subgroup) — already at subgroup level.
    """
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    import matplotlib.colors as mcolors

    # Count distinct papers per (fuel_subgroup, impact_subgroup) cell
    cell_ids: dict[tuple[str, str], set[str]] = {}
    for oid, fuel_sg, impact_sg in rows:
        cell_ids.setdefault((str(fuel_sg), str(impact_sg)), set()).add(str(oid or ""))

    matrix = {k: len(v) for k, v in cell_ids.items()}
    if not matrix:
        return None

    # Subgroup names ordered by total count (descending)
    fuel_totals: dict[str, int] = {}
    impact_totals: dict[str, int] = {}
    for (fsg, isg), cnt in matrix.items():
        fuel_totals[fsg] = fuel_totals.get(fsg, 0) + cnt
        impact_totals[isg] = impact_totals.get(isg, 0) + cnt

    fuel_names = sorted(fuel_totals, key=lambda k: -fuel_totals[k])
    impact_names = sorted(impact_totals, key=lambda k: -impact_totals[k])
    if not fuel_names or not impact_names:
        return None

    nf, ni = len(fuel_names), len(impact_names)
    data = np.zeros((ni, nf), dtype=float)
    for (fsg, isg), count in matrix.items():
        data[impact_names.index(isg), fuel_names.index(fsg)] = count

    vmax = data.max() or 1

    # ── Figure & 3-D axis ────────────────────────────────────────────────
    fig = plt.figure(
        figsize=(max(11, nf * 2.4), max(8, ni * 1.6)),
        facecolor=BG_COLOR,
    )
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    ax.set_facecolor("#FFFFFF")

    # Transparent panes + subtle grid
    for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
        pane.fill = False
        pane.set_edgecolor(GRID_COLOR)
    ax.zaxis._axinfo["grid"].update(linewidth=0.4, color=GRID_COLOR)

    # ── Bars (draw back-to-front for correct occlusion) ────────────────
    bar_w, bar_d = 0.52, 0.52
    # Camera direction vector for azim=-45, elev=25
    import math
    azim_rad = math.radians(-45)
    cam_dx, cam_dy = math.cos(azim_rad), math.sin(azim_rad)

    # Collect all bars, then sort by distance from camera (farthest first)
    bars_to_draw: list[tuple[float, int, int]] = []
    for fi in range(nf):
        for ii in range(ni):
            if data[ii, fi] > 0:
                dist = fi * cam_dx + ii * cam_dy
                bars_to_draw.append((dist, fi, ii))
    bars_to_draw.sort(key=lambda t: t[0])  # farthest (most negative) first

    zorder_counter = 0
    for _, fi, ii in bars_to_draw:
        val = data[ii, fi]
        base_rgb = mcolors.hex2color(PALETTE[fi % len(PALETTE)])
        # Slight intensity gradient by value for depth cue
        t = 0.45 + 0.55 * (val / vmax)
        color = tuple(c * t + (1 - t) for c in base_rgb)
        bar_col = ax.bar3d(
            fi - bar_w / 2, ii - bar_d / 2, 0,
            bar_w, bar_d, val,
            color=color, edgecolor="white", linewidth=0.35,
            alpha=0.90,
        )
        bar_col.set_zorder(zorder_counter)
        zorder_counter += 1
        txt = ax.text(
            fi, ii, val + vmax * 0.018,
            f"{int(val):,}",
            ha="center", va="bottom",
            fontsize=7.5, fontweight="bold", color=TEXT_COLOR,
        )
        txt.set_zorder(zorder_counter)
        zorder_counter += 1

    # ── Axes labels & ticks ──────────────────────────────────────────────
    ax.set_xticks(range(nf))
    ax.set_xticklabels(fuel_names, fontsize=8, rotation=30, ha="right")
    ax.set_yticks(range(ni))
    ax.set_yticklabels(impact_names, fontsize=8)
    ax.set_xlabel("Fuel Subgroup", fontsize=10, labelpad=18, fontweight="bold")
    ax.set_ylabel("Impact Subgroup", fontsize=10, labelpad=18, fontweight="bold")
    ax.set_zlabel("Distinct Papers", fontsize=10, labelpad=12, fontweight="bold")
    ax.zaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"{int(x):,}")
    )
    ax.tick_params(axis="z", labelsize=8)

    ax.set_title(title, fontsize=14, fontweight="bold", pad=22)

    # ── Legend for fuel subgroups ─────────────────────────────────────────
    legend_patches = [
        mpatches.Patch(color=PALETTE[i % len(PALETTE)], label=n)
        for i, n in enumerate(fuel_names)
    ]
    ax.legend(
        handles=legend_patches, loc="upper left", fontsize=8,
        title="Fuel Subgroup", title_fontsize=9,
        framealpha=0.92, edgecolor=GRID_COLOR,
    )

    ax.view_init(elev=25, azim=-45)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return fig


def _subgroup_pie(
    labels: list[str],
    values: list[int],
    title: str,
) -> plt.Figure | None:
    """
    Donut chart showing distinct paper counts per subgroup.
    SQL provides pre-aggregated subgroup-level data (COUNT DISTINCT).
    """
    if not labels or not any(values):
        return None

    colors = [PALETTE[i % len(PALETTE)] for i in range(len(labels))]

    fig, ax = plt.subplots(figsize=(8, 5))
    wedges, _, autotexts = ax.pie(
        values, labels=None, autopct="%1.1f%%",
        colors=colors, startangle=90, pctdistance=0.78,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white", "width": 0.5},
    )
    for t in autotexts:
        t.set_fontsize(9)
    ax.text(0, 0, f"{sum(values):,}\npapers", ha="center", va="center",
            fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax.legend(wedges, [f"{l}  ({v:,})" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=9)
    ax.set_title(title, fontsize=13, fontweight="bold")
    fig.tight_layout()
    return fig


# Keep old heatmap for backward compat (unused in main report now)
def _cooccurrence_heatmap(
    rows: list[tuple],
    title: str,
    fuel_sgs: list[dict],
    impact_sgs: list[dict],
) -> plt.Figure | None:
    """
    2D heatmap: fuel subgroup (X) × impact subgroup (Y).
    *rows* is (openalex_id, fuel_term, impact_term) — one distinct row per paper/term pair.
    Counts distinct papers per cell to avoid double-counting multi-term papers.
    """
    fuel_map,   fuel_others   = _build_term_sg_map(fuel_sgs)
    impact_map, impact_others = _build_term_sg_map(impact_sgs)

    # Collect distinct openalex_ids per (fuel_sg, impact_sg) cell
    cell_ids: dict[tuple[str, str], set[str]] = {}
    for oid, fuel_term, impact_term in rows:
        fsig = fuel_map.get(str(fuel_term or "").lower(), fuel_others)
        isig = impact_map.get(str(impact_term or "").lower(), impact_others)
        cell_ids.setdefault((fsig, isig), set()).add(str(oid or ""))

    matrix = {k: len(v) for k, v in cell_ids.items()}
    if not matrix:
        return None

    def _axis_names(sgs: list[dict], others: str, dim: int) -> list[str]:
        configured = [sg["name"] for sg in sgs if not sg.get("is_others")]
        extra      = sorted(k[dim] for k in matrix if k[dim] not in configured and k[dim] != others)
        has_others = any(k[dim] == others for k in matrix)
        names      = configured + extra + ([others] if has_others else [])
        return [n for n in names if any(k[dim] == n for k in matrix)]

    fuel_names   = _axis_names(fuel_sgs,   fuel_others,   0)
    impact_names = _axis_names(impact_sgs, impact_others, 1)
    if not fuel_names or not impact_names:
        return None

    data = np.zeros((len(impact_names), len(fuel_names)), dtype=float)
    for (fsig, isig), count in matrix.items():
        if fsig in fuel_names and isig in impact_names:
            data[impact_names.index(isig), fuel_names.index(fsig)] = count

    fig, ax = plt.subplots(figsize=(max(7, len(fuel_names) * 1.4), max(4, len(impact_names) * 0.9)))
    im = ax.imshow(data, cmap="YlOrRd", aspect="auto", vmin=0)
    plt.colorbar(im, ax=ax, label="Papers", shrink=0.8)
    ax.set_xticks(range(len(fuel_names)))
    ax.set_xticklabels(fuel_names, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(len(impact_names)))
    ax.set_yticklabels(impact_names, fontsize=9)
    ax.set_xlabel("Fuel Subgroup", fontsize=10)
    ax.set_ylabel("Impact Subgroup", fontsize=10)
    ax.set_title(title)

    vmax = data.max() or 1
    for i in range(len(impact_names)):
        for j in range(len(fuel_names)):
            val = int(data[i, j])
            if val > 0:
                ax.text(j, i, f"{val:,}", ha="center", va="center",
                        fontsize=8, fontweight="bold",
                        color="white" if val > vmax * 0.55 else TEXT_COLOR)

    fig.tight_layout()
    return fig


def _cooccurrence_3d(
    rows: list[tuple],
    title: str,
) -> plt.Figure | None:
    """
    3D bubble chart: fuel subgroup (X) × impact subgroup (Y) × model type (Z).
    Bubble size = distinct paper count, colour = model type.
    *rows* is (openalex_id, fuel_subgroup, impact_subgroup, model_type, model_name).
    """
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    import matplotlib.colors as mcolors

    # Aggregate distinct papers per (fuel_sg, impact_sg, model_type)
    cell_ids: dict[tuple[str, str, str], set[str]] = {}
    for oid, fsg, isg, mtype, mname in rows:
        mtype_clean = str(mtype or "Other").strip()
        if not mtype_clean:
            mtype_clean = "Other"
        key = (str(fsg), str(isg), mtype_clean)
        cell_ids.setdefault(key, set()).add(str(oid or ""))

    agg = {k: len(v) for k, v in cell_ids.items()}
    if not agg:
        return None

    # Axis labels sorted by total count
    fuel_totals: dict[str, int] = {}
    impact_totals: dict[str, int] = {}
    type_totals: dict[str, int] = {}
    for (fsg, isg, mt), cnt in agg.items():
        fuel_totals[fsg] = fuel_totals.get(fsg, 0) + cnt
        impact_totals[isg] = impact_totals.get(isg, 0) + cnt
        type_totals[mt] = type_totals.get(mt, 0) + cnt

    fuel_names = sorted(fuel_totals, key=lambda k: -fuel_totals[k])
    impact_names = sorted(impact_totals, key=lambda k: -impact_totals[k])
    type_names = sorted(type_totals, key=lambda k: -type_totals[k])[:10]

    if not fuel_names or not impact_names or not type_names:
        return None

    nf, ni, nt = len(fuel_names), len(impact_names), len(type_names)
    type_colors = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(type_names)}
    max_cnt = max(agg.values()) or 1

    fig = plt.figure(
        figsize=(max(12, nf * 2), max(8, nt * 1.2)),
        facecolor=BG_COLOR,
    )
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("#FFFFFF")

    for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
        pane.fill = False
        pane.set_edgecolor(GRID_COLOR)

    for (fsg, isg, mt), cnt in agg.items():
        if mt not in type_names:
            continue
        ax.scatter(
            fuel_names.index(fsg),
            impact_names.index(isg),
            type_names.index(mt),
            c=[type_colors[mt]],
            s=max(40, min(cnt / max_cnt * 800, 1200)),
            alpha=0.78, depthshade=True,
            edgecolors="white", linewidths=0.5,
        )
        if cnt >= max_cnt * 0.08:
            ax.text(
                fuel_names.index(fsg), impact_names.index(isg),
                type_names.index(mt) + 0.15,
                f"{cnt:,}", ha="center", va="bottom",
                fontsize=6.5, fontweight="bold", color=TEXT_COLOR,
            )

    ax.set_xticks(range(nf))
    ax.set_xticklabels(fuel_names, fontsize=7.5, rotation=25, ha="right")
    ax.set_yticks(range(ni))
    ax.set_yticklabels(impact_names, fontsize=7.5)
    ax.set_zticks(range(nt))
    ax.set_zticklabels(type_names, fontsize=7.5)
    ax.set_xlabel("Fuel Subgroup", fontsize=10, labelpad=16, fontweight="bold")
    ax.set_ylabel("Impact Subgroup", fontsize=10, labelpad=16, fontweight="bold")
    ax.set_zlabel("Model Type", fontsize=10, labelpad=14, fontweight="bold")
    ax.set_title(title, fontsize=13, fontweight="bold", pad=18)

    legend_patches = [
        mpatches.Patch(color=type_colors[t], label=f"{t} ({type_totals[t]:,})")
        for t in type_names
    ]
    ax.legend(
        handles=legend_patches, loc="upper left", fontsize=7.5,
        title="Model Type", title_fontsize=9,
        framealpha=0.9, edgecolor=GRID_COLOR,
    )
    fig.text(0.02, 0.02, "Bubble size \u221d paper count", fontsize=8, color=MUTED)
    ax.view_init(elev=22, azim=-40)
    fig.tight_layout(rect=[0, 0.03, 1, 0.96])
    return fig


# ── 7. Public API ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _RenderContext:
    db_path: Path
    spec: ChartDefinition
    sql: str
    title: str
    pipeline_config: dict
    subset_sql: str | None
    terms_per_subgroup: int | None


def _render_cooccurrence(ctx: _RenderContext, *, is_3d: bool) -> plt.Figure | None:
    try:
        _, rows = _run_sql(ctx.db_path, ctx.sql)
    except Exception:
        return None
    if not rows:
        return None

    if is_3d:
        return _cooccurrence_3d(rows, ctx.title)

    fuel_sgs = _extract_subgroups(ctx.pipeline_config, "fuel")
    impact_sgs = _extract_subgroups(ctx.pipeline_config, "impact")
    return _cooccurrence_heatmap(rows, ctx.title, fuel_sgs, impact_sgs)


def _render_cooccurrence_3d_bar(ctx: _RenderContext) -> plt.Figure | None:
    try:
        _, rows = _run_sql(ctx.db_path, ctx.sql)
    except Exception:
        return None
    if not rows:
        return None
    return _cooccurrence_3d_bar(rows, ctx.title)


def _render_subgroup_pie(ctx: _RenderContext) -> plt.Figure | None:
    try:
        cols, rows = _run_sql(ctx.db_path, ctx.sql)
    except Exception:
        return None
    if not rows:
        return None

    xi = cols.index(ctx.spec.x_key) if ctx.spec.x_key in cols else 0
    yi = cols.index(ctx.spec.y_key) if ctx.spec.y_key in cols else 1
    labels = [str(r[xi] if r[xi] is not None else "") for r in rows]
    values = [int(r[yi] if r[yi] is not None else 0) for r in rows]

    if not any(values):
        return None
    return _subgroup_pie(labels, values, ctx.title)


# ── Generic annotated heatmap renderer ────────────────────────────────────────

def _generic_heatmap(
    rows: list[tuple],
    title: str,
    x_col: str,
    y_col: str,
) -> plt.Figure | None:
    """
    Annotated heatmap from (x_label, y_label, count) rows.
    X on the horizontal axis, Y on the vertical axis.
    Cells show distinct paper counts; zero-cells are left blank.
    """
    import matplotlib.colors as mcolors

    # Build matrix
    counts: dict[tuple[str, str], int] = {}
    x_totals: dict[str, int] = {}
    y_totals: dict[str, int] = {}
    for row in rows:
        x_val, y_val, cnt = str(row[0]), str(row[1]), int(row[2] or 0)
        if cnt <= 0:
            continue
        counts[(x_val, y_val)] = counts.get((x_val, y_val), 0) + cnt
        x_totals[x_val] = x_totals.get(x_val, 0) + cnt
        y_totals[y_val] = y_totals.get(y_val, 0) + cnt

    if not counts:
        return None

    # Sort axes by total count (highest first)
    x_names = sorted(x_totals, key=lambda k: -x_totals[k])
    y_names = sorted(y_totals, key=lambda k: -y_totals[k])

    nx, ny = len(x_names), len(y_names)
    data = np.zeros((ny, nx), dtype=float)
    for (xv, yv), cnt in counts.items():
        if xv in x_names and yv in y_names:
            data[y_names.index(yv), x_names.index(xv)] = cnt

    vmax = data.max() or 1

    # ── Prettify labels (underscores → spaces, title case) ───────────
    def _pretty(s: str) -> str:
        return s.replace("_", " ").title()

    x_labels = [_pretty(n) for n in x_names]
    y_labels = [_pretty(n) for n in y_names]

    # ── Figure with controlled size ──────────────────────────────────
    cell_w, cell_h = 1.1, 0.55
    fig_w = max(8, nx * cell_w + 3)
    fig_h = max(4, ny * cell_h + 2.5)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Custom colormap: white → palette blue
    base_blue = mcolors.hex2color(PALETTE[0])
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "hm", ["#FFFFFF", mcolors.rgb2hex(base_blue)], N=256
    )

    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=0, vmax=vmax)
    cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label("Distinct Papers", fontsize=9)
    cbar.ax.tick_params(labelsize=8)

    ax.set_xticks(range(nx))
    ax.set_xticklabels(x_labels, rotation=40, ha="right", fontsize=8)
    ax.set_yticks(range(ny))
    ax.set_yticklabels(y_labels, fontsize=8)

    # Cell annotations
    for i in range(ny):
        for j in range(nx):
            val = int(data[i, j])
            if val > 0:
                txt_color = "white" if val > vmax * 0.55 else TEXT_COLOR
                ax.text(j, i, f"{val:,}", ha="center", va="center",
                        fontsize=8, fontweight="bold", color=txt_color)

    ax.set_xlabel(_pretty(x_col), fontsize=10, fontweight="bold", labelpad=8)
    ax.set_ylabel(_pretty(y_col), fontsize=10, fontweight="bold", labelpad=8)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=12)

    fig.tight_layout()
    return fig


def _render_heatmap(ctx: _RenderContext) -> plt.Figure | None:
    try:
        cols, rows = _run_sql(ctx.db_path, ctx.sql)
    except Exception:
        return None
    if not rows:
        return None
    return _generic_heatmap(rows, ctx.title, ctx.spec.x_key, ctx.spec.y_key)


def _render_series(ctx: _RenderContext) -> plt.Figure | None:
    try:
        cols, rows = _run_sql(ctx.db_path, ctx.sql)
    except Exception:
        return None
    if not rows:
        return None

    xi = cols.index(ctx.spec.x_key) if ctx.spec.x_key in cols else 0
    yi = cols.index(ctx.spec.y_key) if ctx.spec.y_key in cols else 1
    labels = [str(r[xi] if r[xi] is not None else "") for r in rows]
    values = [int(r[yi] if r[yi] is not None else 0) for r in rows]

    if not any(values):
        return None
    if ctx.spec.x_key == "country":
        labels = [_country_name(l) for l in labels]

    if ctx.spec.renderer == "stacked_hbar":
        subgroup_key = ctx.spec.subgroup_key or "fuel"
        subgroups = _extract_subgroups(ctx.pipeline_config, subgroup_key)
        sg_counts = _fetch_sg_counts(ctx.db_path, subgroups, subgroup_key, ctx.subset_sql)
        return _stacked_hbar(labels, values, ctx.title, subgroups, sg_counts, ctx.terms_per_subgroup)
    if ctx.spec.renderer == "hbar":
        return _hbar(labels, values, ctx.title)
    if ctx.spec.renderer == "pie":
        return _pie(labels, values, ctx.title)
    return _vbar(labels, values, ctx.title)


_RENDERERS: dict[str, Callable[[_RenderContext], plt.Figure | None]] = {
    "bar": _render_series,
    "hbar": _render_series,
    "pie": _render_series,
    "stacked_hbar": _render_series,
    "subgroup_pie": _render_subgroup_pie,
    "heatmap": _render_heatmap,
    "cooccurrence_3d_bar": _render_cooccurrence_3d_bar,
    "cooccurrence": lambda ctx: _render_cooccurrence(ctx, is_3d=False),
    "cooccurrence_3d": lambda ctx: _render_cooccurrence(ctx, is_3d=True),
}

# ── Chart cache ───────────────────────────────────────────────────────────────

_CACHE_MAX   = 128          # max cached chart PNGs
_CACHE_TTL   = 300          # seconds before a cached entry is stale
_cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_cache_lock  = threading.Lock()


def _cache_key(
    db_path: Path, chart_id: str, pipeline_config: dict | None,
    top_n: int, cutoff_year: int, subset_sql: str | None,
    terms_per_subgroup: int | None,
) -> str:
    """Deterministic hash of all parameters that affect the chart output.

    Also includes the DB file mtime so the cache auto-invalidates when
    the underlying data changes.
    """
    try:
        db_mtime = str(db_path.stat().st_mtime_ns)
    except OSError:
        db_mtime = "0"
    raw = "|".join([
        str(db_path), chart_id, json.dumps(pipeline_config, sort_keys=True, default=str),
        str(top_n), str(cutoff_year), str(subset_sql), str(terms_per_subgroup),
        db_mtime,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def invalidate_chart_cache() -> None:
    """Clear chart cache (e.g. after a pipeline run finishes)."""
    with _cache_lock:
        _cache.clear()


def generate(
    db_path: Path,
    chart_id: str,
    pipeline_config: dict | None = None,
    *,
    top_n: int = 20,
    cutoff_year: int = 2000,
    subset_sql: str | None = None,
    terms_per_subgroup: int | None = None,
) -> bytes:
    """
    Render *chart_id* as a PNG and return the bytes.

    Results are cached in-memory keyed by a hash of all parameters plus
    the DB file mtime.  Cache entries expire after ``_CACHE_TTL`` seconds.
    """
    key = _cache_key(db_path, chart_id, pipeline_config,
                     top_n, cutoff_year, subset_sql, terms_per_subgroup)

    with _cache_lock:
        if key in _cache:
            ts, png = _cache[key]
            if time.monotonic() - ts < _CACHE_TTL:
                _cache.move_to_end(key)
                return png
            else:
                del _cache[key]
    spec = get_chart_definition(chart_id)
    if spec is None:
        return _empty_png(chart_id)

    effective_top_n = spec.default_top_n if spec.default_top_n is not None else top_n
    sql = spec.format_sql(top_n=effective_top_n, cutoff_year=cutoff_year)
    title = spec.title
    pcfg  = pipeline_config or {}

    if subset_sql:
        sql = _apply_subset_cte(sql, subset_sql)

    renderer = _RENDERERS.get(spec.renderer)
    if renderer is None:
        return _empty_png(title)

    ctx = _RenderContext(
        db_path=db_path,
        spec=spec,
        sql=sql,
        title=title,
        pipeline_config=pcfg,
        subset_sql=subset_sql,
        terms_per_subgroup=terms_per_subgroup,
    )

    try:
        fig = renderer(ctx)
        png = _to_png(fig) if fig else _empty_png(title)
    except Exception:
        png = _empty_png(title)

    with _cache_lock:
        _cache[key] = (time.monotonic(), png)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)

    return png
