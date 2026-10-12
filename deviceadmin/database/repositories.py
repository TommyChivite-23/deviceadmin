"""Repositories: one small class per aggregate, all SQL lives here.

Services call these; nothing above this layer builds SQL strings. Column names
in dynamic UPDATEs are checked against an allow-list, so user input can never
reach the SQL text (values are always bound parameters).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .connection import Database

PHONE_FIELDS = {
    "device_name",
    "mac_address",
    "ip_address",
    "extension",
    "user_id",
    "location_id",
    "department_id",
    "model",
    "firmware_version",
    "provisioning_status",
    "status",
    "last_seen",
    "notes",
}
DOCK_FIELDS = {
    "dock_name",
    "model",
    "serial_number",
    "firmware_version",
    "host_computer",
    "user_id",
    "location_id",
    "department_id",
    "status",
    "last_seen",
    "notes",
}

_PHONE_SELECT = """
    SELECT p.*, u.name AS assigned_user, l.name AS location, l.site AS site,
           d.name AS department
    FROM phones p
    LEFT JOIN users u ON u.id = p.user_id
    LEFT JOIN locations l ON l.id = p.location_id
    LEFT JOIN departments d ON d.id = p.department_id
"""
_DOCK_SELECT = """
    SELECT k.*, u.name AS assigned_user, l.name AS location, l.site AS site,
           d.name AS department
    FROM docks k
    LEFT JOIN users u ON u.id = k.user_id
    LEFT JOIN locations l ON l.id = k.location_id
    LEFT JOIN departments d ON d.id = k.department_id
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _clean(fields: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    return fields


class _Repo:
    def __init__(self, db: Database):
        self.db = db

    def _insert(self, table: str, values: dict[str, Any]) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.db.connection() as conn:
            cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))  # noqa: S608
            return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.db.connection() as conn:
            conn.execute(
                f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608
                (*values.values(), _now(), row_id),
            )


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
class ReferenceRepository(_Repo):
    def departments(self) -> list[dict]:
        with self.db.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM departments ORDER BY name")]

    def locations(self) -> list[dict]:
        with self.db.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM locations ORDER BY site, name")]

    def users(self) -> list[dict]:
        with self.db.connection() as c:
            rows = c.execute(
                """SELECT u.*, d.name AS department,
                          (SELECT COUNT(*) FROM phones WHERE user_id = u.id) AS phone_count,
                          (SELECT COUNT(*) FROM docks WHERE user_id = u.id) AS dock_count
                   FROM users u LEFT JOIN departments d ON d.id = u.department_id
                   ORDER BY u.name"""
            )
            return [dict(r) for r in rows]

    def add_department(self, name: str) -> int:
        return self._insert("departments", {"name": name})

    def add_location(self, name: str, site: str) -> int:
        return self._insert("locations", {"name": name, "site": site})

    def add_user(self, name: str, email: str, title: str = "", department_id: int | None = None) -> int:
        return self._insert("users", {"name": name, "email": email, "title": title, "department_id": department_id})


# --------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------
class PhoneRepository(_Repo):
    def list(
        self, search: str = "", status: str = "", department_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict], int]:
        where, params = self._filters(search, status, department_id)
        with self.db.connection() as c:
            total = c.execute(f"SELECT COUNT(*) FROM phones p {where}", params).fetchone()[0]  # noqa: S608
            rows = c.execute(
                f"{_PHONE_SELECT} {where} ORDER BY p.device_name LIMIT ? OFFSET ?",  # noqa: S608
                (*params, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def all(self) -> list[dict]:
        with self.db.connection() as c:
            return [dict(r) for r in c.execute(f"{_PHONE_SELECT} ORDER BY p.device_name")]  # noqa: S608

    @staticmethod
    def _filters(search: str, status: str, department_id: int | None) -> tuple[str, tuple]:
        clauses, params = [], []
        if search:
            like = f"%{search}%"
            clauses.append("""(p.device_name LIKE ? OR p.extension LIKE ? OR p.ip_address LIKE ?
                OR p.mac_address LIKE ? OR p.user_id IN (SELECT id FROM users WHERE name LIKE ?)
                OR p.location_id IN (SELECT id FROM locations WHERE name LIKE ?))""")
            params += [like] * 6
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        if department_id:
            clauses.append("p.department_id = ?")
            params.append(department_id)
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), tuple(params)

    def get(self, phone_id: int) -> dict | None:
        with self.db.connection() as c:
            row = c.execute(f"{_PHONE_SELECT} WHERE p.id = ?", (phone_id,)).fetchone()  # noqa: S608
        return dict(row) if row else None

    def count(self) -> int:
        with self.db.connection() as c:
            return c.execute("SELECT COUNT(*) FROM phones").fetchone()[0]

    def add(self, **fields: Any) -> int:
        return self._insert("phones", _clean(fields, PHONE_FIELDS))

    def update(self, phone_id: int, **fields: Any) -> None:
        self._update("phones", phone_id, _clean(fields, PHONE_FIELDS))

    def delete(self, phone_id: int) -> None:
        with self.db.connection() as c:
            c.execute("DELETE FROM phones WHERE id = ?", (phone_id,))


# --------------------------------------------------------------------------
# Volume policies
# --------------------------------------------------------------------------
class PolicyRepository(_Repo):
    def set(self, phone_id: int, level: int, locked: bool, name: str = "") -> None:
        self.set_many([phone_id], level, locked, name)

    def set_many(self, phone_ids: list[int], level: int, locked: bool, name: str = "") -> None:
        with self.db.connection() as c:
            c.executemany(
                "INSERT INTO volume_policies (phone_id, volume_level, is_locked, policy_name) VALUES (?,?,?,?)",
                [(pid, level, 1 if locked else 0, name) for pid in phone_ids],
            )

    def current(self, phone_id: int) -> dict | None:
        with self.db.connection() as c:
            row = c.execute(
                "SELECT * FROM volume_policies WHERE phone_id = ? ORDER BY id DESC LIMIT 1", (phone_id,)
            ).fetchone()
        return dict(row) if row else None

    def all_current(self, search: str = "") -> list[dict]:
        like = f"%{search}%"
        with self.db.connection() as c:
            rows = c.execute(
                """SELECT p.id, p.device_name, p.extension, p.status,
                          u.name AS assigned_user, l.name AS location, d.name AS department,
                          vp.volume_level, vp.is_locked, vp.policy_name, vp.applied_at
                   FROM phones p
                   LEFT JOIN users u ON u.id = p.user_id
                   LEFT JOIN locations l ON l.id = p.location_id
                   LEFT JOIN departments d ON d.id = p.department_id
                   LEFT JOIN (SELECT * FROM volume_policies WHERE id IN
                              (SELECT MAX(id) FROM volume_policies GROUP BY phone_id)) vp
                          ON vp.phone_id = p.id
                   WHERE (? = '' OR p.device_name LIKE ? OR l.name LIKE ? OR u.name LIKE ?)
                   ORDER BY p.device_name""",
                (search, like, like, like),
            ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Docks
# --------------------------------------------------------------------------
class DockRepository(_Repo):
    def list(self, search: str = "", status: str = "") -> list[dict]:
        clauses, params = [], []
        if search:
            like = f"%{search}%"
            clauses.append("""(k.dock_name LIKE ? OR k.model LIKE ? OR k.serial_number LIKE ?
                OR k.host_computer LIKE ? OR u.name LIKE ? OR l.name LIKE ?)""")
            params += [like] * 6
        if status:
            clauses.append("k.status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.db.connection() as c:
            rows = c.execute(f"{_DOCK_SELECT} {where} ORDER BY k.dock_name", params).fetchall()  # noqa: S608
        return [dict(r) for r in rows]

    def get(self, dock_id: int) -> dict | None:
        with self.db.connection() as c:
            row = c.execute(f"{_DOCK_SELECT} WHERE k.id = ?", (dock_id,)).fetchone()  # noqa: S608
        return dict(row) if row else None

    def count(self) -> int:
        with self.db.connection() as c:
            return c.execute("SELECT COUNT(*) FROM docks").fetchone()[0]

    def add(self, **fields: Any) -> int:
        return self._insert("docks", _clean(fields, DOCK_FIELDS))

    def update(self, dock_id: int, **fields: Any) -> None:
        self._update("docks", dock_id, _clean(fields, DOCK_FIELDS))

    def delete(self, dock_id: int) -> None:
        with self.db.connection() as c:
            c.execute("DELETE FROM docks WHERE id = ?", (dock_id,))


# --------------------------------------------------------------------------
# Audit trail + firmware history
# --------------------------------------------------------------------------
class ActivityRepository(_Repo):
    def log(
        self,
        action_type: str,
        device_type: str = "",
        device_id: int | None = None,
        device_name: str = "",
        description: str = "",
        status: str = "success",
        performed_by: str = "admin",
    ) -> None:
        self._insert(
            "activity_log",
            {
                "action_type": action_type,
                "device_type": device_type,
                "device_id": device_id,
                "device_name": device_name,
                "description": description,
                "status": status,
                "performed_by": performed_by,
                "timestamp": _now(),
            },
        )

    def recent(self, limit: int = 100, device_type: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM activity_log", []
        if device_type:
            sql += " WHERE device_type = ?"
            params.append(device_type)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.db.connection() as c:
            return [dict(r) for r in c.execute(sql, params)]


class FirmwareRepository(_Repo):
    def start(self, device_type: str, device_id: int, device_name: str, old: str, new: str) -> int:
        return self._insert(
            "firmware_history",
            {
                "device_type": device_type,
                "device_id": device_id,
                "device_name": device_name,
                "old_version": old,
                "new_version": new,
                "started_at": _now(),
            },
        )

    def finish(self, record_id: int, status: str, error: str = "", new_version: str | None = None) -> None:
        with self.db.connection() as c:
            c.execute(
                """UPDATE firmware_history SET status = ?, completed_at = ?, error_message = ?,
                   new_version = COALESCE(?, new_version) WHERE id = ?""",
                (status, _now(), error, new_version, record_id),
            )

    def recent(self, limit: int = 50) -> list[dict]:
        with self.db.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM firmware_history ORDER BY id DESC LIMIT ?", (limit,))]


# --------------------------------------------------------------------------
# Contact lists (emergency broadcast + page notifications)
# --------------------------------------------------------------------------
class ContactRepository(_Repo):
    """Parametrised by table so the two contact lists share one implementation."""

    def __init__(self, db: Database, table: str):
        super().__init__(db)
        if table not in {"alert_contacts", "page_notify_contacts"}:
            raise ValueError(table)
        self.table = table

    def list(self, active_only: bool = False) -> list[dict]:
        sql = f"SELECT * FROM {self.table}" + (" WHERE is_active = 1" if active_only else "") + " ORDER BY name"  # noqa: S608
        with self.db.connection() as c:
            return [dict(r) for r in c.execute(sql)]

    def get(self, contact_id: int) -> dict | None:
        with self.db.connection() as c:
            row = c.execute(f"SELECT * FROM {self.table} WHERE id = ?", (contact_id,)).fetchone()  # noqa: S608
        return dict(row) if row else None

    def add(self, name: str, phone: str, role: str | None = None) -> int:
        values: dict[str, Any] = {"name": name, "phone": phone}
        if role is not None and self.table == "alert_contacts":
            values["role"] = role
        return self._insert(self.table, values)

    def set_active(self, contact_id: int, active: bool) -> None:
        with self.db.connection() as c:
            c.execute(
                f"UPDATE {self.table} SET is_active = ? WHERE id = ?",  # noqa: S608
                (1 if active else 0, contact_id),
            )

    def delete(self, contact_id: int) -> None:
        with self.db.connection() as c:
            c.execute(f"DELETE FROM {self.table} WHERE id = ?", (contact_id,))  # noqa: S608


class AlertHistoryRepository(_Repo):
    def add(self, message: str, total: int, sent: int, failed: int, status: str, details: str = "") -> None:
        self._insert(
            "alert_history",
            {
                "message": message,
                "total_contacts": total,
                "sent_count": sent,
                "failed_count": failed,
                "status": status,
                "details": details,
                "timestamp": _now(),
            },
        )

    def recent(self, limit: int = 25) -> list[dict]:
        with self.db.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM alert_history ORDER BY id DESC LIMIT ?", (limit,))]


class PageEventRepository(_Repo):
    FIELDS = {
        "source_phone_id",
        "source_name",
        "source_extension",
        "source_user",
        "source_location",
        "target_phone_id",
        "target_name",
        "target_extension",
        "call_type",
        "sms_sent",
        "sms_detail",
    }

    def add(self, **fields: Any) -> int:
        fields["sms_sent"] = 1 if fields.get("sms_sent") else 0
        return self._insert("page_events", {**_clean(fields, self.FIELDS), "timestamp": _now()})

    def recent(self, limit: int = 50) -> list[dict]:
        with self.db.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM page_events ORDER BY id DESC LIMIT ?", (limit,))]

    def count(self) -> int:
        with self.db.connection() as c:
            return c.execute("SELECT COUNT(*) FROM page_events").fetchone()[0]


# --------------------------------------------------------------------------
# Dashboard statistics
# --------------------------------------------------------------------------
class StatsRepository(_Repo):
    def summary(self) -> dict[str, Any]:
        with self.db.connection() as c:
            one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
            stats = {
                "total_phones": one("SELECT COUNT(*) FROM phones"),
                "online_phones": one("SELECT COUNT(*) FROM phones WHERE status = 'online'"),
                "offline_phones": one("SELECT COUNT(*) FROM phones WHERE status = 'offline'"),
                "total_docks": one("SELECT COUNT(*) FROM docks"),
                "connected_docks": one("SELECT COUNT(*) FROM docks WHERE status = 'connected'"),
                "locked_volumes": one(
                    """SELECT COUNT(*) FROM volume_policies WHERE is_locked = 1 AND id IN
                       (SELECT MAX(id) FROM volume_policies GROUP BY phone_id)"""
                ),
                "actions_24h": one("SELECT COUNT(*) FROM activity_log WHERE timestamp > datetime('now','-24 hours')"),
                "failed_24h": one(
                    "SELECT COUNT(*) FROM activity_log WHERE status = 'failed' "
                    "AND timestamp > datetime('now','-24 hours')"
                ),
                "users": one("SELECT COUNT(*) FROM users"),
                "page_events": one("SELECT COUNT(*) FROM page_events"),
                "alert_contacts": one("SELECT COUNT(*) FROM alert_contacts WHERE is_active = 1"),
            }
            stats["by_department"] = [
                dict(r)
                for r in c.execute(
                    """SELECT d.name AS department,
                          (SELECT COUNT(*) FROM phones WHERE department_id = d.id) AS phones,
                          (SELECT COUNT(*) FROM docks WHERE department_id = d.id) AS docks
                   FROM departments d ORDER BY phones DESC"""
                )
            ]
            stats["firmware_mix"] = [
                dict(r)
                for r in c.execute(
                    "SELECT firmware_version AS version, COUNT(*) AS count FROM phones "
                    "GROUP BY firmware_version ORDER BY count DESC"
                )
            ]
        return stats
