"""
Read-only SQLite helpers for the pipeline research output database.

All connections use SQLite URI ?mode=ro to prevent any writes at the
SQLite level, independent of the query-level guards in run_query().
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from api.business.subset_sql import (
    compose_subset_query,
    normalize_custom_scoped_sql,
    normalize_subset_inner,
    validate_subset_inner,
    validate_subset_scoped_select,
)


def _connect_ro(db_path: Path):
    uri = db_path.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def _available_cols(conn) -> set[str]:
    rows = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
    return {r[1] for r in rows}


# ── Stats ──────────────────────────────────────────────────────────────────────

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


# ── Schema ─────────────────────────────────────────────────────────────────────

def get_schema(db_path: Path) -> list:
    if not db_path.exists():
        return []
    try:
        with _connect_ro(db_path) as conn:
            rows = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
            return [{"name": r[1], "type": r[2]} for r in rows]
    except Exception:
        return []


def get_column_distinct_values(
    db_path: Path,
    subset_inner_sql: str,
    column: str,
    limit: int = 50,
) -> dict:
    """Return distinct non-null values for *column* within the subset, capped at *limit*."""
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        inner = normalize_subset_inner(subset_inner_sql)
        inner_err = validate_subset_inner(inner)
        if inner_err:
            return {"error": inner_err}
        qi = f'"{column}"'
        sql = (
            f"WITH _fr_subset AS (\n{inner}\n)\n"
            f"SELECT DISTINCT {qi} AS v FROM _fr_subset "
            f"WHERE {qi} IS NOT NULL AND {qi} != '' "
            f"ORDER BY {qi} LIMIT ?"
        )
        with _connect_ro(db_path) as conn:
            rows = conn.execute(sql, (limit + 1,)).fetchall()
        values = [r[0] for r in rows[: limit]]
        return {"values": values, "truncated": len(rows) > limit}
    except Exception as exc:
        return {"error": str(exc)}


# ── Paginated records ──────────────────────────────────────────────────────────

def get_records(
    db_path: Path,
    page: int = 1,
    per_page: int = 100,
    sort: str = "publication_year",
    direction: str = "desc",
    search: str = "",
    search_cols: list | None = None,
    visible_cols: list | None = None,
) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}

    direction = "DESC" if direction.lower() == "desc" else "ASC"
    offset = (page - 1) * per_page

    try:
        with _connect_ro(db_path) as conn:
            existing = _available_cols(conn)

            if sort not in existing:
                sort = "publication_year"

            where = ""
            params: list = []
            if search and search.strip():
                s = f"%{search.strip()}%"
                if search_cols:
                    cols_to_search = [c for c in search_cols if c in existing]
                else:
                    cols_to_search = [c for c in (
                        "title", "abstract_text", "matched_fuel_terms", "matched_impact_terms"
                    ) if c in existing]
                if cols_to_search:
                    where = "WHERE " + " OR ".join(f"{c} LIKE ?" for c in cols_to_search)
                    params = [s] * len(cols_to_search)

            total = conn.execute(
                f"SELECT COUNT(*) FROM filtered_records {where}", params
            ).fetchone()[0]

            if visible_cols:
                select_cols = ", ".join(c for c in visible_cols if c in existing)
                if not select_cols:
                    select_cols = "*"
            else:
                select_cols = "*"

            cur = conn.execute(
                f"SELECT {select_cols} FROM filtered_records {where} "
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


# ── Single record detail ───────────────────────────────────────────────────────

def get_record_detail(db_path: Path, openalex_id: str) -> dict | None:
    if not db_path.exists():
        return None
    try:
        with _connect_ro(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM filtered_records WHERE openalex_id = ?",
                (openalex_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def get_record_detail_in_subset(
    db_path: Path, subset_inner_sql: str, openalex_id: str
) -> dict | None:
    if not db_path.exists():
        return None
    try:
        inner = normalize_subset_inner(subset_inner_sql)
        sql = (
            f"WITH _fr_subset AS (\n{inner}\n) "
            f"SELECT * FROM _fr_subset WHERE openalex_id = ? LIMIT 1"
        )
        with _connect_ro(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, (openalex_id,)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


# ── Subset row count ───────────────────────────────────────────────────────────

def count_subset_rows(db_path: Path, subset_inner_sql: str) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        inner = normalize_subset_inner(subset_inner_sql)
        err = validate_subset_inner(inner)
        if err:
            return {"error": err}
        sql = f"WITH _fr_subset AS (\n{inner}\n) SELECT COUNT(*) FROM _fr_subset"
        with _connect_ro(db_path) as conn:
            n = conn.execute(sql).fetchone()[0]
        return {"count": n}
    except Exception as exc:
        return {"error": str(exc)}


# ── Subset record view (filters, sort, optional custom SQL) ───────────────────

_VALID_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _quote_col(col: str, allowed: set[str]) -> str:
    if col not in allowed:
        raise ValueError(f"Invalid column: {col}")
    if not _VALID_IDENT.match(col):
        raise ValueError(f"Invalid column name: {col}")
    return f'"{col}"'


def _build_filter_sql(filters: list[dict], allowed: set[str]) -> tuple[str, list]:
    parts: list[str] = []
    params: list = []
    for f in filters:
        if not isinstance(f, dict):
            continue
        col = f.get("column")
        op = (f.get("op") or "eq").lower()
        if not col or col not in allowed:
            continue
        qi = _quote_col(col, allowed)
        val = f.get("value")

        if op == "is_null":
            parts.append(f"{qi} IS NULL")
        elif op == "is_not_null":
            parts.append(f"{qi} IS NOT NULL")
        elif op == "eq":
            parts.append(f"{qi} = ?")
            params.append(val if val is not None else "")
        elif op == "neq":
            parts.append(f"NOT ({qi} IS ?)")
            params.append(val)
        elif op == "contains":
            parts.append(f"CAST({qi} AS TEXT) LIKE ? ESCAPE '\\'")
            params.append("%" + str(val or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        elif op == "starts_with":
            parts.append(f"CAST({qi} AS TEXT) LIKE ? ESCAPE '\\'")
            params.append(str(val or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        elif op in ("gt", "gte", "lt", "lte"):
            sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            parts.append(f"CAST({qi} AS REAL) {sym} ?")
            try:
                params.append(float(val))
            except (TypeError, ValueError):
                params.append(0.0)
    where = " AND ".join(parts) if parts else "1=1"
    return where, params


def _build_order_sql(sort_rules: list[dict], allowed: set[str], default_col: str) -> str:
    bits: list[str] = []
    for rule in sort_rules:
        if not isinstance(rule, dict):
            continue
        col = rule.get("column")
        if not col or col not in allowed:
            continue
        qi = _quote_col(col, allowed)
        direction = "DESC" if str(rule.get("dir", "desc")).lower() == "desc" else "ASC"
        bits.append(f"{qi} {direction}")
    if not bits:
        if default_col in allowed:
            return f'"{default_col}" DESC'
        return "1"
    return ", ".join(bits)


def get_records_view(
    db_path: Path,
    subset_inner_sql: str,
    *,
    filters: list[dict],
    sort_rules: list[dict],
    page: int,
    per_page: int,
    visible_cols: list[str] | None,
    custom_sql: str | None,
) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}
    page = max(1, page)
    per_page = max(1, min(per_page, 200))

    try:
        with _connect_ro(db_path) as conn:
            allowed = _available_cols(conn)
            inner = normalize_subset_inner(subset_inner_sql)
            inner_err = validate_subset_inner(inner)
            if inner_err:
                return {"error": inner_err}
            cte = f"WITH _fr_subset AS (\n{inner}\n)\n"

            if custom_sql and custom_sql.strip():
                cust = normalize_custom_scoped_sql(custom_sql)
                err = validate_subset_scoped_select(cust)
                if err:
                    return {"error": err}
                count_sql = f"{cte}, _q AS ({cust}) SELECT COUNT(*) FROM _q"
                total = conn.execute(count_sql).fetchone()[0]
                offset = (page - 1) * per_page
                page_sql = f"{cte}, _q AS ({cust}) SELECT * FROM _q LIMIT ? OFFSET ?"
                cur = conn.execute(page_sql, (per_page, offset))
                columns = [d[0] for d in cur.description]
                rows = [list(r) for r in cur.fetchall()]
                return {
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "columns": columns,
                    "rows": rows,
                    "mode": "custom_sql",
                }

            where_sql, params = _build_filter_sql(filters or [], allowed)
            default_sort = "publication_year" if "publication_year" in allowed else next(iter(allowed), "openalex_id")
            order_sql = _build_order_sql(sort_rules or [], allowed, default_sort)

            if visible_cols:
                select_cols = ", ".join(_quote_col(c, allowed) for c in visible_cols if c in allowed)
                if not select_cols:
                    select_cols = "*"
            else:
                select_cols = "*"

            count_sql = f"{cte}SELECT COUNT(*) FROM _fr_subset WHERE {where_sql}"
            total = conn.execute(count_sql, list(params)).fetchone()[0]

            offset = (page - 1) * per_page
            data_sql = (
                f"{cte}SELECT {select_cols} FROM _fr_subset WHERE {where_sql} "
                f"ORDER BY {order_sql} LIMIT ? OFFSET ?"
            )
            cur = conn.execute(data_sql, list(params) + [per_page, offset])
            columns = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]

        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "columns": columns,
            "rows": rows,
            "mode": "builder",
        }
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        # #region agent log
        try:
            with open(
                r"c:\Users\lielm\Desktop\oa_pipeline\debug-b7b538.log",
                "a",
                encoding="utf-8",
            ) as _lf:
                _lf.write(
                    json.dumps(
                        {
                            "sessionId": "b7b538",
                            "hypothesisId": "H3",
                            "location": "research.py:get_records_view",
                            "message": "records view failed",
                            "data": {
                                "exc": str(exc)[:500],
                                "hadCustom": bool(
                                    custom_sql and str(custom_sql).strip()
                                ),
                            },
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        return {"error": str(exc)}


def explain_records_view_sql(
    db_path: Path,
    subset_inner_sql: str,
    *,
    filters: list[dict],
    sort_rules: list[dict],
    visible_cols: list[str] | None,
    custom_sql: str | None,
) -> dict:
    """Return the SQL text the builder would run (without LIMIT/OFFSET)."""
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        with _connect_ro(db_path) as conn:
            allowed = _available_cols(conn)
            inner = normalize_subset_inner(subset_inner_sql)
            inner_err = validate_subset_inner(inner)
            if inner_err:
                return {"error": inner_err}
            cte = f"WITH _fr_subset AS (\n{inner}\n)\n"

            if custom_sql and custom_sql.strip():
                cust = normalize_custom_scoped_sql(custom_sql)
                err = validate_subset_scoped_select(cust)
                if err:
                    return {"error": err}
                sql = f"{cte}, _q AS ({cust}) SELECT * FROM _q"
                return {"sql": sql.strip(), "mode": "custom_sql"}

            where_sql, _ = _build_filter_sql(filters or [], allowed)
            default_sort = "publication_year" if "publication_year" in allowed else next(iter(allowed), "openalex_id")
            order_sql = _build_order_sql(sort_rules or [], allowed, default_sort)

            if visible_cols:
                select_cols = ", ".join(_quote_col(c, allowed) for c in visible_cols if c in allowed)
                if not select_cols:
                    select_cols = "*"
            else:
                select_cols = "*"

            sql = (
                f"{cte}SELECT {select_cols} FROM _fr_subset WHERE {where_sql} "
                f"ORDER BY {order_sql} LIMIT 50 OFFSET 0  -- adjust LIMIT/OFFSET when pasting as custom SQL"
            )
            return {"sql": sql.strip(), "mode": "builder"}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


# ── Ad-hoc query ───────────────────────────────────────────────────────────────

_FORBIDDEN_KW = ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "ATTACH", "DETACH")


def run_query(
    db_path: Path,
    sql: str,
    max_rows: int = 5000,
    subset_inner_sql: str | None = None,
) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}

    sql = sql.strip()
    if subset_inner_sql:
        sql = normalize_custom_scoped_sql(sql)
        err = validate_subset_scoped_select(sql)
        if err:
            return {"error": err}
        full_sql = compose_subset_query(subset_inner_sql, sql)
    else:
        if not sql.upper().startswith("SELECT"):
            return {"error": "Only SELECT queries are allowed"}
        full_sql = sql

    sql_structure = re.sub(r"'(?:[^']|'')*'", " ", full_sql.upper())
    for kw in _FORBIDDEN_KW:
        if re.search(rf"\b{kw}\b", sql_structure):
            return {"error": f"Query contains forbidden keyword: {kw}"}

    try:
        with _connect_ro(db_path) as conn:
            cur = conn.execute(full_sql)
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
