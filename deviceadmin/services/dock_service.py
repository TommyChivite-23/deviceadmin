"""Business logic for docking stations (managed through their host computer)."""

from __future__ import annotations

from datetime import UTC, datetime

from ..connectors.base import ConnectorError, DockConnector
from ..database import ActivityRepository, DockRepository, FirmwareRepository
from .common import BulkResult, OperationResult, RetryPolicy, call_with_retry

DEVICE_TYPE = "dock"


class DockService:
    def __init__(
        self,
        docks: DockRepository,
        firmware: FirmwareRepository,
        activity: ActivityRepository,
        connector: DockConnector,
        retry: RetryPolicy | None = None,
    ):
        self.docks, self.firmware, self.activity = docks, firmware, activity
        self.connector = connector
        self.retry = retry or RetryPolicy()

    def _call(self, fn, *args):
        return call_with_retry(fn, *args, policy=self.retry)

    def _log(self, action: str, dock: dict, description: str, ok: bool) -> None:
        self.activity.log(
            action, DEVICE_TYPE, dock["id"], dock["dock_name"], description, "success" if ok else "failed"
        )

    def is_outdated(self, dock: dict) -> bool:
        try:
            return dock["firmware_version"] != self.connector.latest_firmware(dock["model"])
        except ConnectorError:
            return False  # unknown model: nothing to compare against

    def outdated_count(self) -> int:
        return sum(1 for d in self.docks.list() if self.is_outdated(d))

    def push_firmware(self, dock_id: int, firmware_file: str) -> OperationResult:
        dock = self.docks.get(dock_id)
        if not dock:
            return OperationResult.failure("Dock not found")
        record_id = self.firmware.start(DEVICE_TYPE, dock_id, dock["dock_name"], dock["firmware_version"], "latest")
        try:
            new_version = self._call(self.connector.push_firmware, dock["host_computer"], dock["model"], firmware_file)
        except ConnectorError as exc:
            self.firmware.finish(record_id, "failed", str(exc))
            self._log("firmware_update", dock, f"Firmware update failed: {exc}", False)
            return OperationResult.failure(f"Firmware update failed on {dock['dock_name']}: {exc}")
        self.firmware.finish(record_id, "success", new_version=new_version)
        self.docks.update(dock_id, firmware_version=new_version)
        self._log("firmware_update", dock, f"Firmware updated to {new_version}", True)
        return OperationResult.success(f"{dock['dock_name']} updated to {new_version}", new_version)

    def bulk_push_firmware(self, dock_ids: list[int], firmware_file: str) -> BulkResult:
        result = BulkResult()
        for did in dock_ids:
            dock = self.docks.get(did)
            if not dock:
                result.skipped.append(str(did))
                continue
            outcome = self.push_firmware(did, firmware_file)
            (
                result.succeeded.append(dock["dock_name"])
                if outcome.ok
                else result.failed.append((dock["dock_name"], outcome.message))
            )
        return result

    def refresh(self, dock_id: int) -> OperationResult:
        """Ask the host what firmware the dock is really running."""
        dock = self.docks.get(dock_id)
        if not dock:
            return OperationResult.failure("Dock not found")
        try:
            version = self._call(self.connector.get_firmware_version, dock["host_computer"], dock["model"])
        except ConnectorError as exc:
            self.docks.update(dock_id, status="disconnected")
            self._log("refresh", dock, f"Refresh failed: {exc}", False)
            return OperationResult.failure(f"Could not reach {dock['host_computer']}: {exc}")
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        self.docks.update(dock_id, firmware_version=version, status="connected", last_seen=stamp)
        self._log("refresh", dock, f"Firmware reported as {version}", True)
        return OperationResult.success(f"{dock['dock_name']} reports firmware {version}", version)
