"""Flask application factory."""
from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path

from flask import Flask, Response, render_template, request
from flask_compress import Compress
from werkzeug.middleware.proxy_fix import ProxyFix

# Ordered list of JS modules that make up the frontend bundle.
_JS_MODULES = [
    "utils.js",
    "hooks.js",
    "constants.js",
    "components/shared.js",
    "components/home.js",
    "components/pipeline.js",
    "stages/keywords.js",
    "stages/search.js",
    "stages/report-utils.js",
    "stages/records-panel.js",
    "stages/record-viewer.js",
    "stages/report-stage.js",
    "views/orthogonality.js",
    "stages/llm.js",
    "stages/summary.js",
    "views/pipeline.js",
    "views/jobs.js",
    "views/qa.js",
    "app-root.js",
]

from core.config import META_DB_PATH
from core.job_manager import JobManager
from models.db import init_meta_db, close_meta_db

# Module-scope singleton — must survive across requests.
# gunicorn runs with -w 1, so one worker owns this object.
_jobs = JobManager()


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    # Trust X-Forwarded-* headers from Cloudflare / nginx (one proxy hop)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    # Enable gzip/brotli compression for all responses > 500 bytes
    Compress(app)

    # Session cookie config — compatible with Cloudflare Flexible SSL
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False   # CF handles HTTPS termination
    app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=7)

    app.config["META_DB_PATH"] = META_DB_PATH
    app.config["JOBS"] = _jobs

    if config_overrides:
        app.config.update(config_overrides)

    app.teardown_appcontext(close_meta_db)
    init_meta_db(app)

    # Register blueprints
    from blueprints.auth import auth_bp
    from blueprints.pipelines import pipelines_bp
    from blueprints.jobs import jobs_bp
    from blueprints.data import data_bp
    from blueprints.system import system_bp
    from blueprints.qa import qa_bp
    from blueprints.orthogonality import ortho_bp
    from blueprints.export import export_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pipelines_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(qa_bp)
    app.register_blueprint(ortho_bp)
    app.register_blueprint(export_bp)

    @app.route("/")
    def index():
        js_dir = Path(app.root_path) / "static" / "js"
        compiled = (js_dir / "bundle.compiled.js").exists()
        return render_template("index.html", bundle_compiled=compiled)

    @app.route("/js/bundle.js")
    def js_bundle():
        """Serve all frontend JS modules concatenated as a single script."""
        js_dir = Path(app.root_path) / "static" / "js"
        # Check for pre-compiled bundle first (produced by esbuild)
        compiled = js_dir / "bundle.compiled.js"
        if compiled.exists():
            body = compiled.read_text(encoding="utf-8")
        else:
            parts = []
            for module in _JS_MODULES:
                path = js_dir / module
                if path.exists():
                    parts.append(f"// ═══ {module} ═══\n" + path.read_text(encoding="utf-8"))
            body = "\n\n".join(parts)
        etag = hashlib.md5(body.encode()).hexdigest()
        if request.headers.get("If-None-Match") == etag:
            return Response(status=304)
        return Response(
            body,
            mimetype="application/javascript",
            headers={"Cache-Control": "public, max-age=60", "ETag": etag},
        )

    @app.after_request
    def _cache_headers(response):
        """Set appropriate caching headers.

        - Chart images: allow short browser cache (ETag-based revalidation
          is handled inside the chart endpoint).
        - Other API responses: prevent Cloudflare / CDN caching but allow
          short browser caching for non-mutating GETs.
        """
        if request.path.startswith("/api/charts/"):
            # Chart images are cached server-side; let the browser keep
            # them for 2 minutes to avoid duplicate fetches during
            # normal browsing.  The _r= cache-buster handles manual
            # refreshes.
            pass  # each endpoint sets its own headers
        elif request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    return app
