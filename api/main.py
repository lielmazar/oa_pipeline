"""
FastAPI application factory.

Startup:  initialise metadata DB, ensure data directories exist.
Routing:  all /api/* routers mounted here.
Static:   built React app served from api/static/ (catch-all → index.html).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.config import settings
from api.db.meta import init_db

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.pipelines_base_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OA Pipeline",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=500)

# ── Routers ───────────────────────────────────────────────────────────────────

from api.routers.auth      import router as auth_router
from api.routers.system    import router as system_router
from api.routers.pipelines import router as pipelines_router
from api.routers.data      import router as data_router
from api.routers.charts    import router as charts_router
from api.routers.export    import router as export_router
from api.routers.qa        import router as qa_router

app.include_router(auth_router)
app.include_router(system_router)
app.include_router(pipelines_router)
app.include_router(data_router)
app.include_router(charts_router)
app.include_router(export_router)
app.include_router(qa_router)

# ── Unauthenticated API guard ─────────────────────────────────────────────────

@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    public = {"/api/auth/login", "/api/docs", "/api/openapi.json"}
    if path.startswith("/api/") and path not in public:
        from api.config import settings as s
        from api.deps import read_session_cookie
        token = request.cookies.get(s.session_cookie_name)
        if not token or not read_session_cookie(token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

# ── Static frontend ───────────────────────────────────────────────────────────

_STATIC = Path(__file__).parent / "static"

if _STATIC.exists():
    _ASSETS = _STATIC / "assets"
    if _ASSETS.exists():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = _STATIC / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_STATIC / "index.html"))
