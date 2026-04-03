#!/usr/bin/env python3
"""
batch_process_papers.py
=======================
Process paper records (title + abstract) via OpenAI Batch API
using a stored prompt, then write structured JSON results back
to the same SQLite rows.

Phases
------
  estimate  — print cost table with no API calls
  prepare   — read unprocessed records from DB, write batch JSONL file
  submit    — upload JSONL and create batch job
  poll      — wait (with live progress) until the batch completes
  save      — download results, parse JSON output, UPDATE each paper row
  run       — full pipeline: prepare → submit → poll → save

Usage
-----
  python batch_process_papers.py estimate [--model gpt-5.4]
  python batch_process_papers.py prepare  [--limit 100]
  python batch_process_papers.py submit
  python batch_process_papers.py poll     [--interval 60]
  python batch_process_papers.py save
  python batch_process_papers.py run      [--limit N] [--interval 60]

Requirements
------------
  pip install openai
  export OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

# Load .env before importing openai so the API key is available
def _load_env(path: str = ".env") -> None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass

_load_env()

from openai import OpenAI

# ── Configuration ──────────────────────────────────────────────────────────
# Defaults — overridden at runtime by --db and --batch-dir CLI args
# (main() sets these before calling any phase function)

DB_PATH    = Path("openalex_works_filtered.sqlite")
BATCH_DIR  = Path("batch_runs")
STATE_FILE = BATCH_DIR / "state.json"


def _configure_paths(db: Path | None, batch_dir: Path | None) -> None:
    """Update module-level path constants from CLI args."""
    global DB_PATH, BATCH_DIR, STATE_FILE
    if db is not None:
        DB_PATH = db
    if batch_dir is not None:
        BATCH_DIR = batch_dir
    STATE_FILE = BATCH_DIR / "state.json"

PROMPT_ID      = "pmpt_694c136369fc81939aa9e7eb310bd3170ecd05cb5a9fb123"
PROMPT_VERSION = "21"
MODEL          = "gpt-5.4-nano"

# ── Pricing table (per 1M tokens) ─────────────────────────────────────────
# Source: platform.openai.com/docs/pricing  — Batch API, short-context window
# cached_input: price when the prompt prefix is served from the KV cache.
MODEL_PRICING: dict[str, dict] = {
    # ── gpt-5.4 family  (Batch API · short context) ────────────
    "gpt-5.4": {
        "input": 1.25, "cached_input": 0.13, "output": 7.50,
        "reasoning": None, "reasoning_est_per_paper": 0,
        "note": "Batch API, short context.",
    },
    "gpt-5.4-mini": {
        "input": 0.375, "cached_input": 0.0375, "output": 2.25,
        "reasoning": None, "reasoning_est_per_paper": 0,
        "note": "Batch API, short context.",
    },
    "gpt-5.4-nano": {
        "input": 0.10, "cached_input": 0.01, "output": 0.625,
        "reasoning": None, "reasoning_est_per_paper": 0,
        "note": "Batch API, short context.",
    },
    "gpt-5.4-pro": {
        "input": 15.00, "cached_input": None, "output": 90.00,
        "reasoning": None, "reasoning_est_per_paper": 0,
        "note": "Batch API, short context. No cached-input pricing.",
    },
    # ── previous generation (for comparison) ───────────────────
    "gpt-4o": {
        "input": 1.25, "cached_input": None, "output": 5.00,
        "reasoning": None, "reasoning_est_per_paper": 0,
        "note": "Batch API pricing.",
    },
    "gpt-4o-mini": {
        "input": 0.075, "cached_input": None, "output": 0.30,
        "reasoning": None, "reasoning_est_per_paper": 0,
        "note": "Batch API pricing.",
    },
    "o4-mini": {
        "input": 0.55, "cached_input": None, "output": 2.20,
        "reasoning": 2.20, "reasoning_est_per_paper": 500,
        "note": "Batch API pricing incl. reasoning tokens.",
    },
    "o3": {
        "input": 5.00, "cached_input": None, "output": 20.00,
        "reasoning": 20.00, "reasoning_est_per_paper": 2_000,
        "note": "Batch API pricing incl. reasoning tokens.",
    },
}

# Assumed average output tokens per paper (classification JSON ≈ 300–500 tok)
AVG_OUTPUT_TOKENS    = 400
# Prompt template overhead beyond the paper text itself
PROMPT_OVERHEAD_TOKENS = 300


# ── Helpers ────────────────────────────────────────────────────────────────

def get_client() -> OpenAI:
    return OpenAI()  # reads OPENAI_API_KEY from environment


def ensure_result_columns(conn: sqlite3.Connection) -> None:
    """
    Add all LLM output columns to filtered_records.

    Column mapping from prompt v3 JSON output:
      llm_is_hydrocarbon       ← fuel_relevant (boolean)
      llm_fuel_classification  ← fuel_classification  (JSON array)
      llm_ml_role              ← ml_role              (JSON array)  — v3
      llm_ml_algorithm         ← ml_algorithm         (JSON array)  — v3
      llm_ml_strategy          ← ml_strategy          (JSON array)  — v3
      llm_ml_data_source       ← ml_data_source       (string)      — v3
      llm_confidence           ← confidence
      llm_justification        ← justification
      llm_models               ← models  (JSON array of model objects)
      llm_result_raw           ← full raw text (fallback if JSON parse fails)
      llm_ml_paradigm          ← kept for backward compat with v2 data
      llm_ml_method            ← kept for backward compat with v2 data
    """
    new_cols = [
        ("llm_is_hydrocarbon",      "INTEGER"),   # 1 / 0 / NULL
        ("llm_fuel_classification", "TEXT"),       # JSON array
        ("llm_ml_paradigm",         "TEXT"),       # v2 compat — JSON array
        ("llm_ml_method",           "TEXT"),       # v2 compat — JSON array
        ("llm_ml_role",             "TEXT"),       # v3 — JSON array
        ("llm_ml_algorithm",        "TEXT"),       # v3 — JSON array
        ("llm_ml_strategy",         "TEXT"),       # v3 — JSON array
        ("llm_ml_data_source",      "TEXT"),       # v3 — single string
        ("llm_confidence",          "TEXT"),
        ("llm_justification",       "TEXT"),
        ("llm_models",              "TEXT"),       # JSON array of objects
        ("llm_result_raw",          "TEXT"),       # raw model output
        ("llm_batch_id",            "TEXT"),
        ("llm_processed_at",        "TEXT"),
        ("llm_tokens_input",        "INTEGER"),
        ("llm_tokens_output",       "INTEGER"),
        ("llm_tokens_reasoning",    "INTEGER"),
    ]
    for col, typedef in new_cols:
        try:
            conn.execute(
                f"ALTER TABLE filtered_records ADD COLUMN {col} {typedef}"
            )
        except sqlite3.OperationalError:
            pass  # already exists
    conn.commit()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    BATCH_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def parse_llm_json(text: str) -> dict | None:
    """
    Parse the structured JSON that the prompt returns.
    Handles:
      • markdown code fences (```json ... ```)
      • Python-style booleans (True / False)
    """
    text = text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1 if lines[0].startswith("```") else 0
        end   = -1 if lines[-1].strip() == "```" else len(lines)
        text  = "\n".join(lines[start:end])

    # Fix Python boolean literals
    text = re.sub(r"\bTrue\b",  "true",  text)
    text = re.sub(r"\bFalse\b", "false", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_output_text(body: dict) -> str:
    """Extract assistant text from a /v1/responses body."""
    for item in body.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    return ""



# ── Phase 1: Prepare ───────────────────────────────────────────────────────

def phase_prepare(
    limit: int | None = None,
    overwrite: bool = False,
    subset_sql: str | None = None,
) -> Path | None:
    """
    Export records to a JSONL batch file.

    overwrite=True  → include ALL records (re-process existing results).
    overwrite=False → skip records that already have llm_is_hydrocarbon set.
    subset_sql      → SQL selecting a subset of filtered_records (e.g. from saved subsets).
    """
    BATCH_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_result_columns(conn)

    if subset_sql:
        # Wrap subset in a CTE to avoid SQLite self-reference issues
        clean_subset = subset_sql.strip().rstrip(";")
        cte = f"WITH _fr_subset AS (\n{clean_subset}\n)\n"
        table = "_fr_subset"
    else:
        cte = ""
        table = "filtered_records"

    # rowid is a physical SQLite column — not available through a CTE
    order_col = "openalex_id" if subset_sql else "rowid"

    if overwrite:
        query = f"""
            {cte}SELECT openalex_id, title, abstract_text
            FROM   {table}
            ORDER  BY {order_col}
        """
    else:
        query = f"""
            {cte}SELECT openalex_id, title, abstract_text
            FROM   {table}
            WHERE  llm_is_hydrocarbon IS NULL
            ORDER  BY {order_col}
        """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    conn.close()

    if not rows:
        print("No unprocessed records found — nothing to prepare.")
        return None

    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_file = BATCH_DIR / f"batch_input_{ts}.jsonl"

    with batch_file.open("w", encoding="utf-8") as fh:
        for row in rows:
            title    = row["title"] or ""
            abstract = row["abstract_text"] or ""

            request = {
                "custom_id": row["openalex_id"],
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": MODEL,
                    "prompt": {
                        "id": PROMPT_ID,
                        "version": PROMPT_VERSION,
                        "variables": {
                            "title":    title,
                            "abstract": abstract,
                        },
                    },
                    "input": [],   # content is fully in template variables
                    "reasoning": {"summary": "auto"},
                    "store": True,
                    "include": [
                        "reasoning.encrypted_content",
                        "web_search_call.action.sources",
                    ],
                },
            }
            fh.write(json.dumps(request, ensure_ascii=False) + "\n")

    state = load_state()
    state["batch_file"]   = str(batch_file)
    state["record_count"] = len(rows)
    state["prepared_at"]  = datetime.now().isoformat()
    state.pop("batch_id",        None)
    state.pop("output_file_id",  None)
    state.pop("error_file_id",   None)
    save_state(state)

    size_mb = batch_file.stat().st_size / 1024 / 1024
    print(f"Prepared {len(rows):,} records  ->  {batch_file}  ({size_mb:.1f} MB)")
    return batch_file


# ── Phase 2: Submit ────────────────────────────────────────────────────────

def phase_submit() -> str:
    state = load_state()
    client = get_client()

    # ── Step 1: upload (skip if already done and saved in state) ──────────
    file_id = state.get("file_id")
    if file_id:
        print(f"Reusing already-uploaded file: {file_id}")
    else:
        if "batch_file" not in state:
            raise RuntimeError("Run 'prepare' first.")
        batch_file = Path(state["batch_file"])
        if not batch_file.exists():
            raise FileNotFoundError(f"Batch file not found: {batch_file}")
        size_mb = batch_file.stat().st_size / 1024 / 1024
        print(f"Uploading {batch_file.name} ({size_mb:.1f} MB) ...")
        with batch_file.open("rb") as fh:
            upload = client.files.create(file=fh, purpose="batch")
        file_id = upload.id
        # Save file_id immediately so a later timeout doesn't lose it
        state["file_id"]      = file_id
        state["submitted_at"] = datetime.now().isoformat()
        save_state(state)
        print(f"File ID : {file_id}")

    # ── Step 2: create batch (retry up to 3 times on timeout) ─────────────
    print(f"Creating batch from {file_id} ...")
    batch = None
    for attempt in range(1, 4):
        try:
            batch = client.batches.create(
                input_file_id=file_id,
                endpoint="/v1/responses",
                completion_window="24h",
            )
            break
        except Exception as exc:
            print(f"  Attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                print(f"  Retrying in 10s ...")
                time.sleep(10)
            else:
                raise

    state["file_id"]      = file_id
    state["batch_id"]     = batch.id
    state["submitted_at"] = datetime.now().isoformat()
    state["status"]       = batch.status
    save_state(state)

    print(f"Batch ID : {batch.id}")
    print(f"Status   : {batch.status}")
    print(f"State saved -> {STATE_FILE}")
    return batch.id


# ── Phase 3: Poll ──────────────────────────────────────────────────────────

def phase_poll(interval: int = 60) -> None:
    state = load_state()
    if "batch_id" not in state:
        raise RuntimeError("Run 'submit' first.")

    batch_id         = state["batch_id"]
    client           = get_client()
    terminal_statuses = {"completed", "failed", "expired", "cancelled"}

    print(f"Polling batch {batch_id} every {interval}s ...")
    while True:
        batch  = client.batches.retrieve(batch_id)
        counts = batch.request_counts
        total  = counts.total or 1
        pct    = counts.completed / total * 100
        print(
            f"[{datetime.now():%H:%M:%S}]  {batch.status:12s}  "
            f"{counts.completed:>6,}/{total:,} ({pct:5.1f}%)  "
            f"failed={counts.failed}"
        )

        # Save current progress on every poll so the dashboard can read it
        state["status"] = batch.status
        state["request_counts"] = {
            "completed": counts.completed,
            "failed": counts.failed,
            "total": counts.total,
        }
        save_state(state)

        if batch.status in terminal_statuses:
            state["completed_at"]   = datetime.now().isoformat()
            state["output_file_id"] = batch.output_file_id
            state["error_file_id"]  = batch.error_file_id
            save_state(state)

            print(f"\nBatch finished: {batch.status}")
            if batch.output_file_id:
                print(f"  Output file : {batch.output_file_id}")
            if batch.error_file_id:
                print(f"  Error file  : {batch.error_file_id}")
            break

        time.sleep(interval)


# ── Phase 4: Save ──────────────────────────────────────────────────────────

_UPDATE_SQL = """
    UPDATE filtered_records
    SET
        llm_is_hydrocarbon      = ?,
        llm_fuel_classification = ?,
        llm_ml_paradigm         = ?,
        llm_ml_method           = ?,
        llm_ml_role             = ?,
        llm_ml_algorithm        = ?,
        llm_ml_strategy         = ?,
        llm_ml_data_source      = ?,
        llm_confidence          = ?,
        llm_justification       = ?,
        llm_models              = ?,
        llm_result_raw          = ?,
        llm_batch_id            = ?,
        llm_processed_at        = ?,
        llm_tokens_input        = ?,
        llm_tokens_output       = ?,
        llm_tokens_reasoning    = ?
    WHERE openalex_id = ?
"""


def _parse_result_row(parsed: dict) -> tuple:
    """
    Extract DB column values from a v3 LLM JSON response.
    Schema: fuel_relevant, fuel_classification, ml_role, ml_algorithm,
            ml_strategy, ml_data_source, models, confidence, justification
    """
    hydro_raw = parsed.get("fuel_relevant")
    is_hydro  = 1 if hydro_raw is True else 0 if hydro_raw is False else None

    fuel_cls     = json.dumps(parsed.get("fuel_classification", []), ensure_ascii=False)
    # v3 fields
    ml_role      = json.dumps(parsed.get("ml_role",      []), ensure_ascii=False)
    ml_algo      = json.dumps(parsed.get("ml_algorithm", []), ensure_ascii=False)
    ml_strategy  = json.dumps(parsed.get("ml_strategy",  []), ensure_ascii=False)
    ml_data_src  = str(parsed.get("ml_data_source", "") or "")
    # v2 compat: null out old fields — v3 prompt no longer produces them
    ml_para      = json.dumps([], ensure_ascii=False)
    ml_method    = json.dumps([], ensure_ascii=False)
    models_str   = json.dumps(parsed.get("models",       []), ensure_ascii=False)
    confidence   = str(parsed.get("confidence",    "") or "")
    justif       = str(parsed.get("justification", "") or "")

    return (is_hydro, fuel_cls, ml_para, ml_method,
            ml_role, ml_algo, ml_strategy, ml_data_src,
            confidence, justif, models_str)


def _process_jsonl_lines(lines_iter, conn: sqlite3.Connection,
                          batch_id: str, now_iso: str) -> tuple[int, int, int, int]:
    """
    Iterate over JSONL lines, parse each response, write to DB.
    Returns (saved, json_ok, json_fail, errors).
    """
    saved = json_ok = json_fail = errors = 0

    for line_num, raw_line in enumerate(lines_iter, 1):
        raw_line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace").strip()
        if not raw_line:
            continue

        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            print(f"  Line {line_num}: JSON parse error -- {exc}")
            errors += 1
            continue

        custom_id = item.get("custom_id")
        if not custom_id:
            errors += 1
            continue

        resp        = item.get("response", {})
        status_code = resp.get("status_code", 0)
        if status_code != 200:
            err_msg = resp.get("body", {}).get("error", {}).get("message", "unknown")
            print(f"  {custom_id}: HTTP {status_code} -- {err_msg}")
            errors += 1
            continue

        body     = resp.get("body", {})
        raw_text = _extract_output_text(body)
        parsed   = parse_llm_json(raw_text)

        if parsed:
            json_ok += 1
            (is_hydro, fuel_cls, ml_para, ml_method,
             ml_role, ml_algo, ml_strategy, ml_data_src,
             confidence, justif, models_str) = _parse_result_row(parsed)
        else:
            json_fail   = json_fail + 1
            is_hydro    = None
            fuel_cls    = ml_para = ml_method = None
            ml_role     = ml_algo = ml_strategy = ml_data_src = None
            confidence  = justif = models_str = None

        usage            = body.get("usage", {})
        tokens_input     = usage.get("input_tokens", 0)
        tokens_output    = usage.get("output_tokens", 0)
        tokens_reasoning = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)

        conn.execute(
            _UPDATE_SQL,
            (
                is_hydro, fuel_cls, ml_para, ml_method,
                ml_role, ml_algo, ml_strategy, ml_data_src,
                confidence, justif, models_str, raw_text,
                batch_id, now_iso,
                tokens_input, tokens_output, tokens_reasoning,
                custom_id,
            ),
        )
        saved += 1
        if saved % 5_000 == 0:
            conn.commit()
            print(f"  ... {saved:,} rows written")

    return saved, json_ok, json_fail, errors


def phase_save(local_file: Path | None = None) -> None:
    """
    Parse batch output and write results to the DB.

    local_file — read from a local JSONL file instead of downloading from OpenAI.
                 Use this for large files already saved to disk.
    """
    state         = load_state()
    error_file_id = state.get("error_file_id")
    batch_id      = state.get("batch_id", "unknown")

    conn = sqlite3.connect(DB_PATH)
    ensure_result_columns(conn)
    now_iso = datetime.now().isoformat()

    if local_file:
        if not local_file.exists():
            print(f"File not found: {local_file}")
            conn.close()
            return
        size_mb = local_file.stat().st_size / 1024 / 1024
        print(f"Reading local file {local_file.name} ({size_mb:.0f} MB) ...")
        with local_file.open("r", encoding="utf-8") as fh:
            saved, json_ok, json_fail, errors = _process_jsonl_lines(
                fh, conn, batch_id, now_iso
            )
    else:
        output_file_id = state.get("output_file_id")
        if not output_file_id:
            print("No output file ID in state — batch may not have completed yet.")
            conn.close()
            return
        client = get_client()
        print(f"Streaming results from {output_file_id} ...")
        # Stream chunk-by-chunk so large files don't drop the connection
        with client.files.with_streaming_response.content(output_file_id) as stream:
            def _stream_lines():
                buf = b""
                for chunk in stream.iter_bytes(chunk_size=65_536):
                    buf += chunk
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        yield line_bytes
                if buf:
                    yield buf
            saved, json_ok, json_fail, errors = _process_jsonl_lines(
                _stream_lines(), conn, batch_id, now_iso
            )

    conn.commit()
    conn.close()

    print(f"\nRows saved      : {saved:,}")
    print(f"JSON parsed OK  : {json_ok:,}")
    print(f"JSON parse fail : {json_fail}  (raw text in llm_result_raw)")
    print(f"API errors      : {errors}")

    if error_file_id and not local_file:
        client = get_client()
        print(f"\nDownloading error log {error_file_id} ...")
        err_content = client.files.content(error_file_id).text
        err_path = BATCH_DIR / "errors.jsonl"
        err_path.write_text(err_content, encoding="utf-8")
        print(f"Error details -> {err_path}")

    if error_file_id:
        print(f"\nDownloading error log {error_file_id} ...")
        err_content = client.files.content(error_file_id).text
        err_path    = BATCH_DIR / "errors.jsonl"
        err_path.write_text(err_content, encoding="utf-8")
        print(f"Error details -> {err_path}")


# ── Cost Estimator ─────────────────────────────────────────────────────────

def phase_estimate(model: str = "gpt-5.4-nano", limit: int | None = None,
                   overwrite: bool = False,
                   subset_sql: str | None = None) -> None:
    """
    Print a cost estimate for unprocessed records.
    No API calls — uses character counts from the local database.
    """
    if model not in MODEL_PRICING:
        print(f"Unknown model '{model}'. Available: {', '.join(MODEL_PRICING)}")
        return

    conn = sqlite3.connect(DB_PATH)

    if subset_sql:
        clean_subset = subset_sql.strip().rstrip(";")
        cte = f"WITH _fr_subset AS (\n{clean_subset}\n)\n"
        table = "_fr_subset"
    else:
        cte = ""
        table = "filtered_records"

    total_in_db = conn.execute(
        f"{cte}SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]

    # rowid is a physical SQLite column — not available through a CTE
    order_col = "openalex_id" if subset_sql else "rowid"

    # llm_is_hydrocarbon may not exist yet — fall back to all records
    try:
        unprocessed_total = conn.execute(
            f"{cte}SELECT COUNT(*) FROM {table} WHERE llm_is_hydrocarbon IS NULL"
        ).fetchone()[0]
        if overwrite:
            base_query = f"{cte}SELECT title, abstract_text FROM {table} ORDER BY {order_col}"
        else:
            base_query = (
                f"{cte}SELECT title, abstract_text FROM {table} "
                f"WHERE llm_is_hydrocarbon IS NULL ORDER BY {order_col}"
            )
    except sqlite3.OperationalError:
        unprocessed_total = total_in_db
        base_query = f"{cte}SELECT title, abstract_text FROM {table} ORDER BY {order_col}"

    if limit:
        base_query += f" LIMIT {limit}"
    rows = conn.execute(base_query).fetchall()
    conn.close()

    already_done = total_in_db - unprocessed_total

    if not rows:
        print("All records already processed.")
        return

    n = len(rows)

    total_chars      = sum(len((r[0] or "") + (r[1] or "")) for r in rows)
    est_input_tokens = total_chars / 4 + n * PROMPT_OVERHEAD_TOKENS
    est_output_tokens = n * AVG_OUTPUT_TOKENS

    pricing              = MODEL_PRICING[model]
    reasoning_per_paper  = pricing["reasoning_est_per_paper"]
    est_reasoning_tokens = n * reasoning_per_paper

    cost_input     = est_input_tokens     / 1_000_000 * pricing["input"]
    cost_output    = est_output_tokens    / 1_000_000 * pricing["output"]
    cost_reasoning = (
        est_reasoning_tokens / 1_000_000 * pricing["reasoning"]
        if pricing["reasoning"] else 0.0
    )
    total_cost = cost_input + cost_output + cost_reasoning
    avg_chars  = total_chars / n

    cached_rate = pricing.get("cached_input")
    cached_note = (
        f"  Cached input rate        :  ${cached_rate:.4f}/1M  "
        f"(saves {100*(1 - cached_rate/pricing['input']):.0f}% vs uncached)"
        if cached_rate else
        "  Cached input rate        :  n/a"
    )

    D = "-" * 62
    print()
    print(D)
    print(f"  Cost Estimate  --  model: {model}")
    print(f"  {pricing.get('note', '')}")
    print(D)
    limit_note = f" (first {limit:,} of {unprocessed_total:,} unprocessed)" if limit else ""
    print(f"  Total records in DB      : {total_in_db:>10,}")
    print(f"  Already processed        : {already_done:>10,}")
    print(f"  To process now           : {n:>10,}{limit_note}")
    print(f"  Avg chars per paper      : {avg_chars:>10,.0f}")
    print(f"  Avg est. input tok/paper : {est_input_tokens/n:>10,.0f}")
    print(cached_note)
    print(D)
    print(
        f"  {'Input tokens':<22} {est_input_tokens:>13,.0f}  "
        f"@ ${pricing['input']:.4f}/1M  = ${cost_input:>9,.2f}"
    )
    print(
        f"  {'Output tokens':<22} {est_output_tokens:>13,.0f}  "
        f"@ ${pricing['output']:.4f}/1M  = ${cost_output:>9,.2f}"
    )
    if pricing["reasoning"]:
        print(
            f"  {'Reasoning tokens':<22} {est_reasoning_tokens:>13,.0f}  "
            f"@ ${pricing['reasoning']:.4f}/1M  = ${cost_reasoning:>9,.2f}"
        )
    print(D)
    print(f"  TOTAL ESTIMATED COST     :               ${total_cost:>12,.2f}")
    print(D)
    print()

    # Quick comparison across all models
    print("  Comparison across all models (Batch API, short context):")
    print(f"  {'Model':<16} {'$/1M in':>9} {'$/1M cached':>12} {'$/1M out':>10} {'TOTAL $':>10}")
    print(f"  {'-'*63}")
    for m, p in MODEL_PRICING.items():
        ci = est_input_tokens  / 1_000_000 * p["input"]
        co = est_output_tokens / 1_000_000 * p["output"]
        cr = (
            n * p["reasoning_est_per_paper"] / 1_000_000 * p["reasoning"]
            if p["reasoning"] else 0.0
        )
        c_rate = f"${p['cached_input']:.4f}" if p.get("cached_input") else "  n/a  "
        marker = " <" if m == model else ""
        print(
            f"  {m:<16} {p['input']:>9.4f} {c_rate:>12} {p['output']:>10.4f} "
            f"{ci+co+cr:>10.2f}{marker}"
        )
    print()
    print("  Notes:")
    print("  * Token counts are approximations (~4 chars / token).")
    print("  * Verify final pricing on platform.openai.com/docs/pricing.")
    print("  * Run with --model <name> to highlight a different model.")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Batch-process paper records with the OpenAI Responses API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python llm_classify.py estimate\n"
            "  python llm_classify.py prepare --limit 50\n"
            "  python llm_classify.py run --limit 1000\n"
            "  python llm_classify.py run           # all records\n"
        ),
    )
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to the filtered SQLite database (default: openalex_works_filtered.sqlite)",
    )
    p.add_argument(
        "--batch-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory for batch run files (default: batch_runs/)",
    )
    p.add_argument(
        "--subset-sql",
        default=None,
        metavar="SQL",
        help="SQL query selecting the subset of filtered_records to process (default: all records)",
    )
    sub = p.add_subparsers(dest="phase", required=True)

    # estimate
    p_est = sub.add_parser("estimate", help="Print cost estimate (no API calls)")
    p_est.add_argument(
        "--model", default="gpt-5.4-nano", choices=list(MODEL_PRICING),
        help="Model to use for pricing table (default: gpt-5.4-nano)",
    )
    p_est.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Estimate cost for N records only (default: all unprocessed)",
    )
    p_est.add_argument(
        "--overwrite", action="store_true",
        help="Include already-processed records in the estimate",
    )

    # prepare
    p_prep = sub.add_parser("prepare", help="Write JSONL from DB rows")
    p_prep.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Number of records to include (default: ALL)",
    )
    p_prep.add_argument(
        "--overwrite", action="store_true",
        help="Re-process records that already have LLM results (overwrite cells)",
    )

    # submit
    sub.add_parser("submit", help="Upload JSONL and create batch job")

    # poll
    p_poll = sub.add_parser("poll", help="Poll batch status until done")
    p_poll.add_argument(
        "--interval", type=int, default=60, metavar="SECS",
        help="Polling interval in seconds (default: 60)",
    )

    # save
    p_save = sub.add_parser("save", help="Download results and write to SQLite")
    p_save.add_argument(
        "--file", type=Path, default=None, metavar="PATH",
        help="Read from a local JSONL output file instead of downloading from OpenAI",
    )

    # run (full pipeline)
    p_run = sub.add_parser(
        "run",
        help="Full pipeline: prepare -> submit -> poll -> save",
    )
    p_run.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Number of records to process (default: ALL)",
    )
    p_run.add_argument(
        "--interval", type=int, default=60, metavar="SECS",
        help="Polling interval in seconds (default: 60)",
    )
    p_run.add_argument(
        "--overwrite", action="store_true",
        help="Re-process records that already have LLM results (overwrite cells)",
    )

    return p


def main() -> None:
    args = build_parser().parse_args()
    _configure_paths(getattr(args, "db", None), getattr(args, "batch_dir", None))

    subset_sql = getattr(args, "subset_sql", None)

    if args.phase == "estimate":
        phase_estimate(args.model, args.limit,
                       overwrite=getattr(args, "overwrite", False),
                       subset_sql=subset_sql)

    elif args.phase == "prepare":
        phase_prepare(args.limit, overwrite=args.overwrite, subset_sql=subset_sql)

    elif args.phase == "submit":
        phase_submit()

    elif args.phase == "poll":
        phase_poll(args.interval)

    elif args.phase == "save":
        phase_save(local_file=getattr(args, "file", None))

    elif args.phase == "run":
        result = phase_prepare(args.limit, overwrite=args.overwrite, subset_sql=subset_sql)
        if result is None:
            return
        phase_submit()
        phase_poll(args.interval)
        phase_save()


if __name__ == "__main__":
    main()
