"""Shared fixtures.

Every test gets its own throw-away SQLite file and its own mock connectors, so
tests never interfere with each other and never need the network.
"""

import pytest

from deviceadmin.app import create_app
from deviceadmin.config import Settings
from deviceadmin.connectors.base import ConnectorSet
from deviceadmin.connectors.mock import MockDockConnector, MockPhoneConnector, MockSmsConnector
from deviceadmin.database import Database
from deviceadmin.services.common import RetryPolicy
from deviceadmin.services.container import build_services

NO_WAIT = RetryPolicy(attempts=3, base_delay=0, sleep=lambda _s: None)


def find_ip(reachable: bool, prefix: str = "198.51.100") -> str:
    """Pick a documentation-range IP the mock considers reachable (or not)."""
    from deviceadmin.connectors.mock import phone_is_reachable

    for i in range(2, 250):
        ip = f"{prefix}.{i}"
        if phone_is_reachable(ip) == reachable:
            return ip
    raise AssertionError("no suitable IP found")


@pytest.fixture
def phone_connector():
    return MockPhoneConnector()


@pytest.fixture
def dock_connector():
    return MockDockConnector()


@pytest.fixture
def sms_connector():
    return MockSmsConnector()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_path=str(tmp_path / "test.db"), testing=True, seed_demo_data=False, page_monitor_enabled=False
    )


@pytest.fixture
def connectors(phone_connector, dock_connector, sms_connector):
    return ConnectorSet(phones=phone_connector, docks=dock_connector, sms=sms_connector)


@pytest.fixture
def services(settings, connectors):
    return build_services(settings, Database(settings.database_path), connectors, NO_WAIT)


@pytest.fixture
def seeded_services(services):
    from deviceadmin.seed import seed_database

    seed_database(services.db)
    return services


@pytest.fixture
def make_phone(services):
    """Factory: create a phone row pointing at a reachable/unreachable mock IP."""
    counter = iter(range(1, 1000))

    def _make(reachable=True, **overrides):
        n = next(counter)
        ip = overrides.pop("ip_address", find_ip(reachable, "203.0.113") if n % 2 else find_ip(reachable))
        values = dict(
            device_name=f"TEST-PH{n:03d}",
            mac_address=f"02:00:5E:00:00:{n:02X}",
            ip_address=ip,
            extension=str(2000 + n),
            status="online" if reachable else "offline",
            firmware_version="6.4.1.0999",
        )
        values.update(overrides)
        return services.phone_repo.add(**values)

    return _make


@pytest.fixture
def client(settings, connectors):
    app = create_app(settings, connectors, NO_WAIT)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def seeded_client(tmp_path, connectors):
    settings = Settings(
        database_path=str(tmp_path / "demo.db"), testing=True, seed_demo_data=True, page_monitor_enabled=False
    )
    return create_app(settings, connectors, NO_WAIT).test_client()
