"""chart_gen.py — Server-side matplotlib chart generation for the dashboard API."""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np

# ── Style (matches generate_report.py) ────────────────────────────────────────

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
    "figure.facecolor": BG_COLOR,
    "axes.facecolor": "#FFFFFF",
    "axes.edgecolor": GRID_COLOR,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": GRID_COLOR,
    "grid.linewidth": 0.5,
    "text.color": TEXT_COLOR,
    "axes.labelcolor": TEXT_COLOR,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
})

DPI = 180   # high-resolution output


# ── ISO 3166-1 alpha-2 → full country name ────────────────────────────────────

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
    """Translate an ISO 3166-1 alpha-2 code to a full country name."""
    if not code:
        return code
    return _COUNTRY_NAMES.get(code.upper(), code.upper())


# ── Subgroup config helpers ───────────────────────────────────────────────────

def _extract_subgroups(config: dict, key: str) -> list[dict]:
    """
    Extract subgroup list for 'fuel' (index 0) or 'impact' (index 1) from
    the pipeline config dict.  Always returns at least one subgroup.
    """
    if not config:
        return [{"name": "Others", "terms": [], "is_others": True}]

    groups = config.get("groups", [])
    if not groups:
        # Legacy flat format
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


def _build_term_sg_map(subgroups: list[dict]) -> dict[str, str]:
    """Return {term_lower: subgroup_name} from a subgroup list."""
    m: dict[str, str] = {}
    others_name = "Others"
    for sg in subgroups:
        if sg.get("is_others"):
            others_name = sg.get("name", "Others")
            continue
        for t in sg.get("terms", []):
            m[t.lower()] = sg["name"]
    return m, others_name


# ── Internal helpers ──────────────────────────────────────────────────────────

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


def _run_sql(db_path: Path, sql: str) -> tuple[list[str], list[tuple]]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()
    return cols, rows


# ── Chart type renderers ──────────────────────────────────────────────────────

def _vbar(labels: list, values: list, title: str, color: str = PALETTE[0]) -> plt.Figure:
    n = len(labels)
    fig, ax = plt.subplots(figsize=(max(8, n * 0.5), 5))
    ax.bar(range(n), values, color=color, width=0.72, zorder=3)
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    ax.set_ylabel("Papers")
    ax.set_title(title)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    if values:
        pk = max(range(n), key=lambda i: values[i])
        ax.annotate(str(values[pk]), (pk, values[pk]),
                    textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=9, color=color, fontweight="bold")
    fig.tight_layout()
    return fig


def _hbar(labels: list, values: list, title: str, color: str = PALETTE[0]) -> plt.Figure:
    pairs = sorted(zip(labels, values), key=lambda p: p[1])
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    h = max(3.5, len(labels) * 0.38)
    fig, ax = plt.subplots(figsize=(9, h))
    bars = ax.barh(range(len(labels)), values, color=color, height=0.65, zorder=3)
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


def _pie(labels: list, values: list, title: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    wedges, _, autotexts = ax.pie(
        values, labels=None, autopct="%1.1f%%",
        colors=PALETTE[:len(labels)], startangle=90, pctdistance=0.78,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white", "width": 0.5},
    )
    for t in autotexts:
        t.set_fontsize(9)
    total = sum(values)
    ax.text(0, 0, f"{total:,}\npapers", ha="center", va="center",
            fontsize=11, fontweight="bold", color=TEXT_COLOR)
    ax.legend(wedges, [f"{l}  ({v:,})" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=9)
    ax.set_title(title)
    fig.tight_layout()
    return fig


def _stacked_hbar(
    labels: list, values: list, title: str, subgroups: list[dict],
    sg_counts: dict | None = None,
    terms_per_subgroup: int | None = None,
) -> plt.Figure:
    """
    Stacked horizontal bar chart.
    Major bars = subgroups (sorted by total count descending).
    Stacked segments = individual terms, shaded within each subgroup's base PALETTE color.
    Count numbers are shown inside segments (not term names).

    When terms_per_subgroup is set:
      - Each subgroup shows at most that many individual term segments.
      - Remaining terms collapse into an "Others (N)" segment.
      - Legend lists individual term names per segment and enumerates "Other" terms.
    When not set: legend shows only subgroup names (original behaviour).
    """
    import matplotlib.colors as mcolors

    term_sg_map, others_name = _build_term_sg_map(subgroups)
    label_lower = [l.lower() for l in labels]

    # Group terms by subgroup, preserving data-order (already sorted by count desc)
    sg_items: dict[str, list[tuple[str, int]]] = {}
    assigned: set[str] = set()

    for sg in subgroups:
        if sg.get("is_others"):
            continue
        name = sg["name"]
        items = []
        for orig, val, low in zip(labels, values, label_lower):
            if low in term_sg_map and term_sg_map[low] == name and low not in assigned:
                items.append((orig, val))
                assigned.add(low)
        if items:
            sg_items[name] = items

    others_items = [
        (orig, val) for orig, val, low in zip(labels, values, label_lower)
        if low not in assigned
    ]
    if others_items:
        sg_items[others_name] = others_items

    if not sg_items:
        return None

    # Apply terms-per-subgroup cap
    # other_terms_map[sg_name] = list of term names that were collapsed into "Others"
    other_terms_map: dict[str, list[str]] = {}
    if terms_per_subgroup is not None and terms_per_subgroup > 0:
        capped: dict[str, list[tuple[str, int]]] = {}
        for sg_name, items in sg_items.items():
            if len(items) > terms_per_subgroup:
                top  = items[:terms_per_subgroup]
                rest = items[terms_per_subgroup:]
                rest_count = sum(c for _, c in rest)
                other_terms_map[sg_name] = [t for t, _ in rest]
                capped[sg_name] = top + [(f"Others ({len(rest)})", rest_count)]
            else:
                capped[sg_name] = items
        sg_items = capped

    # Sort subgroups by total count descending (largest bar at top for hbar)
    ordered = sorted(sg_items.items(), key=lambda x: sum(c for _, c in x[1]))

    # Each subgroup gets a distinct base PALETTE color
    sg_names = [name for name, _ in ordered]
    sg_base_hex = {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(sg_names)}

    n_sg = len(ordered)
    h = max(4, n_sg * 0.7 + 1.5)
    fig, ax = plt.subplots(figsize=(10, h))

    legend_patches = []
    totals = [sum(c for _, c in items) for _, items in ordered]
    max_total = max(totals) if totals else 1

    for sg_i, (sg_name, items) in enumerate(ordered):
        n_terms = len(items)
        base_rgb = mcolors.hex2color(sg_base_hex[sg_name])
        left = 0
        for ti, (term, count) in enumerate(items):
            # Shade range: ti=0 → base color; ti=n-1 → lightened 45% max (avoid near-white)
            blend = (0.45 * ti / (n_terms - 1)) if n_terms > 1 else 0.0
            color = tuple(c * (1 - blend) + blend for c in base_rgb)

            ax.barh(sg_i, count, left=left, color=color, height=0.65, zorder=3,
                    edgecolor="white", linewidth=0.5)

            # Show count number inside segment if wide enough (>= 4% of max bar)
            if count >= max_total * 0.04:
                luminance = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
                text_color = "white" if luminance < 0.55 else TEXT_COLOR
                ax.text(left + count / 2, sg_i, f"{count:,}",
                        ha="center", va="center", fontsize=7.5, color=text_color,
                        fontweight="bold", clip_on=True)

            # Per-term legend entries when terms_per_subgroup is active
            if terms_per_subgroup is not None:
                is_others_seg = term.startswith("Others (")
                if is_others_seg:
                    rest = other_terms_map.get(sg_name, [])
                    rest_str = ", ".join(rest)
                    if len(rest_str) > 72:
                        rest_str = rest_str[:69] + "…"
                    lbl = f"[{sg_name}] Other terms: {rest_str}"
                else:
                    lbl = f"[{sg_name}] {term}"
                legend_patches.append(mpatches.Patch(color=mcolors.to_hex(color), label=lbl))

            left += count

        # Total label: use actual distinct-record count from sg_counts when available,
        # falling back to the summed term count (which double-counts multi-term records)
        display_total = (sg_counts or {}).get(sg_name, left)
        ax.text(left + max_total * 0.015, sg_i, f"{display_total:,}",
                va="center", fontsize=9, color=MUTED)

        # Subgroup-level legend entry when not in per-term mode
        if terms_per_subgroup is None:
            legend_patches.append(mpatches.Patch(color=sg_base_hex[sg_name], label=sg_name))

    ax.set_yticks(range(n_sg))
    ax.set_yticklabels(sg_names, fontsize=10)
    ax.set_xlabel("Papers")
    ax.set_title(title)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    # Extra 18% headroom so the total labels never clip (they sit at bar_end + 1.5%)
    ax.set_xlim(0, max_total * 1.18)

    if legend_patches:
        if terms_per_subgroup is not None:
            # Legend outside the axes to avoid overlap with (potentially many) entries
            ax.legend(handles=legend_patches, loc="upper left",
                      bbox_to_anchor=(1.02, 1), fontsize=7.5, framealpha=0.8,
                      title="Terms per bar", title_fontsize=8, borderaxespad=0)
        else:
            ax.legend(handles=legend_patches, loc="lower right",
                      fontsize=8, framealpha=0.8,
                      title="Subgroup", title_fontsize=9)

    fig.tight_layout()
    return fig


def _cooccurrence_heatmap(
    rows: list[tuple], title: str,
    fuel_sgs: list[dict], impact_sgs: list[dict],
) -> plt.Figure:
    """
    2D heatmap: fuel subgroups (X) × impact subgroups (Y).
    rows: (openalex_id, fuel_term, impact_term) — DISTINCT rows, one per paper×term pair.
    Counts distinct papers per subgroup cell to avoid double-counting multi-term papers.
    """
    fuel_map, fuel_others = _build_term_sg_map(fuel_sgs)
    impact_map, impact_others = _build_term_sg_map(impact_sgs)

    # Aggregate to subgroup level: collect distinct openalex_ids per cell
    matrix_sets: dict[tuple[str, str], set] = {}
    for row in rows:
        oid         = str(row[0] or "")
        fuel_term   = str(row[1] or "")
        impact_term = str(row[2] or "")
        fsig = fuel_map.get(fuel_term.lower(), fuel_others)
        isig = impact_map.get(impact_term.lower(), impact_others)
        key = (fsig, isig)
        if key not in matrix_sets:
            matrix_sets[key] = set()
        matrix_sets[key].add(oid)

    matrix: dict[tuple[str, str], int] = {k: len(v) for k, v in matrix_sets.items()}

    if not matrix:
        return None

    # Ordered axis labels — config order, then fill from data
    def _sg_names(sgs: list[dict], others: str) -> list[str]:
        names = [sg["name"] for sg in sgs if not sg.get("is_others")]
        extra = {k for k in {r[0] for r in matrix} | {r[1] for r in matrix}
                 if k not in names and k != others}
        return names + sorted(extra) + ([others] if any(k == others for k in matrix) else [])

    fuel_names   = _sg_names(fuel_sgs,   fuel_others)
    impact_names = _sg_names(impact_sgs, impact_others)

    # Filter to axes that have data
    fuel_names   = [n for n in fuel_names   if any(k[0] == n for k in matrix)]
    impact_names = [n for n in impact_names if any(k[1] == n for k in matrix)]

    if not fuel_names or not impact_names:
        return None

    data = np.zeros((len(impact_names), len(fuel_names)), dtype=float)
    for (fsig, isig), count in matrix.items():
        if fsig in fuel_names and isig in impact_names:
            fi = fuel_names.index(fsig)
            ii = impact_names.index(isig)
            data[ii, fi] = count

    w = max(7, len(fuel_names) * 1.4)
    h = max(4, len(impact_names) * 0.9)
    fig, ax = plt.subplots(figsize=(w, h))

    im = ax.imshow(data, cmap="YlOrRd", aspect="auto", vmin=0)
    plt.colorbar(im, ax=ax, label="Papers", shrink=0.8)

    ax.set_xticks(range(len(fuel_names)))
    ax.set_xticklabels(fuel_names, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(len(impact_names)))
    ax.set_yticklabels(impact_names, fontsize=9)
    ax.set_xlabel("Fuel Subgroup", fontsize=10)
    ax.set_ylabel("Impact Subgroup", fontsize=10)
    ax.set_title(title)

    vmax = data.max() if data.max() > 0 else 1
    for i in range(len(impact_names)):
        for j in range(len(fuel_names)):
            val = int(data[i, j])
            if val > 0:
                text_color = "white" if val > vmax * 0.55 else TEXT_COLOR
                ax.text(j, i, f"{val:,}", ha="center", va="center",
                        fontsize=8, color=text_color, fontweight="bold")

    fig.tight_layout()
    return fig


def _cooccurrence_3d(
    rows: list[tuple], title: str,
    fuel_sgs: list[dict], impact_sgs: list[dict],
) -> plt.Figure:
    """
    3D bubble chart: fuel subgroup (X) × impact subgroup (Y) × LLM model (Z).
    Color = ML paradigm.
    rows: (fuel_term, impact_term, model_raw, paradigm, count)
    """
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    fuel_map, fuel_others   = _build_term_sg_map(fuel_sgs)
    impact_map, impact_others = _build_term_sg_map(impact_sgs)

    def _parse_model(raw: str) -> str:
        if not raw:
            return "Unknown"
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return str(obj.get("name") or raw)
        except Exception:
            pass
        return str(raw)

    # Aggregate to (fuel_sg, impact_sg, model, paradigm) → count
    data: dict[tuple[str, str, str, str], int] = {}
    for row in rows:
        fuel_term  = str(row[0] or "")
        impact_term = str(row[1] or "")
        model_raw  = str(row[2] or "")
        paradigm   = str(row[3] or "Unknown")
        count      = int(row[4] or 0)
        fsig   = fuel_map.get(fuel_term.lower(), fuel_others)
        isig   = impact_map.get(impact_term.lower(), impact_others)
        model  = _parse_model(model_raw)
        key = (fsig, isig, model, paradigm)
        data[key] = data.get(key, 0) + count

    if not data:
        return None

    # Collect unique axis values
    fuel_names   = sorted({k[0] for k in data})
    impact_names = sorted({k[1] for k in data})
    # Top-15 models by total count to keep the Z axis readable
    model_counts: dict[str, int] = {}
    for (_, _, model, _), cnt in data.items():
        model_counts[model] = model_counts.get(model, 0) + cnt
    models = [m for m, _ in sorted(model_counts.items(), key=lambda x: -x[1])[:15]]
    paradigms = sorted({k[3] for k in data})

    paradigm_colors = {p: PALETTE[i % len(PALETTE)] for i, p in enumerate(paradigms)}

    fig = plt.figure(figsize=(13, 8), facecolor=BG_COLOR)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("#FFFFFF")

    size_scale = 120

    for (fsig, isig, model, paradigm), count in data.items():
        if model not in models:
            continue
        x = fuel_names.index(fsig)
        y = impact_names.index(isig)
        z = models.index(model)
        color = paradigm_colors.get(paradigm, PALETTE[0])
        ax.scatter(x, y, z, c=[color], s=min(count * size_scale, 1200),
                   alpha=0.75, depthshade=True, edgecolors="white", linewidths=0.4)

    # Axis labels
    ax.set_xticks(range(len(fuel_names)))
    ax.set_xticklabels(fuel_names, fontsize=7, rotation=20, ha="right")
    ax.set_yticks(range(len(impact_names)))
    ax.set_yticklabels(impact_names, fontsize=7)
    ax.set_zticks(range(len(models)))
    ax.set_zticklabels(models, fontsize=7)
    ax.set_xlabel("Fuel Subgroup", fontsize=9, labelpad=12)
    ax.set_ylabel("Impact Subgroup", fontsize=9, labelpad=12)
    ax.set_zlabel("LLM Model", fontsize=9, labelpad=12)
    ax.set_title(title, pad=14)

    # Paradigm color legend
    legend_patches = [
        mpatches.Patch(color=paradigm_colors[p], label=p) for p in paradigms
    ]
    ax.legend(handles=legend_patches, loc="upper left", fontsize=8,
              title="ML Paradigm", title_fontsize=9, framealpha=0.8)

    # Bubble size note
    fig.text(0.02, 0.02, "Bubble size ∝ paper count", fontsize=8, color=MUTED)

    fig.tight_layout()
    return fig


# ── Public API ────────────────────────────────────────────────────────────────

def generate(
    db_path: Path,
    sql: str,
    x_key: str,
    y_key: str,
    chart_type: str,
    title: str,
    pipeline_config: dict | None = None,
    subgroup_key: str | None = None,
    subset_sql: str | None = None,
    terms_per_subgroup: int | None = None,
) -> bytes:
    """
    Run *sql* against *db_path*, render a matplotlib chart, return PNG bytes.

    chart_type: 'bar' | 'hbar' | 'stacked_hbar' | 'pie' | 'cooccurrence' | 'cooccurrence_3d'
    subset_sql: optional CTE — replaces filtered_records with a user-defined subset
    """
    if subset_sql:
        # Rename the real table reference in chart SQL to avoid SQLite CTE self-reference
        patched = sql.replace("filtered_records", "_fr_subset")
        sql = f"WITH _fr_subset AS (\n{subset_sql}\n)\n{patched}"
    cfg = pipeline_config or {}

    # ── Co-occurrence charts (multi-column SQL, special renderers) ────────────
    if chart_type in ("cooccurrence", "cooccurrence_3d"):
        try:
            _, rows = _run_sql(db_path, sql)
        except Exception:
            return _empty_png(title)
        if not rows:
            return _empty_png(title)
        fuel_sgs   = _extract_subgroups(cfg, "fuel")
        impact_sgs = _extract_subgroups(cfg, "impact")
        try:
            if chart_type == "cooccurrence":
                fig = _cooccurrence_heatmap(rows, title, fuel_sgs, impact_sgs)
            else:
                fig = _cooccurrence_3d(rows, title, fuel_sgs, impact_sgs)
            return _to_png(fig) if fig else _empty_png(title)
        except Exception:
            return _empty_png(title)

    # ── Standard charts ───────────────────────────────────────────────────────
    try:
        cols, rows = _run_sql(db_path, sql)
    except Exception:
        return _empty_png(title)

    if not rows:
        return _empty_png(title)

    xi = cols.index(x_key) if x_key in cols else 0
    yi = cols.index(y_key) if y_key in cols else 1

    labels = [str(r[xi] if r[xi] is not None else "") for r in rows]
    values = [int(r[yi] if r[yi] is not None else 0) for r in rows]

    if not any(values):
        return _empty_png(title)

    # Translate ISO country codes to full names for the countries chart
    if x_key == "country":
        labels = [_country_name(l) for l in labels]

    try:
        if chart_type == "stacked_hbar":
            subgroups = _extract_subgroups(cfg, subgroup_key or "fuel")
            # Fetch distinct record counts per subgroup so the total label is accurate.
            # Uses the same dual-condition as the records viewer:
            #   matched_*_subgroups contains the subgroup name
            #   OR matched_*_terms contains any of the subgroup's terms
            # This ensures records where subgroups is null/empty but terms match are counted.
            sg_col = (
                "matched_fuel_subgroups"
                if (subgroup_key or "fuel") == "fuel"
                else "matched_impact_subgroups"
            )
            terms_col = (
                "matched_fuel_terms"
                if (subgroup_key or "fuel") == "fuel"
                else "matched_impact_terms"
            )
            # Build one SELECT per named subgroup and UNION ALL them
            sg_count_parts: list[str] = []
            for sg in subgroups:
                if sg.get("is_others"):
                    continue
                sg_name = sg["name"]
                sg_terms = sg.get("terms", [])
                safe_name = sg_name.replace("'", "''")
                cond_parts = [
                    f"EXISTS (SELECT 1 FROM json_each({sg_col}) WHERE value = '{safe_name}')"
                ]
                if sg_terms:
                    safe_terms = ", ".join("'" + t.replace("'", "''") + "'" for t in sg_terms)
                    cond_parts.append(
                        f"EXISTS (SELECT 1 FROM json_each({terms_col}) WHERE value IN ({safe_terms}))"
                    )
                where_clause = " OR ".join(cond_parts)
                sg_count_parts.append(
                    f"SELECT '{safe_name}' AS sg, COUNT(DISTINCT openalex_id) AS cnt "
                    f"FROM filtered_records WHERE {where_clause}"
                )
            if sg_count_parts:
                _sg_base = " UNION ALL ".join(sg_count_parts)
            else:
                # Fallback when no named subgroups defined
                _sg_base = (
                    f"SELECT value AS sg, COUNT(DISTINCT openalex_id) AS cnt "
                    f"FROM filtered_records, json_each({sg_col}) "
                    f"WHERE {sg_col} IS NOT NULL AND {sg_col} != '[]' "
                    f"GROUP BY value"
                )
            if subset_sql:
                _sg_patched = _sg_base.replace("filtered_records", "_fr_subset")
                _sg_sql = f"WITH _fr_subset AS (\n{subset_sql}\n)\n{_sg_patched}"
            else:
                _sg_sql = _sg_base
            try:
                _, sg_rows = _run_sql(db_path, _sg_sql)
                sg_counts = {str(r[0]): int(r[1]) for r in sg_rows}
            except Exception:
                sg_counts = {}
            fig = _stacked_hbar(labels, values, title, subgroups, sg_counts, terms_per_subgroup)
            return _to_png(fig) if fig else _empty_png(title)
        elif chart_type == "hbar":
            fig = _hbar(labels, values, title)
        elif chart_type == "pie":
            fig = _pie(labels, values, title)
        else:
            fig = _vbar(labels, values, title)
        return _to_png(fig)
    except Exception:
        return _empty_png(title)
