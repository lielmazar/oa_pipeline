"""Normalize saved subset SQL and wrap queries scoped to a subset CTE (_fr_subset)."""
from __future__ import annotations

import json
import re
import time

_FORBIDDEN_IN_SUBSET_QUERY = ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "ATTACH", "DETACH")


def _strip_sql_line_comments(sql: str) -> str:
    """Remove ``-- ...`` line comments (outside string literals)."""
    return re.sub(r"--[^\n]*", "", sql)


def _strip_numeric_limit_offset(sql: str) -> str:
    """Remove trailing ``LIMIT <int> OFFSET <int>``."""
    return re.sub(r"\s+LIMIT\s+\d+\s+OFFSET\s+\d+\s*$", "", sql.strip(), flags=re.IGNORECASE)


def normalize_subset_inner(sql: str) -> str:
    """Return a SELECT (or WITH...SELECT) body suitable for WITH _fr_subset AS (...).

    Handles several kinds of corruption that can creep in when users copy-paste
    explained SQL or builder output as a subset definition:

    * Leading ``WITH _fr_subset AS (...)`` wrapper → stripped
    * ``LIMIT N OFFSET N`` / ``LIMIT <page_size> ...`` → stripped
    * ``-- ...`` line comments → stripped
    * References to ``_fr_subset`` → rewritten to ``filtered_records``
    * Bare ``WHERE ...`` fragment → wrapped as ``SELECT * FROM filtered_records WHERE ...``
    """
    s = sql.strip().rstrip(";")

    s = _strip_sql_line_comments(s).strip()
    s = strip_explain_placeholders(s)
    s = _strip_numeric_limit_offset(s).rstrip(";").strip()

    s = strip_outer_fr_subset_cte(s)

    s = _strip_sql_line_comments(s).strip()
    s = strip_explain_placeholders(s)
    s = _strip_numeric_limit_offset(s).rstrip(";").strip()

    s = re.sub(r"(?i)(FROM|JOIN)\s+_fr_subset\b", r"\1 filtered_records", s)

    result = s
    if not re.match(r"^\s*(SELECT|WITH)\b", s, re.IGNORECASE | re.DOTALL):
        result = f"SELECT * FROM filtered_records WHERE {s}"

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
                        "hypothesisId": "H_inner",
                        "location": "subset_sql.py:normalize_subset_inner",
                        "message": "subset inner normalized",
                        "data": {
                            "inputFirst200": sql[:200],
                            "outputFirst200": result[:200],
                            "hasQuestionMark": "?" in result,
                            "hasFrSubset": "_fr_subset" in result.lower(),
                            "hasWithFrSubset": "with _fr_subset" in result.lower(),
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

    return result


def strip_string_literals(upper_sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", " ", upper_sql)


def contains_filtered_records_reference(sql: str) -> bool:
    """True if sql references table filtered_records outside of string literals."""
    structure = strip_string_literals(sql.upper())
    return bool(re.search(r"\bFILTERED_RECORDS\b", structure))


def validate_subset_scoped_select(sql: str) -> str | None:
    """Return error message if sql is not a safe SELECT using _fr_subset, else None."""
    sql = sql.strip()
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE | re.DOTALL):
        return "Only SELECT queries are allowed (WITH ... SELECT is allowed)"
    structure = strip_string_literals(sql.upper())
    for kw in _FORBIDDEN_IN_SUBSET_QUERY:
        if re.search(rf"\b{kw}\b", structure):
            return f"Query contains forbidden keyword: {kw}"
    if contains_filtered_records_reference(sql):
        return "Subset-scoped queries must use _fr_subset, not filtered_records"
    if not re.search(r"\b_FR_SUBSET\b", strip_string_literals(sql.upper())):
        return "Query must reference _fr_subset"
    return None


def validate_subset_inner(sql: str) -> str | None:
    """Return error string if the (already-normalised) inner SQL cannot be used as a CTE body."""
    cleaned = strip_string_literals(sql.upper())
    if "?" in cleaned:
        return (
            "Subset definition contains '?' bind placeholders and cannot be executed. "
            "Please edit the subset to use literal values "
            "(e.g. WHERE is_oa = 1 instead of WHERE is_oa = ?)."
        )
    return None


def apply_subset_to_chart_sql(chart_sql: str) -> str:
    """Rewrite predefined chart SQL to read from _fr_subset instead of filtered_records."""
    return re.sub(r"\bfiltered_records\b", "_fr_subset", chart_sql, flags=re.IGNORECASE)


def compose_subset_query(subset_inner: str, scoped_sql: str) -> str:
    """Prepend CTE so scoped_sql can query _fr_subset."""
    inner = normalize_subset_inner(subset_inner)
    return f"WITH _fr_subset AS (\n{inner}\n)\n{scoped_sql.strip()}"


def strip_outer_fr_subset_cte(sql: str) -> str:
    """Remove a leading ``WITH _fr_subset AS (...)`` wrapper.

    The Research DB Viewer sometimes pastes the full explained SQL (including this CTE)
    into custom SQL while the server also prepends the same CTE from the saved subset,
    which would nest two CTEs and cause syntax errors.
    """
    s = sql.strip()
    for _ in range(5):
        m = re.match(r"^\s*WITH\s+_fr_subset\s+AS\s*\(", s, re.IGNORECASE | re.DOTALL)
        if not m:
            break
        i = m.end()
        depth = 1
        found = False
        while i < len(s):
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    s = s[i + 1 :].strip().rstrip(";")
                    found = True
                    break
            i += 1
        if not found:
            break
    return s


def strip_explain_placeholders(sql: str) -> str:
    """Remove invalid ``LIMIT <page_size> OFFSET <offset>`` tokens from explain output.

    Explain SQL may contain more than one placeholder block (inner + outer); strip all.
    """
    s = sql.strip()
    for _ in range(32):
        prev = s
        s = re.sub(
            r"\s+LIMIT\s+<page_size>\s+OFFSET\s+<offset>\s*",
            "",
            s,
            flags=re.IGNORECASE,
        )
        s = re.sub(r"\s+LIMIT\s+<[^>]+>\s+OFFSET\s+<[^>]+>\s*", "", s, flags=re.IGNORECASE)
        if s == prev:
            break
    return s.rstrip().rstrip(";")


def prefer_last_simple_fr_subset_select(sql: str) -> str:
    """If the user pasted multiple statements, keep the last ``SELECT ... FROM _fr_subset``."""
    s = sql.strip()
    if s.upper().count("SELECT") <= 1:
        return s
    up = s.upper()
    if "_fr_subset" not in s.lower():
        return s
    idx = up.rfind("SELECT")
    if idx <= 0:
        return s
    tail = s[idx:].strip()
    if re.search(r"(?i)FROM\s+_fr_subset\b", tail):
        return tail.rstrip(";")
    return s


def normalize_custom_scoped_sql(sql: str) -> str:
    """Prepare user-provided subset-scoped SQL for wrapping with the pipeline subset CTE."""
    raw = sql.strip()
    s = raw
    s = strip_explain_placeholders(s)
    s = strip_outer_fr_subset_cte(s)
    s = strip_explain_placeholders(s)
    s = prefer_last_simple_fr_subset_select(s)
    out = s.strip()
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
                        "hypothesisId": "H1",
                        "location": "subset_sql.py:normalize_custom_scoped_sql",
                        "message": "custom SQL normalized",
                        "data": {
                            "rawHasAngleLimit": "<page_size>" in raw
                            or bool(re.search(r"LIMIT\s+<", raw, re.I)),
                            "outHasAngleLimit": "<page_size>" in out
                            or bool(re.search(r"LIMIT\s+<", out, re.I)),
                            "rawLen": len(raw),
                            "outLen": len(out),
                            "withFrCountRaw": raw.lower().count("with _fr_subset"),
                            "withFrCountOut": out.lower().count("with _fr_subset"),
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
    return out
