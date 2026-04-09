"""Export router — GET /api/pipeline/{id}/export"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from api.business.export import build_export_zip
from api.db.pipelines import get_pipeline
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["export"])


@router.get("/{pipeline_id}/export")
def export_zip(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    background_tasks: BackgroundTasks,
    db_format: str = "csv",
    chunk_mb: int | None = Query(
        default=None,
        description="For db_format=csv_chunks: target part size in MB; omit or 0 for one CSV per table.",
    ),
):
    """Build and return a ZIP archive of the pipeline's results.

    db_format: csv | sqlite | both | csv_chunks | csv_chunks_N (legacy, e.g. csv_chunks_50).
    chunk_mb applies when db_format is csv_chunks (not legacy csv_chunks_N).
    """
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "pipeline not found")

    eff_chunk_mb: int | None = None
    fmt = db_format
    if db_format.startswith("csv_chunks_"):
        try:
            eff_chunk_mb = int(db_format.split("_")[-1])
        except ValueError:
            eff_chunk_mb = 50
        fmt = "csv_chunks"
    elif db_format == "csv_chunks":
        fmt = "csv_chunks"
        eff_chunk_mb = chunk_mb
    elif db_format not in ("csv", "sqlite", "both"):
        fmt = "csv"

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (p.get("name") or f"pipeline_{pipeline_id}")
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_name}_{ts}.zip"

    # Write ZIP to a temp file so FileResponse can stream it to the browser
    # immediately — avoids holding the full archive in memory.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="oa_export_")
    os.close(tmp_fd)

    try:
        zip_bytes = build_export_zip(p, db_format=fmt, chunk_mb=eff_chunk_mb, meta_db=db)
        with open(tmp_path, "wb") as f:
            f.write(zip_bytes)
    except Exception as exc:
        os.unlink(tmp_path)
        raise HTTPException(500, str(exc))

    background_tasks.add_task(os.unlink, tmp_path)

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )
