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
    limit: int = 500,
) -> dict:
    """Return distinct non-null values for *column* within the subset, capped at *limit*.

    Also returns *blank_count*: rows where the column is NULL or empty string (Excel \"blanks\").
    """
    if not db_path.exists():
        return {"error": "Database not found"}
    lim = max(1, min(int(limit), 5000))
    try:
        inner = normalize_subset_inner(subset_inner_sql)
        inner_err = validate_subset_inner(inner)
        if inner_err:
            return {"error": inner_err}
        qi = f'"{column}"'
        cte = f"WITH _fr_subset AS (\n{inner}\n)\n"
        sql = (
            f"{cte}"
            f"SELECT DISTINCT {qi} AS v FROM _fr_subset "
            f"WHERE {qi} IS NOT NULL AND CAST({qi} AS TEXT) != '' "
            f"ORDER BY {qi} LIMIT ?"
        )
        blank_sql = f"{cte}SELECT COUNT(*) FROM _fr_subset WHERE {qi} IS NULL OR CAST({qi} AS TEXT) = ''"
        with _connect_ro(db_path) as conn:
            rows = conn.execute(sql, (lim + 1,)).fetchall()
            blank_count = int(conn.execute(blank_sql).fetchone()[0])
        values = [r[0] for r in rows[:lim]]
        return {"values": values, "truncated": len(rows) > lim, "blank_count": blank_count}
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
        elif op == "in":
            # Excel-style multi-select: (col IN (?,...) OR col IS NULL for "blanks")
            inc_null = f.get("include_null") is True
            raw = val
            if not isinstance(raw, list):
                raw = []
            ors: list[str] = []
            for item in raw:
                ors.append(f"{qi} = ?")
                params.append(item)
            null_cond = f"({qi} IS NULL OR CAST({qi} AS TEXT) = '')"
            if ors and inc_null:
                parts.append(f"(({' OR '.join(ors)}) OR {null_cond})")
            elif ors:
                parts.append(f"({' OR '.join(ors)})")
            elif inc_null:
                parts.append(f"({null_cond})")
            else:
                parts.append("(0=1)")
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
                where_sql, filt_params = _build_filter_sql(filters or [], allowed)
                default_sort = "publication_year" if "publication_year" in allowed else next(iter(allowed), "openalex_id")
                order_sql = _build_order_sql(sort_rules or [], allowed, default_sort)
                if visible_cols:
                    select_cols = ", ".join(_quote_col(c, allowed) for c in visible_cols if c in allowed)
                    if not select_cols:
                        select_cols = "*"
                else:
                    select_cols = "*"
                base = f"{cte}, _q AS ({cust})"
                count_sql = f"{base} SELECT COUNT(*) FROM _q WHERE {where_sql}"
                total = conn.execute(count_sql, list(filt_params)).fetchone()[0]
                offset = (page - 1) * per_page
                page_sql = (
                    f"{base} SELECT {select_cols} FROM _q WHERE {where_sql} "
                    f"ORDER BY {order_sql} LIMIT ? OFFSET ?"
                )
                cur = conn.execute(page_sql, list(filt_params) + [per_page, offset])
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
        return {"error": str(exc)}


def _csv_format_row(values: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(values)
    return buf.getvalue()


def iter_records_view_csv(
    db_path: Path,
    subset_inner_sql: str,
    *,
    filters: list[dict],
    sort_rules: list[dict],
    visible_cols: list[str] | None,
    custom_sql: str | None,
    max_rows: int = 500_000,
) -> Iterator[str]:
    """Yield CSV lines (header first) for the same logical query as get_records_view, without pagination."""
    if not db_path.exists():
        return
    cap = max(1, min(int(max_rows), 2_000_000))
    try:
        with _connect_ro(db_path) as conn:
            allowed = _available_cols(conn)
            inner = normalize_subset_inner(subset_inner_sql)
            inner_err = validate_subset_inner(inner)
            if inner_err:
                return
            cte = f"WITH _fr_subset AS (\n{inner}\n)\n"

            if custom_sql and custom_sql.strip():
                cust = normalize_custom_scoped_sql(custom_sql)
                err = validate_subset_scoped_select(cust)
                if err:
                    return
                where_sql, filt_params = _build_filter_sql(filters or [], allowed)
                default_sort = "publication_year" if "publication_year" in allowed else next(iter(allowed), "openalex_id")
                order_sql = _build_order_sql(sort_rules or [], allowed, default_sort)
                if visible_cols:
                    select_cols = ", ".join(_quote_col(c, allowed) for c in visible_cols if c in allowed)
                    if not select_cols:
                        select_cols = "*"
                else:
                    select_cols = "*"
                base = f"{cte}, _q AS ({cust})"
                page_sql = (
                    f"{base} SELECT {select_cols} FROM _q WHERE {where_sql} ORDER BY {order_sql}"
                )
                cur = conn.execute(page_sql, list(filt_params))
            else:
                where_sql, params = _build_filter_sql(filters or [], allowed)
                default_sort = "publication_year" if "publication_year" in allowed else next(iter(allowed), "openalex_id")
                order_sql = _build_order_sql(sort_rules or [], allowed, default_sort)

                if visible_cols:
                    select_cols = ", ".join(_quote_col(c, allowed) for c in visible_cols if c in allowed)
                    if not select_cols:
                        select_cols = "*"
                else:
                    select_cols = "*"

                data_sql = (
                    f"{cte}SELECT {select_cols} FROM _fr_subset WHERE {where_sql} "
                    f"ORDER BY {order_sql}"
                )
                cur = conn.execute(data_sql, list(params))

            columns = [d[0] for d in cur.description]
            yield _csv_format_row(columns)
            data_count = 0
            while data_count < cap:
                batch = cur.fetchmany(1000)
                if not batch:
                    break
                for row in batch:
                    if data_count >= cap:
                        return
                    yield _csv_format_row(list(row))
                    data_count += 1
    except Exception:
        return


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
                    f"{cte}, _q AS ({cust}) SELECT {select_cols} FROM _q WHERE {where_sql} "
                    f"ORDER BY {order_sql}  -- pagination adds LIMIT/OFFSET"
                )
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
        # Allow WITH ... SELECT (subset-scoped default charts use compose_subset_query).
        if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE | re.DOTALL):
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


def fetch_pairwise_contingency(
    db_path: Path,
    subset_inner_sql: str | None,
    column_x: str,
    column_y: str,
    json_x: bool,
    json_y: bool,
) -> dict:
    """Run heatmap-style grouped counts for orthogonality analysis."""
    if not db_path.exists():
        return {"error": "Database not found"}
    from api.business.dynamic_chart import build_dynamic_sql

    try:
        with _connect_ro(db_path) as conn:
            allowed = _available_cols(conn)
    except Exception as exc:
        return {"error": str(exc)}
    if column_x not in allowed or column_y not in allowed:
        return {"error": f"Invalid column(s): {column_x!r}, {column_y!r}"}
    try:
        sql, _ = build_dynamic_sql(
            chart_type="heatmap",
            column_x=column_x,
            column_y=column_y,
            stack_column=None,
            json_x=json_x,
            json_y=json_y,
            top_n=500,
            allowed_cols=allowed,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    if subset_inner_sql and subset_inner_sql.strip():
        inner = normalize_subset_inner(subset_inner_sql)
        err = validate_subset_inner(inner)
        if err:
            return {"error": err}
    else:
        inner = "SELECT * FROM filtered_records"
    full_sql = compose_subset_query(inner, sql)
    try:
        with _connect_ro(db_path) as conn:
            cur = conn.execute(full_sql)
            col_names = [d[0] for d in cur.description] if cur.description else []
            raw_rows = cur.fetchall()
    except Exception as exc:
        return {"error": str(exc)}
    rows: list[tuple] = []
    for r in raw_rows:
        if len(r) >= 3:
            try:
                cnt = int(r[2])
            except (TypeError, ValueError):
                cnt = 0
            rows.append((r[0], r[1], cnt))
    return {"rows": rows, "columns": col_names}


def iter_subset_openalex_csv(db_path: Path, subset_inner_sql: str, limit: int = 100_000):
    """Yield CSV text chunks (header first) of openalex_id values in subset. Raises ValueError if invalid."""
    inner = normalize_subset_inner(subset_inner_sql)
    err = validate_subset_inner(inner)
    if err:
        raise ValueError(err)
    lim = max(1, min(int(limit), 500_000))
    sql = (
        f"WITH _fr_subset AS (\n{inner}\n) "
        f"SELECT openalex_id FROM _fr_subset WHERE openalex_id IS NOT NULL LIMIT ?"
    )
    yield "openalex_id\n"
    with _connect_ro(db_path) as conn:
        cur = conn.execute(sql, (lim,))
        for row in cur:
            oid = row[0]
            if oid is None:
                continue
            s = str(oid).replace('"', '""')
            if "," in s or "\n" in s or '"' in s:
                yield f'"{s}"\n'
            else:
                yield f"{s}\n"
