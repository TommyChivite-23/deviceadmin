"""HTTP layer: every page renders, forms work, errors are friendly."""

import pytest

from deviceadmin.connectors.mock import Fault

PAGES = [
    "/",
    "/phones/",
    "/phones/new",
    "/docks/",
    "/users/",
    "/volume/",
    "/pages/",
    "/alerts/",
    "/activity/",
    "/integrations/",
    "/healthz",
]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_with_demo_data(seeded_client, path):
    assert seeded_client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/", "/phones/", "/docks/", "/volume/", "/pages/", "/alerts/", "/activity/"])
def test_every_page_renders_on_an_empty_database(client, path):
    """Empty states must work, not crash."""
    assert client.get(path).status_code == 200


def test_unknown_url_gets_the_friendly_404(client):
    response = client.get("/nope")
    assert response.status_code == 404 and b"Page not found" in response.data


def test_healthz_reports_connector_state(client, phone_connector):
    assert client.get("/healthz").get_json()["degraded"] is False
    phone_connector.set_fault(Fault.OUTAGE)
    body = client.get("/healthz").get_json()
    assert body["degraded"] is True and body["connectors"]["phones"] == "down"


def test_phone_list_search_and_filter(seeded_client):
    html = seeded_client.get("/phones/?status=offline").get_data(as_text=True)
    assert "Offline" in html and "Online</span>" not in html.split("<tbody>")[1]
    assert b"No phones found" in seeded_client.get("/phones/?search=zzzzzz").data


def test_add_phone_then_duplicate_mac_shows_error(client):
    form = {"device_name": "NEW-PH1", "mac_address": "02:00:5E:AB:CD:01", "ip_address": "198.51.100.77"}
    assert client.post("/phones/new", data=form).status_code == 302
    page = client.post("/phones/new", data=form, follow_redirects=True)
    assert b"Could not add phone" in page.data


def test_add_phone_requires_core_fields(client):
    page = client.post("/phones/new", data={"device_name": "x"}, follow_redirects=True)
    assert b"are required" in page.data


def test_reboot_failure_is_reported_to_the_user(seeded_client, phone_connector):
    phone_connector.set_fault(Fault.TIMEOUT)
    page = seeded_client.post("/phones/1/reboot", follow_redirects=True)
    assert b"Reboot failed" in page.data


def test_fleet_volume_policy_end_to_end(seeded_client):
    page = seeded_client.post("/volume/apply-all", data={"volume_level": "9", "is_locked": "1"}, follow_redirects=True)
    assert b"Fleet volume policy" in page.data
    assert b"Locked" in seeded_client.get("/volume/").data


def test_bulk_actions_with_nothing_selected_warn(seeded_client):
    page = seeded_client.post("/phones/bulk-reboot", data={}, follow_redirects=True)
    assert b"Nothing selected" in page.data


def test_simulated_page_appears_in_the_monitor(seeded_client):
    page = seeded_client.post("/pages/simulate", data={"source_id": "1", "target_id": "2"}, follow_redirects=True)
    assert b"Simulated page" in page.data


def test_fault_injection_form_changes_connector_state(client, sms_connector):
    client.post("/integrations/sms/fault", data={"fault": "outage"})
    assert sms_connector.fault is Fault.OUTAGE
    assert b"Down" in client.get("/integrations/").data


def test_invalid_fault_value_is_rejected(client):
    assert (
        b"Unknown fault" in client.post("/integrations/sms/fault", data={"fault": "bogus"}, follow_redirects=True).data
    )


def test_unknown_connector_name_is_404(client):
    assert client.post("/integrations/nope/fault", data={"fault": "none"}).status_code == 404


def test_html_in_user_data_is_escaped(client):
    client.post(
        "/phones/new",
        data={
            "device_name": "<script>alert(1)</script>",
            "mac_address": "02:00:5E:00:00:99",
            "ip_address": "198.51.100.5",
        },
    )
    assert b"<script>alert(1)</script>" not in client.get("/phones/").data


def test_edit_form_preselects_current_user_location_and_department(seeded_client):
    """Regression: macro lost template context, so dropdowns were empty and saving wiped these fields."""
    html = seeded_client.get("/phones/3").get_data(as_text=True)
    form = html.split('name="device_name"')[1]
    assert form.count("<option") > 30  # users, locations and departments are all listed
    assert form.count("selected") >= 5  # user, location, department, status, provisioning


def test_saving_the_edit_form_keeps_assignments(seeded_client):
    import re

    html = seeded_client.get("/phones/3").get_data(as_text=True)
    block = html.split('name="user_id"')[1].split("</select>")[0]
    user_id = re.search(r'value="(\d+)" selected', block).group(1)
    seeded_client.post(
        "/phones/3/edit",
        data={"device_name": "X", "mac_address": "02:00:5E:00:00:03", "ip_address": "198.51.100.4", "user_id": user_id},
    )
    assert f'value="{user_id}" selected' in seeded_client.get("/phones/3").get_data(as_text=True)
