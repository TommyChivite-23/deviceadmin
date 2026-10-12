"""Mock connectors.

These behave like real network integrations: they take time (optionally),
validate input, keep state between calls, and fail in realistic ways. Every
failure is *deterministic* so demos and tests are repeatable.

Failure injection
-----------------
Each mock has a ``fault`` attribute (see :class:`Fault`). Set it from the
Integrations page of the UI, or in a test, to see how the rest of the
application copes with an outage, a timeout, a rate limit, or bad credentials.
"""

from __future__ import annotations

import re
import time
import zlib
from collections import deque
from enum import Enum
from threading import Lock

from .base import (
    CallInfo,
    ConnectorAuthError,
    ConnectorTimeout,
    ConnectorUnavailable,
    DeviceInfo,
    DockConnector,
    DockInfo,
    FirmwarePlan,
    HealthState,
    HealthStatus,
    InvalidRequestError,
    PhoneConnector,
    RateLimitError,
    SendResult,
    SmsConnector,
)


class Fault(str, Enum):
    NONE = "none"
    OUTAGE = "outage"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"


def _stable_hash(value: str) -> int:
    """Deterministic across processes (unlike the built-in ``hash``)."""
    return zlib.crc32(value.encode("utf-8"))


def phone_is_reachable(ip_address: str) -> bool:
    """About 1 phone in 14 is 'offline'. Seed data uses the same rule."""
    return _stable_hash(ip_address) % 14 != 0


def host_is_reachable(host_computer: str) -> bool:
    """About 1 host in 12 is 'unreachable'."""
    return _stable_hash(host_computer) % 12 != 0


class _MockBase:
    implementation = "mock"

    def __init__(self, latency_ms: int = 0):
        self.latency_ms = latency_ms
        self.fault = Fault.NONE
        self.calls = 0
        self.last_error: str = ""
        self._lock = Lock()

    def set_fault(self, fault: Fault | str) -> None:
        self.fault = Fault(fault)

    def _enter(self) -> None:
        """Common preamble: count the call, simulate latency, raise faults."""
        with self._lock:
            self.calls += 1
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        try:
            if self.fault is Fault.OUTAGE:
                raise ConnectorUnavailable("503 Service Unavailable (injected outage)")
            if self.fault is Fault.TIMEOUT:
                raise ConnectorTimeout("Request timed out after 10s (injected timeout)")
            if self.fault is Fault.RATE_LIMIT:
                raise RateLimitError("429 Too Many Requests (injected)", retry_after=0.05)
            if self.fault is Fault.AUTH:
                raise ConnectorAuthError("401 Unauthorized (injected bad credentials)")
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def health_check(self) -> HealthStatus:
        start = time.perf_counter()
        try:
            self._enter()
        except Exception as exc:
            state = HealthState.DEGRADED if isinstance(exc, RateLimitError) else HealthState.DOWN
            return HealthStatus(state, str(exc), (time.perf_counter() - start) * 1000)
        return HealthStatus(HealthState.HEALTHY, "OK (mock)", (time.perf_counter() - start) * 1000)


# --------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------
_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


class MockPhoneConnector(_MockBase, PhoneConnector):
    """Simulates a fleet of IP desk phones addressed by IP."""

    def __init__(self, latency_ms: int = 0):
        super().__init__(latency_ms)
        self._config: dict[str, dict[str, str]] = {}
        self._reboots: dict[str, int] = {}
        self._pending_pages: dict[str, deque[CallInfo]] = {}

    # -- helpers -----------------------------------------------------------
    def _check_ip(self, ip_address: str) -> None:
        match = _IPV4.match(ip_address or "")
        if not match or any(int(g) > 255 for g in match.groups()):
            raise InvalidRequestError(f"Invalid IPv4 address: {ip_address!r}")

    def _reach(self, ip_address: str) -> None:
        self._enter()
        self._check_ip(ip_address)
        if not phone_is_reachable(ip_address):
            raise ConnectorTimeout(f"No response from {ip_address} (host unreachable)")

    def _default_config(self) -> dict[str, str]:
        return {
            "voice.volume.handset": "5",
            "voice.volume.headset": "5",
            "voice.volume.handsfree": "5",
            "voice.volume.locked": "0",
            "prov.server.type": "HTTP",
        }

    # -- PhoneConnector ----------------------------------------------------
    def get_device_info(self, ip_address: str) -> DeviceInfo:
        self._reach(ip_address)
        digest = _stable_hash(ip_address)
        return DeviceInfo(
            model="Vantage VX-250",
            firmware="6.4.3.1234",
            mac_address=f"02:00:5E:{(digest >> 16) & 255:02X}:{(digest >> 8) & 255:02X}:{digest & 255:02X}",
            serial=f"VX{digest:08X}",
            uptime=f"{digest % 20} days {digest % 24:02d}:{digest % 60:02d}:00",
            ip_address=ip_address,
        )

    def reboot(self, ip_address: str) -> str:
        self._reach(ip_address)
        self._reboots[ip_address] = self._reboots.get(ip_address, 0) + 1
        return f"Reboot accepted by {ip_address}; expect it back in ~60s"

    def get_config(self, ip_address: str) -> dict[str, str]:
        self._reach(ip_address)
        return dict(self._config.setdefault(ip_address, self._default_config()))

    def set_volume(self, ip_address: str, level: int, locked: bool) -> None:
        self._reach(ip_address)
        if not 0 <= level <= 10:
            raise InvalidRequestError(f"Volume must be 0-10, got {level}")
        cfg = self._config.setdefault(ip_address, self._default_config())
        for key in ("handset", "headset", "handsfree"):
            cfg[f"voice.volume.{key}"] = str(level)
        cfg["voice.volume.locked"] = "1" if locked else "0"

    def push_firmware(self, ip_address: str, firmware_url: str, dry_run: bool = False) -> FirmwarePlan:
        self._reach(ip_address)
        warnings: list[str] = []
        if not firmware_url or not firmware_url.lower().startswith(("http://", "https://")):
            raise InvalidRequestError("firmware_url must be a fully-qualified http(s) URL")
        if re.search(r"//(localhost|127\.0\.0\.1)", firmware_url.lower()):
            raise InvalidRequestError("firmware_url points at localhost; the phone cannot reach it")
        if not firmware_url.lower().endswith((".fw", ".bin")):
            warnings.append("URL does not end in .fw/.bin - double-check the file")
        steps = [
            {
                "step": 1,
                "method": "POST",
                "url": f"http://{ip_address}/provisioning/firmware",
                "body": {"firmware_url": firmware_url},
            },
            {"step": 2, "method": "POST", "url": f"http://{ip_address}/device/reboot"},
        ]
        return FirmwarePlan(steps=steps, warnings=warnings)

    def get_call_status(self, ip_address: str) -> list[CallInfo]:
        self._reach(ip_address)
        queue = self._pending_pages.get(ip_address)
        if queue:
            return [queue.popleft()]
        return []

    # -- test / demo hooks (not part of the interface) ----------------------
    def simulate_page(self, target_ip: str, source_name: str, source_number: str) -> None:
        """Queue a page so the next poll of ``target_ip`` sees it."""
        self._pending_pages.setdefault(target_ip, deque()).append(
            CallInfo("Connected", "Paging", source_name, source_number)
        )

    def reboot_count(self, ip_address: str) -> int:
        return self._reboots.get(ip_address, 0)


# --------------------------------------------------------------------------
# Docks
# --------------------------------------------------------------------------
DOCK_MODELS = {
    "Orbit Thunderbolt Dock 180W": "1.0.78.0",
    "Orbit USB-C Dock G2": "1.0.22.0",
    "Orbit Thunderbolt Dock 120W": "1.0.75.0",
}


class MockDockConnector(_MockBase, DockConnector):
    def __init__(self, latency_ms: int = 0):
        super().__init__(latency_ms)
        self._versions: dict[tuple[str, str], str] = {}

    def _reach(self, host_computer: str) -> None:
        self._enter()
        if not host_computer or not host_computer.strip():
            raise InvalidRequestError("host_computer is required")
        if not host_is_reachable(host_computer):
            raise ConnectorTimeout(f"Remote session to {host_computer} timed out")

    def supported_models(self) -> list[str]:
        return list(DOCK_MODELS)

    def latest_firmware(self, model: str) -> str:
        try:
            return DOCK_MODELS[model]
        except KeyError:
            raise InvalidRequestError(f"Unsupported dock model: {model!r}") from None

    def detect_dock(self, host_computer: str) -> DockInfo:
        self._reach(host_computer)
        digest = _stable_hash(host_computer)
        model = list(DOCK_MODELS)[digest % len(DOCK_MODELS)]
        return DockInfo(
            model,
            self.get_firmware_version(host_computer, model),
            "OK",
            f"USB\\VID_0000&PID_{digest & 0xFFFF:04X}\\MOCK",
        )

    def get_firmware_version(self, host_computer: str, model: str) -> str:
        self._reach(host_computer)
        latest = self.latest_firmware(model)
        return self._versions.get((host_computer, model), self._older(latest))

    @staticmethod
    def _older(version: str) -> str:
        parts = version.split(".")
        parts[2] = str(max(int(parts[2]) - 3, 0))
        return ".".join(parts)

    def push_firmware(self, host_computer: str, model: str, firmware_file: str) -> str:
        self._reach(host_computer)
        latest = self.latest_firmware(model)
        if not (firmware_file or "").lower().endswith(".cab"):
            raise InvalidRequestError("Firmware package must be a .cab file")
        self._versions[(host_computer, model)] = latest
        return latest


# --------------------------------------------------------------------------
# SMS
# --------------------------------------------------------------------------
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
OPTED_OUT_SUFFIX = "0100"  # numbers ending in 0100 have opted out of messages


class MockSmsConnector(_MockBase, SmsConnector):
    """Mock SMS gateway with validation, opt-outs, and a sliding rate limit."""

    def __init__(self, latency_ms: int = 0, max_per_minute: int = 60):
        super().__init__(latency_ms)
        self.max_per_minute = max_per_minute
        self.sent: list[dict] = []
        self._window: deque[float] = deque()

    def send(self, to_number: str, text: str) -> SendResult:
        self._enter()
        if not _E164.match(to_number or ""):
            raise InvalidRequestError(f"Recipient must be E.164 (+12025550123), got {to_number!r}")
        if not text or not text.strip():
            raise InvalidRequestError("Message text is empty")
        if len(text) > 1600:
            raise InvalidRequestError("Message exceeds 1600 characters")

        now = time.monotonic()
        while self._window and now - self._window[0] > 60:
            self._window.popleft()
        if len(self._window) >= self.max_per_minute:
            oldest = self._window[0] if self._window else now
            raise RateLimitError(retry_after=60 - (now - oldest))
        self._window.append(now)

        message_id = f"mock-{len(self.sent) + 1:06d}"
        segments = max(1, -(-len(text) // 160))
        if to_number.endswith(OPTED_OUT_SUFFIX):
            result = SendResult(message_id, "undeliverable", segments, "Recipient has opted out")
        else:
            result = SendResult(message_id, "delivered", segments, "Accepted by mock gateway")
        self.sent.append({"to": to_number, "text": text, "status": result.status, "id": message_id})
        return result
