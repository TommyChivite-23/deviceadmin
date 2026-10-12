from flask import Blueprint, render_template

from .helpers import svc

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def home():
    s = svc()
    stats = s.stats.summary()
    stats["outdated_docks"] = s.docks.outdated_count()
    health = [(c, c.health_check()) for c in s.connectors.all()]
    return render_template("dashboard.html", stats=stats, recent=s.activity.recent(limit=8), health=health)
