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
            llm_count = conn.execute(
                "SELECT COUNT(*) FROM filtered_records WHERE llm_processed_at IS NOT NULL"
            ).fetchone()[0]
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

# Columns shown in the default browse view
_BROWSE_COLS = [
    "openalex_id",
    "doi",
    "title",
    "publication_year",
    "fwci",
    "primary_location_source_display_name",
    "authors_countries",
    "matched_fuel_terms",
    "matched_impact_terms",
    "llm_fuel_classification",
    "llm_is_hydrocarbon",
    "llm_confidence",
]

_ALLOWED_SORT = {
    "publication_year", "title", "doi", "fwci", "openalex_id",
    "primary_location_source_display_name", "llm_is_hydrocarbon", "llm_confidence",
}


def get_records(
    db_path: Path,
    page: int = 1,
    per_page: int = 100,
    sort: str = "publication_year",
    direction: str = "desc",
    search: str = "",
) -> dict:
    if not db_path.exists():
        return {"error": "Database not found"}

    sort = sort if sort in _ALLOWED_SORT else "publication_year"
    direction = "DESC" if direction.lower() == "desc" else "ASC"
    offset = (page - 1) * per_page

    where = ""
    params: list = []
    if search and search.strip():
        where = "WHERE title LIKE ? OR doi LIKE ? OR abstract_text LIKE ?"
        s = f"%{search.strip()}%"
        params = [s, s, s]

    col_list = ", ".join(_BROWSE_COLS)
    try:
        with _connect_ro(db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM filtered_records {where}", params
            ).fetchone()[0]

            cur = conn.execute(
                f"SELECT {col_list} FROM filtered_records {where} "
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
