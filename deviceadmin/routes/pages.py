from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..services.alert_service import normalize_phone
from .helpers import svc, to_int

bp = Blueprint("pages", __name__, url_prefix="/pages")


@bp.get("/")
def page_home():
    s = svc()
    contacts = s.page_contacts.list()
    online = [p for p in s.phone_repo.all() if p["status"] == "online"][:40]
    return render_template(
        "pages/monitor.html",
        events=s.page_events.recent(50),
        contacts=contacts,
        active_count=sum(1 for c in contacts if c["is_active"]),
        monitor=s.monitor,
        total_events=s.page_events.count(),
        phones=online,
    )


@bp.post("/toggle")
def toggle():
    s = svc()
    s.monitor.enabled = not s.monitor.enabled
    if s.monitor.enabled and not s.monitor.is_running and not s.settings.testing:
        s.monitor.start()
    s.activity.log(
        "page_monitor_toggle",
        "system",
        None,
        "Page Monitor",
        "Monitoring activated" if s.monitor.enabled else "Monitoring paused",
    )
    flash("Page monitor activated." if s.monitor.enabled else "Page monitor paused.", "success")
    return redirect(url_for("pages.page_home"))


@bp.post("/scan")
def scan():
    found = svc().monitor.poll_once()
    flash(f"Scan complete: {found} new page(s) recorded.", "info")
    return redirect(url_for("pages.page_home"))


@bp.post("/simulate")
def simulate():
    """Demo helper: queue a fake page on a phone, then scan immediately."""
    s = svc()
    target = s.phone_repo.get(to_int(request.form.get("target_id"), 0) or 0)
    source = s.phone_repo.get(to_int(request.form.get("source_id"), 0) or 0)
    if not target or not source:
        flash("Choose both a calling phone and a receiving phone.", "danger")
    elif not hasattr(s.connectors.phones, "simulate_page"):
        flash("The active phone connector cannot simulate pages.", "danger")
    else:
        s.connectors.phones.simulate_page(target["ip_address"], source["device_name"], source["extension"])
        found = s.monitor.poll_once()
        flash(
            f"Simulated page {source['device_name']} -> {target['device_name']}; {found} event(s) recorded.",
            "success" if found else "warning",
        )
    return redirect(url_for("pages.page_home"))


@bp.post("/contacts/add")
def add_contact():
    s = svc()
    name, raw = request.form.get("name", "").strip(), request.form.get("phone", "")
    phone = normalize_phone(raw)
    if not name or not phone:
        flash("A name and a valid 10-digit US phone number are required.", "danger")
    else:
        s.page_contacts.add(name, phone)
        s.activity.log("page_add_contact", "system", None, name, f"Added to page notification list: {phone}")
        flash(f"{name} added.", "success")
    return redirect(url_for("pages.page_home"))


@bp.post("/contacts/<int:contact_id>/toggle")
def toggle_contact(contact_id: int):
    s = svc()
    contact = s.page_contacts.get(contact_id)
    if contact:
        s.page_contacts.set_active(contact_id, not contact["is_active"])
    return redirect(url_for("pages.page_home"))


@bp.post("/contacts/<int:contact_id>/delete")
def delete_contact(contact_id: int):
    svc().page_contacts.delete(contact_id)
    flash("Contact removed.", "success")
    return redirect(url_for("pages.page_home"))
