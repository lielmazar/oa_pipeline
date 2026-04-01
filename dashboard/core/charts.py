"""core/charts.py — Chart query definitions for the dashboard."""
from __future__ import annotations

# Each entry: title, sql (may contain {top_n}), type (bar|hbar|pie), x_key, y_key
# llm_only=True means only meaningful when LLM columns are populated.
CHART_QUERIES: dict[str, dict] = {
    "publications_by_year": {
        "title": "Publications by Year",
        "sql": (
            "SELECT "
            "  CASE WHEN publication_year IS NULL OR publication_year <= 2000 "
            "       THEN 'Unknown/≤2000' "
            "       ELSE CAST(publication_year AS TEXT) END AS year, "
            "  COUNT(*) AS count "
            "FROM filtered_records "
            "GROUP BY year "
            "ORDER BY MIN(COALESCE(publication_year, 0))"
        ),
        "type": "bar", "x_key": "year", "y_key": "count",
    },
    "top_fuel_terms": {
        "title": "Top Fuel Terms",
        "sql": (
            "SELECT value AS term, COUNT(*) AS count "
            "FROM filtered_records, json_each(matched_fuel_terms) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "term", "y_key": "count",
    },
    "top_impact_terms": {
        "title": "Top Impact Terms",
        "sql": (
            "SELECT value AS term, COUNT(*) AS count "
            "FROM filtered_records, json_each(matched_impact_terms) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "term", "y_key": "count",
    },
    "top_countries": {
        "title": "Top Contributing Countries",
        "sql": (
            "SELECT value AS country, COUNT(*) AS count "
            "FROM filtered_records, json_each(authors_countries) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "country", "y_key": "count",
    },
    "top_journals": {
        "title": "Top Journals / Sources",
        "sql": (
            "SELECT primary_location_source_display_name AS journal, COUNT(*) AS count "
            "FROM filtered_records "
            "WHERE primary_location_source_display_name IS NOT NULL "
            "GROUP BY journal ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "journal", "y_key": "count",
    },
    "oa_status": {
        "title": "Open Access Status",
        "sql": (
            "SELECT COALESCE(open_access_oa_status, 'unknown') AS status, COUNT(*) AS count "
            "FROM filtered_records GROUP BY status ORDER BY count DESC"
        ),
        "type": "pie", "x_key": "status", "y_key": "count",
    },
    "work_types": {
        "title": "Work Types",
        "sql": (
            "SELECT COALESCE(type, 'unknown') AS type, COUNT(*) AS count "
            "FROM filtered_records GROUP BY type ORDER BY count DESC LIMIT 10"
        ),
        "type": "bar", "x_key": "type", "y_key": "count",
    },
    "top_institutions": {
        "title": "Top Institutions",
        "sql": (
            "SELECT value AS institution, COUNT(*) AS count "
            "FROM filtered_records, json_each(authors_institutions) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "institution", "y_key": "count",
    },
    "llm_scope": {
        "title": "Fuel Classification (LLM)",
        "sql": (
            "SELECT value AS fuel_type, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_fuel_classification) "
            "WHERE value IS NOT NULL AND value != '' "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        "type": "hbar", "x_key": "fuel_type", "y_key": "count",
        "llm_only": True,
    },
    "llm_ml_paradigm": {
        "title": "ML/AI Paradigm (LLM)",
        "sql": (
            "SELECT value AS paradigm, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_ml_paradigm) "
            "WHERE value IS NOT NULL AND value != '' AND LOWER(value) != 'none' "
            "GROUP BY value ORDER BY count DESC"
        ),
        "type": "hbar", "x_key": "paradigm", "y_key": "count",
        "llm_only": True,
    },
    "llm_ml_usage": {
        "title": "ML/AI Usage Rate (LLM)",
        "sql": (
            "SELECT "
            "  CASE WHEN llm_ml_paradigm IS NOT NULL "
            "            AND llm_ml_paradigm NOT IN ('[]', 'null', '') "
            "            AND LOWER(llm_ml_paradigm) NOT LIKE '%\"none\"%' "
            "       THEN 'Uses ML/AI' ELSE 'No ML/AI' END AS uses_ml, "
            "  COUNT(*) AS count "
            "FROM filtered_records GROUP BY uses_ml"
        ),
        "type": "pie", "x_key": "uses_ml", "y_key": "count",
        "llm_only": True,
    },
}


def get_chart_configs(top_n: int = 20) -> list[dict]:
    """Return all chart configs with {top_n} interpolated."""
    configs = []
    for cid, cfg in CHART_QUERIES.items():
        configs.append({
            "id": cid,
            "title": cfg["title"],
            "sql": cfg["sql"].replace("{top_n}", str(top_n)),
            "type": cfg["type"],
            "x_key": cfg["x_key"],
            "y_key": cfg["y_key"],
            "llm_only": cfg.get("llm_only", False),
        })
    return configs


def get_chart_sql(chart_id: str, top_n: int = 20) -> str | None:
    """Return the interpolated SQL for a single chart, or None if unknown."""
    cfg = CHART_QUERIES.get(chart_id)
    if cfg is None:
        return None
    return cfg["sql"].replace("{top_n}", str(top_n))
