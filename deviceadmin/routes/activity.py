from flask import Blueprint, render_template, request

from .helpers import svc, to_int

bp = Blueprint("activity", __name__, url_prefix="/activity")


@bp.get("/")
def activity_log():
    s = svc()
    kind = request.args.get("type", "all")
    limit = min(max(to_int(request.args.get("limit"), 100) or 100, 10), 500)
    entries = s.activity.recent(limit, None if kind == "all" else kind)
    return render_template("activity/log.html", entries=entries, firmware=s.firmware.recent(25), kind=kind, limit=limit)
