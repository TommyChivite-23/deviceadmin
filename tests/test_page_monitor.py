"""Page monitor: detection, source matching, cooldown and notification."""

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def setup(services, make_phone, phone_connector):
    source = make_phone(device_name="FRONT-DESK", extension="1001")
    target = make_phone(device_name="WAREHOUSE-1", extension="1002")
    services.page_contacts.add("Security Desk", "+12025550111")
    return services, services.phone_repo.get(source), services.phone_repo.get(target), phone_connector


def test_page_is_detected_logged_and_texted(setup):
    s, source, target, phones = setup
    phones.simulate_page(target["ip_address"], "FRONT-DESK", "1001")

    assert s.monitor.poll_once() == 1

    event = s.page_events.recent(1)[0]
    assert (event["source_name"], event["target_name"]) == ("FRONT-DESK", "WAREHOUSE-1")
    assert event["sms_sent"] == 1
    assert "PAGE ALERT" in s.connectors.sms.sent[-1]["text"]
    assert s.activity.recent(1)[0]["action_type"] == "page_detected"


def test_second_page_inside_cooldown_is_suppressed(setup):
    s, _, target, phones = setup
    clock = {"now": datetime(2025, 1, 1, 12, 0, 0)}
    s.monitor._clock = lambda: clock["now"]

    phones.simulate_page(target["ip_address"], "A", "1001")
    assert s.monitor.poll_once() == 1
    phones.simulate_page(target["ip_address"], "A", "1001")
    clock["now"] += timedelta(seconds=10)
    assert s.monitor.poll_once() == 0  # still cooling down

    phones.simulate_page(target["ip_address"], "A", "1001")
    clock["now"] += timedelta(seconds=s.monitor.cooldown_seconds)
    assert s.monitor.poll_once() == 1  # cooldown expired


def test_no_contacts_still_records_event_but_marks_sms_unsent(services, make_phone, phone_connector):
    target = services.phone_repo.get(make_phone())
    phone_connector.simulate_page(target["ip_address"], "Someone", "7")
    services.monitor.poll_once()
    event = services.page_events.recent(1)[0]
    assert event["sms_sent"] == 0 and "No active" in event["sms_detail"]


def test_offline_phones_are_not_polled(services, make_phone, phone_connector):
    offline = services.phone_repo.get(make_phone(reachable=False))
    phone_connector.simulate_page(offline["ip_address"], "X", "1")
    assert services.monitor.poll_once() == 0


def test_resolve_source_by_extension_then_name_then_unknown(services):
    phones = [
        {"id": 1, "device_name": "LOBBY-1", "extension": "1001", "assigned_user": "Ada Lee", "location": "Lobby"},
        {"id": 2, "device_name": "DOCK-2", "extension": "1002", "assigned_user": None, "location": None},
    ]
    resolve = services.monitor.resolve_source
    assert resolve("", "1001", phones)["device_name"] == "LOBBY-1"
    assert resolve("ada", "", phones)["id"] == 1
    assert resolve("nobody", "9999", phones)["id"] is None
    assert resolve("", "1002", phones)["assigned_user"] == ""  # None is normalised to ""
