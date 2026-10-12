"""Composition root: the one place where everything is wired together.

Routes receive a :class:`Services` object from the Flask app, never build their
own dependencies. Tests build the same container around a temporary database
and whichever connectors they like (dependency injection without a framework).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..connectors.base import ConnectorSet
from ..database import (
    ActivityRepository,
    AlertHistoryRepository,
    ContactRepository,
    Database,
    DockRepository,
    FirmwareRepository,
    PageEventRepository,
    PhoneRepository,
    PolicyRepository,
    ReferenceRepository,
    StatsRepository,
)
from .alert_service import AlertService
from .common import RetryPolicy
from .dock_service import DockService
from .page_monitor import PageMonitor
from .phone_service import PhoneService


@dataclass
class Services:
    settings: Settings
    db: Database
    connectors: ConnectorSet
    # repositories
    reference: ReferenceRepository
    phone_repo: PhoneRepository
    dock_repo: DockRepository
    policy_repo: PolicyRepository
    activity: ActivityRepository
    firmware: FirmwareRepository
    stats: StatsRepository
    alert_contacts: ContactRepository
    alert_history: AlertHistoryRepository
    page_contacts: ContactRepository
    page_events: PageEventRepository
    # services
    phones: PhoneService
    docks: DockService
    alerts: AlertService
    monitor: PageMonitor


def build_services(
    settings: Settings, db: Database, connectors: ConnectorSet, retry: RetryPolicy | None = None
) -> Services:
    reference = ReferenceRepository(db)
    phone_repo, dock_repo = PhoneRepository(db), DockRepository(db)
    policy_repo, activity = PolicyRepository(db), ActivityRepository(db)
    firmware, stats = FirmwareRepository(db), StatsRepository(db)
    alert_contacts = ContactRepository(db, "alert_contacts")
    page_contacts = ContactRepository(db, "page_notify_contacts")
    alert_history, page_events = AlertHistoryRepository(db), PageEventRepository(db)

    alerts = AlertService(alert_contacts, alert_history, activity, connectors.sms, retry)
    monitor = PageMonitor(
        phone_repo,
        page_events,
        page_contacts,
        activity,
        connectors.phones,
        alerts,
        poll_interval=settings.page_monitor_poll_interval,
        cooldown_seconds=settings.page_monitor_cooldown,
        enabled=settings.page_monitor_enabled,
    )
    return Services(
        settings=settings,
        db=db,
        connectors=connectors,
        reference=reference,
        phone_repo=phone_repo,
        dock_repo=dock_repo,
        policy_repo=policy_repo,
        activity=activity,
        firmware=firmware,
        stats=stats,
        alert_contacts=alert_contacts,
        alert_history=alert_history,
        page_contacts=page_contacts,
        page_events=page_events,
        phones=PhoneService(
            phone_repo,
            policy_repo,
            firmware,
            activity,
            connectors.phones,
            settings.min_volume,
            settings.max_volume,
            retry,
        ),
        docks=DockService(dock_repo, firmware, activity, connectors.docks, retry),
        alerts=alerts,
        monitor=monitor,
    )
