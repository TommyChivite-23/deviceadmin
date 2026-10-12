from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..services.alert_service import DEFAULT_MESSAGE, normalize_phone
from .helpers import flash_result, svc

bp = Blueprint("alerts", __name__, url_prefix="/alerts")


@bp.get("/")
def alert_home():
    s = svc()
    contacts = s.alert_contacts.list()
    return render_template(
        "alerts/home.html",
        contacts=contacts,
        history=s.alert_history.recent(15),
        active_count=sum(1 for c in contacts if c["is_active"]),
        default_message=DEFAULT_MESSAGE,
    )


@bp.post("/contacts/add")
def add_contact():
    s = svc()
    name, role = request.form.get("name", "").strip(), request.form.get("role", "").strip()
    phone = normalize_phone(request.form.get("phone", ""))
    if not name or not phone:
        flash("A name and a valid 10-digit US phone number are required.", "danger")
    else:
        s.alert_contacts.add(name, phone, role)
        s.activity.log("alert_add_contact", "alert", None, name, f"Added to alert list: {phone}")
        flash(f"{name} added to the alert list.", "success")
    return redirect(url_for("alerts.alert_home"))


@bp.post("/contacts/<int:contact_id>/toggle")
def toggle_contact(contact_id: int):
    s = svc()
    contact = s.alert_contacts.get(contact_id)
    if contact:
        s.alert_contacts.set_active(contact_id, not contact["is_active"])
        flash(f"{contact['name']} {'deactivated' if contact['is_active'] else 'activated'}.", "info")
    return redirect(url_for("alerts.alert_home"))


@bp.post("/contacts/<int:contact_id>/delete")
def delete_contact(contact_id: int):
    s = svc()
    contact = s.alert_contacts.get(contact_id)
    if contact:
        s.alert_contacts.delete(contact_id)
        s.activity.log("alert_remove_contact", "alert", None, contact["name"], "Removed from alert list")
        flash(f"{contact['name']} removed.", "success")
    return redirect(url_for("alerts.alert_home"))


@bp.post("/contacts/<int:contact_id>/test")
def test_contact(contact_id: int):
    flash_result(svc().alerts.send_test(contact_id))
    return redirect(url_for("alerts.alert_home"))


@bp.post("/broadcast")
def broadcast():
    result = svc().alerts.broadcast(request.form.get("message", ""))
    if result.data is None:
        flash(result.message, "warning")
    else:
        flash(result.message, "success" if result.ok else "warning")
    return redirect(url_for("alerts.alert_home"))
