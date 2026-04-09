"""Build subset-scoped SQL for the dynamic charting UI."""
from __future__ import annotations

import re

_VALID_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _quote_col(name: str, allowed: set[str]) -> str:
    if name not in allowed:
        raise ValueError(f"Unknown column: {name}")
    if not _VALID_IDENT.match(name):
        raise ValueError(f"Invalid column name: {name}")
    return f'"{name}"'


def build_dynamic_sql(
    *,
    chart_type: str,
    column_x: str,
    column_y: str | None,
    stack_column: str | None,
    json_x: bool,
    json_y: bool,
    top_n: int,
    allowed_cols: set[str],
) -> tuple[str, str]:
    """Return (sql_using__fr_subset, renderer name)."""
    top_n = max(1, min(int(top_n), 500))
    cx = _quote_col(column_x, allowed_cols)

    if chart_type in ("bar", "hbar", "pie"):
        if json_x:
            sql = (
                f"SELECT value AS label, COUNT(*) AS count FROM _fr_subset, json_each({cx}) "
                f"WHERE value IS NOT NULL AND TRIM(CAST(value AS TEXT)) != '' "
                f"GROUP BY value ORDER BY count DESC LIMIT {top_n}"
            )
        else:
            sql = (
                f"SELECT CAST({cx} AS TEXT) AS label, COUNT(*) AS count FROM _fr_subset "
                f"WHERE {cx} IS NOT NULL AND TRIM(CAST({cx} AS TEXT)) != '' "
                f"GROUP BY label ORDER BY count DESC LIMIT {top_n}"
            )
        renderer = "hbar" if chart_type == "hbar" else ("pie" if chart_type == "pie" else "bar")
        return sql, renderer

    if chart_type == "heatmap":
        if not column_y:
            raise ValueError("heatmap requires column_y")
        cy = _quote_col(column_y, allowed_cols)
        if json_x and json_y:
            sql = (
                f"SELECT CAST(xv.value AS TEXT) AS x_label, CAST(yv.value AS TEXT) AS y_label, "
                f"COUNT(*) AS count FROM _fr_subset, json_each({cx}) AS xv, json_each({cy}) AS yv "
                f"WHERE xv.value IS NOT NULL AND yv.value IS NOT NULL "
                f"AND TRIM(CAST(xv.value AS TEXT)) != '' AND TRIM(CAST(yv.value AS TEXT)) != '' "
                f"GROUP BY x_label, y_label"
            )
        elif json_y:
            # Scalar X × JSON array Y (e.g. year × llm_ml_role)
            sql = (
                f"SELECT CAST({cx} AS TEXT) AS x_label, CAST(yv.value AS TEXT) AS y_label, "
                f"COUNT(*) AS count FROM _fr_subset, json_each({cy}) AS yv "
                f"WHERE {cx} IS NOT NULL AND yv.value IS NOT NULL "
                f"AND TRIM(CAST(yv.value AS TEXT)) != '' "
                f"GROUP BY x_label, y_label"
            )
        elif json_x:
            sql = (
                f"SELECT CAST(xv.value AS TEXT) AS x_label, CAST({cy} AS TEXT) AS y_label, "
                f"COUNT(*) AS count FROM _fr_subset, json_each({cx}) AS xv "
                f"WHERE xv.value IS NOT NULL AND {cy} IS NOT NULL "
                f"AND TRIM(CAST({cy} AS TEXT)) != '' "
                f"GROUP BY x_label, y_label"
            )
        else:
            sql = (
                f"SELECT CAST({cx} AS TEXT) AS x_label, CAST({cy} AS TEXT) AS y_label, "
                f"COUNT(*) AS count FROM _fr_subset "
                f"WHERE {cx} IS NOT NULL AND {cy} IS NOT NULL "
                f"AND TRIM(CAST({cx} AS TEXT)) != '' AND TRIM(CAST({cy} AS TEXT)) != '' "
                f"GROUP BY x_label, y_label"
            )
        return sql, "heatmap"

    if chart_type == "stacked":
        if not stack_column:
            raise ValueError("stacked chart requires stack_column")
        cs = _quote_col(stack_column, allowed_cols)
        sql = (
            f"SELECT CAST({cx} AS TEXT) AS x_label, CAST({cs} AS TEXT) AS series, "
            f"COUNT(*) AS count FROM _fr_subset "
            f"WHERE {cx} IS NOT NULL AND {cs} IS NOT NULL "
            f"AND TRIM(CAST({cx} AS TEXT)) != '' AND TRIM(CAST({cs} AS TEXT)) != '' "
            f"GROUP BY x_label, series ORDER BY x_label, series LIMIT {top_n * 50}"
        )
        return sql, "stacked"

    raise ValueError(f"Unsupported chart_type: {chart_type}")
