"""
Read-only SQLite helpers for the pipeline research output database.

All connections use SQLite URI ?mode=ro to prevent any writes at the
SQLite level, independent of the query-level guards in run_query().
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from pathlib import Path
from typing import Iterator


def _connect_ro(db_path: Path):
    uri = db_path.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def _available_cols(conn) -> set[str]:
    rows = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
    return {r[1] for r in rows}


# -- Stats -------------------------------------------------------------------

def get_db_stats(db_path: Path) -> dict:
    if not db_path.exists():
        return {"exists": False}
    try:
        size_mb = round(db_path.stat().st_size / (1024 * 1024), 1)
        with _connect_ro(db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM filtered_records").fetchone()[0]
            cols = _available_cols(conn)
            if "llm_is_hydrocarbon" in cols:
                llm_count = conn.execute(
                    "SELECT COUNT(*) FROM filtered_records WHERE llm_is_hydrocarbon IS NOT NULL"
                ).fetchone()[0]
            else:
                llm_count = 0
        return {
            "exists": True,
            "size_mb": size_mb,
            "record_count": total,
            "llm_processed_count": llm_count,
        }
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


# -- Schema ------------------------------------------------------------------

def get_schema(db_path: Path) -> list:
    if not db_path.exists():
        return []
    try:
        with _connect_ro(db_path) as conn:
            rows = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
            return [{"name": r[1], "type": r[2]} for r in rows]
    except Exception:
        return []


# -- Paginated records -------------------------------------------------------

_JSON_ARRAY_COLS = frozenset({
    "authors_countries", "authors_institutions",
    "matched_fuel_terms", "matched_impact_terms",
    "matched_fuel_stems", "matched_impact_stems",
    "matched_fuel_subgroups", "matched_impact_subgroups",
    "llm_ml_role", "llm_ml_algorithm", "llm_research_objective",
})

_COL_TYPE_CACHE: dict[str, dict[str, str]] = {}


def _col_types(conn) -> dict[str, str]:
    """Return {col_name: declared_type} for filtered_records."""
    rows = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
    return {r[1]: (r[2] or "TEXT").upper() for r in rows}


def get_records(
    db_path: Path,
    page: int = 1,
    per_page: int = 100,
    sort: str = "publication_year",
    direction: str = "desc",
    search: str = "",
    subset_sql: str | None = None,
    filters: dict[str, str] | None = None,
) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}

    direction = "DESC" if direction.lower() == "desc" else "ASC"
    offset = (page - 1) * per_page

    try:
        with _connect_ro(db_path) as conn:
            existing = _available_cols(conn)
            types = _col_types(conn)

            if sort not in existing:
                sort = "publication_year"

            table_expr = "filtered_records"
            if subset_sql:
                inner = subset_sql.strip().rstrip(";")
                table_expr = f"({inner}) AS filtered_records"

            clauses: list[str] = []
            params: list = []

            if search and search.strip():
                s = f"%{search.strip()}%"
                cols_to_search = [c for c in (
                    "title", "abstract_text", "matched_fuel_terms", "matched_impact_terms"
                ) if c in existing]
                if cols_to_search:
                    clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols_to_search) + ")")
                    params.extend([s] * len(cols_to_search))

            if filters:
                for raw_key, val in filters.items():
                    op = "eq"
                    col = raw_key
                    if raw_key.endswith("__lte"):
                        col = raw_key[:-5]
                        op = "lte"
                    elif raw_key.endswith("__gte"):
                        col = raw_key[:-5]
                        op = "gte"
                    if col not in existing:
                        continue

                    if op == "lte":
                        clauses.append(f"({col} <= ? OR {col} IS NULL)")
                        params.append(val)
                    elif op == "gte":
                        clauses.append(f"{col} >= ?")
                        params.append(val)
                    elif "||" in str(val):
                        parts = str(val).split("||")
                        if col in _JSON_ARRAY_COLS:
                            sub = " OR ".join(
                                f"EXISTS (SELECT 1 FROM json_each({col}) WHERE value = ?)"
                                for _ in parts
                            )
                            clauses.append(f"({sub})")
                            params.extend(parts)
                        else:
                            ph = ",".join("?" for _ in parts)
                            clauses.append(f"{col} IN ({ph})")
                            params.extend(parts)
                    elif col in _JSON_ARRAY_COLS:
                        clauses.append(
                            f"EXISTS (SELECT 1 FROM json_each({col}) WHERE value = ?)"
                        )
                        params.append(val)
                    elif types.get(col, "TEXT") in ("INTEGER", "REAL"):
                        clauses.append(f"{col} = ?")
                        params.append(val)
                    else:
                        clauses.append(f"{col} LIKE ?")
                        params.append(f"%{val}%")

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

            total = conn.execute(
                f"SELECT COUNT(*) FROM {table_expr} {where}", params
            ).fetchone()[0]

            cur = conn.execute(
                f"SELECT * FROM {table_expr} {where} "
                f"ORDER BY {sort} {direction} LIMIT ? OFFSET ?",
                params + [per_page, offset],
            )
            columns = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]

        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "columns": columns,
            "rows": rows,
        }
    except Exception as exc:
        return {"error": str(exc)}


# -- Distinct column values (for filter dropdowns) --------------------------

def get_distinct_values(
    db_path: Path,
    column: str,
    limit: int = 200,
    subset_sql: str | None = None,
) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        with _connect_ro(db_path) as conn:
            existing = _available_cols(conn)
            if column not in existing:
                return {"error": f"Unknown column: {column}"}

            table_expr = "filtered_records"
            if subset_sql:
                inner = subset_sql.strip().rstrip(";")
                table_expr = f"({inner}) AS filtered_records"

            if column in _JSON_ARRAY_COLS:
                sql = (
                    f"SELECT DISTINCT value AS v FROM {table_expr}, "
                    f"json_each({column}) WHERE value IS NOT NULL AND TRIM(value) != '' "
                    f"ORDER BY value LIMIT ?"
                )
            else:
                sql = (
                    f"SELECT DISTINCT {column} AS v FROM {table_expr} "
                    f"WHERE {column} IS NOT NULL ORDER BY {column} LIMIT ?"
                )

            rows = conn.execute(sql, [limit]).fetchall()
            return {"values": [r[0] for r in rows]}
    except Exception as exc:
        return {"error": str(exc)}


# -- Subset row count --------------------------------------------------------

def count_subset_rows(db_path: Path, subset_sql: str) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        sql = f"WITH _fr_subset AS (\n{subset_sql.strip().rstrip(';')}\n) SELECT COUNT(*) FROM _fr_subset"
        with _connect_ro(db_path) as conn:
            n = conn.execute(sql).fetchone()[0]
        return {"count": n}
    except Exception as exc:
        return {"error": str(exc)}


# -- Run preset chart SQL ----------------------------------------------------

_FORBIDDEN_KW = ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "ATTACH", "DETACH")


def run_query(db_path: Path, sql: str, max_rows: int = 5000) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}

    sql = sql.strip()
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE | re.DOTALL):
        return {"error": "Only SELECT queries are allowed"}

    sql_structure = re.sub(r"'(?:[^']|'')*'", " ", sql.upper())
    for kw in _FORBIDDEN_KW:
        if re.search(rf"\b{kw}\b", sql_structure):
            return {"error": f"Query contains forbidden keyword: {kw}"}

    try:
        with _connect_ro(db_path) as conn:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "count": len(rows),
                "truncated": truncated,
            }
    except Exception as exc:
        return {"error": str(exc)}


# -- CSV export streaming ----------------------------------------------------

def _csv_format_row(values: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(values)
    return buf.getvalue()


def iter_all_records_csv(
    db_path: Path,
    max_rows: int = 500_000,
) -> Iterator[str]:
    """Yield CSV lines for all filtered_records, used by the chunked CSV export."""
    if not db_path.exists():
        return
    cap = max(1, min(int(max_rows), 2_000_000))
    try:
        with _connect_ro(db_path) as conn:
            cur = conn.execute("SELECT * FROM filtered_records ORDER BY publication_year DESC")
            columns = [d[0] for d in cur.description]
            yield _csv_format_row(columns)
            count = 0
            while count < cap:
                batch = cur.fetchmany(1000)
                if not batch:
                    break
                for row in batch:
                    if count >= cap:
                        return
                    yield _csv_format_row(list(row))
                    count += 1
    except Exception:
        return
