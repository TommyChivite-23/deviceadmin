from flask import Blueprint, redirect, render_template, request, url_for

from .helpers import flash_bulk, flash_result, ids_from_form, svc, to_int

bp = Blueprint("volume", __name__, url_prefix="/volume")


def _policy_args(default_name: str) -> tuple[int, bool, str]:
    return (
        to_int(request.form.get("volume_level"), 5) or 0,
        request.form.get("is_locked") == "1",
        request.form.get("policy_name", "").strip() or default_name,
    )


@bp.get("/")
def policies():
    search = request.args.get("search", "").strip()
    return render_template("volume/policies.html", rows=svc().policy_repo.all_current(search), search=search)


@bp.post("/set/<int:phone_id>")
def set_one(phone_id: int):
    level, locked, name = _policy_args("")
    flash_result(svc().phones.set_volume(phone_id, level, locked, name))
    return redirect(url_for("volume.policies"))


@bp.post("/bulk")
def bulk():
    level, locked, name = _policy_args("Bulk Policy")
    result = svc().phones.apply_volume_policy(ids_from_form(request.form, "phone_ids"), level, locked, name)
    flash_bulk(result, "Volume policy")
    return redirect(url_for("volume.policies"))


@bp.post("/apply-all")
def apply_all():
    level, locked, name = _policy_args("Fleet-Wide Policy")
    flash_bulk(svc().phones.apply_volume_to_fleet(level, locked, name), "Fleet volume policy")
    return redirect(url_for("volume.policies"))
