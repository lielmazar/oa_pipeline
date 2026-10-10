"""Runs on the EC2 instance (started by the launch script that remote.py generates).

Executes one pipeline command, copies its output to <job_root>/job.log every
30 seconds, and writes <job_root>/status.json (running → succeeded/failed),
so the job can be followed from your own machine with `remote.py logs`.

    python deploy/job_agent.py --job-root s3://bucket/openalex/deploy/jobs/<id> -- python -m pipeline.run_all
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline.lib import storage

UPLOAD_EVERY_SEC = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job-root", required=True, help="S3 (or local) folder for job.log and status.json")
    p.add_argument("--log-file", type=Path, default=Path("/tmp/oa-job.log"))
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd:
        p.error("no command given")

    log_uri = storage.join(args.job_root, "job.log")
    status_uri = storage.join(args.job_root, "status.json")
    started = time.monotonic()
    status = {"state": "running", "command": cmd, "started_at": _now()}
    storage.write_json(status_uri, status)

    stop = threading.Event()
    lock = threading.Lock()

    def upload_log() -> None:
        with lock:
            try:
                storage.upload(args.log_file, log_uri)
            except Exception as e:  # noqa: BLE001 — log shipping must never kill the job
                print(f"[job_agent] log upload failed: {e}", file=sys.stderr)

    def uploader() -> None:
        while not stop.wait(UPLOAD_EVERY_SEC):
            upload_log()

    def on_term(signum, _frame) -> None:   # spot reclaim / shutdown: record what happened
        status.update(state="interrupted", signal=signum, finished_at=_now())
        storage.write_json(status_uri, status)
        upload_log()
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, on_term)
    threading.Thread(target=uploader, daemon=True).start()

    with args.log_file.open("w", encoding="utf-8") as log:
        log.write(f"[job_agent] {_now()} running: {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
            log.flush()
        code = proc.wait()
        log.write(f"[job_agent] {_now()} finished with exit code {code}\n")

    stop.set()
    upload_log()
    status.update(state="succeeded" if code == 0 else "failed", exit_code=code,
                  finished_at=_now(), duration_sec=round(time.monotonic() - started))
    storage.write_json(status_uri, status)
    return code


if __name__ == "__main__":
    sys.exit(main())
