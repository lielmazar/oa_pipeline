"""Self-contained HTML reports (no external assets) for each pipeline stage."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;
--bar:#2f6f8f;--bar2:#c9822b;--bad:#b3412f;--good:#2e7d4f;--heat:47,111,143}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#151514;--card:#1f1f1d;
--ink:#ecebe6;--muted:#a3a29b;--line:#34332f;--bar:#5fa8c9;--bar2:#e0a35a;--bad:#e07a66;
--good:#6fc28f;--heat:95,168,201}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}h3{font-size:14px;margin:16px 0 8px}
.sub{color:var(--muted);margin:0 0 20px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}
.kpi .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.kpi .n{color:var(--muted);font-size:12px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:8px 28px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}td.num,th.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto}
.bars .row{display:grid;grid-template-columns:minmax(90px,38%) 1fr auto;gap:8px;align-items:center;margin:3px 0}
.bars .lab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bars .track{height:12px;background:var(--line);border-radius:3px;overflow:hidden}
.bars .fill{height:100%;background:var(--bar)}
.bars .val{color:var(--muted);font-size:12px;white-space:nowrap;text-align:right;min-width:92px}
.cols{display:flex;align-items:flex-end;gap:2px;height:160px;border-bottom:1px solid var(--line)}
.cols div{flex:1;background:var(--bar);min-height:1px}
.axis{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}
.funnel .track{height:16px}.funnel .fill{background:var(--good)}.removed{color:var(--bad)}
.heat td{text-align:center;font-size:12px}.heat th{font-size:11px}
.rec{border-top:1px solid var(--line);padding:10px 0}.rec:first-child{border-top:0}
.rec .t{font-weight:600}.rec .m{color:var(--muted);font-size:12px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin:2px 4px 0 0;font-size:12px}
.tag.b{border-color:var(--bar2)}
mark{background:rgba(var(--heat),.25);color:inherit;border-radius:2px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto;font-size:12px}
.warn{border-left:3px solid var(--bar2);padding:6px 10px;background:var(--bg)}
a{color:var(--bar)}
summary{cursor:pointer;font-weight:600;margin:12px 0 6px}
"""


def num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return f"{v:,}" if isinstance(v, int) else escape(str(v))


def pct(part: float, whole: float, digits: int = 1) -> str:
    return f"{part / whole * 100:.{digits}f}%" if whole else "—"


class Report:
    def __init__(self, title: str, subtitle: str = "") -> None:
        self.title = title
        self.subtitle = subtitle
        self._parts: list[str] = []
        self._open = False

    # -- structure ---------------------------------------------------------
    def section(self, title: str, intro: str | None = None) -> "Report":
        self._close()
        self._parts.append(f"<section><h2>{escape(title)}</h2>")
        if intro:
            self._parts.append(f"<p class='sub'>{intro}</p>")
        self._open = True
        return self

    def _close(self) -> None:
        if self._open:
            self._parts.append("</section>")
            self._open = False

    def grid(self, *blocks: str) -> None:
        self._parts.append("<div class='grid2'>" + "".join(f"<div>{b}</div>" for b in blocks) + "</div>")

    def raw(self, html: str) -> None:
        self._parts.append(html)

    def text(self, html: str) -> None:
        self._parts.append(f"<p>{html}</p>")

    def warning(self, html: str) -> None:
        self._parts.append(f"<p class='warn'>{html}</p>")

    def code(self, text: str) -> None:
        self._parts.append(f"<pre>{escape(text)}</pre>")

    # -- components (return HTML so they can be placed in grids) -----------
    def kpis(self, items: list[tuple[str, object, str]]) -> None:
        cells = "".join(
            f"<div class='kpi'><div class='l'>{escape(l)}</div><div class='v'>{num(v)}</div>"
            f"<div class='n'>{escape(n)}</div></div>" for l, v, n in items)
        self._parts.append(f"<div class='kpis'>{cells}</div>")

    @staticmethod
    def table(headers: list[str], rows: list[list], numeric: set[int] | None = None,
              title: str | None = None, html_cols: set[int] | None = None) -> str:
        numeric = numeric or set()
        html_cols = html_cols or set()
        head = "".join(f"<th class='{'num' if i in numeric else ''}'>{escape(h)}</th>"
                       for i, h in enumerate(headers))
        body = "".join(
            "<tr>" + "".join(
                f"<td class='{'num' if i in numeric else ''}'>"
                f"{c if i in html_cols else num(c)}</td>" for i, c in enumerate(r)) + "</tr>"
            for r in rows)
        t = f"<h3>{escape(title)}</h3>" if title else ""
        return f"{t}<div class='scroll'><table><tr>{head}</tr>{body}</table></div>"

    @staticmethod
    def bars(items: list[tuple[str, float]], title: str | None = None,
             total: float | None = None) -> str:
        if not items:
            return (f"<h3>{escape(title)}</h3>" if title else "") + "<p class='sub'>No data.</p>"
        top = max(v for _, v in items) or 1
        rows = []
        for label, v in items:
            share = f" · {pct(v, total)}" if total else ""
            rows.append(
                f"<div class='row'><div class='lab' title='{escape(str(label))}'>{escape(str(label))}</div>"
                f"<div class='track'><div class='fill' style='width:{v / top * 100:.2f}%'></div></div>"
                f"<div class='val'>{num(v)}{share}</div></div>")
        t = f"<h3>{escape(title)}</h3>" if title else ""
        return f"{t}<div class='bars'>{''.join(rows)}</div>"

    @staticmethod
    def columns(items: list[tuple[object, float]], title: str | None = None) -> str:
        """Vertical bars for an ordered series (e.g. records per year)."""
        if not items:
            return (f"<h3>{escape(title)}</h3>" if title else "") + "<p class='sub'>No data.</p>"
        top = max(v for _, v in items) or 1
        cols = "".join(f"<div style='height:{v / top * 100:.2f}%' title='{escape(str(k))}: {v:,}'></div>"
                       for k, v in items)
        t = f"<h3>{escape(title)}</h3>" if title else ""
        return (f"{t}<div class='cols'>{cols}</div><div class='axis'><span>{escape(str(items[0][0]))}</span>"
                f"<span>peak {num(top)}</span><span>{escape(str(items[-1][0]))}</span></div>")

    def funnel(self, start_label: str, start: int, steps: list[tuple[str, int]]) -> str:
        rows = [["", escape(start_label), "", num(start), "100%",
                 "<div class='bars funnel'><div class='track'><div class='fill' style='width:100%'></div></div></div>"]]
        remaining = start
        for i, (label, removed) in enumerate(steps, 1):
            remaining -= removed
            w = remaining / start * 100 if start else 0
            rows.append([str(i), escape(label), (f"<span class='removed'>−{removed:,}</span>" if removed else "0"),
                         num(remaining), pct(remaining, start),
                         f"<div class='bars funnel'><div class='track'><div class='fill' "
                         f"style='width:{w:.2f}%'></div></div></div>"])
        return self.table(["#", "Step", "Removed", "Remaining", "Share", ""], rows,
                          numeric={2, 3, 4}, html_cols={1, 2, 5})

    @staticmethod
    def heatmap(row_labels: list[str], col_labels: list[str], matrix: list[list[int]],
                title: str | None = None) -> str:
        top = max((v for r in matrix for v in r), default=0) or 1
        head = "<tr><th></th>" + "".join(f"<th>{escape(c)}</th>" for c in col_labels) + "</tr>"
        body = ""
        for label, row in zip(row_labels, matrix):
            cells = "".join(
                f"<td style='background:rgba(var(--heat),{0.08 + 0.85 * v / top:.2f})'>{v:,}</td>"
                if v else "<td>·</td>" for v in row)
            body += f"<tr><th>{escape(label)}</th>{cells}</tr>"
        t = f"<h3>{escape(title)}</h3>" if title else ""
        return f"{t}<div class='scroll'><table class='heat'>{head}{body}</table></div>"

    @staticmethod
    def records(items: list[dict]) -> str:
        """items: {title | title_html, url, meta, tags: [(text, alt_style)], snippet_html}"""
        out = []
        for it in items:
            title = it.get("title_html") or escape(it.get("title") or "(untitled)")
            if it.get("url"):
                title = f"<a href='{escape(it['url'])}' target='_blank' rel='noopener'>{title}</a>"
            tags = "".join(f"<span class='tag{' b' if alt else ''}'>{escape(t)}</span>"
                           for t, alt in it.get("tags", []))
            snippet = f"<div>{it['snippet_html']}</div>" if it.get("snippet_html") else ""
            out.append(f"<div class='rec'><div class='t'>{title}</div>"
                       f"<div class='m'>{escape(it.get('meta', ''))}</div>{snippet}<div>{tags}</div></div>")
        return "".join(out)

    # -- output --------------------------------------------------------------
    def to_html(self) -> str:
        self._close()
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(self.title)}</title><style>{_CSS}</style></head><body><main>"
            f"<h1>{escape(self.title)}</h1><p class='sub'>{escape(self.subtitle)} · generated {generated}</p>"
            + "".join(self._parts) + "</main></body></html>")
