"""Server-side chart data CSV + matplotlib PNG for pipeline ZIP exports."""
from __future__ import annotations

import io
from collections import Counter, defaultdict

from api.business.chart_defs import ChartDefinition

_MATPLOTLIB_READY = False


def _ensure_mpl():
    global _MATPLOTLIB_READY
    if _MATPLOTLIB_READY:
        return True
    try:
        import matplotlib

        matplotlib.use("Agg")
        _MATPLOTLIB_READY = True
    except Exception:
        return False
    return True


def rows_to_csv_text(columns: list[str], rows: list[tuple]) -> str:
    import csv

    buf = io.StringIO()
    w = csv.writer(buf)
    if columns:
        w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue()


def _col_index(columns: list[str], *names: str) -> int | None:
    lower = {c.lower(): i for i, c in enumerate(columns)}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def render_preset_chart_png(spec: ChartDefinition, columns: list[str], rows: list[tuple]) -> bytes | None:
    """Return PNG bytes or None if rendering is skipped or fails."""
    if not rows or not _ensure_mpl():
        return None
    import matplotlib.pyplot as plt
    import numpy as np

    title = spec.title
    r = spec.renderer
    try:
        fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
        fig.patch.set_facecolor("white")

        if r in ("bar", "hbar", "pie"):
            xi = _col_index(columns, spec.x_key, "year", "label", "country", "journal", "type", "stage", "status", "source", "uses_ml")
            yi = _col_index(columns, spec.y_key, "count")
            if xi is None or yi is None:
                plt.close(fig)
                return None
            labels = [str(row[xi]) for row in rows]
            vals = []
            for row in rows:
                try:
                    vals.append(float(row[yi]))
                except (TypeError, ValueError):
                    vals.append(0.0)
            if r == "pie":
                ax.pie(vals, labels=labels, autopct="%1.0f%%", textprops={"fontsize": 8})
                ax.set_title(title, fontsize=11)
            elif r == "hbar":
                ax.barh(np.arange(len(labels)), vals, color="#2563eb")
                ax.set_yticks(np.arange(len(labels)))
                ax.set_yticklabels(labels, fontsize=8)
                ax.set_xlabel("Count")
                ax.invert_yaxis()
                ax.set_title(title, fontsize=11)
                ax.grid(axis="x", alpha=0.3)
            else:
                ax.bar(np.arange(len(labels)), vals, color="#2563eb")
                ax.set_xticks(np.arange(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
                ax.set_ylabel("Count")
                ax.set_title(title, fontsize=11)
                ax.grid(axis="y", alpha=0.3)
        elif r == "heatmap":
            xi = _col_index(columns, spec.x_key, "x_label")
            yi = _col_index(columns, spec.y_key, "y_label")
            ci = _col_index(columns, "count")
            if xi is None or yi is None or ci is None:
                plt.close(fig)
                return None
            xs = sorted({str(row[xi]) for row in rows})
            ys = sorted({str(row[yi]) for row in rows})
            mat = np.zeros((len(ys), len(xs)))
            ix_map = {x: i for i, x in enumerate(xs)}
            iy_map = {y: i for i, y in enumerate(ys)}
            for row in rows:
                try:
                    c = float(row[ci])
                except (TypeError, ValueError):
                    c = 0.0
                mat[iy_map[str(row[yi])], ix_map[str(row[xi])]] = c
            im = ax.imshow(mat, aspect="auto", cmap="Blues")
            ax.set_xticks(np.arange(len(xs)))
            ax.set_xticklabels(xs, rotation=45, ha="right", fontsize=7)
            ax.set_yticks(np.arange(len(ys)))
            ax.set_yticklabels(ys, fontsize=7)
            ax.set_title(title, fontsize=11)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        elif r in ("cooccurrence_3d", "cooccurrence_3d_bar"):
            # Aggregate to 2D counts for a heatmap
            fi = _col_index(columns, "fuel_subgroup")
            ii = _col_index(columns, "impact_subgroup")
            oi = _col_index(columns, "openalex_id")
            if fi is None or ii is None:
                plt.close(fig)
                return None
            if oi is not None:
                cells: dict[tuple[str, str], set[str]] = defaultdict(set)
                for row in rows:
                    cells[(str(row[fi]), str(row[ii]))].add(str(row[oi]))
                counts = {(a, b): len(s) for (a, b), s in cells.items()}
            else:
                counts = Counter()
                for row in rows:
                    counts[(str(row[fi]), str(row[ii]))] += 1
            if not counts:
                plt.close(fig)
                return None
            xs = sorted({a for a, _ in counts})
            ys = sorted({b for _, b in counts})
            mat = np.zeros((len(ys), len(xs)))
            xmap = {x: i for i, x in enumerate(xs)}
            ymap = {y: i for i, y in enumerate(ys)}
            for (xa, xb), v in counts.items():
                mat[ymap[xb], xmap[xa]] = float(v)
            im = ax.imshow(mat, aspect="auto", cmap="YlOrRd")
            ax.set_xticks(np.arange(len(xs)))
            ax.set_xticklabels(xs, rotation=45, ha="right", fontsize=7)
            ax.set_yticks(np.arange(len(ys)))
            ax.set_yticklabels(ys, fontsize=7)
            ax.set_xlabel("Fuel subgroup")
            ax.set_ylabel("Impact subgroup")
            ax.set_title(title, fontsize=11)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        else:
            plt.close(fig)
            return None

        fig.tight_layout()
        bio = io.BytesIO()
        fig.savefig(bio, format="png", bbox_inches="tight")
        plt.close(fig)
        return bio.getvalue()
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def render_dynamic_chart_png(renderer: str, columns: list[str], rows: list[tuple], title: str) -> bytes | None:
    """PNG for saved dynamic charts (subset-scoped explorer)."""
    if not rows or not _ensure_mpl():
        return None
    import matplotlib.pyplot as plt
    import numpy as np

    try:
        fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
        fig.patch.set_facecolor("white")
        if renderer in ("bar", "hbar", "pie"):
            xi = _col_index(columns, "label")
            yi = _col_index(columns, "count")
            if xi is None or yi is None:
                plt.close(fig)
                return None
            labels = [str(row[xi]) for row in rows]
            vals = [float(row[yi]) if row[yi] is not None else 0.0 for row in rows]
            if renderer == "pie":
                ax.pie(vals, labels=labels, autopct="%1.0f%%", textprops={"fontsize": 8})
            elif renderer == "hbar":
                ax.barh(np.arange(len(labels)), vals, color="#2563eb")
                ax.set_yticks(np.arange(len(labels)))
                ax.set_yticklabels(labels, fontsize=8)
                ax.set_xlabel("Count")
                ax.invert_yaxis()
            else:
                ax.bar(np.arange(len(labels)), vals, color="#2563eb")
                ax.set_xticks(np.arange(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
                ax.set_ylabel("Count")
            ax.set_title(title[:120], fontsize=11)
        elif renderer == "heatmap":
            xi = _col_index(columns, "x_label")
            yi = _col_index(columns, "y_label")
            ci = _col_index(columns, "count")
            if xi is None or yi is None or ci is None:
                plt.close(fig)
                return None
            xs = sorted({str(row[xi]) for row in rows})
            ys = sorted({str(row[yi]) for row in rows})
            mat = np.zeros((len(ys), len(xs)))
            ix_map = {x: i for i, x in enumerate(xs)}
            iy_map = {y: i for i, y in enumerate(ys)}
            for row in rows:
                try:
                    c = float(row[ci])
                except (TypeError, ValueError):
                    c = 0.0
                mat[iy_map[str(row[yi])], ix_map[str(row[xi])]] = c
            im = ax.imshow(mat, aspect="auto", cmap="Blues")
            ax.set_xticks(np.arange(len(xs)))
            ax.set_xticklabels(xs, rotation=45, ha="right", fontsize=7)
            ax.set_yticks(np.arange(len(ys)))
            ax.set_yticklabels(ys, fontsize=7)
            ax.set_title(title[:120], fontsize=11)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        elif renderer == "stacked":
            # Long format: x_label, series, count — show grouped bars (first series dim)
            xi = _col_index(columns, "x_label")
            si = _col_index(columns, "series")
            ci = _col_index(columns, "count")
            if xi is None or si is None or ci is None:
                plt.close(fig)
                return None
            x_labels = sorted({str(row[xi]) for row in rows})
            series_labels = sorted({str(row[si]) for row in rows})
            x_idx = {x: i for i, x in enumerate(x_labels)}
            smap = {s: i for i, s in enumerate(series_labels)}
            data = np.zeros((len(series_labels), len(x_labels)))
            for row in rows:
                try:
                    data[smap[str(row[si])], x_idx[str(row[xi])]] += float(row[ci])
                except Exception:
                    pass
            w = 0.8 / max(len(series_labels), 1)
            for i, sl in enumerate(series_labels):
                ax.bar(
                    np.arange(len(x_labels)) + i * w,
                    data[i],
                    width=w,
                    label=sl[:30],
                )
            ax.set_xticks(np.arange(len(x_labels)) + w * (len(series_labels) - 1) / 2)
            ax.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=7)
            ax.legend(fontsize=6, loc="upper right")
            ax.set_title(title[:120], fontsize=11)
        else:
            plt.close(fig)
            return None
        fig.tight_layout()
        bio = io.BytesIO()
        fig.savefig(bio, format="png", bbox_inches="tight")
        plt.close(fig)
        return bio.getvalue()
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None
