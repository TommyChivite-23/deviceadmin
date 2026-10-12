"""HTTP layer. Routes parse input, call a service, and render or redirect - no business logic."""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import activity, alerts, dashboard, docks, integrations, pages, phones, users, volume

    for module in (dashboard, phones, docks, users, volume, pages, alerts, activity, integrations):
        app.register_blueprint(module.bp)
