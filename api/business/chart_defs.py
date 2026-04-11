"""Chart definitions — reads from research_schema.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / ".." / "pipeline" / "research_schema.json"


@lru_cache(maxsize=1)
def _load_charts() -> dict[str, list[dict]]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))["charts"]


def _format_sql(tpl: str, top_n: int = 20, cutoff_year: int = 2000) -> str:
    return tpl.replace("{top_n}", str(top_n)).replace("{cutoff_year}", str(cutoff_year))


def get_chart_configs(
    top_n: int = 20,
    cutoff_year: int = 2000,
    tab: str | None = None,
) -> list[dict]:
    charts = _load_charts()
    if tab:
        raw = charts.get(tab, [])
    else:
        raw = charts.get("search", []) + charts.get("llm", [])

    result = []
    for c in raw:
        entry = {
            "id": c["id"],
            "title": c["title"],
            "sql": _format_sql(c["sql_template"], top_n=top_n, cutoff_year=cutoff_year),
            "renderer": c["renderer"],
            "x_key": c["x_key"],
            "y_key": c["y_key"],
            "llm_only": tab == "llm",
            "params": c.get("params", []),
        }
        result.append(entry)
    return result
