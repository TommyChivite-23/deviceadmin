from flask import Blueprint, render_template, request

from .helpers import svc

bp = Blueprint("users", __name__, url_prefix="/users")


@bp.get("/")
def user_list():
    search = request.args.get("search", "").strip().lower()
    users = svc().reference.users()
    if search:
        users = [
            u
            for u in users
            if search in u["name"].lower() or search in u["email"].lower() or search in (u["department"] or "").lower()
        ]
    return render_template("users/list.html", users=users, search=search)
