"""Flask application factory."""
from __future__ import annotations

import os

from flask import Flask, render_template

from core.config import META_DB_PATH
from core.job_manager import JobManager
from models.db import init_meta_db, close_meta_db

# Module-scope singleton — must survive across requests.
# gunicorn runs with -w 1, so one worker owns this object.
_jobs = JobManager()


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(pipelines_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(data_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    return app
