"""MockDockConnector + the connector factory."""

import pytest

from deviceadmin.config import Settings
from deviceadmin.connectors import ConfigurationError, build_connectors, register_connector_set
from deviceadmin.connectors.base import ConnectorSet, ConnectorTimeout, InvalidRequestError
from deviceadmin.connectors.mock import MockPhoneConnector, host_is_reachable

MODEL = "Orbit USB-C Dock G2"


@pytest.fixture
def host():
    return next(h for h in (f"WS-TEST-{i:03d}" for i in range(100)) if host_is_reachable(h))


def test_new_dock_reports_older_firmware_than_latest(dock_connector, host):
    assert dock_connector.get_firmware_version(host, MODEL) != dock_connector.latest_firmware(MODEL)


def test_push_firmware_updates_to_latest(dock_connector, host):
    new = dock_connector.push_firmware(host, MODEL, "dock-fw.cab")
    assert new == dock_connector.latest_firmware(MODEL)
    assert dock_connector.get_firmware_version(host, MODEL) == new  # state persisted


def test_push_requires_a_cab_package(dock_connector, host):
    with pytest.raises(InvalidRequestError):
        dock_connector.push_firmware(host, MODEL, "dock-fw.exe")


def test_unknown_model_is_rejected(dock_connector, host):
    with pytest.raises(InvalidRequestError):
        dock_connector.get_firmware_version(host, "Nonexistent Dock")


def test_unreachable_host_times_out(dock_connector):
    bad = next(h for h in (f"WS-TEST-{i:03d}" for i in range(100)) if not host_is_reachable(h))
    with pytest.raises(ConnectorTimeout):
        dock_connector.detect_dock(bad)


def test_blank_host_is_a_bad_request(dock_connector):
    with pytest.raises(InvalidRequestError):
        dock_connector.detect_dock("  ")


def test_detect_dock_returns_supported_model(dock_connector, host):
    assert dock_connector.detect_dock(host).model in dock_connector.supported_models()


# -- factory ---------------------------------------------------------------
def test_factory_builds_a_full_mock_set():
    connectors = build_connectors(Settings())
    assert [c.name for c in connectors.all()] == ["phones", "docks", "sms"]
    assert all(c.implementation == "mock" for c in connectors.all())


def test_factory_rejects_unknown_mode_with_helpful_message():
    with pytest.raises(ConfigurationError, match="Available: mock"):
        build_connectors(Settings(connector_mode="live"))


def test_a_custom_connector_set_can_be_registered(connectors):
    register_connector_set("custom", lambda settings: connectors)
    built = build_connectors(Settings(connector_mode="custom"))
    assert isinstance(built, ConnectorSet) and isinstance(built.phones, MockPhoneConnector)
