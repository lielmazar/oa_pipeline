"""Pipelines router — all pipeline management under /api/pipeline/"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from api.business.pipeline_status import build_stage_status
from api.business.preset_subsets import ensure_preset_subsets
from api.business.runner import (
    kill_filter_run,
    poll_filter_run,
    start_filter_run_for,
)
from api.business.subset_sql import normalize_subset_inner, validate_subset_inner
from api.config import settings
from api.db.research import count_subset_rows, get_db_stats, iter_subset_openalex_csv
from api.db.pipelines import (
    STAGES,
    STAGE_LABELS,
    advance_pipeline_stage,
    create_pipeline,
    create_saved_chart,
    create_subset,
    delete_pipeline,
    delete_saved_chart,
    delete_subset,
    fork_pipeline,
    get_next_stage,
    get_pipeline,
    get_prev_stage,
    get_subset,
    get_active_run as db_get_active_run,
    go_back_pipeline_stage,
    list_all_pipelines,
    list_events,
    list_saved_charts,
    list_subsets,
    record_event,
    set_pipeline_stage,
    update_pipeline,
    update_pipeline_paths,
)
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["pipelines"])


# ── LLM model catalog (from pipeline/llm_classify.py) ───────────────────────

@functools.lru_cache(maxsize=1)
def _llm_pricing_module():
    """Load pipeline/llm_pricing.py (no OpenAI dependency) — same source as llm_classify.py."""
    path = settings.pipeline_scripts_dir / "llm_pricing.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("oa_pipeline_llm_pricing", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _serialize_llm_pricing_table(pricing: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, row in pricing.items():
        out[name] = {
            "input": row.get("input"),
            "cached_input": row.get("cached_input"),
            "output": row.get("output"),
            "reasoning": row.get("reasoning"),
            "reasoning_est_per_paper": row.get("reasoning_est_per_paper", 0),
            "note": row.get("note") or "",
        }
    return out


@router.get("/llm/model-catalog")
def llm_model_catalog():
    """Expose MODEL_PRICING and default MODEL from pipeline/llm_pricing.py for the LLM stage UI."""
    mod = _llm_pricing_module()
    if mod is None:
        raise HTTPException(500, "llm_pricing.py not found")
    pricing = getattr(mod, "MODEL_PRICING", None)
    if not isinstance(pricing, dict) or not pricing:
        raise HTTPException(500, "MODEL_PRICING missing in llm_pricing.py")
    default_model = getattr(mod, "MODEL", next(iter(pricing.keys())))
    return {
        "default_model": default_model,
        "models": _serialize_llm_pricing_table(pricing),
        "model_ids": list(pricing.keys()),
    }


def _require_known_llm_model(model: str) -> None:
    mod = _llm_pricing_module()
    if mod is None:
        raise HTTPException(500, "llm_pricing.py not found")
    pricing = getattr(mod, "MODEL_PRICING", None)
    if not isinstance(pricing, dict) or model not in pricing:
        ids = ", ".join(pricing.keys()) if isinstance(pricing, dict) else ""
        raise HTTPException(400, f"Unknown model {model!r}. Choose one of: {ids}")


# ── Path helpers ──────────────────────────────────────────────────────────────

def _pipeline_paths(pipeline_id: int) -> dict:
    base = settings.pipelines_base_dir / str(pipeline_id)
    return {
        "output_db_path":   str(base / "output.sqlite"),
        "state_file_path":  str(base / "pipeline_state.json"),
        "batch_state_path": str(base / "batch_runs" / "state.json"),
        "report_path":      str(base / "report.html"),
        "llm_report_path":  str(base / "llm_report.html"),
        "keywords_path":    str(base / "keywords.json"),
        "log_path":         str(base / "pipeline.log"),
        "base":             str(base),
    }


def _pipeline_response(pipeline: dict) -> dict:
    pipeline["stage_label"]      = STAGE_LABELS.get(pipeline["current_stage"], pipeline["current_stage"])
    pipeline["next_stage"]       = get_next_stage(pipeline["current_stage"])
    pipeline["next_stage_label"] = STAGE_LABELS.get(pipeline.get("next_stage") or "", "")
    pipeline["prev_stage"]       = get_prev_stage(pipeline["current_stage"])
    pipeline["prev_stage_label"] = STAGE_LABELS.get(pipeline.get("prev_stage") or "", "")
    pipeline["stages"]           = [{"id": s, "label": STAGE_LABELS[s]} for s in STAGES]
    return pipeline


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


def _tail(path: Path, n: int) -> list[str]:
    chunk = 8192
    lines: list[str] = []
    with open(path, "rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        buf = b""
        while remaining > 0 and len(lines) < n + 1:
            read_size = min(chunk, remaining)
            remaining -= read_size
            f.seek(remaining)
            buf = f.read(read_size) + buf
            lines = buf.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]


def _load_default_keywords() -> dict:
    p = settings.pipeline_scripts_dir / "default_keywords.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"groups": []}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("")
def list_pipelines(user: CurrentUser, db: Db):
    return [_pipeline_response(p) for p in list_all_pipelines(db, user["id"])]


class CreatePipelineBody(BaseModel):
    name: str
    description: str = ""
    groups: list | None = None
    workers: int = 4
    min_free_gb: float = 2.0


@router.post("", status_code=201)
def create_pipeline_route(body: CreatePipelineBody, user: CurrentUser, db: Db):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    groups = body.groups if body.groups is not None else _load_default_keywords().get("groups", [])
    config = {"groups": groups, "workers": body.workers, "min_free_gb": body.min_free_gb}
    pipeline_id = create_pipeline(db, user["id"], body.name.strip(), body.description, config)
    paths = _pipeline_paths(pipeline_id)
    Path(paths["base"]).mkdir(parents=True, exist_ok=True)
    Path(paths["keywords_path"]).write_text(json.dumps({"groups": groups}, indent=2), encoding="utf-8")
    update_pipeline_paths(db, pipeline_id, **{k: v for k, v in paths.items()
                          if k in ("output_db_path", "state_file_path", "batch_state_path",
                                   "report_path", "llm_report_path")})
    record_event(db, pipeline_id, "pipeline_created")
    return _pipeline_response(get_pipeline(db, pipeline_id, user["id"]))


@router.get("/{pipeline_id}")
def get_pipeline_route(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    return _pipeline_response(p)


class UpdatePipelineBody(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None


@router.patch("/{pipeline_id}")
def update_pipeline_route(pipeline_id: int, body: UpdatePipelineBody, user: CurrentUser, db: Db):
    if not update_pipeline(db, pipeline_id, user["id"],
                           name=body.name, description=body.description, config=body.config):
        raise HTTPException(403, "not found or not owner")
    if body.config:
        cfg = body.config
        kw_path = Path(_pipeline_paths(pipeline_id)["keywords_path"])
        kw_path.parent.mkdir(parents=True, exist_ok=True)
        if "groups" in cfg or "fuel_terms" in cfg or "impact_terms" in cfg:
            existing: dict = {}
            if kw_path.exists():
                try:
                    existing = json.loads(kw_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            if "groups" in cfg:
                existing = {"groups": cfg["groups"]}
            else:
                existing.update({k: cfg[k] for k in ("fuel_terms", "impact_terms") if k in cfg})
            kw_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return _pipeline_response(get_pipeline(db, pipeline_id, user["id"]))


@router.delete("/{pipeline_id}")
def delete_pipeline_route(pipeline_id: int, user: CurrentUser, db: Db):
    if not delete_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(403, "not found or not owner")
    return {"ok": True}


# ── Stage navigation ──────────────────────────────────────────────────────────

@router.post("/{pipeline_id}/advance")
def advance_stage(pipeline_id: int, user: CurrentUser, db: Db):
    new_stage = advance_pipeline_stage(db, pipeline_id, user["id"])
    if new_stage is None:
        raise HTTPException(403, "cannot advance: not owner, or already at last stage")
    record_event(db, pipeline_id, "stage_advanced", new_stage)
    return _pipeline_response(get_pipeline(db, pipeline_id, user["id"]))


@router.post("/{pipeline_id}/go-back")
def go_back_stage(pipeline_id: int, user: CurrentUser, db: Db):
    prev_stage = go_back_pipeline_stage(db, pipeline_id, user["id"])
    if prev_stage is None:
        raise HTTPException(403, "cannot go back: not owner, or already at first stage")
    record_event(db, pipeline_id, "stage_back", prev_stage)
    return _pipeline_response(get_pipeline(db, pipeline_id, user["id"]))


class SetStageBody(BaseModel):
    stage: str


@router.post("/{pipeline_id}/set-stage")
def set_stage(pipeline_id: int, body: SetStageBody, user: CurrentUser, db: Db):
    if set_pipeline_stage(db, pipeline_id, user["id"], body.stage.strip()) is None:
        raise HTTPException(400, "invalid stage or not owner")
    record_event(db, pipeline_id, "stage_set", body.stage.strip())
    return _pipeline_response(get_pipeline(db, pipeline_id, user["id"]))


class ForkBody(BaseModel):
    name: str


@router.post("/{pipeline_id}/fork", status_code=201)
def fork_pipeline_route(pipeline_id: int, body: ForkBody, user: CurrentUser, db: Db):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    try:
        new_id = fork_pipeline(db, pipeline_id, user["id"], body.name.strip(), settings.pipelines_base_dir)
    except ValueError as e:
        raise HTTPException(404, str(e))
    record_event(db, new_id, "fork_created", f"forked_from={pipeline_id}")
    return _pipeline_response(get_pipeline(db, new_id, user["id"]))


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/status")
def pipeline_status(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    return build_stage_status(p)


@router.get("/run/active")
def active_run_status(user: CurrentUser, db: Db):
    """Return system-wide active filter run state for global navbar indicators."""
    active = db_get_active_run(db)
    if not active:
        return {"status": "idle", "run": None, "state": {}, "log_tail": [], "pipeline_id": None}
    payload = poll_filter_run(db, active["pipeline_id"])
    payload["pipeline_id"] = active["pipeline_id"]
    return payload


@router.get("/{pipeline_id}/file-count")
def file_count(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    state_path = p.get("state_file_path")
    total = None
    if state_path and Path(state_path).exists():
        try:
            total = len(json.loads(Path(state_path).read_text()).get("all_files", []))
        except Exception:
            pass
    return {"total_files": total}


# ── Events ────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/events")
def pipeline_events(pipeline_id: int, user: CurrentUser, db: Db, limit: int = 200):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_events(db, pipeline_id, limit=limit)


# ── Filter run (polling) ──────────────────────────────────────────────────────

@router.post("/{pipeline_id}/run", status_code=201)
def start_run(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    try:
        run = start_filter_run_for(db, pipeline_id, p.get("config") or {})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return run


@router.get("/{pipeline_id}/run")
def get_run(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return poll_filter_run(db, pipeline_id)


@router.delete("/{pipeline_id}/run")
def stop_run(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    return kill_filter_run(db, pipeline_id)


# ── Keywords ──────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/keywords/json")
def download_pipeline_keywords(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    groups = (p.get("config") or {}).get("groups") or _load_default_keywords().get("groups", [])
    text = json.dumps({"groups": groups}, indent=2, ensure_ascii=False)
    return Response(content=text, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="keywords.json"'})


@router.post("/{pipeline_id}/keywords/json")
async def upload_pipeline_keywords(pipeline_id: int, request: Request, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
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
    config = dict(p.get("config") or {})
    config["groups"] = groups
    if not update_pipeline(db, pipeline_id, user["id"], config=config):
        raise HTTPException(500, "update failed")
    kw_path = Path(_pipeline_paths(pipeline_id)["keywords_path"])
    kw_path.parent.mkdir(parents=True, exist_ok=True)
    kw_path.write_text(json.dumps({"groups": groups}, indent=2, ensure_ascii=False), encoding="utf-8")
    p = get_pipeline(db, pipeline_id, user["id"])
    return {"ok": True, "groups": len(groups), "config": p.get("config")}


# ── Backfill subgroups ────────────────────────────────────────────────────────

@router.post("/{pipeline_id}/backfill-subgroups")
def backfill_subgroups(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    paths = _pipeline_paths(pipeline_id)
    db_path = Path(paths["output_db_path"])
    kw_path = Path(paths["keywords_path"])
    if not db_path.exists():
        raise HTTPException(404, "database not found")
    if not kw_path.exists():
        raise HTTPException(404, "keywords.json not found")
    try:
        groups = json.loads(kw_path.read_text(encoding="utf-8")).get("groups", [])
    except Exception as exc:
        raise HTTPException(500, f"cannot read keywords.json: {exc}")

    def _build_map(g: dict) -> dict:
        return {t: sg["name"] for sg in g.get("subgroups", []) for t in sg.get("terms", [])}

    fuel_map   = _build_map(groups[0]) if len(groups) > 0 else {}
    impact_map = _build_map(groups[1]) if len(groups) > 1 else {}
    if not fuel_map and not impact_map:
        return {"updated": 0, "message": "no subgroups defined in keywords.json"}

    def _compute(terms: list, sg_map: dict) -> list:
        seen: list = []
        for t in terms:
            sg = sg_map.get(t)
            if sg and sg not in seen:
                seen.append(sg)
        return seen

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT openalex_id, matched_fuel_terms, matched_impact_terms FROM filtered_records "
            "WHERE matched_fuel_subgroups IS NULL OR matched_fuel_subgroups = '' "
            "OR matched_fuel_subgroups = '[]'"
        ).fetchall()
        updates = []
        for oid, ft_json, it_json in rows:
            ft = json.loads(ft_json or "[]") if ft_json else []
            it = json.loads(it_json or "[]") if it_json else []
            updates.append((
                json.dumps(_compute(ft, fuel_map), ensure_ascii=False),
                json.dumps(_compute(it, impact_map), ensure_ascii=False),
                oid,
            ))
        if updates:
            conn.executemany(
                "UPDATE filtered_records SET matched_fuel_subgroups=?, matched_impact_subgroups=? WHERE openalex_id=?",
                updates,
            )
            conn.commit()
        conn.close()
        return {"updated": len(updates)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Subsets ───────────────────────────────────────────────────────────────────

class SubsetBody(BaseModel):
    name: str
    sql_query: str


@router.get("/{pipeline_id}/subsets")
def get_subsets(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_subsets(db, pipeline_id)


@router.post("/{pipeline_id}/subsets", status_code=201)
def add_subset(pipeline_id: int, body: SubsetBody, user: CurrentUser, db: Db):
    raise HTTPException(
        403,
        "Creating custom subsets is disabled. This pipeline uses fixed preset subsets only.",
    )


@router.delete("/{pipeline_id}/subsets/{subset_id}")
def remove_subset(pipeline_id: int, subset_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    sub = get_subset(db, pipeline_id, subset_id)
    if not sub:
        raise HTTPException(404, "subset not found")
    if sub.get("preset_key"):
        raise HTTPException(403, "preset subsets cannot be deleted")
    if not delete_subset(db, pipeline_id, subset_id):
        raise HTTPException(404, "subset not found")
    return {"ok": True}


@router.get("/{pipeline_id}/subsets/{subset_id}/export-ids")
def export_subset_openalex_ids(
    pipeline_id: int,
    subset_id: int,
    user: CurrentUser,
    db: Db,
    limit: int = 100_000,
):
    """Download CSV of openalex_id values in a subset (capped)."""
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    db_path_str = p.get("output_db_path")
    if not db_path_str:
        raise HTTPException(404, "pipeline has no database yet")
    db_path = Path(db_path_str)
    if not db_path.exists():
        raise HTTPException(404, "database file not found")
    sub = get_subset(db, pipeline_id, subset_id)
    if not sub:
        raise HTTPException(404, "subset not found")
    try:
        it = iter_subset_openalex_csv(db_path, sub["sql_query"], limit)
        first = next(it)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    def gen():
        yield first
        yield from it

    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="subset_{subset_id}_openalex_ids.csv"',
        },
    )


@router.get("/{pipeline_id}/subsets/summary")
def subsets_summary(pipeline_id: int, user: CurrentUser, db: Db):
    """Subsets with live row counts and total dataset size from the research DB."""
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    rp = Path(p["output_db_path"]) if p.get("output_db_path") else None
    try:
        ensure_preset_subsets(db, pipeline_id, rp)
    except Exception:
        pass
    db_path_str = p.get("output_db_path")
    path = Path(db_path_str) if db_path_str else None
    dataset_total = None
    if path and path.exists():
        st = get_db_stats(path)
        if st.get("exists"):
            dataset_total = st.get("record_count")
    subsets = list_subsets(db, pipeline_id)
    enriched = []
    for s in subsets:
        row_count = None
        if path and path.exists():
            r = count_subset_rows(path, s["sql_query"])
            row_count = r.get("count") if "error" not in r else None
        enriched.append({**s, "row_count": row_count})
    return {"dataset_total": dataset_total, "subsets": enriched}


# ── Saved charts (dynamic chart configs) ───────────────────────────────────────

class SavedChartBody(BaseModel):
    name: str
    config: dict


@router.get("/{pipeline_id}/saved-charts")
def list_saved_charts_route(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    rows = list_saved_charts(db, pipeline_id, user["id"])
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["config"] = json.loads(d.get("config_json") or "{}")
        except json.JSONDecodeError:
            d["config"] = {}
        del d["config_json"]
        out.append(d)
    return out


@router.post("/{pipeline_id}/saved-charts", status_code=201)
def create_saved_chart_route(pipeline_id: int, body: SavedChartBody, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    row = create_saved_chart(db, pipeline_id, user["id"], name, json.dumps(body.config, ensure_ascii=False))
    row["config"] = json.loads(row.pop("config_json") or "{}")
    return row


@router.delete("/{pipeline_id}/saved-charts/{chart_id}")
def delete_saved_chart_route(pipeline_id: int, chart_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    if not delete_saved_chart(db, pipeline_id, chart_id, user["id"]):
        raise HTTPException(404, "saved chart not found")
    return {"ok": True}


# ── Logs ──────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/logs")
def pipeline_logs(pipeline_id: int, user: CurrentUser, db: Db, lines: int = 200):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    db_path = p.get("output_db_path")
    if not db_path:
        return {"lines": []}
    log_path = Path(db_path).parent / "pipeline.log"
    if not log_path.exists():
        return {"lines": []}
    return {"lines": _tail(log_path, lines)}


# ── LLM classification ────────────────────────────────────────────────────────

def _llm_classify_cmd(pipeline_id: int) -> list[str]:
    p_base = settings.pipelines_base_dir / str(pipeline_id)
    script  = settings.pipeline_scripts_dir / "llm_classify.py"
    db_path = p_base / "output.sqlite"
    batch_dir = p_base / "batch_runs"
    return [sys.executable, str(script), "--db", str(db_path), "--batch-dir", str(batch_dir)]


def _run_llm_cmd(args: list[str], pipeline_dir: Path) -> tuple[int, str]:
    """Run a command, return (returncode, combined stdout+stderr)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=str(pipeline_dir),
            timeout=120,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "Command timed out after 120 seconds"
    except Exception as exc:
        return -1, str(exc)


def _load_batch_state(pipeline_id: int) -> dict | None:
    path = settings.pipelines_base_dir / str(pipeline_id) / "batch_runs" / "state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save_batch_state(pipeline_id: int, state: dict) -> None:
    path = settings.pipelines_base_dir / str(pipeline_id) / "batch_runs" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _merge_batch_subset_meta(db: sqlite3.Connection, pipeline_id: int, subset_id: int | None) -> None:
    """Annotate batch state with which preset subset was used (for UI)."""
    state = _load_batch_state(pipeline_id) or {}
    if subset_id is not None:
        sub = get_subset(db, pipeline_id, subset_id)
        if sub:
            state["subset_id"] = subset_id
            state["subset_name"] = sub.get("name") or f"Subset #{subset_id}"
        else:
            state["subset_id"] = subset_id
            state["subset_name"] = None
    else:
        state["subset_id"] = None
        state["subset_name"] = "Entire dataset (unprocessed records)"
    _save_batch_state(pipeline_id, state)


def _resolve_llm_subset_inner_sql(db, pipeline_id: int, subset_id: int | None) -> str | None:
    """Return normalized inner SQL for llm_classify --subset-sql, or None for the full filtered_records table."""
    if subset_id is None:
        return None
    sub = get_subset(db, pipeline_id, subset_id)
    if not sub:
        raise HTTPException(404, "subset not found")
    inner = normalize_subset_inner(sub["sql_query"])
    err = validate_subset_inner(inner)
    if err:
        raise HTTPException(400, f"Subset cannot be used for LLM batch: {err}")
    return inner


class LlmEstimateBody(BaseModel):
    model: str = "gpt-5.4-nano"
    subset_id: int | None = None


@router.post("/{pipeline_id}/llm/estimate")
def llm_estimate(pipeline_id: int, body: LlmEstimateBody, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    _require_known_llm_model(body.model)
    inner = _resolve_llm_subset_inner_sql(db, pipeline_id, body.subset_id)
    cmd = _llm_classify_cmd(pipeline_id) + ["estimate", "--model", body.model]
    if inner:
        cmd += ["--subset-sql", inner]
    rc, out = _run_llm_cmd(cmd, settings.pipeline_scripts_dir)
    if rc != 0:
        raise HTTPException(500, out[:2000])
    return {"output": out, "returncode": rc}


class LlmSubmitBody(BaseModel):
    model: str = "gpt-5.4-nano"
    limit: int | None = None
    subset_id: int | None = None


@router.post("/{pipeline_id}/llm/submit")
def llm_submit(pipeline_id: int, body: LlmSubmitBody, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    _require_known_llm_model(body.model)
    inner = _resolve_llm_subset_inner_sql(db, pipeline_id, body.subset_id)
    # Prepare JSONL with the same model the user selected (batch body embeds model id).
    cmd = _llm_classify_cmd(pipeline_id) + ["prepare", "--model", body.model]
    if inner:
        cmd += ["--subset-sql", inner]
    if body.limit:
        cmd += ["--limit", str(body.limit)]
    # Only prepare + submit, don't poll (that's the frontend's job via /llm/status)
    rc, out = _run_llm_cmd(cmd, settings.pipeline_scripts_dir)
    if rc != 0:
        raise HTTPException(500, out[:2000])
    _merge_batch_subset_meta(db, pipeline_id, body.subset_id)
    cmd2 = _llm_classify_cmd(pipeline_id) + ["submit"]
    rc2, out2 = _run_llm_cmd(cmd2, settings.pipeline_scripts_dir)
    if rc2 != 0:
        raise HTTPException(500, out2[:2000])
    record_event(db, pipeline_id, "llm_submitted")
    state = _load_batch_state(pipeline_id) or {}
    return {"ok": True, "batch_id": state.get("batch_id"), "output": out2}


_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
_STATUS_STALE_SECS = 300  # 5 minutes


@router.get("/{pipeline_id}/llm/status")
def llm_status(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    state = _load_batch_state(pipeline_id)
    if not state:
        return {"status": "not_started", "batch_id": None}

    batch_id = state.get("batch_id")
    current_status = state.get("status", "")

    # Terminal — return cached
    if current_status in _TERMINAL_STATUSES:
        return state

    # Non-terminal — check staleness
    last_checked = state.get("checked_at")
    stale = True
    if last_checked:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_checked)).total_seconds()
            stale = age > _STATUS_STALE_SECS
        except Exception:
            stale = True

    if not stale:
        return state

    # Refresh from OpenAI
    if not batch_id:
        return state
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    try:
        from openai import OpenAI
        batch = OpenAI().batches.retrieve(batch_id)
        counts = batch.request_counts
        state["status"] = batch.status
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        state["request_counts"] = {
            "completed": counts.completed if counts else 0,
            "failed": counts.failed if counts else 0,
            "total": counts.total if counts else 0,
        }
        if batch.status in _TERMINAL_STATUSES:
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
            if batch.output_file_id:
                state["output_file_id"] = batch.output_file_id
            if batch.error_file_id:
                state["error_file_id"] = batch.error_file_id
        _save_batch_state(pipeline_id, state)
        return state
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/{pipeline_id}/llm/collect")
def llm_collect(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    cmd = _llm_classify_cmd(pipeline_id) + ["save"]
    rc, out = _run_llm_cmd(cmd, settings.pipeline_scripts_dir)
    if rc != 0:
        raise HTTPException(500, out[:2000])
    record_event(db, pipeline_id, "llm_collected")
    return {"ok": True, "output": out}


@router.post("/{pipeline_id}/llm/cancel")
def llm_cancel(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    state = _load_batch_state(pipeline_id)
    if not state or not state.get("batch_id"):
        raise HTTPException(404, "no active batch")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    try:
        from openai import OpenAI
        batch = OpenAI().batches.cancel(state["batch_id"])
        state["status"] = batch.status
        _save_batch_state(pipeline_id, state)
        record_event(db, pipeline_id, "llm_cancelled")
        return {"ok": True, "status": batch.status}
    except Exception as exc:
        raise HTTPException(500, str(exc))
