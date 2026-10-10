"""
LLM Batch processing — OpenAI Batch API via SDK.

All batch logic in one module: prepare JSONL, upload, submit, poll,
download results, parse into SQLite, estimate cost.  Uses stored
prompts (``/v1/responses``) so the prompt text is never embedded in
the JSONL — only the prompt ID + version + template variables.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BATCH_SIZE_LIMIT = 50_000
COLLECT_PROGRESS_FILE = "collect_progress.json"
STREAM_PROGRESS_BYTES = 32 * 1024 * 1024
ROW_PROGRESS_EVERY = 2500
DOWNLOAD_MAX_RETRIES = 10
DOWNLOAD_CHUNK_SIZE = 256 * 1024

AVG_OUTPUT_TOKENS = 250
PROMPT_OVERHEAD_TOKENS = 300

# Columns that the new schema writes for each classified record
_LLM_COLUMNS = [
    ("fuel_relevant", "INTEGER"),
    ("research_field", "TEXT"),
    ("uses_ml", "INTEGER"),
    ("confidence", "TEXT"),
    ("justification", "TEXT"),
    ("llm_result_raw", "TEXT"),
    ("llm_batch_id", "TEXT"),
    ("llm_processed_at", "TEXT"),
    ("llm_tokens_input", "INTEGER"),
    ("llm_tokens_output", "INTEGER"),
    ("llm_tokens_reasoning", "INTEGER"),
]

_DEPRECATED_COLUMNS = {
    "llm_fuel_classification", "llm_ml_paradigm", "llm_ml_method",
    "llm_ml_role", "llm_ml_algorithm", "llm_ml_data_source",
    "llm_scope", "llm_models", "llm_research_objective",
    "llm_value_chain_stage",
    "llm_is_hydrocarbon", "llm_research_field", "llm_uses_ml",
    "llm_confidence", "llm_justification",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client() -> OpenAI:
    return OpenAI()


def _state_path(batch_dir: Path) -> Path:
    return batch_dir / "state.json"


def _collect_progress_path(batch_dir: Path) -> Path:
    return batch_dir / COLLECT_PROGRESS_FILE


def load_state(batch_dir: Path) -> dict:
    p = _state_path(batch_dir)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_state(batch_dir: Path, state: dict) -> None:
    batch_dir.mkdir(parents=True, exist_ok=True)
    _state_path(batch_dir).write_text(json.dumps(state, indent=2))


def _progress_write(batch_dir: Path, payload: dict) -> None:
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        data = {**payload, "updated_at": datetime.now(timezone.utc).isoformat()}
        _collect_progress_path(batch_dir).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Schema migration — ensure columns, drop deprecated
# ---------------------------------------------------------------------------

def ensure_result_columns(conn: sqlite3.Connection) -> None:
    """Add new LLM columns and drop deprecated ones via table rebuild."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}

    for col, typedef in _LLM_COLUMNS:
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE filtered_records ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass

    deprecated_present = _DEPRECATED_COLUMNS & existing
    if deprecated_present:
        _drop_columns(conn, deprecated_present)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_fr_openalex_id ON filtered_records(openalex_id)")
    conn.commit()


def _drop_columns(conn: sqlite3.Connection, to_drop: set[str]) -> None:
    """Rebuild table without deprecated columns (SQLite compat)."""
    cols_info = conn.execute("PRAGMA table_info(filtered_records)").fetchall()
    keep_cols = [r[1] for r in cols_info if r[1] not in to_drop]
    if not keep_cols:
        return
    cols_csv = ", ".join(f'"{c}"' for c in keep_cols)
    conn.execute("BEGIN")
    try:
        conn.execute(f"CREATE TABLE _fr_new AS SELECT {cols_csv} FROM filtered_records")
        conn.execute("DROP TABLE filtered_records")
        conn.execute("ALTER TABLE _fr_new RENAME TO filtered_records")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def parse_llm_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1 if lines[0].startswith("```") else 0
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end])
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        return result[0]
    return result if isinstance(result, dict) else None


def _extract_output_text(body: dict) -> str:
    for item in body.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    return ""


def _parse_result_row(parsed: dict) -> tuple:
    hydro_raw = parsed.get("fuel_relevant")
    is_hydro = 1 if hydro_raw is True else 0 if hydro_raw is False else None

    research_field = str(parsed.get("research_field", "none") or "none")

    ml_raw = parsed.get("uses_ml")
    uses_ml = 1 if ml_raw is True else 0 if ml_raw is False else None

    confidence = str(parsed.get("confidence", "") or "")
    justification = str(parsed.get("justification", "") or "")

    return is_hydro, research_field, uses_ml, confidence, justification


_UPDATE_SQL = """
    UPDATE filtered_records
    SET
        fuel_relevant        = ?,
        research_field       = ?,
        uses_ml              = ?,
        confidence           = ?,
        justification        = ?,
        llm_result_raw       = ?,
        llm_batch_id         = ?,
        llm_processed_at     = ?,
        llm_tokens_input     = ?,
        llm_tokens_output    = ?,
        llm_tokens_reasoning = ?
    WHERE openalex_id = ?
"""


# ---------------------------------------------------------------------------
# Phase: Prepare
# ---------------------------------------------------------------------------

def prepare_batch(
    db_path: Path,
    batch_dir: Path,
    *,
    prompt_id: str,
    prompt_version: str,
    model: str,
    limit: int | None = None,
    overwrite: bool = False,
    subset_sql: str | None = None,
) -> list[Path] | None:
    batch_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_result_columns(conn)

    if subset_sql:
        clean = subset_sql.strip().rstrip(";")
        cte = f"WITH _fr_subset AS (\n{clean}\n)\n"
        table = "_fr_subset"
    else:
        cte = ""
        table = "filtered_records"

    order_col = "openalex_id" if subset_sql else "rowid"

    if overwrite:
        query = f"{cte}SELECT openalex_id, title, abstract_text FROM {table} ORDER BY {order_col}"
    else:
        query = (
            f"{cte}SELECT openalex_id, title, abstract_text FROM {table} "
            f"WHERE fuel_relevant IS NULL ORDER BY {order_col}"
        )
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    conn.close()

    if not rows:
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    chunks = [rows[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(rows), BATCH_SIZE_LIMIT)]

    batch_files: list[Path] = []
    for chunk_idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            bf = batch_dir / f"batch_input_{ts}_part{chunk_idx + 1:02d}.jsonl"
        else:
            bf = batch_dir / f"batch_input_{ts}.jsonl"

        with bf.open("w", encoding="utf-8") as fh:
            for row in chunk:
                title = row["title"] or ""
                abstract = row["abstract_text"] or ""
                request = {
                    "custom_id": row["openalex_id"],
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": {
                        "model": model,
                        "prompt": {
                            "id": prompt_id,
                            "version": prompt_version,
                            "variables": {
                                "title": title,
                                "abstract": abstract,
                            },
                        },
                        "input": [],
                        "store": True,
                    },
                }
                fh.write(json.dumps(request, ensure_ascii=False) + "\n")
        batch_files.append(bf)

    state = load_state(batch_dir)
    state["batch_files"] = [str(p) for p in batch_files]
    state["record_count"] = len(rows)
    state["prepare_limit"] = limit
    state["batch_input_file_count"] = len(batch_files)
    state["batch_input_filenames"] = [p.name for p in batch_files]
    state["prepared_at"] = datetime.now().isoformat()
    state["prompt_id"] = prompt_id
    state["prompt_version"] = prompt_version
    state["model"] = model
    state.pop("batch_id", None)
    state.pop("file_id", None)
    state.pop("output_file_id", None)
    state.pop("error_file_id", None)
    state.pop("batch_ids", None)
    save_state(batch_dir, state)
    return batch_files


# ---------------------------------------------------------------------------
# Phase: Submit
# ---------------------------------------------------------------------------

def submit_batch(batch_dir: Path) -> list[str]:
    """Upload JSONL files and create batch jobs. Returns list of batch IDs."""
    state = load_state(batch_dir)
    batch_files = [Path(p) for p in state.get("batch_files", [])]
    if not batch_files:
        bf = state.get("batch_file")
        if bf:
            batch_files = [Path(bf)]
    if not batch_files:
        raise RuntimeError("Run prepare first.")

    client = _get_client()
    batch_ids: list[str] = []

    for bf in batch_files:
        if not bf.exists():
            raise FileNotFoundError(f"Batch file not found: {bf}")

        with bf.open("rb") as fh:
            upload = client.files.create(file=fh, purpose="batch")

        batch = None
        for attempt in range(1, 4):
            try:
                batch = client.batches.create(
                    input_file_id=upload.id,
                    endpoint="/v1/responses",
                    completion_window="24h",
                )
                break
            except Exception as exc:
                if attempt < 3:
                    time.sleep(10)
                else:
                    raise RuntimeError(f"Batch creation failed after 3 attempts: {exc}") from exc

        batch_ids.append(batch.id)

    state["batch_ids"] = batch_ids
    state["batch_id"] = batch_ids[0] if len(batch_ids) == 1 else batch_ids[-1]
    state["submitted_at"] = datetime.now().isoformat()
    state["status"] = "validating"
    save_state(batch_dir, state)
    return batch_ids


# ---------------------------------------------------------------------------
# Phase: Check status
# ---------------------------------------------------------------------------

def check_status(batch_dir: Path) -> dict:
    state = load_state(batch_dir)
    if not state:
        return {"status": "not_started", "batch_id": None}

    batch_ids = state.get("batch_ids") or []
    if not batch_ids:
        single = state.get("batch_id")
        batch_ids = [single] if single else []
    if not batch_ids:
        return state

    current = state.get("status", "")
    terminal = {"completed", "failed", "expired", "cancelled"}

    if current in terminal:
        return state

    client = _get_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    agg_completed = agg_failed = agg_total = 0
    all_terminal = True
    per_batch: list[dict] = []
    output_file_ids: list[str] = []
    error_file_ids: list[str] = []
    worst_status = "completed"
    status_priority = {"completed": 0, "expired": 1, "cancelled": 2, "failed": 3,
                       "in_progress": 4, "finalizing": 4, "validating": 5}

    for bid in batch_ids:
        batch = client.batches.retrieve(bid)
        counts = batch.request_counts
        c = counts.completed if counts else 0
        f = counts.failed if counts else 0
        t = counts.total if counts else 0
        agg_completed += c
        agg_failed += f
        agg_total += t

        info = {"batch_id": bid, "status": batch.status,
                "completed": c, "failed": f, "total": t,
                "output_file_id": batch.output_file_id,
                "error_file_id": batch.error_file_id}
        per_batch.append(info)

        if batch.output_file_id:
            output_file_ids.append(batch.output_file_id)
        if batch.error_file_id:
            error_file_ids.append(batch.error_file_id)

        if batch.status not in terminal:
            all_terminal = False
        if status_priority.get(batch.status, 5) > status_priority.get(worst_status, 0):
            worst_status = batch.status

    overall_status = worst_status if all_terminal else worst_status

    state["status"] = overall_status
    state["checked_at"] = now_iso
    state["request_counts"] = {
        "completed": agg_completed,
        "failed": agg_failed,
        "total": agg_total,
    }
    state["per_batch"] = per_batch
    state["output_file_ids"] = output_file_ids
    state["error_file_ids"] = error_file_ids

    if all_terminal:
        state["completed_at"] = now_iso
        if len(output_file_ids) == 1:
            state["output_file_id"] = output_file_ids[0]

    save_state(batch_dir, state)
    return state


# ---------------------------------------------------------------------------
# Phase: Collect results
# ---------------------------------------------------------------------------

def _download_output_file(file_id: str, dest: Path, batch_dir: Path, collect_total: int | None) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".jsonl.partial")
    already = partial.stat().st_size if partial.exists() else 0

    client = _get_client()
    api_key = client.api_key
    base_url = str(client.base_url).rstrip("/")
    url = f"{base_url}/files/{file_id}/content"
    headers_base = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        already = partial.stat().st_size if partial.exists() else 0
        headers = {**headers_base}
        if already > 0:
            headers["Range"] = f"bytes={already}-"

        _progress_write(batch_dir, {
            "status": "running", "phase": "streaming",
            "bytes_received": already, "total": collect_total,
            "message": f"Downloading output file (attempt {attempt}, {already / 1024**2:.1f} MB so far)…",
        })

        try:
            with httpx.stream("GET", url, headers=headers, timeout=httpx.Timeout(30, read=120)) as resp:
                if resp.status_code == 416:
                    break
                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                expected_total = int(content_length) + already if content_length else None
                with partial.open("ab") as fh:
                    last_log_at = already
                    for chunk in resp.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        fh.write(chunk)
                        already += len(chunk)
                        if already - last_log_at >= STREAM_PROGRESS_BYTES:
                            last_log_at = already
                            pct_str = f" ({100 * already / expected_total:.1f}%)" if expected_total else ""
                            _progress_write(batch_dir, {
                                "status": "running", "phase": "streaming",
                                "bytes_received": already, "total": collect_total,
                                "message": f"Downloading ({already / 1024**2:.0f} MiB{pct_str})…",
                            })
            break
        except (httpx.RemoteProtocolError, httpx.ReadTimeout,
                httpx.ConnectError, httpx.ReadError, OSError) as exc:
            if attempt == DOWNLOAD_MAX_RETRIES:
                _progress_write(batch_dir, {
                    "status": "error", "phase": "failed",
                    "message": f"Download failed after {DOWNLOAD_MAX_RETRIES} attempts: {exc}",
                })
                raise
            time.sleep(min(2 ** attempt, 60))

    partial.rename(dest)
    _progress_write(batch_dir, {
        "status": "running", "phase": "streaming",
        "bytes_received": already, "total": collect_total,
        "message": "Download complete, processing results…",
    })


def _process_jsonl_lines(
    lines_iter,
    conn: sqlite3.Connection,
    batch_id: str,
    now_iso: str,
    *,
    batch_dir: Path,
    collect_total: int | None = None,
    collect_progress_every: int = 0,
    processed_offset: int = 0,
) -> tuple[int, int, int, int]:
    saved = json_ok = json_fail = errors = 0

    for raw_line in lines_iter:
        raw_line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace").strip()
        if not raw_line:
            continue

        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            errors += 1
            continue

        custom_id = item.get("custom_id")
        if not custom_id:
            errors += 1
            continue

        resp = item.get("response", {})
        if resp.get("status_code", 0) != 200:
            errors += 1
            continue

        body = resp.get("body", {})
        raw_text = _extract_output_text(body)
        parsed = parse_llm_json(raw_text)

        if parsed:
            json_ok += 1
            is_hydro, research_field, uses_ml, confidence, justification = _parse_result_row(parsed)
        else:
            json_fail += 1
            is_hydro = research_field = uses_ml = confidence = justification = None

        usage = body.get("usage", {})
        tokens_input = usage.get("input_tokens", 0)
        tokens_output = usage.get("output_tokens", 0)
        otd = usage.get("output_tokens_details") or {}
        tokens_reasoning = otd.get("reasoning_tokens", 0) if isinstance(otd, dict) else 0

        conn.execute(_UPDATE_SQL, (
            is_hydro, research_field, uses_ml, confidence, justification,
            raw_text, batch_id, now_iso,
            tokens_input, tokens_output, tokens_reasoning,
            custom_id,
        ))
        saved += 1

        global_saved = processed_offset + saved
        if collect_progress_every > 0 and saved % collect_progress_every == 0:
            pct = round(100.0 * global_saved / collect_total, 2) if collect_total and collect_total > 0 else None
            _progress_write(batch_dir, {
                "status": "running", "phase": "writing",
                "processed": global_saved, "total": collect_total, "pct": pct,
            })
        if saved % 5_000 == 0:
            conn.commit()

    return saved, json_ok, json_fail, errors


def _resolve_batch_file_ids(state: dict) -> list[tuple[str, str]]:
    """Return list of (batch_id, output_file_id) pairs to collect."""
    per_batch = state.get("per_batch")
    if isinstance(per_batch, list) and per_batch:
        pairs = []
        for pb in per_batch:
            bid = pb.get("batch_id")
            ofid = pb.get("output_file_id")
            if bid and ofid:
                pairs.append((bid, ofid))
        if pairs:
            return pairs

    output_file_ids = state.get("output_file_ids")
    batch_ids = state.get("batch_ids") or []
    if isinstance(output_file_ids, list) and output_file_ids:
        if len(batch_ids) == len(output_file_ids):
            return list(zip(batch_ids, output_file_ids))
        return [(batch_ids[i] if i < len(batch_ids) else f"part{i+1}", fid)
                for i, fid in enumerate(output_file_ids)]

    bid = state.get("batch_id", "unknown")
    ofid = state.get("output_file_id")
    if ofid:
        return [(bid, ofid)]
    return []


def collect_results(db_path: Path, batch_dir: Path) -> dict:
    state = load_state(batch_dir)

    rc = state.get("request_counts") or {}
    collect_total: int | None = None
    if isinstance(rc, dict):
        t = rc.get("total")
        if isinstance(t, int) and t > 0:
            collect_total = t
    if collect_total is None:
        n = state.get("record_count")
        if isinstance(n, int) and n > 0:
            collect_total = n

    file_pairs = _resolve_batch_file_ids(state)
    if not file_pairs:
        _progress_write(batch_dir, {
            "status": "error", "phase": "failed",
            "message": "No output file IDs — batch(es) may not have completed.",
        })
        return {"error": "No output file IDs in state"}

    _progress_write(batch_dir, {
        "status": "running", "phase": "starting",
        "processed": 0, "bytes_received": 0, "total": collect_total,
        "message": f"Starting collect ({len(file_pairs)} batch file(s))…",
    })

    conn = sqlite3.connect(db_path)
    ensure_result_columns(conn)
    now_iso = datetime.now().isoformat()

    try:
        total_saved = total_json_ok = total_json_fail = total_errors = 0

        for part_idx, (batch_id, output_file_id) in enumerate(file_pairs, 1):
            local_output = batch_dir / f"output_{batch_id}.jsonl"
            if not (local_output.exists() and local_output.stat().st_size > 0):
                _progress_write(batch_dir, {
                    "status": "running", "phase": "streaming",
                    "processed": total_saved, "total": collect_total,
                    "message": f"Downloading batch {part_idx}/{len(file_pairs)} output…",
                })
                _download_output_file(output_file_id, local_output, batch_dir, collect_total)

            _progress_write(batch_dir, {
                "status": "running", "phase": "reading_file",
                "message": f"Processing batch {part_idx}/{len(file_pairs)} ({local_output.stat().st_size / 1024**2:.0f} MB)…",
                "processed": total_saved, "total": collect_total,
            })

            with local_output.open("r", encoding="utf-8") as fh:
                saved, json_ok, json_fail, errors = _process_jsonl_lines(
                    fh, conn, batch_id, now_iso,
                    batch_dir=batch_dir,
                    collect_total=collect_total,
                    collect_progress_every=ROW_PROGRESS_EVERY,
                    processed_offset=total_saved,
                )
            conn.commit()
            total_saved += saved
            total_json_ok += json_ok
            total_json_fail += json_fail
            total_errors += errors

        result = {
            "saved": total_saved, "json_ok": total_json_ok,
            "json_fail": total_json_fail, "errors": total_errors,
        }

        _progress_write(batch_dir, {
            "status": "completed", "phase": "done",
            "processed": total_saved, "total": collect_total or total_saved,
            "pct": round(100.0 * total_saved / collect_total, 2) if collect_total and collect_total > 0 else None,
            "json_ok": total_json_ok, "json_fail": total_json_fail, "errors": total_errors,
            "message": "Collect finished.",
        })

        error_file_ids = state.get("error_file_ids") or []
        single_err = state.get("error_file_id")
        if single_err and single_err not in error_file_ids:
            error_file_ids.append(single_err)
        for efid in error_file_ids:
            try:
                client = _get_client()
                err_content = client.files.content(efid).text
                (batch_dir / f"errors_{efid}.jsonl").write_text(err_content, encoding="utf-8")
            except Exception:
                pass

        return result

    except Exception as exc:
        _progress_write(batch_dir, {
            "status": "error", "phase": "failed",
            "message": str(exc)[:2000],
        })
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def cancel_batch(batch_dir: Path) -> dict:
    state = load_state(batch_dir)
    batch_id = state.get("batch_id")
    if not batch_id:
        raise RuntimeError("No active batch")
    client = _get_client()
    batch = client.batches.cancel(batch_id)
    state["status"] = batch.status
    save_state(batch_dir, state)
    return {"status": batch.status}


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------

def estimate_cost(
    db_path: Path,
    model: str,
    pricing: dict,
    *,
    limit: int | None = None,
    overwrite: bool = False,
    subset_sql: str | None = None,
) -> dict:
    if model not in pricing:
        return {"error": f"Unknown model '{model}'"}

    conn = sqlite3.connect(db_path)

    if subset_sql:
        clean = subset_sql.strip().rstrip(";")
        cte = f"WITH _fr_subset AS (\n{clean}\n)\n"
        table = "_fr_subset"
    else:
        cte = ""
        table = "filtered_records"

    order_col = "openalex_id" if subset_sql else "rowid"
    total_in_db = conn.execute(f"{cte}SELECT COUNT(*) FROM {table}").fetchone()[0]

    try:
        unprocessed = conn.execute(
            f"{cte}SELECT COUNT(*) FROM {table} WHERE fuel_relevant IS NULL"
        ).fetchone()[0]
        if overwrite:
            base = f"{cte}SELECT title, abstract_text FROM {table} ORDER BY {order_col}"
        else:
            base = f"{cte}SELECT title, abstract_text FROM {table} WHERE fuel_relevant IS NULL ORDER BY {order_col}"
    except sqlite3.OperationalError:
        unprocessed = total_in_db
        base = f"{cte}SELECT title, abstract_text FROM {table} ORDER BY {order_col}"

    if limit:
        base += f" LIMIT {limit}"
    rows = conn.execute(base).fetchall()
    conn.close()

    already_done = total_in_db - unprocessed

    if not rows:
        return {"total_in_db": total_in_db, "already_done": already_done, "to_process": 0, "total_cost": 0}

    n = len(rows)
    total_chars = sum(len((r[0] or "") + (r[1] or "")) for r in rows)
    est_input = total_chars / 4 + n * PROMPT_OVERHEAD_TOKENS
    est_output = n * AVG_OUTPUT_TOKENS

    p = pricing[model]
    reasoning_per_paper = p.get("reasoning_est_per_paper", 0)
    est_reasoning = n * reasoning_per_paper

    cost_in = est_input / 1_000_000 * p["input"]
    cost_out = est_output / 1_000_000 * p["output"]
    cost_reason = est_reasoning / 1_000_000 * p["reasoning"] if p.get("reasoning") else 0.0

    comparison = []
    for m, mp in pricing.items():
        ci = est_input / 1_000_000 * mp["input"]
        co = est_output / 1_000_000 * mp["output"]
        cr = n * mp.get("reasoning_est_per_paper", 0) / 1_000_000 * mp["reasoning"] if mp.get("reasoning") else 0.0
        comparison.append({
            "model": m, "input_rate": mp["input"], "output_rate": mp["output"],
            "cached_rate": mp.get("cached_input"),
            "reasoning_rate": mp.get("reasoning"),
            "total": round(ci + co + cr, 2),
            "selected": m == model,
        })

    return {
        "model": model,
        "total_in_db": total_in_db,
        "already_done": already_done,
        "to_process": n,
        "avg_chars": round(total_chars / n),
        "est_input_tokens": round(est_input),
        "est_output_tokens": round(est_output),
        "est_reasoning_tokens": round(est_reasoning),
        "cost_input": round(cost_in, 2),
        "cost_output": round(cost_out, 2),
        "cost_reasoning": round(cost_reason, 2),
        "total_cost": round(cost_in + cost_out + cost_reason, 2),
        "cached_input_rate": p.get("cached_input"),
        "note": p.get("note", ""),
        "comparison": comparison,
    }
