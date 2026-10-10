"""Run one worker function per snapshot partition across processes.

Each worker writes its own outputs plus a small stats JSON as its final step.
That stats file is the completion marker: on restart, partitions that already
have one are skipped, so an interrupted run (spot reclaim, Ctrl-C) resumes.
"""
from __future__ import annotations

import logging
import multiprocessing
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("oa")


def setup_logging(log_dir: Path, stage: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{stage}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.propagate = False
    return path


def fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def _call(worker: Callable[[dict], dict], task: dict, attempts: int) -> dict:
    for attempt in range(1, attempts + 1):
        try:
            return worker(task)
        except Exception as e:  # noqa: BLE001 — retried, then reported as a failed partition
            if attempt == attempts:
                return {"name": task["name"], "failed": True,
                        "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def run_tasks(
    tasks: list[dict],
    worker: Callable[[dict], dict],
    workers: int,
    initializer: Callable | None = None,
    initargs: tuple = (),
    attempts: int = 3,
    size_key: str = "size",
) -> list[dict]:
    """Run worker(task) for every task. Returns one result dict per task.

    workers == 1 runs inline in this process (used by tests and for debugging).
    """
    results: list[dict] = []
    total = len(tasks)
    total_bytes = sum(t.get(size_key, 0) for t in tasks) or 1
    done_bytes = 0
    start = time.monotonic()
    last_log = 0.0

    def _record(res: dict, task: dict) -> None:
        nonlocal done_bytes, last_log
        results.append(res)
        done_bytes += task.get(size_key, 0)
        if res.get("failed"):
            log.error("FAILED %s: %s", task["name"], res["error"])
        now = time.monotonic()
        if now - last_log >= 30 or len(results) == total:
            last_log = now
            elapsed = now - start
            frac = done_bytes / total_bytes
            eta = elapsed / frac * (1 - frac) if frac > 0 else 0
            recs = sum(r.get("records_in", 0) for r in results)
            log.info("progress %d/%d partitions (%.1f%% of bytes) · %s records · %.0f rec/s · "
                     "elapsed %s · ETA %s", len(results), total, frac * 100, f"{recs:,}",
                     recs / elapsed if elapsed else 0, fmt_duration(elapsed), fmt_duration(eta))

    if not tasks:
        return results

    if workers <= 1:
        if initializer:
            initializer(*initargs)
        for task in tasks:
            _record(_call(worker, task, attempts), task)
        return results

    # Largest partitions first keeps all cores busy until the end.
    queue = sorted(tasks, key=lambda t: -t.get(size_key, 0))
    # "spawn" rather than fork: the parent may hold DuckDB/boto3 threads, and forking a
    # multi-threaded process can deadlock the children.
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, initializer=initializer, initargs=initargs,
                             mp_context=ctx) as pool:
        pending = {}
        while queue or pending:
            while queue and len(pending) < workers * 2:
                task = queue.pop(0)
                pending[pool.submit(_call, worker, task, attempts)] = task
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                task = pending.pop(fut)
                _record(fut.result(), task)
    return results
