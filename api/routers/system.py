"""System info and default-keywords endpoints."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.config import settings
from api.deps import CurrentUser

router = APIRouter(prefix="/api/system", tags=["system"])


# ── System info ───────────────────────────────────────────────────────────────

@router.get("/info")
def system_info(user: CurrentUser):
    return {"cpu_count": os.cpu_count() or 1}


# ── Default keywords (system-level) ──────────────────────────────────────────

def _kw_path() -> Path:
    return settings.pipeline_scripts_dir / "default_keywords.json"


def _load_default_keywords() -> dict:
    p = _kw_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"groups": []}


def _json_to_groups(text: str) -> list:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    result = []
    for g in data.get("groups", []):
        subgroups = []
        for sg in g.get("subgroups", []):
            subgroups.append({
                "name": sg["name"],
                "terms": sg.get("terms", []),
                **({"is_others": True} if sg.get("is_default") or sg.get("is_others") else {}),
            })
        if not subgroups:
            subgroups = [{"name": "Default", "is_others": True, "terms": []}]
        result.append({"name": g["name"], "subgroups": subgroups})
    return result


@router.get("/default-keywords")
def get_default_keywords(user: CurrentUser):
    return _load_default_keywords()


@router.get("/default-keywords/json")
def download_default_keywords(user: CurrentUser):
    text = json.dumps(_load_default_keywords(), indent=2, ensure_ascii=False)
    return Response(
        content=text,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="keywords.json"'},
    )


@router.post("/default-keywords/json")
async def upload_default_keywords(request: Request, user: CurrentUser):
    raw = ""
    try:
        body = await request.json()
        raw = (body.get("json") or "").strip() if isinstance(body, dict) else ""
    except Exception:
        pass
    if not raw:
        raw = (await request.body()).decode(errors="replace").strip()
    if not raw:
        raise HTTPException(400, "empty body")
    try:
        groups = _json_to_groups(raw)
    except Exception as exc:
        raise HTTPException(400, f"Invalid JSON: {exc}")
    _kw_path().parent.mkdir(parents=True, exist_ok=True)
    _kw_path().write_text(json.dumps({"groups": groups}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "groups": len(groups)}
