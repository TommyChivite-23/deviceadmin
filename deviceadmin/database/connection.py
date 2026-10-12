"""SQLite connection handling and schema.

SQLite keeps the project zero-dependency and the whole dataset in one file. The
repository classes in ``repositories.py`` are the only code that writes SQL, so
moving to PostgreSQL later means changing this package, not the services.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS departments (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS locations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    site TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    title         TEXT DEFAULT '',
    department_id INTEGER REFERENCES departments(id)
);
CREATE TABLE IF NOT EXISTS phones (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name         TEXT NOT NULL,
    mac_address         TEXT NOT NULL UNIQUE,
    ip_address          TEXT NOT NULL,
    extension           TEXT DEFAULT '',
    user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
    location_id         INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    department_id       INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    model               TEXT DEFAULT 'Vantage VX-250',
    firmware_version    TEXT DEFAULT '',
    provisioning_status TEXT DEFAULT 'provisioned',
    status              TEXT DEFAULT 'offline',
    last_seen           TEXT,
    notes               TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_phones_status ON phones(status);
CREATE INDEX IF NOT EXISTS idx_phones_department ON phones(department_id);
CREATE TABLE IF NOT EXISTS volume_policies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_id     INTEGER NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    volume_level INTEGER NOT NULL,
    is_locked    INTEGER NOT NULL DEFAULT 0,
    policy_name  TEXT DEFAULT '',
    applied_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_volume_phone ON volume_policies(phone_id);
CREATE TABLE IF NOT EXISTS docks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dock_name        TEXT NOT NULL,
    model            TEXT NOT NULL,
    serial_number    TEXT NOT NULL UNIQUE,
    firmware_version TEXT DEFAULT '',
    host_computer    TEXT DEFAULT '',
    user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    location_id      INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    department_id    INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    status           TEXT DEFAULT 'unknown',
    last_seen        TEXT,
    notes            TEXT DEFAULT '',
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS activity_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT DEFAULT (datetime('now')),
    action_type  TEXT NOT NULL,
    device_type  TEXT DEFAULT '',
    device_id    INTEGER,
    device_name  TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    status       TEXT DEFAULT 'success',
    performed_by TEXT DEFAULT 'admin'
);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(timestamp);
CREATE TABLE IF NOT EXISTS firmware_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_type   TEXT NOT NULL,
    device_id     INTEGER NOT NULL,
    device_name   TEXT DEFAULT '',
    old_version   TEXT DEFAULT '',
    new_version   TEXT DEFAULT '',
    status        TEXT DEFAULT 'in_progress',
    started_at    TEXT DEFAULT (datetime('now')),
    completed_at  TEXT,
    error_message TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS alert_contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL,
    role       TEXT DEFAULT '',
    is_active  INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS alert_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT DEFAULT (datetime('now')),
    message        TEXT NOT NULL,
    total_contacts INTEGER DEFAULT 0,
    sent_count     INTEGER DEFAULT 0,
    failed_count   INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'success',
    details        TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS page_notify_contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL,
    is_active  INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS page_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT DEFAULT (datetime('now')),
    source_phone_id     INTEGER,
    source_name         TEXT DEFAULT '',
    source_extension    TEXT DEFAULT '',
    source_user         TEXT DEFAULT '',
    source_location     TEXT DEFAULT '',
    target_phone_id     INTEGER,
    target_name         TEXT DEFAULT '',
    target_extension    TEXT DEFAULT '',
    call_type           TEXT DEFAULT 'Paging',
    sms_sent            INTEGER DEFAULT 0,
    sms_detail          TEXT DEFAULT ''
);
"""


class Database:
    """Opens short-lived connections and guarantees they are closed."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.init_schema()
        logger.info("Database ready at %s", path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection; commit on success, roll back on error, always close."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
