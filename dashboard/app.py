"""Flask application factory."""
from __future__ import annotations

import datetime
import os

from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(pipelines_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(system_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.after_request
    def _no_cache_api(response):
        """Prevent Cloudflare (and any CDN) from caching API responses."""
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    return app
