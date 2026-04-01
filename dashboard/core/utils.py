"""core/utils.py — General-purpose utilities for the dashboard."""
from __future__ import annotations

from pathlib import Path


def tail(path: Path, n: int) -> list[str]:
    """Return the last *n* lines of a potentially large file."""
    chunk_size = 8192
    lines: list[str] = []
    with open(path, "rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        buf = b""
        while remaining > 0 and len(lines) < n + 1:
            read_size = min(chunk_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            buf = f.read(read_size) + buf
            lines = buf.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]
