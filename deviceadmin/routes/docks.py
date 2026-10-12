from flask import Blueprint, flash, redirect, render_template, request, url_for

from .helpers import flash_bulk, flash_result, ids_from_form, svc, to_int

bp = Blueprint("docks", __name__, url_prefix="/docks")
FIELDS = ("dock_name", "model", "serial_number", "firmware_version", "host_computer", "status", "notes")


def _form_values() -> dict:
    values = {f: request.form.get(f, "").strip() for f in FIELDS}
    for key in ("user_id", "location_id", "department_id"):
        values[key] = to_int(request.form.get(key))
    return values


@bp.get("/")
def dock_list():
    s = svc()
    search, status = request.args.get("search", "").strip(), request.args.get("status", "")
    docks = s.dock_repo.list(search, status)
    for dock in docks:
        dock["outdated"] = s.docks.is_outdated(dock)
        try:
            dock["latest"] = s.connectors.docks.latest_firmware(dock["model"])
        except Exception:  # noqa: BLE001
            dock["latest"] = "unknown"
    return render_template(
        "docks/list.html",
        docks=docks,
        search=search,
        status=status,
        models=s.connectors.docks.supported_models(),
        locations=s.reference.locations(),
        users=s.reference.users(),
        departments=s.reference.departments(),
    )


@bp.post("/add")
def add_dock():
    s = svc()
    values = _form_values()
    if not values["dock_name"] or not values["serial_number"] or not values["model"]:
        flash("Dock name, model and serial number are required.", "danger")
        return redirect(url_for("docks.dock_list"))
    try:
        dock_id = s.dock_repo.add(**values)
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not add dock: {exc}", "danger")
    else:
        s.activity.log("add_dock", "dock", dock_id, values["dock_name"], "Dock added to inventory")
        flash(f"{values['dock_name']} added.", "success")
    return redirect(url_for("docks.dock_list"))


@bp.post("/<int:dock_id>/delete")
def delete_dock(dock_id: int):
    s = svc()
    dock = s.dock_repo.get(dock_id)
    if dock:
        s.dock_repo.delete(dock_id)
        s.activity.log("delete_dock", "dock", dock_id, dock["dock_name"], "Dock removed from inventory")
        flash(f"{dock['dock_name']} removed.", "success")
    return redirect(url_for("docks.dock_list"))


@bp.post("/<int:dock_id>/firmware")
def firmware(dock_id: int):
    flash_result(svc().docks.push_firmware(dock_id, request.form.get("firmware_file", "").strip()))
    return redirect(url_for("docks.dock_list"))


@bp.post("/<int:dock_id>/refresh")
def refresh(dock_id: int):
    flash_result(svc().docks.refresh(dock_id))
    return redirect(url_for("docks.dock_list"))


@bp.post("/bulk-firmware")
def bulk_firmware():
    result = svc().docks.bulk_push_firmware(
        ids_from_form(request.form, "dock_ids"), request.form.get("firmware_file", "").strip()
    )
    flash_bulk(result, "Firmware update")
    return redirect(url_for("docks.dock_list"))
