"""Service layer: business rules on top of connectors + repositories."""

from deviceadmin.connectors.mock import Fault

from .conftest import find_ip


def test_set_volume_clamps_and_records_policy(services, make_phone):
    phone_id = make_phone()
    result = services.phones.set_volume(phone_id, 99, locked=True, policy_name="Lobby")
    assert result.ok
    policy = services.policy_repo.current(phone_id)
    assert (policy["volume_level"], policy["is_locked"], policy["policy_name"]) == (10, 1, "Lobby")


def test_failed_push_does_not_record_a_policy(services, make_phone):
    """The DB must never claim a phone is locked when the device refused."""
    phone_id = make_phone(reachable=False)
    result = services.phones.set_volume(phone_id, 5, locked=True)
    assert not result.ok
    assert services.policy_repo.current(phone_id) is None
    assert services.activity.recent(1)[0]["status"] == "failed"


def test_bulk_policy_reports_partial_failure_and_only_saves_successes(services, make_phone):
    good, bad = make_phone(), make_phone(reachable=False)
    result = services.phones.apply_volume_policy([good, bad, 9999], level=6, locked=False)
    assert len(result.succeeded) == 1 and len(result.failed) == 1 and result.skipped == ["9999"]
    assert services.policy_repo.current(good) is not None
    assert services.policy_repo.current(bad) is None


def test_transient_outage_is_retried_then_reported(services, make_phone, phone_connector):
    phone_id = make_phone()
    phone_connector.set_fault(Fault.OUTAGE)
    result = services.phones.reboot(phone_id)
    assert not result.ok and "503" in result.message
    assert phone_connector.calls == 3  # three attempts, then give up


def test_permanent_error_is_not_retried(services, make_phone, phone_connector):
    phone_id = make_phone()
    phone_connector.set_fault(Fault.AUTH)
    assert not services.phones.reboot(phone_id).ok
    assert phone_connector.calls == 1


def test_firmware_push_records_history_on_success_and_failure(services, make_phone):
    ok_id, bad_id = make_phone(), make_phone()
    assert services.phones.push_firmware(ok_id, "http://updates.example/vx250.fw").ok
    assert not services.phones.push_firmware(bad_id, "http://localhost/vx250.fw").ok
    statuses = {h["device_id"]: h["status"] for h in services.firmware.recent()}
    assert statuses == {ok_id: "success", bad_id: "failed"}


def test_firmware_dry_run_writes_nothing(services, make_phone):
    phone_id = make_phone()
    result = services.phones.push_firmware(phone_id, "http://updates.example/vx250.fw", dry_run=True)
    assert result.ok and result.data.steps
    assert services.firmware.recent() == []


def test_fleet_sync_updates_status_from_the_devices(services, make_phone):
    up = make_phone(reachable=True, status="offline")
    down = make_phone(reachable=False, status="online")
    result = services.phones.sync_fleet()
    assert len(result.succeeded) == 1 and len(result.failed) == 1
    assert services.phone_repo.get(up)["status"] == "online"
    assert services.phone_repo.get(down)["status"] == "offline"


def test_dock_firmware_update_changes_version_and_history(services):
    dock_id = services.dock_repo.add(
        dock_name="DOCK-T1",
        model="Orbit USB-C Dock G2",
        serial_number="ODK-1",
        host_computer=_reachable_host(),
        firmware_version="1.0.10.0",
    )
    assert services.docks.is_outdated(services.dock_repo.get(dock_id))
    assert services.docks.push_firmware(dock_id, "fw.cab").ok
    assert services.dock_repo.get(dock_id)["firmware_version"] == "1.0.22.0"
    assert services.docks.outdated_count() == 0
    assert services.firmware.recent()[0]["new_version"] == "1.0.22.0"


def test_dock_firmware_with_wrong_package_fails_cleanly(services):
    dock_id = services.dock_repo.add(
        dock_name="DOCK-T2",
        model="Orbit USB-C Dock G2",
        serial_number="ODK-2",
        host_computer=_reachable_host(),
        firmware_version="1.0.10.0",
    )
    result = services.docks.push_firmware(dock_id, "fw.exe")
    assert not result.ok and ".cab" in result.message
    assert services.dock_repo.get(dock_id)["firmware_version"] == "1.0.10.0"


def _reachable_host():
    from deviceadmin.connectors.mock import host_is_reachable

    return next(h for h in (f"WS-SVC-{i}" for i in range(50)) if host_is_reachable(h))


# -- emergency broadcast -------------------------------------------------------------
def test_broadcast_with_no_contacts_fails(services):
    assert not services.alerts.broadcast("hi").ok


def test_broadcast_counts_delivered_and_undeliverable(services):
    services.alert_contacts.add("Ok One", "+12025550101")
    services.alert_contacts.add("Opted Out", "+12025550100")  # mock: opted out
    result = services.alerts.broadcast("Evacuate lobby")
    assert (result.data["sent"], result.data["failed"], result.data["status"]) == (1, 1, "partial")
    assert services.alert_history.recent(1)[0]["message"].startswith("ALERT: Evacuate")


def test_broadcast_skips_inactive_contacts(services):
    cid = services.alert_contacts.add("Paused", "+12025550101")
    services.alert_contacts.set_active(cid, False)
    assert not services.alerts.broadcast().ok


def test_broadcast_survives_gateway_outage(services, sms_connector):
    services.alert_contacts.add("Ok One", "+12025550101")
    sms_connector.set_fault(Fault.OUTAGE)
    result = services.alerts.broadcast("x")
    assert result.data["status"] == "failed" and result.data["failed"] == 1


def test_broadcast_gives_up_when_rate_limit_persists(services, sms_connector):
    services.alert_contacts.add("Ok One", "+12025550101")
    sms_connector.max_per_minute = 0  # always limited -> retries exhausted -> failure
    assert services.alerts.broadcast("x").data["failed"] == 1


def test_broadcast_recovers_when_the_rate_limit_clears_during_backoff(services, sms_connector):
    from deviceadmin.connectors.mock import Fault
    from deviceadmin.services.common import RetryPolicy

    services.alert_contacts.add("Ok One", "+12025550101")
    sms_connector.set_fault(Fault.RATE_LIMIT)
    # While the service "sleeps" between attempts, the limit lifts.
    services.alerts.retry = RetryPolicy(attempts=3, base_delay=0, sleep=lambda _s: sms_connector.set_fault(Fault.NONE))
    result = services.alerts.broadcast("x")
    assert result.ok and result.data["sent"] == 1


def test_phone_number_normalisation():
    from deviceadmin.services.alert_service import normalize_phone as n

    assert n("(202) 555-0123") == "+12025550123"
    assert n("1-202-555-0123") == "+12025550123"
    assert n("12345") is None and n("") is None


def test_ip_helper_returns_requested_reachability():
    from deviceadmin.connectors.mock import phone_is_reachable

    assert phone_is_reachable(find_ip(True)) and not phone_is_reachable(find_ip(False))
