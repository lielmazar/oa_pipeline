"""Export router — download SQLite or chunked CSVs for Claude Desktop."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from api.business.export import build_csv_export_zip
from api.db.pipelines import get_pipeline
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["export"])


@router.get("/{pipeline_id}/export")
def export_data(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
    background_tasks: BackgroundTasks,
    fmt: str = Query("csv", description="Export format: csv or sqlite"),
):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "pipeline not found")

    db_path = Path(p["output_db_path"]) if p.get("output_db_path") else None
    if not db_path or not db_path.exists():
        raise HTTPException(404, "research database not found")

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (p.get("name") or f"pipeline_{pipeline_id}")
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if fmt == "sqlite":
        return FileResponse(
            str(db_path),
            media_type="application/x-sqlite3",
            filename=f"{safe_name}_{ts}.sqlite",
            headers={"Cache-Control": "no-store"},
        )

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="oa_export_")
    os.close(tmp_fd)
    try:
        zip_bytes = build_csv_export_zip(db_path, meta_db=db, pipeline_id=pipeline_id)
        with open(tmp_path, "wb") as f:
            f.write(zip_bytes)
    except Exception as exc:
        os.unlink(tmp_path)
        raise HTTPException(500, str(exc))

    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"{safe_name}_{ts}.zip",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{pipeline_id}/export/irr")
def export_irr(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
):
    """Download IRR statistics and QA ratings as JSON."""
    from api.business.qa import compute_irr, list_ratings

    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "pipeline not found")
    return {
        "irr": compute_irr(db, pipeline_id),
        "ratings": list_ratings(db, pipeline_id),
    }


@router.get("/{pipeline_id}/export/schema")
def export_schema(
    pipeline_id: int,
    user: CurrentUser,
    db: Db,
):
    """Download the research_schema.json file directly."""
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "pipeline not found")

    import json
    from api.business.export import _load_schema
    schema = _load_schema()
    return schema
