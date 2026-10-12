"""Emergency broadcast: send one SMS to every active contact and record the outcome."""

from __future__ import annotations

import re

from ..connectors.base import ConnectorError, SmsConnector
from ..database import ActivityRepository, AlertHistoryRepository, ContactRepository
from .common import OperationResult, RetryPolicy, call_with_retry

DEFAULT_MESSAGE = (
    "DEMOCORP ALERT: This is an official notification from DemoCorp. "
    "Please follow established procedures and contact your supervisor for instructions."
)


def normalize_phone(raw: str) -> str | None:
    """Return a US number in E.164 (+1XXXXXXXXXX), or None if it is not valid."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


class AlertService:
    def __init__(
        self,
        contacts: ContactRepository,
        history: AlertHistoryRepository,
        activity: ActivityRepository,
        sms: SmsConnector,
        retry: RetryPolicy | None = None,
    ):
        self.contacts, self.history, self.activity, self.sms = contacts, history, activity, sms
        self.retry = retry or RetryPolicy()

    def send_one(self, phone: str, text: str) -> tuple[bool, str]:
        """Send a single SMS; returns (delivered, human-readable detail)."""
        try:
            result = call_with_retry(self.sms.send, phone, text, policy=self.retry)
        except ConnectorError as exc:
            return False, str(exc)
        return result.delivered, result.detail

    def broadcast(self, custom_message: str = "") -> OperationResult:
        contacts = self.contacts.list(active_only=True)
        if not contacts:
            return OperationResult.failure("No active contacts on the alert list")
        message = f"ALERT: {custom_message.strip()}" if custom_message.strip() else DEFAULT_MESSAGE

        lines, sent = [], 0
        for contact in contacts:
            delivered, detail = self.send_one(contact["phone"], message)
            sent += delivered
            lines.append(f"{contact['name']} ({contact['phone']}): {'OK' if delivered else 'FAILED'} - {detail}")
        failed = len(contacts) - sent
        status = "success" if failed == 0 else ("partial" if sent else "failed")
        self.history.add(message, len(contacts), sent, failed, status, "\n".join(lines))
        self.activity.log(
            "broadcast",
            "alert",
            None,
            "Emergency Broadcast",
            f"Broadcast sent: {sent}/{len(contacts)} delivered",
            "success" if failed == 0 else "failed",
        )
        text = f"Broadcast finished: {sent} delivered, {failed} failed"
        return OperationResult(failed == 0, text, {"sent": sent, "failed": failed, "status": status})

    def send_test(self, contact_id: int) -> OperationResult:
        contact = self.contacts.get(contact_id)
        if not contact:
            return OperationResult.failure("Contact not found")
        delivered, detail = self.send_one(
            contact["phone"], "TEST: This is a test message from DemoCorp Device Admin. No action required."
        )
        self.activity.log(
            "broadcast_test",
            "alert",
            None,
            contact["name"],
            f"Test SMS to {contact['phone']}: {detail}",
            "success" if delivered else "failed",
        )
        if delivered:
            return OperationResult.success(f"Test message delivered to {contact['name']}")
        return OperationResult.failure(f"Test failed for {contact['name']}: {detail}")
