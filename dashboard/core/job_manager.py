"""
Subprocess lifecycle manager with SSE-friendly streaming.

Each job runs in a background thread that reads stdout/stderr line by line
and appends to a bounded deque. The stream_output() generator yields lines
to the SSE endpoint using a Condition variable for efficient blocking.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from typing import Generator, Optional


class JobManager:
    def __init__(self):
        self._jobs: dict = {}
        self._global_lock = threading.Lock()

    # ── Launch ────────────────────────────────────────────────────────────────

    def launch(
        self,
        script_key: str,
        script_path: str,
        args: list,
        cwd: str,
        label: str,
        pipeline_id: int | None = None,
        workers: int = 1,
    ) -> dict:
        """Launch a script as a subprocess. Returns job summary dict."""
        with self._global_lock:
            # run_pipeline: app-level lock — only one search at a time across all pipelines
            if script_key == "run_pipeline":
                for job in self._jobs.values():
                    if job["script"] == "run_pipeline" and job["status"] == "running":
                        raise ValueError(
                            f"A search is already running (job {job['id']}, "
                            f"pipeline {job.get('pipeline_id')}). "
                            "Only one OpenAlex search can run at a time."
                        )
            else:
                for job in self._jobs.values():
                    if (
                        job["script"] == script_key
                        and job["status"] == "running"
                        and job.get("pipeline_id") == pipeline_id
                    ):
                        raise ValueError(
                            f"'{script_key}' is already running (job {job['id']}). "
                            "Kill it first or wait for it to finish."
                        )

            job_id = uuid.uuid4().hex[:12]
            cmd = [sys.executable, "-u", str(script_path)] + [str(a) for a in args]
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb"}

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,  # own process group for clean kill
                    env=env,
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to launch script: {exc}") from exc

            cond = threading.Condition()
            job: dict = {
                "id": job_id,
                "script": script_key,
                "pipeline_id": pipeline_id,
                "label": label,
                "workers": workers,
                "cmd": cmd,
                "pid": proc.pid,
                "status": "running",
                "started_at": time.time(),
                "ended_at": None,
                "returncode": None,
                "output": deque(maxlen=5000),
                "_seq": 0,
                "_cond": cond,
                "_process": proc,
            }

            t = threading.Thread(target=self._reader, args=(job,), daemon=True)
            self._jobs[job_id] = job
            t.start()

        return self._summary(job)

    # ── Reader thread ─────────────────────────────────────────────────────────

    def _reader(self, job: dict) -> None:
        """Background thread: read subprocess lines, append to deque."""
        try:
            for line in job["_process"].stdout:
                with job["_cond"]:
                    job["output"].append((job["_seq"], line.rstrip("\n\r")))
                    job["_seq"] += 1
                    job["_cond"].notify_all()
        except Exception:
            pass
        finally:
            try:
                job["_process"].wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass

            with job["_cond"]:
                rc = job["_process"].returncode
                job["returncode"] = rc
                if job["status"] not in ("killed",):
                    job["status"] = "done" if (rc is not None and rc == 0) else "error"
                job["ended_at"] = time.time()
                job["_cond"].notify_all()

    # ── Kill ──────────────────────────────────────────────────────────────────

    def kill(self, job_id: str) -> bool:
        """Send SIGTERM to the process group, escalate to SIGKILL after 5s."""
        job = self._jobs.get(job_id)
        if not job or job["status"] != "running":
            return False

        with job["_cond"]:
            job["status"] = "killed"
            job["_cond"].notify_all()

        try:
            pgid = os.getpgid(job["_process"].pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

        def _force_kill() -> None:
            time.sleep(5)
            try:
                if job["_process"].poll() is None:
                    pgid = os.getpgid(job["_process"].pid)
                    os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass

        threading.Thread(target=_force_kill, daemon=True).start()
        return True

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[dict]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list:
        return [self._summary(j) for j in self._jobs.values()]

    def _summary(self, job: dict) -> dict:
        return {
            "id": job["id"],
            "script": job["script"],
            "pipeline_id": job.get("pipeline_id"),
            "label": job["label"],
            "workers": job.get("workers", 1),
            "pid": job["pid"],
            "status": job["status"],
            "started_at": job["started_at"],
            "ended_at": job["ended_at"],
            "returncode": job["returncode"],
            "line_count": len(job["output"]),
        }

    # ── SSE stream ────────────────────────────────────────────────────────────

    def stream_output(self, job_id: str, after_seq: int = -1) -> Generator:
        """
        Generator yielding (seq, line) tuples.

        Blocks efficiently using a Condition variable when no new lines are
        available and the job is still running. Terminates when the job ends
        and all buffered lines have been yielded.

        after_seq: last sequence number the client already has (-1 = send all).
        """
        job = self._jobs.get(job_id)
        if not job:
            return

        last_yielded = after_seq

        while True:
            with job["_cond"]:
                snapshot = list(job["output"])
                status = job["status"]

            # Yield any new lines
            new_items = [(s, l) for s, l in snapshot if s > last_yielded]
            for seq, line in new_items:
                yield seq, line
                last_yielded = seq

            # Job is finished — do one final drain and return
            if status in ("done", "error", "killed"):
                with job["_cond"]:
                    final = list(job["output"])
                for seq, line in final:
                    if seq > last_yielded:
                        yield seq, line
                        last_yielded = seq
                return

            # Wait for new lines or job-end notification
            with job["_cond"]:
                # Re-check under lock to avoid missing a notification
                has_new = any(s > last_yielded for s, _ in job["output"])
                still_running = job["status"] == "running"
                if not has_new and still_running:
                    job["_cond"].wait(timeout=15)
