"""Connector factory: the single place that decides which implementation runs.

To plug in real connectors in a private deployment, write a function that
returns a :class:`ConnectorSet` and register it::

    from deviceadmin.connectors.factory import register_connector_set
    register_connector_set("live", build_live_connectors)

then start the app with ``DEVICEADMIN_CONNECTOR_MODE=live``. Nothing else in the
code base changes - services only ever see the abstract interfaces.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Settings
from .base import ConfigurationError, ConnectorSet
from .mock import MockDockConnector, MockPhoneConnector, MockSmsConnector

Builder = Callable[[Settings], ConnectorSet]


def _build_mock(settings: Settings) -> ConnectorSet:
    latency = settings.mock_latency_ms
    return ConnectorSet(
        phones=MockPhoneConnector(latency),
        docks=MockDockConnector(latency),
        sms=MockSmsConnector(latency),
    )


_REGISTRY: dict[str, Builder] = {"mock": _build_mock}


def register_connector_set(mode: str, builder: Builder) -> None:
    _REGISTRY[mode.lower()] = builder


def build_connectors(settings: Settings) -> ConnectorSet:
    try:
        builder = _REGISTRY[settings.connector_mode]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ConfigurationError(
            f"Unknown connector mode {settings.connector_mode!r}. Available: {known}. "
            "This public edition only ships the 'mock' connectors."
        ) from None
    return builder(settings)
