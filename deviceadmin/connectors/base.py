"""Connector contracts.

Everything the application knows about the outside world is expressed here:
abstract interfaces, plain data objects, and a small exception hierarchy.

Services depend on these abstractions only. A mock connector (shipped in this
repository) and a real connector (kept private in the original deployment)
implement the same interfaces, so swapping one for the other is a configuration
change, not a code change in the business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class ConnectorError(Exception):
    """Base class for every failure raised by a connector.

    ``retryable`` tells the resilience layer whether trying again could help.
    """

    retryable = False


class ConnectorTimeout(ConnectorError):
    """The remote system did not answer in time."""

    retryable = True


class ConnectorUnavailable(ConnectorError):
    """The remote system is down or refusing connections."""

    retryable = True


class RateLimitError(ConnectorError):
    """The remote system asked us to slow down (HTTP 429 equivalent)."""

    retryable = True

    def __init__(self, message: str = "Rate limit exceeded", retry_after: float = 1.0):
        super().__init__(message)
        self.retry_after = retry_after


class ConnectorAuthError(ConnectorError):
    """Credentials were rejected (HTTP 401/403 equivalent). Never retried."""


class InvalidRequestError(ConnectorError):
    """The request itself was wrong (HTTP 400/422 equivalent). Never retried."""


class ConfigurationError(Exception):
    """Raised at start-up when connectors are mis-configured."""


# --------------------------------------------------------------------------
# Data objects returned by connectors
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DeviceInfo:
    model: str
    firmware: str
    mac_address: str
    serial: str
    uptime: str
    ip_address: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CallInfo:
    call_state: str
    call_type: str  # "Normal", "Paging", "Intercom"
    remote_party_name: str
    remote_party_number: str
    line_id: str = "1"

    @property
    def is_paging(self) -> bool:
        return self.call_type.lower() in {"paging", "intercom"}


@dataclass(frozen=True)
class DockInfo:
    model: str
    firmware: str
    status: str
    instance_id: str = ""


@dataclass(frozen=True)
class SendResult:
    message_id: str
    status: str  # "delivered" | "undeliverable"
    segments: int = 1
    detail: str = ""

    @property
    def delivered(self) -> bool:
        return self.status == "delivered"


@dataclass(frozen=True)
class FirmwarePlan:
    """What a firmware push *would* do. Returned for dry runs."""

    steps: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class HealthStatus:
    state: HealthState
    detail: str = ""
    latency_ms: float = 0.0


# --------------------------------------------------------------------------
# Interfaces
# --------------------------------------------------------------------------
class Connector(ABC):
    """Common surface every connector exposes."""

    #: stable machine name, e.g. "phones"
    name: str = "connector"
    #: display name for the UI
    label: str = "Connector"
    #: human description shown on the Integrations page
    description: str = ""
    #: which implementation this is, e.g. "mock"
    implementation: str = "unknown"

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Cheap probe used by the Integrations page and /healthz."""


class PhoneConnector(Connector):
    """Remote management of IP desk phones."""

    name = "phones"
    label = "Phones"
    description = "IP desk-phone management API (inventory, config, reboot, firmware, call status)"

    @abstractmethod
    def get_device_info(self, ip_address: str) -> DeviceInfo: ...

    @abstractmethod
    def reboot(self, ip_address: str) -> str: ...

    @abstractmethod
    def get_config(self, ip_address: str) -> dict[str, str]: ...

    @abstractmethod
    def set_volume(self, ip_address: str, level: int, locked: bool) -> None: ...

    @abstractmethod
    def push_firmware(self, ip_address: str, firmware_url: str, dry_run: bool = False) -> FirmwarePlan: ...

    @abstractmethod
    def get_call_status(self, ip_address: str) -> list[CallInfo]: ...


class DockConnector(Connector):
    """Docking-station firmware management through the host computer."""

    name = "docks"
    label = "Docks"
    description = "Docking-station management via the attached host computer"

    @abstractmethod
    def detect_dock(self, host_computer: str) -> DockInfo: ...

    @abstractmethod
    def get_firmware_version(self, host_computer: str, model: str) -> str: ...

    @abstractmethod
    def push_firmware(self, host_computer: str, model: str, firmware_file: str) -> str:
        """Apply firmware and return the new version string."""

    @abstractmethod
    def latest_firmware(self, model: str) -> str: ...

    @abstractmethod
    def supported_models(self) -> list[str]: ...


class SmsConnector(Connector):
    """Outbound SMS gateway."""

    name = "sms"
    label = "SMS gateway"
    description = "SMS gateway used for page alerts and emergency broadcasts"

    @abstractmethod
    def send(self, to_number: str, text: str) -> SendResult: ...


@dataclass
class ConnectorSet:
    """The bundle of connectors a running application uses."""

    phones: PhoneConnector
    docks: DockConnector
    sms: SmsConnector

    def all(self) -> list[Connector]:
        return [self.phones, self.docks, self.sms]
