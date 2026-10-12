"""Business logic for desk phones: inventory sync, reboot, volume policy, firmware."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from ..connectors.base import ConnectorError, PhoneConnector
from ..database import (
    ActivityRepository,
    FirmwareRepository,
    PhoneRepository,
    PolicyRepository,
)
from .common import BulkResult, OperationResult, RetryPolicy, call_with_retry

logger = logging.getLogger(__name__)
DEVICE_TYPE = "phone"


class PhoneService:
    def __init__(
        self,
        phones: PhoneRepository,
        policies: PolicyRepository,
        firmware: FirmwareRepository,
        activity: ActivityRepository,
        connector: PhoneConnector,
        min_volume: int = 0,
        max_volume: int = 10,
        retry: RetryPolicy | None = None,
        max_workers: int = 16,
    ):
        self.phones, self.policies, self.firmware, self.activity = phones, policies, firmware, activity
        self.connector = connector
        self.min_volume, self.max_volume = min_volume, max_volume
        self.retry = retry or RetryPolicy()
        self.max_workers = max_workers

    # -- helpers -----------------------------------------------------------
    def _call(self, fn, *args, **kwargs):
        return call_with_retry(fn, *args, policy=self.retry, **kwargs)

    def clamp_volume(self, level: int) -> int:
        return max(self.min_volume, min(self.max_volume, int(level)))

    def _log(self, action: str, phone: dict, description: str, ok: bool) -> None:
        self.activity.log(
            action, DEVICE_TYPE, phone["id"], phone["device_name"], description, "success" if ok else "failed"
        )

    def _fan_out(self, fn, items: list) -> list:
        """Run an I/O-bound ``fn`` over ``items`` in a thread pool, preserving order."""
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(fn, items))

    # -- single-device operations --------------------------------------------
    def reboot(self, phone_id: int) -> OperationResult:
        phone = self.phones.get(phone_id)
        if not phone:
            return OperationResult.failure("Phone not found")
        try:
            message = self._call(self.connector.reboot, phone["ip_address"])
        except ConnectorError as exc:
            self._log("reboot", phone, f"Reboot failed: {exc}", False)
            return OperationResult.failure(f"Reboot failed for {phone['device_name']}: {exc}")
        self._log("reboot", phone, message, True)
        return OperationResult.success(f"Reboot sent to {phone['device_name']}")

    def get_config(self, phone_id: int) -> OperationResult:
        phone = self.phones.get(phone_id)
        if not phone:
            return OperationResult.failure("Phone not found")
        try:
            config = self._call(self.connector.get_config, phone["ip_address"])
        except ConnectorError as exc:
            self._log("pull_config", phone, f"Config pull failed: {exc}", False)
            return OperationResult.failure(str(exc))
        self._log("pull_config", phone, "Configuration pulled", True)
        return OperationResult.success("Configuration retrieved", config)

    def set_volume(self, phone_id: int, level: int, locked: bool, policy_name: str = "") -> OperationResult:
        phone = self.phones.get(phone_id)
        if not phone:
            return OperationResult.failure("Phone not found")
        level = self.clamp_volume(level)
        label = "LOCKED" if locked else "USER ADJUSTABLE"
        try:
            self._call(self.connector.set_volume, phone["ip_address"], level, locked)
        except ConnectorError as exc:
            self._log("set_volume", phone, f"Volume push failed: {exc}", False)
            return OperationResult.failure(f"Could not set volume on {phone['device_name']}: {exc}")
        # Persist the policy only after the device accepted it.
        self.policies.set(phone_id, level, locked, policy_name)
        self._log("set_volume", phone, f"Volume set to {level} ({label})", True)
        return OperationResult.success(f"Volume {level} ({label}) applied to {phone['device_name']}")

    def push_firmware(self, phone_id: int, firmware_url: str, dry_run: bool = False) -> OperationResult:
        phone = self.phones.get(phone_id)
        if not phone:
            return OperationResult.failure("Phone not found")
        record_id = (
            None
            if dry_run
            else self.firmware.start(
                DEVICE_TYPE, phone_id, phone["device_name"], phone["firmware_version"], firmware_url
            )
        )
        try:
            plan = self._call(self.connector.push_firmware, phone["ip_address"], firmware_url, dry_run)
        except ConnectorError as exc:
            if record_id:
                self.firmware.finish(record_id, "failed", str(exc))
            self._log("firmware_update", phone, f"Firmware update failed: {exc}", False)
            return OperationResult.failure(f"Firmware update failed: {exc}")
        if dry_run:
            return OperationResult.success("Dry run complete - nothing was sent", plan)
        self.firmware.finish(record_id, "success")
        self._log("firmware_update", phone, f"Firmware update initiated: {firmware_url}", True)
        return OperationResult.success(f"Firmware update started on {phone['device_name']}", plan)

    # -- fleet operations -------------------------------------------------------
    def bulk_reboot(self, phone_ids: list[int]) -> BulkResult:
        result = BulkResult()
        for pid in phone_ids:
            phone = self.phones.get(pid)
            if not phone:
                result.skipped.append(str(pid))
                continue
            outcome = self.reboot(pid)
            (
                result.succeeded.append(phone["device_name"])
                if outcome.ok
                else result.failed.append((phone["device_name"], outcome.message))
            )
        return result

    def apply_volume_policy(
        self, phone_ids: list[int], level: int, locked: bool, policy_name: str = "Bulk Policy"
    ) -> BulkResult:
        """Apply one policy to many phones.

        Policies are recorded only for phones that actually accepted the change,
        so the database never claims a phone is locked when it is not.
        """
        level = self.clamp_volume(level)
        result = BulkResult()
        phones = []
        for pid in phone_ids:
            phone = self.phones.get(pid)
            (phones.append(phone) if phone else result.skipped.append(str(pid)))

        def push(phone: dict):
            try:
                self._call(self.connector.set_volume, phone["ip_address"], level, locked)
            except ConnectorError as exc:
                return phone, exc
            return phone, None

        # Network calls fan out across threads; database writes happen afterwards on this thread.
        pushed: list[int] = []
        for phone, error in self._fan_out(push, phones):
            if error is None:
                pushed.append(phone["id"])
                result.succeeded.append(phone["device_name"])
            else:
                result.failed.append((phone["device_name"], str(error)))
                self._log("set_volume", phone, f"Bulk volume failed: {error}", False)
        if pushed:
            self.policies.set_many(pushed, level, locked, policy_name)
        label = "LOCKED" if locked else "USER ADJUSTABLE"
        self.activity.log(
            "bulk_set_volume",
            DEVICE_TYPE,
            None,
            f"{len(phone_ids)} phones",
            f"Volume {level} ({label}): {len(result.succeeded)} ok, {len(result.failed)} failed",
            "success" if result.ok else "failed",
        )
        return result

    def apply_volume_to_fleet(self, level: int, locked: bool, policy_name: str = "Fleet-Wide Policy") -> BulkResult:
        return self.apply_volume_policy([p["id"] for p in self.phones.all()], level, locked, policy_name)

    def sync_fleet(self) -> BulkResult:
        """Poll every phone in parallel and refresh status, firmware and last-seen.

        This is the "one click instead of logging into each phone" workflow.
        Network calls run in a thread pool (they are I/O bound); database writes
        happen afterwards on the calling thread so SQLite sees one writer.
        """
        phones = self.phones.all()

        def probe(phone: dict):
            try:
                return phone, self._call(self.connector.get_device_info, phone["ip_address"]), None
            except ConnectorError as exc:
                return phone, None, exc

        probes = self._fan_out(probe, phones)

        result = BulkResult()
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        for phone, info, error in probes:
            if error is None:
                self.phones.update(phone["id"], status="online", firmware_version=info.firmware, last_seen=stamp)
                result.succeeded.append(phone["device_name"])
            else:
                self.phones.update(phone["id"], status="offline")
                result.failed.append((phone["device_name"], str(error)))
        self.activity.log(
            "fleet_sync",
            DEVICE_TYPE,
            None,
            "Fleet",
            f"Synced {len(phones)} phones: {len(result.succeeded)} online, {len(result.failed)} offline",
            "success",
        )
        return result
