"""Demo data: a fully fictional company with ~200 managed assets.

Everything here is invented. Addresses come from the reserved documentation
ranges (RFC 5737), MACs use the locally-administered prefix 02:00:5E, phone
numbers use the fictional 555-01xx block, and emails use the reserved
``example`` TLD, so nothing can ever point at a real person or network.

Generation is deterministic (seeded RNG): the same data every run.

Usage:  python -m deviceadmin.seed [--reset]
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import UTC, datetime, timedelta

from .config import Settings
from .connectors.mock import DOCK_MODELS, host_is_reachable, phone_is_reachable
from .database import Database

DEPARTMENTS = [
    "Finance",
    "Human Resources",
    "Engineering",
    "Sales",
    "Customer Support",
    "Operations",
    "Legal",
    "Marketing",
    "IT Services",
    "Facilities",
]
DEPT_CODE = {
    "Finance": "FIN",
    "Human Resources": "HR",
    "Engineering": "ENG",
    "Sales": "SAL",
    "Customer Support": "CS",
    "Operations": "OPS",
    "Legal": "LEG",
    "Marketing": "MKT",
    "IT Services": "IT",
    "Facilities": "FAC",
}
SITES = {
    "Riverside Campus": ("RVS", ["Lobby", "Floor 2 - Open Plan", "Floor 3 - Open Plan", "Conference Wing"]),
    "Lakeview Office": ("LKV", ["Reception", "Floor 1 - Bullpen", "Floor 2 - Bullpen"]),
    "Harbor Branch": ("HRB", ["Front Desk", "Back Office", "Warehouse Annex"]),
    "Summit Center": ("SMT", ["Reception", "Training Room", "Floor 4 - Executive", "Support Floor"]),
}
FIRST = [
    "Avery",
    "Jordan",
    "Riley",
    "Morgan",
    "Casey",
    "Quinn",
    "Rowan",
    "Skyler",
    "Dakota",
    "Emerson",
    "Harper",
    "Finley",
    "Sage",
    "Reese",
    "Blake",
    "Cameron",
    "Devon",
    "Elliot",
    "Hayden",
    "Jamie",
    "Kendall",
    "Logan",
    "Marlow",
    "Noel",
    "Parker",
    "Remy",
    "Sasha",
    "Tatum",
    "Wren",
    "Zion",
]
LAST = [
    "Ashford",
    "Bellamy",
    "Calloway",
    "Dunmore",
    "Eastwood",
    "Fairbanks",
    "Garrity",
    "Hollis",
    "Ingram",
    "Jessup",
    "Kingsley",
    "Langford",
    "Montrose",
    "Northcott",
    "Oakley",
    "Pemberton",
    "Quillen",
    "Radcliffe",
    "Sterling",
    "Thornbury",
    "Underhill",
    "Vance",
    "Whitlock",
    "Yardley",
]
TITLES = ["Analyst", "Coordinator", "Manager", "Specialist", "Associate", "Director", "Lead", "Representative"]
IP_BLOCKS = ["198.51.100", "203.0.113", "192.0.2"]  # RFC 5737 documentation ranges

N_PHONES, N_DOCKS, N_USERS = 160, 45, 120


def _ts(base: datetime, **delta) -> str:
    return (base - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def seed_database(db: Database, rng_seed: int = 42) -> dict[str, int]:
    """Populate an empty database. Returns row counts. No-op if phones already exist."""
    with db.connection() as c:
        if c.execute("SELECT COUNT(*) FROM phones").fetchone()[0]:
            return {}
        rng = random.Random(rng_seed)
        now = datetime.now(UTC)

        # -- reference data --------------------------------------------------
        for name in DEPARTMENTS:
            c.execute("INSERT INTO departments (name) VALUES (?)", (name,))
        dept_ids = {r["name"]: r["id"] for r in c.execute("SELECT * FROM departments")}
        locations: list[tuple[int, str]] = []  # (id, site code)
        for site, (code, rooms) in SITES.items():
            for room in rooms:
                cur = c.execute("INSERT INTO locations (name, site) VALUES (?, ?)", (f"{code} {room}", site))
                locations.append((cur.lastrowid, code))

        # -- users -------------------------------------------------------------
        used, user_rows = set(), []
        while len(user_rows) < N_USERS:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            if (first, last) in used:
                continue
            used.add((first, last))
            dept = rng.choice(DEPARTMENTS)
            cur = c.execute(
                "INSERT INTO users (name, email, title, department_id) VALUES (?,?,?,?)",
                (
                    f"{first} {last}",
                    f"{first}.{last}@democorp.example".lower(),
                    f"{dept} {rng.choice(TITLES)}",
                    dept_ids[dept],
                ),
            )
            user_rows.append((cur.lastrowid, dept))

        # -- phones --------------------------------------------------------------
        firmware_pool = ["6.4.3.1234"] * 14 + ["6.4.2.1100"] * 4 + ["6.4.1.0999"] * 2
        phone_users = rng.sample(user_rows, N_PHONES) if N_PHONES <= N_USERS else user_rows
        phone_ids = []
        phone_names: dict[int, tuple[str, str]] = {}  # id -> (device name, extension)
        dock_names: dict[int, str] = {}
        for i in range(N_PHONES):
            loc_id, site_code = rng.choice(locations)
            user_id, dept = phone_users[i % len(phone_users)]
            ip = f"{IP_BLOCKS[i // 250 % 3]}.{i % 250 + 2}"
            online = phone_is_reachable(ip)
            cur = c.execute(
                """INSERT INTO phones (device_name, mac_address, ip_address, extension, user_id, location_id,
                   department_id, firmware_version, provisioning_status, status, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"PH{i + 1:03d}-{site_code}-{DEPT_CODE[dept]}",
                    f"02:00:5E:{i // 65025:02X}:{i // 255 % 255:02X}:{i % 255 + 1:02X}",
                    ip,
                    str(1000 + i),
                    user_id,
                    loc_id,
                    dept_ids[dept],
                    rng.choice(firmware_pool),
                    "provisioned" if rng.random() > 0.06 else "pending",
                    "online" if online else "offline",
                    _ts(now, minutes=rng.randint(1, 90)) if online else _ts(now, days=rng.randint(2, 20)),
                ),
            )
            phone_ids.append((cur.lastrowid, ip, dept))
            phone_names[cur.lastrowid] = (f"PH{i + 1:03d}-{site_code}-{DEPT_CODE[dept]}", str(1000 + i))

        # volume policies: lock front-desk style phones, a default on a few others
        for pid, _ip, _dept in phone_ids:
            roll = rng.random()
            if roll < 0.18:
                c.execute(
                    "INSERT INTO volume_policies (phone_id, volume_level, is_locked, policy_name, applied_at)"
                    " VALUES (?,?,?,?,?)",
                    (pid, 7, 1, "Reception Locked", _ts(now, days=rng.randint(1, 40))),
                )
            elif roll < 0.30:
                c.execute(
                    "INSERT INTO volume_policies (phone_id, volume_level, is_locked, policy_name, applied_at)"
                    " VALUES (?,?,?,?,?)",
                    (pid, 5, 0, "Office Default", _ts(now, days=rng.randint(1, 40))),
                )

        # -- docks -----------------------------------------------------------------
        models = list(DOCK_MODELS)
        for i in range(N_DOCKS):
            model = rng.choice(models)
            latest = DOCK_MODELS[model]
            parts = latest.split(".")
            version = (
                latest
                if rng.random() < 0.6
                else ".".join(parts[:2] + [str(max(int(parts[2]) - rng.randint(2, 6), 0)), parts[3]])
            )
            user_id, dept = rng.choice(user_rows)
            loc_id, _ = rng.choice(locations)
            host = f"WS-DEMO-{i + 1:04d}"
            reachable = host_is_reachable(host)
            cur = c.execute(
                """INSERT INTO docks (dock_name, model, serial_number, firmware_version, host_computer, user_id,
                   location_id, department_id, status, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"DOCK-{i + 1:03d}",
                    model,
                    f"ODK-{rng.randint(10_000_000, 99_999_999)}",
                    version,
                    host,
                    user_id,
                    loc_id,
                    dept_ids[dept],
                    "connected" if reachable else "disconnected",
                    _ts(now, minutes=rng.randint(1, 600)) if reachable else _ts(now, days=rng.randint(3, 30)),
                ),
            )
            dock_names[cur.lastrowid] = f"DOCK-{i + 1:03d}"

        # -- contacts ------------------------------------------------------------------
        alert = [
            ("Alex Rivera", "+12025550101", "Security Lead"),
            ("Sam Okafor", "+12025550102", "Operations Manager"),
            ("Jules Meyer", "+12025550103", "IT On-Call"),
            ("Priya Nandan", "+12025550104", "Facilities"),
            ("Chris Lindqvist", "+12025550105", "HR Director"),
            ("Taylor Brandt", "+12025550106", "Site Manager"),
            ("Robin Castellan", "+12025550100", "Executive Assistant (opted out of SMS)"),
        ]
        for name, phone, role in alert:
            c.execute("INSERT INTO alert_contacts (name, phone, role) VALUES (?,?,?)", (name, phone, role))
        for name, phone in [
            ("Front Desk Supervisor", "+12025550111"),
            ("Security Desk", "+12025550112"),
            ("Building Manager", "+12025550113"),
        ]:
            c.execute("INSERT INTO page_notify_contacts (name, phone) VALUES (?,?)", (name, phone))

        # -- history (so dashboards and logs are not empty on first run) ---------------------
        actions = [
            ("reboot", "phone", "Reboot accepted"),
            ("set_volume", "phone", "Volume set to 7 (LOCKED)"),
            ("firmware_update", "dock", "Firmware updated"),
            ("pull_config", "phone", "Configuration pulled"),
            ("fleet_sync", "phone", "Synced fleet"),
            ("refresh", "dock", "Firmware reported"),
        ]
        for _ in range(60):
            action, dtype, desc = rng.choice(actions)
            ok = rng.random() > 0.12
            if dtype == "phone":
                pid = rng.choice(phone_ids)[0]
                name = phone_names[pid][0]
            else:
                pid = rng.choice(list(dock_names))
                name = dock_names[pid]
            c.execute(
                "INSERT INTO activity_log (timestamp, action_type, device_type, device_id, device_name,"
                " description, status) VALUES (?,?,?,?,?,?,?)",
                (
                    _ts(now, hours=rng.randint(0, 70), minutes=rng.randint(0, 59)),
                    action,
                    dtype,
                    pid,
                    name,
                    desc if ok else f"{desc.split()[0]} failed: no response (timeout)",
                    "success" if ok else "failed",
                ),
            )
        for i in range(8):
            old, new = ("6.4.2.1100", "6.4.3.1234") if i % 2 else ("1.0.72.0", "1.0.78.0")
            ok = i != 5
            is_phone = bool(i % 2)
            dev_id = rng.choice(phone_ids)[0] if is_phone else rng.choice(list(dock_names))
            c.execute(
                "INSERT INTO firmware_history (device_type, device_id, device_name, old_version, new_version,"
                " status, started_at, completed_at, error_message) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "phone" if is_phone else "dock",
                    dev_id,
                    phone_names[dev_id][0] if is_phone else dock_names[dev_id],
                    old,
                    new,
                    "success" if ok else "failed",
                    _ts(now, days=i + 1),
                    _ts(now, days=i + 1, minutes=-3),
                    "" if ok else "Remote session timed out",
                ),
            )
        for _ in range(10):
            src, dst = rng.sample(phone_ids, 2)
            c.execute(
                "INSERT INTO page_events (timestamp, source_name, source_extension, target_name, target_extension,"
                " call_type, sms_sent, sms_detail) VALUES (?,?,?,?,?,?,?,?)",
                (
                    _ts(now, hours=rng.randint(1, 60)),
                    phone_names[src[0]][0],
                    phone_names[src[0]][1],
                    phone_names[dst[0]][0],
                    phone_names[dst[0]][1],
                    "Paging",
                    1,
                    "3/3 sent.",
                ),
            )
        for i, (status, sent, failed) in enumerate([("success", 7, 0), ("partial", 6, 1), ("success", 7, 0)]):
            c.execute(
                "INSERT INTO alert_history (timestamp, message, total_contacts, sent_count, failed_count, status,"
                " details) VALUES (?,?,?,?,?,?,?)",
                (
                    _ts(now, days=7 * (i + 1)),
                    "TEST: quarterly alert-list verification",
                    7,
                    sent,
                    failed,
                    status,
                    "Seeded demo history",
                ),
            )

        return {
            t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in (
                "departments",
                "locations",
                "users",
                "phones",
                "docks",
                "volume_policies",
                "alert_contacts",
                "activity_log",
            )
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the DeviceAdmin demo database")
    parser.add_argument("--reset", action="store_true", help="delete the existing database first")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.reset and os.path.exists(settings.database_path):
        os.remove(settings.database_path)
    counts = seed_database(Database(settings.database_path))
    print(counts or "Database already contains data; use --reset to rebuild.")


if __name__ == "__main__":
    main()
