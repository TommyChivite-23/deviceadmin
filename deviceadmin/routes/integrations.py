"""Integrations page: connector health plus the mock fault-injection controls."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..connectors.mock import Fault
from .helpers import svc

bp = Blueprint("integrations", __name__, url_prefix="/integrations")


def _connector(name: str):
    for connector in svc().connectors.all():
        if connector.name == name:
            return connector
    abort(404)


@bp.get("/")
def overview():
    s = svc()
    rows = []
    for connector in s.connectors.all():
        rows.append(
            {
                "connector": connector,
                "health": connector.health_check(),
                "fault": getattr(connector, "fault", Fault.NONE),
                "calls": getattr(connector, "calls", 0),
                "last_error": getattr(connector, "last_error", ""),
                "can_inject": hasattr(connector, "set_fault"),
            }
        )
    return render_template(
        "integrations/overview.html", rows=rows, faults=list(Fault), settings_view=s.settings.public_view()
    )


@bp.post("/<name>/fault")
def set_fault(name: str):
    connector = _connector(name)
    if not hasattr(connector, "set_fault"):
        flash("This connector does not support fault injection.", "danger")
    else:
        try:
            connector.set_fault(request.form.get("fault", "none"))
        except ValueError:
            flash("Unknown fault type.", "danger")
        else:
            flash(f"{name} connector fault set to '{connector.fault.value}'.", "info")
    return redirect(url_for("integrations.overview"))
