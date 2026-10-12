"""End-to-end HTTP workflows for docks, alerts, the page monitor and phone detail actions."""

import pytest

from deviceadmin.connectors.mock import Fault, host_is_reachable


def follow(client, path, **data):
    return client.post(path, data=data, follow_redirects=True).get_data(as_text=True)


@pytest.fixture
def reachable_host():
    return next(h for h in (f"WS-WEB-{i:03d}" for i in range(50)) if host_is_reachable(h))


# -- docks -------------------------------------------------------------------------------
def test_dock_add_validate_refresh_update_delete(client, reachable_host):
    assert "are required" in follow(client, "/docks/add", dock_name="only-name")

    page = follow(client, "/docks/add", dock_name="DOCK-W1", model="Orbit USB-C Dock G2", serial_number="ODK-W1",
                  host_computer=reachable_host, firmware_version="1.0.10.0")  # fmt: skip
    assert "DOCK-W1 added" in page and "Update available" in page

    assert "reports firmware" in follow(client, "/docks/1/refresh")
    assert "updated to 1.0.22.0" in follow(client, "/docks/1/firmware", firmware_file="fw.cab")
    assert "Update available" not in client.get("/docks/").get_data(as_text=True)
    assert "removed" in follow(client, "/docks/1/delete")
    assert "No docks found" in client.get("/docks/").get_data(as_text=True)


def test_duplicate_dock_serial_is_reported(client):
    data = {"dock_name": "D", "model": "Orbit USB-C Dock G2", "serial_number": "ODK-DUP"}
    follow(client, "/docks/add", **data)
    assert "Could not add dock" in follow(client, "/docks/add", **data)


def test_bulk_dock_firmware_reports_partial_failure(client, reachable_host):
    follow(client, "/docks/add", dock_name="Good", model="Orbit USB-C Dock G2", serial_number="S1",
           host_computer=reachable_host)  # fmt: skip
    follow(client, "/docks/add", dock_name="Bad", model="Orbit USB-C Dock G2", serial_number="S2", host_computer="")
    page = client.post("/docks/bulk-firmware", data={"dock_ids": ["1", "2"], "firmware_file": "fw.cab"},
                       follow_redirects=True).get_data(as_text=True)  # fmt: skip
    assert "1 succeeded, 1 failed" in page and "host_computer is required" in page


def test_dock_refresh_failure_marks_dock_disconnected(client, reachable_host, dock_connector):
    follow(client, "/docks/add", dock_name="D", model="Orbit USB-C Dock G2", serial_number="S1",
           host_computer=reachable_host, status="connected")  # fmt: skip
    dock_connector.set_fault(Fault.TIMEOUT)
    assert "Could not reach" in follow(client, "/docks/1/refresh")
    assert "Disconnected" in client.get("/docks/").get_data(as_text=True)


# -- emergency alerts ------------------------------------------------------------------------
def test_alert_contact_lifecycle_and_broadcast(client, sms_connector):
    assert "valid 10-digit" in follow(client, "/alerts/contacts/add", name="Bad", phone="123")
    assert "added to the alert list" in follow(client, "/alerts/contacts/add", name="Ana Test",
                                               phone="(202) 555-0123", role="Lead")  # fmt: skip

    assert "Test message delivered" in follow(client, "/alerts/contacts/1/test")
    page = follow(client, "/alerts/broadcast", message="Building closed")
    assert "1 delivered, 0 failed" in page and "ALERT: Building closed" in page
    assert sms_connector.sent[-1]["text"] == "ALERT: Building closed"

    assert "deactivated" in follow(client, "/alerts/contacts/1/toggle")
    assert "No active contacts" in follow(client, "/alerts/broadcast")
    assert "removed" in follow(client, "/alerts/contacts/1/delete")


def test_broadcast_during_gateway_outage_is_reported(client, sms_connector):
    follow(client, "/alerts/contacts/add", name="Ana", phone="2025550123")
    sms_connector.set_fault(Fault.OUTAGE)
    page = follow(client, "/alerts/broadcast", message="x")
    assert "0 delivered, 1 failed" in page
    assert "FAILED" in client.get("/activity/").get_data(as_text=True) or "failed" in page


# -- page monitor ------------------------------------------------------------------------------
def test_page_monitor_controls(seeded_client):
    # The fixture starts with the monitor disabled, so the first toggle activates it.
    assert "activated" in follow(seeded_client, "/pages/toggle").lower()
    assert "paused" in follow(seeded_client, "/pages/toggle").lower()
    assert "Scan complete" in follow(seeded_client, "/pages/scan")
    assert "Choose both" in follow(seeded_client, "/pages/simulate", source_id="1")


def test_page_contact_lifecycle(client):
    assert "valid 10-digit" in follow(client, "/pages/contacts/add", name="X", phone="1")
    assert "added" in follow(client, "/pages/contacts/add", name="Desk", phone="202-555-0111")
    follow(client, "/pages/contacts/1/toggle")
    assert "Off" in client.get("/pages/").get_data(as_text=True)
    assert "removed" in follow(client, "/pages/contacts/1/delete")


# -- phone detail actions ---------------------------------------------------------------------------
def test_phone_detail_actions(seeded_client):
    from .conftest import find_ip

    ip = find_ip(True)
    follow(seeded_client, "/phones/new", device_name="WEB-PH", mac_address="02:00:5E:EE:EE:01", ip_address=ip,
           extension="9001", status="online")  # fmt: skip
    from deviceadmin.seed import N_PHONES

    pid = N_PHONES + 1
    assert "Reboot sent" in follow(seeded_client, f"/phones/{pid}/reboot")
    assert "Volume 4 (LOCKED)" in follow(seeded_client, f"/volume/set/{pid}", volume_level="4", is_locked="1")
    assert "Planned:" in follow(seeded_client, f"/phones/{pid}/firmware", firmware_url="http://u.example/a.txt",
                                dry_run="1")  # fmt: skip
    assert "localhost" in follow(seeded_client, f"/phones/{pid}/firmware", firmware_url="http://localhost/a.fw")

    config = seeded_client.get(f"/phones/{pid}/config")
    assert config.status_code == 200 and config.get_json()["config"]["voice.volume.locked"] == "1"
    assert "removed" in follow(seeded_client, f"/phones/{pid}/delete")
    assert "Phone not found" in seeded_client.get(f"/phones/{pid}", follow_redirects=True).get_data(as_text=True)


def test_config_endpoint_returns_502_when_device_unreachable(seeded_client, phone_connector):
    phone_connector.set_fault(Fault.OUTAGE)
    response = seeded_client.get("/phones/1/config")
    assert response.status_code == 502 and response.get_json()["ok"] is False


def test_bulk_reboot_and_fleet_sync_flash_summaries(seeded_client):
    assert "Reboot:" in follow(seeded_client, "/phones/bulk-reboot", phone_ids=["1", "2", "3"])
    assert "Fleet sync:" in follow(seeded_client, "/phones/sync")
    assert "Volume policy:" in follow(seeded_client, "/volume/bulk", phone_ids=["1", "2"], volume_level="3")


def test_activity_filters(seeded_client):
    html = seeded_client.get("/activity/?type=dock&limit=50").get_data(as_text=True)
    assert "Dock" in html
