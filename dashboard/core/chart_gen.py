"""chart_gen.py — Server-side matplotlib chart generation for the dashboard API."""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
    # annotate peak
    if values:
        pk = max(range(n), key=lambda i: values[i])
        ax.annotate(str(values[pk]), (pk, values[pk]),
                    textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=9, color=color, fontweight="bold")
    fig.tight_layout()
    return fig


def _hbar(labels: list, values: list, title: str, color: str = PALETTE[0]) -> plt.Figure:
    # Sort ascending so the largest bar is at the top
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


# ── Public API ────────────────────────────────────────────────────────────────

def generate(
    db_path: Path,
    sql: str,
    x_key: str,
    y_key: str,
    chart_type: str,
    title: str,
) -> bytes:
    """
    Run *sql* against *db_path*, render a matplotlib chart, return PNG bytes.

    chart_type: 'bar' | 'hbar' | 'pie'
    """
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

    try:
        if chart_type == "hbar":
            fig = _hbar(labels, values, title)
        elif chart_type == "pie":
            fig = _pie(labels, values, title)
        else:
            fig = _vbar(labels, values, title)
        return _to_png(fig)
    except Exception:
        return _empty_png(title)
