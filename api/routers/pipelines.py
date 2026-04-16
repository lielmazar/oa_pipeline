"""Pipelines router — all pipeline management under /api/pipeline/"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.business.pipeline_status import build_stage_status
from api.business.preset_subsets import ensure_preset_subsets
from api.business.runner import (
    kill_filter_run,
    poll_filter_run,
    start_filter_run_for,
)
from api.config import settings
from api.db.meta import get_db
from api.db.research import count_subset_rows, get_db_stats
from api.db.pipelines import (
    STAGES,
    STAGE_LABELS,
    create_pipeline,
    delete_pipeline,
    get_pipeline,
    get_next_stage,
    get_prev_stage,
    get_subset,
    get_active_run as db_get_active_run,
    list_all_pipelines,
    list_events,
    list_subsets,
    record_event,
    set_pipeline_stage,
    update_pipeline,
    update_pipeline_paths,
)
from api.deps import CurrentUser, Db

router = APIRouter(prefix="/api/pipeline", tags=["pipelines"])


# -- LLM model catalog (from pipeline/llm_pricing.py) ------------------------

@functools.lru_cache(maxsize=1)
def _llm_pricing_module():
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
        "default_batch_limit": settings.llm_batch_default_limit,
    }


def _require_known_llm_model(model: str) -> None:
    mod = _llm_pricing_module()
    if mod is None:
        raise HTTPException(500, "llm_pricing.py not found")
    pricing = getattr(mod, "MODEL_PRICING", None)
    if not isinstance(pricing, dict) or model not in pricing:
        ids = ", ".join(pricing.keys()) if isinstance(pricing, dict) else ""
        raise HTTPException(400, f"Unknown model {model!r}. Choose one of: {ids}")


# -- Path helpers -------------------------------------------------------------

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
    p = settings.pipeline_scripts_dir / "research_schema.json"
    if p.exists():
        schema = json.loads(p.read_text(encoding="utf-8"))
        return schema.get("keywords", {"groups": []})
    legacy = settings.pipeline_scripts_dir / "default_keywords.json"
    return json.loads(legacy.read_text(encoding="utf-8")) if legacy.exists() else {"groups": []}


# -- CRUD ---------------------------------------------------------------------

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


# -- Tab navigation (lightweight — just remembers last-viewed tab) -----------

class SetStageBody(BaseModel):
    stage: str


@router.post("/{pipeline_id}/set-stage")
def set_stage(pipeline_id: int, body: SetStageBody, user: CurrentUser, db: Db):
    if set_pipeline_stage(db, pipeline_id, user["id"], body.stage.strip()) is None:
        raise HTTPException(400, "invalid stage or not owner")
    return _pipeline_response(get_pipeline(db, pipeline_id, user["id"]))


# -- Status -------------------------------------------------------------------

@router.get("/{pipeline_id}/status")
def pipeline_status(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    return build_stage_status(p)


@router.get("/run/active")
def active_run_status(user: CurrentUser, db: Db):
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


# -- Events -------------------------------------------------------------------

@router.get("/{pipeline_id}/events")
def pipeline_events(pipeline_id: int, user: CurrentUser, db: Db, limit: int = 200):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_events(db, pipeline_id, limit=limit)


# -- Filter run ---------------------------------------------------------------

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


# -- Keywords -----------------------------------------------------------------

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


# -- Subsets (preset-only, read + summary) ------------------------------------

@router.get("/{pipeline_id}/subsets")
def get_subsets(pipeline_id: int, user: CurrentUser, db: Db):
    if not get_pipeline(db, pipeline_id, user["id"]):
        raise HTTPException(404, "not found")
    return list_subsets(db, pipeline_id)


@router.get("/{pipeline_id}/subsets/summary")
def subsets_summary(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    rp = Path(p["output_db_path"]) if p.get("output_db_path") else None
    try:
        ensure_preset_subsets(db, pipeline_id, rp)
    except Exception:
        pass
    path = rp
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


# -- Logs ---------------------------------------------------------------------

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


# -- LLM classification -------------------------------------------------------

from api.business import llm_batch as _llm


def _batch_dir(pipeline_id: int) -> Path:
    return settings.pipelines_base_dir / str(pipeline_id) / "batch_runs"


def _db_path(p: dict) -> Path:
    path = p.get("output_db_path")
    if not path:
        raise HTTPException(400, "pipeline has no research database path")
    return Path(path)


def _resolve_llm_subset_sql(db, pipeline_id: int, subset_id: int | None) -> str | None:
    if subset_id is None:
        return None
    sub = get_subset(db, pipeline_id, subset_id)
    if not sub:
        raise HTTPException(404, "subset not found")
    return sub["sql_query"].strip().rstrip(";")


def _effective_llm_batch_limit(requested: int | None) -> int | None:
    if requested is not None:
        return requested
    return settings.llm_batch_default_limit


def _get_pipeline_prompt_config(p: dict) -> tuple[str, str]:
    """Get prompt_id and prompt_version from pipeline config."""
    cfg = p.get("config") or {}
    return cfg.get("llm_prompt_id", ""), cfg.get("llm_prompt_version", "")


def _llm_scope_stats(db_path: Path, inner_sql: str | None) -> dict:
    if not db_path.is_file():
        return {"db_exists": False, "total": 0, "llm_done": 0, "llm_pending": 0, "has_llm_column": False}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}
        except sqlite3.OperationalError:
            return {"db_exists": True, "total": 0, "llm_done": 0, "llm_pending": 0, "has_llm_column": False}
        has_llm = "fuel_relevant" in cols
        if inner_sql:
            clean_subset = inner_sql.strip().rstrip(";")
            cte = f"WITH _fr_subset AS (\n{clean_subset}\n)\n"
            frm = "_fr_subset"
        else:
            cte = ""
            frm = "filtered_records"
        total = conn.execute(f"{cte}SELECT COUNT(*) FROM {frm}").fetchone()[0]
        done = conn.execute(
            f"{cte}SELECT COUNT(*) FROM {frm} WHERE fuel_relevant IS NOT NULL"
        ).fetchone()[0] if has_llm else 0
        pending = max(0, total - done)
        return {"db_exists": True, "total": int(total), "llm_done": int(done), "llm_pending": int(pending), "has_llm_column": has_llm}
    finally:
        conn.close()


class LlmEstimateBody(BaseModel):
    model: str = "gpt-5.4-nano"
    subset_id: int | None = None
    limit: int | None = Field(default=None, gt=0)


@router.post("/{pipeline_id}/llm/estimate")
def llm_estimate(pipeline_id: int, body: LlmEstimateBody, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    _require_known_llm_model(body.model)
    inner = _resolve_llm_subset_sql(db, pipeline_id, body.subset_id)
    mod = _llm_pricing_module()
    pricing = getattr(mod, "MODEL_PRICING", {})
    eff = _effective_llm_batch_limit(body.limit)
    result = _llm.estimate_cost(
        _db_path(p), body.model, pricing,
        limit=eff, subset_sql=inner,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class LlmSubmitBody(BaseModel):
    model: str = "gpt-5.4-nano"
    limit: int | None = Field(default=None, gt=0)
    subset_id: int | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None


@router.get("/{pipeline_id}/llm/stats")
def llm_stats(pipeline_id: int, user: CurrentUser, db: Db, subset_id: int | None = Query(default=None)):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    inner = _resolve_llm_subset_sql(db, pipeline_id, subset_id)
    stats = _llm_scope_stats(_db_path(p), inner)
    eff = _effective_llm_batch_limit(None)
    cfg_pid, cfg_pv = _get_pipeline_prompt_config(p)
    return {**stats, "effective_default_limit": eff, "prompt_id": cfg_pid, "prompt_version": cfg_pv}


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

    cfg_pid, cfg_pv = _get_pipeline_prompt_config(p)
    prompt_id = body.prompt_id or cfg_pid
    prompt_version = body.prompt_version or cfg_pv
    if not prompt_id:
        raise HTTPException(400, "prompt_id is required (set it in the UI or pipeline config)")

    inner = _resolve_llm_subset_sql(db, pipeline_id, body.subset_id)
    eff_limit = _effective_llm_batch_limit(body.limit)
    bd = _batch_dir(pipeline_id)

    files = _llm.prepare_batch(
        _db_path(p), bd,
        prompt_id=prompt_id, prompt_version=prompt_version or "1",
        model=body.model, limit=eff_limit, subset_sql=inner,
    )
    if not files:
        raise HTTPException(400, "No unprocessed records to submit")

    state = _llm.load_state(bd)
    if body.subset_id is not None:
        sub = get_subset(db, pipeline_id, body.subset_id)
        state["subset_id"] = body.subset_id
        state["subset_name"] = sub.get("name") if sub else None
    else:
        state["subset_id"] = None
        state["subset_name"] = "Entire dataset (unprocessed records)"
    state["effective_batch_limit"] = eff_limit
    _llm.save_state(bd, state)

    # Save prompt config back to pipeline
    cfg = dict(p.get("config") or {})
    cfg["llm_prompt_id"] = prompt_id
    cfg["llm_prompt_version"] = prompt_version or "1"
    update_pipeline(db, pipeline_id, user["id"], config=cfg)

    batch_ids = _llm.submit_batch(bd)
    record_event(db, pipeline_id, "llm_submitted")
    return {"ok": True, "batch_ids": batch_ids, "effective_batch_limit": eff_limit, "record_count": state.get("record_count")}


_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
_STATUS_STALE_SECS = 300


@router.get("/{pipeline_id}/llm/status")
def llm_status(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    bd = _batch_dir(pipeline_id)
    state = _llm.load_state(bd)
    if not state:
        return {"status": "not_started", "batch_id": None}
    current_status = state.get("status", "")
    if current_status in _TERMINAL_STATUSES:
        return state
    batch_id = state.get("batch_id")
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
    if not batch_id:
        return state
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    return _llm.check_status(bd)


@router.get("/{pipeline_id}/llm/collect_status")
def llm_collect_status(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    bd = _batch_dir(pipeline_id)
    path = bd / _llm.COLLECT_PROGRESS_FILE
    if not path.exists():
        return {"status": "idle"}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"status": "error", "message": "Could not read collect progress file"}


@router.post("/{pipeline_id}/llm/collect")
def llm_collect(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    bd = _batch_dir(pipeline_id)
    progress_path = bd / _llm.COLLECT_PROGRESS_FILE
    if progress_path.exists():
        try:
            prior = json.loads(progress_path.read_text())
            if prior.get("status") == "running":
                raise HTTPException(409, "Collect is already running.")
        except json.JSONDecodeError:
            pass

    db_p = _db_path(p)
    pid = pipeline_id

    def _worker() -> None:
        try:
            _llm.collect_results(db_p, bd)
            try:
                with get_db() as meta_db:
                    record_event(meta_db, pid, "llm_collected")
            except Exception:
                pass
        except Exception:
            pass

    threading.Thread(target=_worker, name=f"llm-collect-{pipeline_id}", daemon=True).start()
    return {"ok": True, "started": True}


@router.post("/{pipeline_id}/llm/cancel")
def llm_cancel(pipeline_id: int, user: CurrentUser, db: Db):
    p = get_pipeline(db, pipeline_id, user["id"])
    if not p:
        raise HTTPException(404, "not found")
    if p["user_id"] != user["id"]:
        raise HTTPException(403, "not owner")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    bd = _batch_dir(pipeline_id)
    state = _llm.load_state(bd)
    if not state or not state.get("batch_id"):
        raise HTTPException(404, "no active batch")
    result = _llm.cancel_batch(bd)
    record_event(db, pipeline_id, "llm_cancelled")
    return {"ok": True, "status": result["status"]}
