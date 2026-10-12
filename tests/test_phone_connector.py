"""MockPhoneConnector: does it behave like a real network device?"""

import pytest

from deviceadmin.connectors.base import (
    ConnectorAuthError,
    ConnectorTimeout,
    ConnectorUnavailable,
    HealthState,
    InvalidRequestError,
    RateLimitError,
)
from deviceadmin.connectors.mock import Fault

from .conftest import find_ip


@pytest.fixture
def ip():
    return find_ip(reachable=True)


def test_device_info_has_expected_structure(phone_connector, ip):
    info = phone_connector.get_device_info(ip)
    assert info.ip_address == ip
    assert info.model and info.firmware
    assert info.mac_address.startswith("02:00:5E:")  # locally-administered, never a real vendor MAC


def test_device_info_is_deterministic(phone_connector, ip):
    assert phone_connector.get_device_info(ip) == phone_connector.get_device_info(ip)


def test_unreachable_phone_times_out(phone_connector):
    with pytest.raises(ConnectorTimeout):
        phone_connector.get_device_info(find_ip(reachable=False))


@pytest.mark.parametrize("bad_ip", ["", "not-an-ip", "300.1.1.1", "1.2.3"])
def test_malformed_ip_is_rejected_as_bad_request(phone_connector, bad_ip):
    with pytest.raises(InvalidRequestError):
        phone_connector.get_device_info(bad_ip)


def test_volume_change_is_remembered_by_the_device(phone_connector, ip):
    phone_connector.set_volume(ip, 8, locked=True)
    config = phone_connector.get_config(ip)
    assert config["voice.volume.handset"] == "8"
    assert config["voice.volume.locked"] == "1"


@pytest.mark.parametrize("level", [-1, 11])
def test_out_of_range_volume_is_rejected(phone_connector, ip, level):
    with pytest.raises(InvalidRequestError):
        phone_connector.set_volume(ip, level, locked=False)


def test_reboot_is_counted(phone_connector, ip):
    phone_connector.reboot(ip)
    phone_connector.reboot(ip)
    assert phone_connector.reboot_count(ip) == 2


@pytest.mark.parametrize("url", ["", "ftp://files.example/fw.bin", "http://localhost/fw.bin", "http://127.0.0.1/x.fw"])
def test_firmware_push_validates_the_url(phone_connector, ip, url):
    with pytest.raises(InvalidRequestError):
        phone_connector.push_firmware(ip, url)


def test_firmware_dry_run_returns_a_plan_and_warnings(phone_connector, ip):
    plan = phone_connector.push_firmware(ip, "http://updates.example/fw/vx250.txt", dry_run=True)
    assert [s["step"] for s in plan.steps] == [1, 2]
    assert any(".fw" in w for w in plan.warnings)


def test_queued_page_is_returned_once(phone_connector, ip):
    assert phone_connector.get_call_status(ip) == []
    phone_connector.simulate_page(ip, "Front Desk", "1001")
    calls = phone_connector.get_call_status(ip)
    assert len(calls) == 1 and calls[0].is_paging and calls[0].remote_party_number == "1001"
    assert phone_connector.get_call_status(ip) == []  # consumed


@pytest.mark.parametrize(
    "fault, error",
    [
        (Fault.OUTAGE, ConnectorUnavailable),
        (Fault.TIMEOUT, ConnectorTimeout),
        (Fault.RATE_LIMIT, RateLimitError),
        (Fault.AUTH, ConnectorAuthError),
    ],
)
def test_injected_faults_raise_the_matching_error(phone_connector, ip, fault, error):
    phone_connector.set_fault(fault)
    with pytest.raises(error):
        phone_connector.reboot(ip)
    assert phone_connector.last_error


def test_retryable_flags_match_http_semantics():
    assert ConnectorTimeout.retryable and ConnectorUnavailable.retryable and RateLimitError.retryable
    assert not ConnectorAuthError.retryable and not InvalidRequestError.retryable


def test_health_check_reports_outage(phone_connector):
    assert phone_connector.health_check().state is HealthState.HEALTHY
    phone_connector.set_fault(Fault.OUTAGE)
    assert phone_connector.health_check().state is HealthState.DOWN
    phone_connector.set_fault(Fault.RATE_LIMIT)
    assert phone_connector.health_check().state is HealthState.DEGRADED
