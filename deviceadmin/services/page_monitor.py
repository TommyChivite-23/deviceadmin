"""Page monitor: background polling for paging/intercom calls with SMS alerts.

Runs as a daemon thread. For each online phone it asks the phone connector for
active calls; a paging call is matched to a known source phone, stored as an
event, and announced by SMS to the notification list. A per-phone cooldown stops
one long page from flooding the contact list.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from ..connectors.base import ConnectorError, PhoneConnector
from ..database import ActivityRepository, ContactRepository, PageEventRepository, PhoneRepository
from .alert_service import AlertService

logger = logging.getLogger(__name__)


class PageMonitor:
    def __init__(
        self,
        phones: PhoneRepository,
        events: PageEventRepository,
        contacts: ContactRepository,
        activity: ActivityRepository,
        connector: PhoneConnector,
        alerts: AlertService,
        poll_interval: int = 15,
        cooldown_seconds: int = 60,
        enabled: bool = True,
        clock=datetime.now,
    ):
        self.phones, self.events, self.contacts, self.activity = phones, events, contacts, activity
        self.connector, self.alerts = connector, alerts
        self.poll_interval, self.cooldown_seconds, self.enabled = poll_interval, cooldown_seconds, enabled
        self._clock = clock
        self._cooldowns: dict[int, datetime] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle -------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PageMonitor", daemon=True)
        self._thread.start()
        logger.info("Page monitor started (poll %ss, cooldown %ss)", self.poll_interval, self.cooldown_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.enabled:
                try:
                    self.poll_once()
                except Exception:  # keep the thread alive no matter what
                    logger.exception("Page monitor poll failed")
            self._stop.wait(self.poll_interval)

    # -- one polling cycle -------------------------------------------------------
    def poll_once(self) -> int:
        """Check every online phone once; return the number of pages recorded."""
        phones = self.phones.all()
        recorded = 0
        for phone in (p for p in phones if p["status"] == "online"):
            try:
                calls = self.connector.get_call_status(phone["ip_address"])
            except ConnectorError as exc:
                logger.debug("Call status unavailable for %s: %s", phone["device_name"], exc)
                continue
            for call in calls:
                if call.is_paging and self._record_page(phone, call, phones):
                    recorded += 1
        return recorded

    def _record_page(self, target: dict, call, all_phones: list[dict]) -> bool:
        now = self._clock()
        last = self._cooldowns.get(target["id"])
        if last and (now - last).total_seconds() < self.cooldown_seconds:
            return False
        self._cooldowns[target["id"]] = now

        source = self.resolve_source(call.remote_party_name, call.remote_party_number, all_phones)
        sms_sent, sms_detail = self._notify(source, target, call.call_type, now)
        self.events.add(
            source_phone_id=source["id"],
            source_name=source["device_name"],
            source_extension=source["extension"],
            source_user=source["assigned_user"],
            source_location=source["location"],
            target_phone_id=target["id"],
            target_name=target["device_name"],
            target_extension=target["extension"],
            call_type=call.call_type,
            sms_sent=sms_sent,
            sms_detail=sms_detail,
        )
        description = (
            f"Page detected: {source['device_name']} (ext {source['extension']}) -> "
            f"{target['device_name']} (ext {target['extension']})"
        )
        self.activity.log("page_detected", "phone", target["id"], target["device_name"], description)
        logger.info("%s | SMS sent: %s", description, sms_sent)
        return True

    @staticmethod
    def resolve_source(name: str, number: str, phones: list[dict]) -> dict:
        """Match the calling party to a known phone by extension, then by name."""
        blank = {
            "id": None,
            "device_name": name or "Unknown Source",
            "extension": number or "",
            "assigned_user": "",
            "location": "",
        }
        if number:
            for p in phones:
                if p["extension"] == number:
                    return {
                        k: (p[k] or "") if k != "id" else p[k]
                        for k in ("id", "device_name", "extension", "assigned_user", "location")
                    }
        if name:
            needle = name.lower()
            for p in phones:
                if needle in p["device_name"].lower() or needle in (p["assigned_user"] or "").lower():
                    return {
                        k: (p[k] or "") if k != "id" else p[k]
                        for k in ("id", "device_name", "extension", "assigned_user", "location")
                    }
        return blank

    def _notify(self, source: dict, target: dict, call_type: str, now: datetime) -> tuple[bool, str]:
        contacts = self.contacts.list(active_only=True)
        if not contacts:
            return False, "No active notification contacts configured"
        text = f"PAGE ALERT ({now.strftime('%I:%M %p')}): {call_type} from {source['device_name']} (ext {source['extension']})"
        if source["assigned_user"]:
            text += f" [{source['assigned_user']}]"
        if source["location"]:
            text += f" at {source['location']}"
        text += f" -> {target['device_name']} (ext {target['extension']})"
        outcomes = [(c["name"], *self.alerts.send_one(c["phone"], text)) for c in contacts]
        sent = sum(1 for _, ok, _ in outcomes if ok)
        detail = f"{sent}/{len(contacts)} sent. " + "; ".join(
            f"{name}: {'OK' if ok else 'FAILED'}" for name, ok, _ in outcomes
        )
        return sent > 0, detail
