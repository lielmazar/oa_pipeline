"""
process_all_works.py  (optimised — Tier 1 + 2 + live TUI)
==========================================================
Full-pipeline orchestrator for OpenAlex → SQLite.

Reads .gz partition files from a local snapshot directory (fastest) or
falls back to streaming from the public S3 bucket.

Terminal output:
  • Live progress bar with file count and percentage
  • Per-worker status (processing file, or downloading + speed)
  • Aggregate record statistics with per-reason skip breakdown
  • Color-coded, single-block display that refreshes in-place

Usage
-----
    # LOCAL — fastest (data already on disk)
    python process_all_works.py --local-dir ~/data/openalex/works

    # S3 — downloads one file at a time per worker
    python process_all_works.py

    # Common options
    python process_all_works.py --local-dir ./works --workers 8
    python process_all_works.py --max-files 10        # smoke test
    python process_all_works.py --no-color             # plain output

Logs are written to pipeline.log (file only — the TUI owns stdout).
State is persisted in pipeline_state.json for safe resume.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED, Future
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Enable ANSI escape sequences on Windows 10+
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    os.system("")

# ---------------------------------------------------------------------------
# Optional boto3 — only needed for S3 mode
# ---------------------------------------------------------------------------
_HAS_BOTO3 = False
try:
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config as BotocoreConfig

    _HAS_BOTO3 = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Sibling module
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from filter_dataset import (  # noqa: E402
    process_records_to_rows,
    ensure_db,
    _INSERT_SQL,
    _CREATE_TABLE_SQL,
    _init_terms,
)

# ---------------------------------------------------------------------------
# S3 constants (used only in S3 mode)
# ---------------------------------------------------------------------------
S3_BUCKET = "openalex"
S3_REGION = "us-east-1"
S3_PREFIX = "data/works/"

# ---------------------------------------------------------------------------
# Local paths
# ---------------------------------------------------------------------------
DEFAULT_STATE_FILE = SCRIPT_DIR / "pipeline_state.json"
DEFAULT_LOG_FILE = SCRIPT_DIR / "pipeline.log"
DEFAULT_OUTPUT_DB = SCRIPT_DIR / "openalex_works_filtered.sqlite"

# ---------------------------------------------------------------------------
# Logging — file only (the TUI owns stdout)
# ---------------------------------------------------------------------------
logger = logging.getLogger("pipeline")


def _setup_logging(log_file: Path) -> None:
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


# ═══════════════════════════════════════════════════════════════════════════
# ANSI helpers
# ═══════════════════════════════════════════════════════════════════════════
class _C:
    """ANSI colour codes — disabled when --no-color or non-TTY."""
    RST = ""
    BOLD = ""
    DIM = ""
    GREEN = ""
    YELLOW = ""
    CYAN = ""
    RED = ""
    MAGENTA = ""
    BLUE = ""

    @classmethod
    def enable(cls) -> None:
        cls.RST = "\033[0m"
        cls.BOLD = "\033[1m"
        cls.DIM = "\033[2m"
        cls.GREEN = "\033[32m"
        cls.YELLOW = "\033[33m"
        cls.CYAN = "\033[36m"
        cls.RED = "\033[31m"
        cls.MAGENTA = "\033[35m"
        cls.BLUE = "\033[34m"


# ═══════════════════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════════════════
_CLEAR_LINE = "\033[K"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"

# Human-readable labels for skip reasons (order matters for display)
_SKIP_LABELS: dict[str, str] = {
    "not_oa": "not open access",
    "not_english": "not English",
    "no_doi": "no DOI",
    "no_abstract": "no abstract",
    "no_keywords": "no keywords",
    "empty_abstract": "empty abstract",
    "json_error": "JSON error",
    "empty_line": "empty line",
    "no_id": "no ID",
    "processing_error": "error",
}


def _bar(fraction: float, width: int = 36) -> str:
    filled = int(width * min(fraction, 1.0))
    return f"{_C.GREEN}{'█' * filled}{_C.DIM}{'░' * (width - filled)}{_C.RST}"


def _mini_bar(fraction: float, width: int = 8) -> str:
    filled = int(width * min(fraction, 1.0))
    return f"{_C.GREEN}{'█' * filled}{_C.DIM}{'░' * (width - filled)}{_C.RST}"


def _short_key(s3_key: str) -> str:
    """data/works/updated_date=2025-10-10/part_0028.gz → 2025-10-10/part_0028"""
    return s3_key.replace("data/works/updated_date=", "").replace(".gz", "")


def _fmt_dur(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _fmt_size(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class Display:
    """Live terminal display that overwrites itself in-place."""

    def __init__(
        self,
        total_files: int,
        num_workers: int,
        worker_status: dict,
        is_tty: bool,
        mode: str,  # "local" or "s3"
    ) -> None:
        self.total_files = total_files
        self.num_workers = num_workers
        self.worker_status = worker_status
        self.is_tty = is_tty
        self.mode = mode

        self.files_done: int = 0
        self.total_seen: int = 0
        self.total_inserted: int = 0
        self.total_data_mb: float = 0.0  # compressed data processed
        self.file_errors: int = 0        # whole-file failures
        self.record_errors: int = 0      # per-record processing errors
        self.recent_errors: list[str] = []  # last N error messages
        self.skip_stats: dict[str, int] = {}
        self.last_match_title: str = ""
        self.free_gb: float = 0.0
        self.db_size_mb: float = 0.0

        self._start = time.monotonic()
        self._lines_printed = 0
        self._last_render = 0.0
        self._speed_samples: list[tuple[float, int]] = []

    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    def _files_per_min(self) -> float:
        now = time.monotonic()
        self._speed_samples.append((now, self.files_done))
        cutoff = now - 120.0
        self._speed_samples = [
            (t, n) for t, n in self._speed_samples if t >= cutoff
        ]
        if len(self._speed_samples) < 2:
            elapsed = self._elapsed()
            if elapsed < 5 or self.files_done == 0:
                return 0.0
            return self.files_done / elapsed * 60
        oldest_t, oldest_n = self._speed_samples[0]
        dt = now - oldest_t
        dn = self.files_done - oldest_n
        if dt < 1:
            return 0.0
        return dn / dt * 60

    def _eta(self) -> str:
        fpm = self._files_per_min()
        remaining = self.total_files - self.files_done
        if remaining <= 0:
            return "done"
        if fpm <= 0:
            return "calculating…"
        return _fmt_dur(remaining / fpm * 60)

    def _records_per_sec(self) -> float:
        elapsed = self._elapsed()
        if elapsed < 2:
            return 0.0
        return self.total_seen / elapsed

    def add_skip_stats(self, skips: dict[str, int]) -> None:
        for reason, count in skips.items():
            self.skip_stats[reason] = self.skip_stats.get(reason, 0) + count

    def add_errors(self, file_key: str, errors: list[str]) -> None:
        """Record per-record errors from a processed file."""
        self.record_errors += len(errors)
        for e in errors:
            self.recent_errors.append(f"{_short_key(file_key)}: {e}")
        # Keep only the most recent 20
        if len(self.recent_errors) > 20:
            self.recent_errors = self.recent_errors[-20:]

    def render(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_render) < 0.30:
            return
        self._last_render = now

        if self.is_tty:
            lines = self._build_lines()
            if self._lines_printed > 0:
                sys.stdout.write(f"\033[{self._lines_printed}A")
            for line in lines:
                sys.stdout.write(f"{_CLEAR_LINE}{line}\n")
            sys.stdout.flush()
            self._lines_printed = len(lines)
        else:
            pct = (
                (self.files_done / self.total_files * 100)
                if self.total_files
                else 0
            )
            print(
                f"[{self.files_done:,}/{self.total_files:,} ({pct:.1f}%)] "
                f"seen={self.total_seen:,} ins={self.total_inserted:,} "
                f"err={self.file_errors}+{self.record_errors}",
                flush=True,
            )

    def _build_lines(self) -> list[str]:
        C = _C
        elapsed = self._elapsed()
        pct = (
            (self.files_done / self.total_files * 100) if self.total_files else 0
        )
        fpm = self._files_per_min()
        hit_rate = (
            (self.total_inserted / self.total_seen * 100)
            if self.total_seen
            else 0
        )
        rps = self._records_per_sec()

        lines: list[str] = []

        # ---- Header + progress bar ----
        src = "local" if self.mode == "local" else "S3"
        lines.append(
            f" {C.BOLD}OpenAlex ({src}) → SQLite{C.RST}"
            f"                    elapsed {C.CYAN}{_fmt_dur(elapsed)}{C.RST}"
        )
        lines.append(
            f" {_bar(pct / 100)}  "
            f"{self.files_done:,} / {self.total_files:,}  "
            f"({C.BOLD}{pct:.1f}%{C.RST})"
        )
        if fpm > 0:
            speed_str = (
                f"{_fmt_count(int(rps))} rec/s"
                if self.mode == "local"
                else f"{_fmt_size(self.total_data_mb / (elapsed / 60) if elapsed > 5 else 0)}/min"
            )
            lines.append(
                f" {speed_str}"
                f"  ·  {fpm:.1f} files/min"
                f"  ·  {C.CYAN}ETA ~{self._eta()}{C.RST}"
            )
        else:
            lines.append(
                f" {C.DIM}warming up…{C.RST}"
                f"  ·  {C.CYAN}ETA ~{self._eta()}{C.RST}"
            )
        lines.append("")

        # ---- Worker rows ----
        for slot in range(self.num_workers):
            try:
                ws = self.worker_status.get(slot)
            except Exception:
                ws = None

            if ws is None or ws.get("state") == "idle":
                lines.append(f" {C.DIM} ·  w{slot}  idle{C.RST}")
                continue

            state = ws.get("state", "?")
            key = _short_key(ws.get("key", "?"))
            if len(key) > 28:
                key = "…" + key[-27:]

            if state == "downloading":
                dl_bytes = ws.get("dl_bytes", 0)
                dl_total = ws.get("dl_total", 1)
                dl_speed = ws.get("dl_speed", 0)
                dl_frac = dl_bytes / dl_total if dl_total else 0
                dl_mb = dl_bytes / (1024 ** 2)
                tot_mb = dl_total / (1024 ** 2)
                lines.append(
                    f" {C.BLUE} ↓{C.RST}  w{slot}  {key}"
                    f"  {_mini_bar(dl_frac)}"
                    f"  {dl_mb:.0f}/{tot_mb:.0f} MB"
                    f"  {C.DIM}{dl_speed:.1f} MB/s{C.RST}"
                )
            elif state == "processing":
                size_mb = ws.get("size_mb", 0)
                size_str = f"  {C.DIM}{size_mb:.0f} MB{C.RST}" if size_mb else ""
                lines.append(
                    f" {C.YELLOW} ⚙{C.RST}  w{slot}  {key}"
                    f"  {C.YELLOW}filtering…{C.RST}{size_str}"
                )
            elif state == "done":
                lines.append(
                    f" {C.GREEN} ✓{C.RST}  w{slot}  {key}"
                    f"  {C.DIM}done{C.RST}"
                )
            elif state == "error":
                lines.append(
                    f" {C.RED} ✗{C.RST}  w{slot}  {key}"
                    f"  {C.RED}error{C.RST}"
                )
            else:
                lines.append(f" {C.DIM} ?  w{slot}  {key}  {state}{C.RST}")

        lines.append("")

        # ---- Records ----
        ins_color = C.GREEN if self.total_inserted > 0 else C.DIM
        lines.append(
            f" {C.BOLD}Records{C.RST}   "
            f"{_fmt_count(self.total_seen)} seen"
            f"  →  {ins_color}{self.total_inserted:,} matched{C.RST}"
            f"  ({hit_rate:.4f}%)"
        )

        # ---- Skip reasons (all, sorted by count) ----
        total_skipped = sum(self.skip_stats.values())
        if total_skipped > 0:
            sorted_reasons = sorted(
                self.skip_stats.items(), key=lambda kv: kv[1], reverse=True
            )
            # Show top 6 in one line
            parts: list[str] = []
            for reason, count in sorted_reasons[:6]:
                pct_r = count / total_skipped * 100
                label = _SKIP_LABELS.get(reason, reason)
                parts.append(f"{label} {pct_r:.0f}%")
            lines.append(
                f" {C.BOLD}Skipped{C.RST}   "
                + f"  {C.DIM}·{C.RST}  ".join(parts)
            )
        else:
            lines.append(f" {C.BOLD}Skipped{C.RST}   {C.DIM}—{C.RST}")

        # ---- System ----
        total_errs = self.file_errors + self.record_errors
        if total_errs > 0:
            parts = []
            if self.file_errors:
                parts.append(f"{self.file_errors} file")
            if self.record_errors:
                parts.append(f"{self.record_errors} record")
            err_part = f"{C.RED}{' + '.join(parts)} errors{C.RST}"
        else:
            err_part = f"{C.DIM}0 errors{C.RST}"
        lines.append(
            f" {C.BOLD}System{C.RST}    "
            f"{self.free_gb:.1f} GB free"
            f"  ·  DB {self.db_size_mb:.1f} MB"
            f"  ·  {err_part}"
        )

        # ---- Last match ----
        if self.last_match_title:
            title = self.last_match_title
            if len(title) > 60:
                title = title[:57] + "…"
            lines.append(
                f" {C.BOLD}Last hit{C.RST}  {C.MAGENTA}{title}{C.RST}"
            )
        else:
            lines.append(
                f" {C.BOLD}Last hit{C.RST}  {C.DIM}—{C.RST}"
            )

        # ---- Recent errors (last 3) ----
        if self.recent_errors:
            lines.append("")
            lines.append(
                f" {C.RED}{C.BOLD}Errors{C.RST}"
            )
            for e in self.recent_errors[-3:]:
                if len(e) > 72:
                    e = e[:69] + "…"
                lines.append(f" {C.RED} ·{C.RST}  {C.DIM}{e}{C.RST}")

        lines.append("")
        return lines

    def print_final_summary(self, state: dict) -> None:
        C = _C
        elapsed = self._elapsed()
        pct = (
            (self.files_done / self.total_files * 100)
            if self.total_files
            else 0
        )
        hit_rate = (
            (self.total_inserted / self.total_seen * 100)
            if self.total_seen
            else 0
        )

        print()
        print(f" {C.BOLD}{'═' * 64}{C.RST}")
        print(f" {C.BOLD}Pipeline complete{C.RST}  —  {_fmt_dur(elapsed)}")
        print(f" {'═' * 64}")
        print()
        print(
            f"   Files      {self.files_done:,} / {self.total_files:,}"
            f" ({pct:.1f}%)"
        )
        print(
            f"   Records    {self.total_seen:,} seen  →  "
            f"{C.GREEN}{self.total_inserted:,} matched{C.RST}"
            f"  ({hit_rate:.4f}%)"
        )
        if self.total_data_mb > 0:
            print(f"   Data       {_fmt_size(self.total_data_mb)}")
        if self.file_errors or self.record_errors:
            parts = []
            if self.file_errors:
                parts.append(f"{self.file_errors} file(s) failed")
            if self.record_errors:
                parts.append(f"{self.record_errors} record-level errors")
            print(f"   Errors     {C.RED}{', '.join(parts)}{C.RST}")
        print()

        total_skipped = sum(self.skip_stats.values())
        if total_skipped > 0:
            print(f"   {C.BOLD}Skip breakdown{C.RST} ({total_skipped:,} total)")
            sorted_reasons = sorted(
                self.skip_stats.items(), key=lambda kv: kv[1], reverse=True
            )
            max_count = sorted_reasons[0][1] if sorted_reasons else 1
            for reason, count in sorted_reasons:
                pct_r = count / total_skipped * 100
                label = _SKIP_LABELS.get(reason, reason)
                bw = int(30 * count / max_count)
                bar_str = f"{C.DIM}{'▪' * bw}{C.RST}"
                print(
                    f"   {bar_str} {label:>16s}  "
                    f"{count:>12,}  ({pct_r:5.1f}%)"
                )
            print()

        if self.recent_errors:
            print(
                f"   {C.BOLD}Recent errors{C.RST}"
                f" ({self.file_errors + self.record_errors} total"
                f" — full details in pipeline.log)"
            )
            for e in self.recent_errors[-10:]:
                if len(e) > 68:
                    e = e[:65] + "…"
                print(f"   {C.RED}·{C.RST} {C.DIM}{e}{C.RST}")
            print()

        print(f" {C.DIM}{'─' * 64}{C.RST}")
        print(
            f" {C.DIM}State saved to pipeline_state.json  ·  "
            f"Full log in pipeline.log{C.RST}"
        )
        print()


# ═══════════════════════════════════════════════════════════════════════════
# File listing — local or S3
# ═══════════════════════════════════════════════════════════════════════════
def list_local_files(local_dir: Path) -> list[str]:
    """Discover .gz files under *local_dir* and return S3-style keys."""
    print(f" Scanning {local_dir} …", end="", flush=True)
    keys: list[str] = []
    for gz in sorted(local_dir.rglob("*.gz")):
        # Build a key like  data/works/updated_date=2025-10-10/part_0028.gz
        try:
            rel = gz.relative_to(local_dir)
        except ValueError:
            continue
        key = "data/works/" + str(rel).replace("\\", "/")
        keys.append(key)
    print(f"  found {len(keys):,} files.")
    logger.info(f"Local scan: found {len(keys):,} .gz files in {local_dir}")
    return keys


def _s3_client():
    return boto3.client(
        "s3",
        region_name=S3_REGION,
        config=BotocoreConfig(signature_version=UNSIGNED),
    )


def list_s3_files() -> list[str]:
    print(" Listing partition files from S3…", end="", flush=True)
    client = _s3_client()
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            if key.endswith(".gz"):
                keys.append(key)
    keys.sort()
    print(f"  found {len(keys):,} files.")
    logger.info(f"S3 listing: found {len(keys):,} partition files.")
    return keys


def _key_to_local_path(key: str, local_dir: Path) -> Path:
    """Convert S3-style key back to a local path."""
    # key: data/works/updated_date=2025-10-10/part_0028.gz
    # local: local_dir / updated_date=2025-10-10 / part_0028.gz
    rel = key.removeprefix("data/works/")
    return local_dir / rel


# ═══════════════════════════════════════════════════════════════════════════
# Disk helpers
# ═══════════════════════════════════════════════════════════════════════════
def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def _db_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 ** 2) if path.exists() else 0.0
    except OSError:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════
# State persistence  (Windows / OneDrive safe)
# ═══════════════════════════════════════════════════════════════════════════
def load_state(state_file: Path) -> dict:
    if state_file.exists():
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "all_files": [],
        "processed_files": [],
        "failed_files": [],
        "total_records_seen": 0,
        "total_records_inserted": 0,
        "started_at": None,
        "last_updated": None,
    }


def save_state(state: dict, state_file: Path) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    for attempt in range(5):
        try:
            tmp.replace(state_file)
            return
        except PermissionError:
            if attempt < 4:
                time.sleep(0.2 * (attempt + 1))
            else:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                with state_file.open("w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# Worker — LOCAL mode  (runs in a child process)
# ═══════════════════════════════════════════════════════════════════════════
def _worker_local(
    key: str,
    gz_path: str,           # full local path, as string for pickling
    worker_status: dict,
    slot_id: int,
    keyword_config_path: str | None = None,
) -> dict:
    """Process a local .gz file. No download needed."""
    if keyword_config_path:
        import json as _json_worker
        _cfg = _json_worker.loads(Path(keyword_config_path).read_text(encoding="utf-8"))
        _grps = _cfg.get("groups", [])

        def _flatten_grp(g):
            if "subgroups" in g:
                return [t for sg in g["subgroups"] for t in sg.get("terms", [])]
            return g.get("terms", [])

        _ft = _flatten_grp(_grps[0]) if len(_grps) > 0 else _cfg.get("fuel_terms")
        _it = _flatten_grp(_grps[1]) if len(_grps) > 1 else _cfg.get("impact_terms")
        _init_terms(_ft, _it)
    else:
        _init_terms()

    local = Path(gz_path)
    try:
        size_mb = local.stat().st_size / (1024 ** 2)
    except OSError:
        size_mb = 0.0

    if worker_status is not None:
        worker_status[slot_id] = {
            "state": "processing",
            "key": key,
            "size_mb": size_mb,
        }

    try:
        seen, matched, rows, skips, record_errors = process_records_to_rows(local)

        if worker_status is not None:
            worker_status[slot_id] = {"state": "done", "key": key}
        last_title = rows[-1][2] if rows else ""

        return {
            "s3_key": key,
            "seen": seen,
            "inserted": matched,
            "data_mb": size_mb,
            "rows": rows,
            "skips": skips,
            "last_title": last_title,
            "record_errors": record_errors,
            "error": None,
        }
    except Exception as e:
        import traceback
        if worker_status is not None:
            worker_status[slot_id] = {"state": "error", "key": key}
        return {
            "s3_key": key,
            "seen": 0,
            "inserted": 0,
            "data_mb": 0.0,
            "rows": [],
            "skips": {},
            "last_title": "",
            "record_errors": [f"FATAL: {type(e).__name__}: {e}\n{traceback.format_exc()}"],
            "error": str(e),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Worker — S3 mode  (runs in a child process)
# ═══════════════════════════════════════════════════════════════════════════
def _worker_s3(
    s3_key: str,
    temp_dir: str,
    worker_status: dict,
    slot_id: int,
    keyword_config_path: str | None = None,
) -> dict:
    """Download one S3 partition, filter it, return matched rows."""
    if keyword_config_path:
        import json as _json_worker
        _cfg = _json_worker.loads(Path(keyword_config_path).read_text(encoding="utf-8"))
        _grps = _cfg.get("groups", [])

        def _flatten_grp(g):
            if "subgroups" in g:
                return [t for sg in g["subgroups"] for t in sg.get("terms", [])]
            return g.get("terms", [])

        _ft = _flatten_grp(_grps[0]) if len(_grps) > 0 else _cfg.get("fuel_terms")
        _it = _flatten_grp(_grps[1]) if len(_grps) > 1 else _cfg.get("impact_terms")
        _init_terms(_ft, _it)
    else:
        _init_terms()

    local_path = Path(temp_dir) / f"w{slot_id}_{os.getpid()}.gz"
    dl_mb = 0.0

    try:
        client = _s3_client()
        head = client.head_object(Bucket=S3_BUCKET, Key=s3_key)
        total_bytes: int = head.get("ContentLength", 0)

        if worker_status is not None:
            worker_status[slot_id] = {
                "state": "downloading",
                "key": s3_key,
                "dl_bytes": 0,
                "dl_total": total_bytes,
                "dl_speed": 0.0,
            }

        downloaded = 0
        chunk_size = 8 * 1024 * 1024
        dl_start = time.time()

        with local_path.open("wb") as fout:
            response = client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            body = response["Body"]
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                fout.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - dl_start
                speed = (
                    (downloaded / (1024 ** 2)) / elapsed if elapsed > 0 else 0
                )
                if worker_status is not None:
                    worker_status[slot_id] = {
                        "state": "downloading",
                        "key": s3_key,
                        "dl_bytes": downloaded,
                        "dl_total": total_bytes,
                        "dl_speed": speed,
                    }

        dl_mb = downloaded / (1024 ** 2)

        if worker_status is not None:
            worker_status[slot_id] = {"state": "processing", "key": s3_key}
        seen, matched, rows, skips, record_errors = process_records_to_rows(local_path)

        if worker_status is not None:
            worker_status[slot_id] = {"state": "done", "key": s3_key}
        last_title = rows[-1][2] if rows else ""

        return {
            "s3_key": s3_key,
            "seen": seen,
            "inserted": matched,
            "data_mb": dl_mb,
            "rows": rows,
            "skips": skips,
            "last_title": last_title,
            "record_errors": record_errors,
            "error": None,
        }
    except Exception as e:
        import traceback
        if worker_status is not None:
            worker_status[slot_id] = {"state": "error", "key": s3_key}
        return {
            "s3_key": s3_key,
            "seen": 0,
            "inserted": 0,
            "data_mb": 0.0,
            "rows": [],
            "skips": {},
            "last_title": "",
            "record_errors": [f"FATAL: {type(e).__name__}: {e}\n{traceback.format_exc()}"],
            "error": str(e),
        }
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════════════
def _init_main_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    ensure_db(conn)  # creates table + adds new columns if upgrading
    return conn


def _insert_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    if rows:
        conn.executemany(_INSERT_SQL, rows)
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════════
def run_pipeline(
    output_db: Path,
    state_file: Path,
    max_files: int | None,
    min_free_gb: float,
    workers: int,
    use_color: bool,
    local_dir: Path | None,
    keyword_config: Path | None = None,
) -> None:
    is_tty = sys.stdout.isatty()
    if use_color and is_tty:
        _C.enable()

    mode = "local" if local_dir is not None else "s3"

    if mode == "s3" and not _HAS_BOTO3:
        print(
            "ERROR: boto3 is not installed (needed for S3 mode).\n"
            "Install it with:  pip install boto3\n"
            "Or use --local-dir to process files from disk."
        )
        sys.exit(1)

    state = load_state(state_file)

    # ---- First run: enumerate files ----
    if not state["all_files"]:
        if mode == "local":
            state["all_files"] = list_local_files(local_dir)
        else:
            state["all_files"] = list_s3_files()
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state, state_file)

    all_files = state["all_files"]
    processed_set: set[str] = set(state["processed_files"])
    failed_set: set[str] = set(state["failed_files"])

    pending = [k for k in all_files if k not in processed_set]
    if max_files is not None:
        pending = pending[:max_files]

    total_files = len(all_files)
    already_done = len(processed_set)

    logger.info(
        f"Pipeline start — {total_files:,} total, "
        f"{already_done:,} done, {len(pending):,} to process, "
        f"{workers} worker(s), mode={mode}."
    )

    if not pending:
        print(" Nothing left to process. All done!")
        return

    # ---- Shared state for worker progress ----
    if is_tty:
        manager = multiprocessing.Manager()
        worker_status: dict = manager.dict()
        for slot in range(workers):
            worker_status[slot] = {"state": "idle"}
    else:
        manager = None
        worker_status = None   # workers skip status IPC; no sleep overhead

    # ---- Display ----
    display = Display(total_files, workers, worker_status, is_tty, mode)
    display.files_done = already_done
    display.total_seen = state["total_records_seen"]
    display.total_inserted = state["total_records_inserted"]

    # ---- DB ----
    conn = _init_main_db(output_db)

    # ---- Temp dir (S3 mode only) ----
    temp_dir: str | None = None
    if mode == "s3":
        temp_dir = tempfile.mkdtemp(prefix="openalex_", dir=SCRIPT_DIR)

    # ---- Hide cursor ----
    if is_tty:
        sys.stdout.write(_HIDE_CURSOR)
        sys.stdout.flush()
        first_frame = display._build_lines()
        for _ in first_frame:
            print()
        display._lines_printed = len(first_frame)

    submitted: dict[Future, tuple[str, int]] = {}
    free_slots: list[int] = list(range(workers))
    idx = 0

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            while idx < len(pending) or submitted:
                # ---- Disk check ----
                db_dir = output_db.parent
                fg = _free_gb(db_dir)
                display.free_gb = fg
                display.db_size_mb = _db_size_mb(output_db)

                if fg < min_free_gb:
                    logger.error(
                        f"Disk space low: {fg:.2f} GB < {min_free_gb} GB"
                    )
                    break

                # ---- Submit work ----
                while free_slots and idx < len(pending):
                    key = pending[idx]
                    idx += 1
                    slot = free_slots.pop(0)

                    if mode == "local":
                        gz_path = str(
                            _key_to_local_path(key, local_dir)
                        )
                        fut = pool.submit(
                            _worker_local,
                            key,
                            gz_path,
                            worker_status,
                            slot,
                            str(keyword_config) if keyword_config else None,
                        )
                    else:
                        if worker_status is not None:
                            worker_status[slot] = {
                                "state": "downloading",
                                "key": key,
                                "dl_bytes": 0,
                                "dl_total": 0,
                                "dl_speed": 0.0,
                            }
                        fut = pool.submit(
                            _worker_s3,
                            key,
                            temp_dir,
                            worker_status,
                            slot,
                            str(keyword_config) if keyword_config else None,
                        )

                    submitted[fut] = (key, slot)

                if not submitted:
                    break

                # ---- Wait for completions ----
                done, _ = wait(
                    submitted.keys(),
                    timeout=0.05,
                    return_when=FIRST_COMPLETED,
                )

                for fut in done:
                    key, slot = submitted.pop(fut)
                    free_slots.append(slot)
                    if worker_status is not None:
                        worker_status[slot] = {"state": "idle"}

                    result = fut.result()

                    # --- Log and track record-level errors ---
                    rec_errs = result.get("record_errors", [])
                    if rec_errs:
                        display.add_errors(key, rec_errs)
                        for e in rec_errs:
                            logger.error(f"  {_short_key(key)}: {e}")

                    if result["error"]:
                        logger.error(f"FAILED {key}: {result['error']}")
                        display.file_errors += 1
                        display.add_errors(key, [f"FILE FAILED: {result['error']}"])
                        if key not in failed_set:
                            state["failed_files"].append(key)
                            failed_set.add(key)
                        save_state(state, state_file)
                        display.files_done = len(
                            state["processed_files"]
                        ) + len(state["failed_files"])
                        continue

                    _insert_rows(conn, result["rows"])

                    state["processed_files"].append(key)
                    processed_set.add(key)
                    state["total_records_seen"] += result["seen"]
                    state["total_records_inserted"] += result["inserted"]
                    save_state(state, state_file)

                    display.files_done = len(state["processed_files"])
                    display.total_seen = state["total_records_seen"]
                    display.total_inserted = state["total_records_inserted"]
                    display.total_data_mb += result["data_mb"]
                    display.add_skip_stats(result["skips"])
                    if result["last_title"]:
                        display.last_match_title = result["last_title"]

                    err_note = (
                        f"  ({len(rec_errs)} record errors)"
                        if rec_errs
                        else ""
                    )
                    logger.info(
                        f"[{display.files_done:,}/{total_files:,}] {key} — "
                        f"ins: {result['inserted']}  "
                        f"seen: {result['seen']:,}{err_note}"
                    )

                display.render()

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        if is_tty:
            sys.stdout.write(_SHOW_CURSOR)
            sys.stdout.flush()
        conn.close()
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        save_state(state, state_file)

    display.render(force=True)
    display.print_final_summary(state)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Process OpenAlex works partition files into a filtered SQLite "
            "database.  Reads from a local snapshot directory (--local-dir) "
            "or streams from the public S3 bucket."
        )
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Path to local OpenAlex works directory "
            "(e.g. ~/data/openalex/works).  "
            "Skips all S3 downloading — much faster."
        ),
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=DEFAULT_OUTPUT_DB,
        help=f"Path to output SQLite database (default: {DEFAULT_OUTPUT_DB.name})",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Path to pipeline state JSON (default: {DEFAULT_STATE_FILE.name})",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help=f"Path to log file (default: {DEFAULT_LOG_FILE.name})",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        metavar="N",
        help="Stop after processing N files (useful for smoke tests).",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=2.0,
        help="Abort if free disk space falls below this (GB, default: 2.0).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Number of parallel workers (default: 4).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output.",
    )
    parser.add_argument(
        "--keyword-config",
        type=Path,
        default=None,
        metavar="JSON",
        help="JSON file with {fuel_terms: [...], impact_terms: [...]}. "
             "Defaults to built-in term lists when omitted.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    _setup_logging(args.log_file)
    run_pipeline(
        output_db=args.output_db,
        state_file=args.state_file,
        max_files=args.max_files,
        min_free_gb=args.min_free_gb,
        workers=args.workers,
        use_color=not args.no_color,
        local_dir=args.local_dir,
        keyword_config=args.keyword_config,
    )
