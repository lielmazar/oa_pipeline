"""
Read-only SQLite helpers for the dashboard.

All connections use the URI ?mode=ro flag which prevents any writes at the
SQLite level, independent of the query-level guards in run_query().
"""
import sqlite3
from pathlib import Path


def _connect_ro(db_path: Path):
    """Open a read-only connection via SQLite URI mode."""
    uri = db_path.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


# ── Stats ──────────────────────────────────────────────────────────────────────

def get_db_stats(db_path: Path) -> dict:
    if not db_path.exists():
        return {"exists": False}
    try:
        size_mb = round(db_path.stat().st_size / (1024 * 1024), 1)
        with _connect_ro(db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM filtered_records"
            ).fetchone()[0]
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


# ── Paginated records ──────────────────────────────────────────────────────────

def _available_cols(conn) -> set[str]:
    """Return the set of columns that actually exist in filtered_records."""
    rows = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
    return {r[1] for r in rows}


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

            # Validate sort column against actual schema
            if sort not in existing:
                sort = "publication_year"

            # Search across specified columns (or default text columns)
            where = ""
            params: list = []
            if search and search.strip():
                s = f"%{search.strip()}%"
                if search_cols:
                    # Use caller-specified columns, filtered to those that exist
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

            # Build column select list
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

def get_record_detail(db_path: Path, openalex_id: str) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        with _connect_ro(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM filtered_records WHERE openalex_id = ?",
                (openalex_id,),
            ).fetchone()
            if row:
                return dict(row)
            return {"error": "Record not found"}
    except Exception as exc:
        return {"error": str(exc)}


# ── Ad-hoc query ───────────────────────────────────────────────────────────────

_FORBIDDEN_KW = ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "ATTACH", "DETACH")


def run_query(db_path: Path, sql: str, max_rows: int = 5000) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}

    sql = sql.strip()
    if not sql.upper().startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed"}

    upper = sql.upper()
    for kw in _FORBIDDEN_KW:
        if kw in upper:
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
