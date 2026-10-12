from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .helpers import flash_bulk, flash_result, ids_from_form, svc, to_int

bp = Blueprint("phones", __name__, url_prefix="/phones")
PAGE_SIZE = 25
FORM_FIELDS = (
    "device_name",
    "mac_address",
    "ip_address",
    "extension",
    "firmware_version",
    "provisioning_status",
    "status",
    "notes",
)


def _form_values() -> dict:
    values = {f: request.form.get(f, "").strip() for f in FORM_FIELDS}
    for key in ("user_id", "location_id", "department_id"):
        values[key] = to_int(request.form.get(key))
    return values


@bp.get("/")
def phone_list():
    s = svc()
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "")
    department_id = to_int(request.args.get("department"))
    page = max(to_int(request.args.get("page"), 1) or 1, 1)
    phones, total = s.phone_repo.list(search, status, department_id, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    pages = max(-(-total // PAGE_SIZE), 1)
    return render_template(
        "phones/list.html",
        phones=phones,
        total=total,
        page=page,
        pages=pages,
        search=search,
        status=status,
        department_id=department_id,
        departments=s.reference.departments(),
    )


@bp.get("/new")
def new_phone():
    s = svc()
    return render_template(
        "phones/form.html",
        phone=None,
        departments=s.reference.departments(),
        locations=s.reference.locations(),
        users=s.reference.users(),
    )


@bp.post("/new")
def create_phone():
    s = svc()
    values = _form_values()
    if not values["device_name"] or not values["mac_address"] or not values["ip_address"]:
        flash("Device name, MAC address and IP address are required.", "danger")
        return redirect(url_for("phones.new_phone"))
    try:
        phone_id = s.phone_repo.add(**values)
    except Exception as exc:  # noqa: BLE001 - e.g. duplicate MAC (UNIQUE constraint)
        flash(f"Could not add phone: {exc}", "danger")
        return redirect(url_for("phones.new_phone"))
    s.activity.log("add_phone", "phone", phone_id, values["device_name"], "Phone added to inventory")
    flash(f"{values['device_name']} added.", "success")
    return redirect(url_for("phones.phone_detail", phone_id=phone_id))


@bp.get("/<int:phone_id>")
def phone_detail(phone_id: int):
    s = svc()
    phone = s.phone_repo.get(phone_id)
    if not phone:
        flash("Phone not found.", "danger")
        return redirect(url_for("phones.phone_list"))
    return render_template(
        "phones/detail.html",
        phone=phone,
        policy=s.policy_repo.current(phone_id),
        departments=s.reference.departments(),
        locations=s.reference.locations(),
        users=s.reference.users(),
        history=[a for a in s.activity.recent(300, "phone") if a["device_id"] == phone_id][:8],
    )


@bp.post("/<int:phone_id>/edit")
def edit_phone(phone_id: int):
    s = svc()
    phone = s.phone_repo.get(phone_id)
    if not phone:
        flash("Phone not found.", "danger")
        return redirect(url_for("phones.phone_list"))
    try:
        s.phone_repo.update(phone_id, **_form_values())
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not update phone: {exc}", "danger")
    else:
        s.activity.log("edit_phone", "phone", phone_id, phone["device_name"], "Phone details updated")
        flash("Phone updated.", "success")
    return redirect(url_for("phones.phone_detail", phone_id=phone_id))


@bp.post("/<int:phone_id>/delete")
def delete_phone(phone_id: int):
    s = svc()
    phone = s.phone_repo.get(phone_id)
    if phone:
        s.phone_repo.delete(phone_id)
        s.activity.log("delete_phone", "phone", phone_id, phone["device_name"], "Phone removed from inventory")
        flash(f"{phone['device_name']} removed.", "success")
    return redirect(url_for("phones.phone_list"))


@bp.post("/<int:phone_id>/reboot")
def reboot(phone_id: int):
    flash_result(svc().phones.reboot(phone_id))
    return redirect(url_for("phones.phone_detail", phone_id=phone_id))


@bp.post("/bulk-reboot")
def bulk_reboot():
    flash_bulk(svc().phones.bulk_reboot(ids_from_form(request.form, "phone_ids")), "Reboot")
    return redirect(request.referrer or url_for("phones.phone_list"))


@bp.post("/sync")
def sync_fleet():
    result = svc().phones.sync_fleet()
    flash_bulk(result, "Fleet sync")
    return redirect(url_for("phones.phone_list"))


@bp.get("/<int:phone_id>/config")
def config(phone_id: int):
    result = svc().phones.get_config(phone_id)
    return jsonify({"ok": result.ok, "message": result.message, "config": result.data}), (200 if result.ok else 502)


@bp.post("/<int:phone_id>/firmware")
def firmware(phone_id: int):
    dry_run = request.form.get("dry_run") == "1"
    result = svc().phones.push_firmware(phone_id, request.form.get("firmware_url", "").strip(), dry_run)
    flash_result(result)
    if dry_run and result.ok:
        steps = " | ".join(f"{st['method']} {st['url']}" for st in result.data.steps)
        flash(f"Planned: {steps}", "info")
        for warning in result.data.warnings:
            flash(warning, "warning")
    return redirect(url_for("phones.phone_detail", phone_id=phone_id))
