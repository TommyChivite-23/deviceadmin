"""Flask application factory."""

from __future__ import annotations

import logging
import sys

from flask import Flask, jsonify, render_template

from . import __version__
from .config import Settings
from .connectors import build_connectors
from .connectors.base import ConnectorSet, HealthState
from .database import Database
from .routes import register_blueprints
from .seed import seed_database
from .services.common import RetryPolicy
from .services.container import Services, build_services

logger = logging.getLogger(__name__)

NAV = [
    ("dashboard.home", "Dashboard", "speedometer2"),
    ("phones.phone_list", "Phones", "telephone"),
    ("docks.dock_list", "Docks", "usb-drive"),
    ("users.user_list", "Users", "people"),
    ("volume.policies", "Volume Policies", "volume-up"),
    ("pages.page_home", "Page Monitor", "megaphone"),
    ("alerts.alert_home", "Emergency Alerts", "broadcast"),
    ("activity.activity_log", "Activity", "journal-text"),
    ("integrations.overview", "Integrations", "plug"),
]


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def create_app(
    settings: Settings | None = None, connectors: ConnectorSet | None = None, retry: RetryPolicy | None = None
) -> Flask:
    """Build the app. Tests pass their own settings/connectors/retry policy."""
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config.update(SECRET_KEY=settings.secret_key, TESTING=settings.testing)

    db = Database(settings.database_path)
    services: Services = build_services(settings, db, connectors or build_connectors(settings), retry)
    app.extensions["deviceadmin"] = services

    if settings.seed_demo_data:
        counts = seed_database(db)
        if counts:
            logger.info("Seeded demo data: %s", counts)

    @app.context_processor
    def inject_globals():
        return {
            "app_name": settings.app_name,
            "nav_items": NAV,
            "version": __version__,
            "connector_mode": settings.connector_mode,
        }

    register_blueprints(app)
    register_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        """Liveness/readiness probe (also used by the Docker healthcheck)."""
        try:
            services.phone_repo.count()
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        connectors_health = {c.name: c.health_check().state.value for c in services.connectors.all()}
        degraded = any(s != HealthState.HEALTHY.value for s in connectors_health.values())
        body = {
            "status": "ok" if db_ok else "error",
            "database": db_ok,
            "connectors": connectors_health,
            "degraded": degraded,
            "version": __version__,
        }
        return jsonify(body), (200 if db_ok else 503)

    if settings.page_monitor_enabled and not settings.testing:
        services.monitor.start()
    return app


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_):
        return render_template(
            "error.html", code=404, title="Page not found", message="The page you asked for does not exist."
        ), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template(
            "error.html",
            code=500,
            title="Something went wrong",
            message="The error has been logged. Try again, or return to the dashboard.",
        ), 500
