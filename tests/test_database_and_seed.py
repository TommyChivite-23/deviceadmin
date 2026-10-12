"""Repositories and the demo seed data."""

import ipaddress

import pytest

from deviceadmin.seed import N_DOCKS, N_PHONES, seed_database


def test_unknown_columns_are_rejected(services, make_phone):
    """Column names are allow-listed so request data can't reach the SQL text."""
    phone_id = make_phone()
    with pytest.raises(ValueError):
        services.phone_repo.update(phone_id, **{"device_name = 'x', status": "y"})


def test_duplicate_mac_is_a_database_error(services, make_phone):
    make_phone(mac_address="02:00:5E:AA:AA:AA")
    with pytest.raises(Exception, match="UNIQUE"):
        make_phone(mac_address="02:00:5E:AA:AA:AA")


def test_deleting_a_phone_cascades_to_its_policies(services, make_phone):
    phone_id = make_phone()
    services.policy_repo.set(phone_id, 5, False)
    services.phone_repo.delete(phone_id)
    assert services.policy_repo.current(phone_id) is None


def test_phone_list_filters_and_paginates(seeded_services):
    repo = seeded_services.phone_repo
    page1, total = repo.list(limit=25, offset=0)
    page2, _ = repo.list(limit=25, offset=25)
    assert total == N_PHONES and len(page1) == 25
    assert not {p["id"] for p in page1} & {p["id"] for p in page2}
    online, online_total = repo.list(status="online")
    assert online_total and all(p["status"] == "online" for p in online)
    hit, hit_total = repo.list(search=page1[0]["device_name"])
    assert hit_total >= 1


# -- seed data -------------------------------------------------------------------------
def test_seed_is_large_enough_to_demonstrate_scale(seeded_services):
    assert seeded_services.phone_repo.count() == N_PHONES >= 150
    assert seeded_services.dock_repo.count() == N_DOCKS
    assert seeded_services.phone_repo.count() + seeded_services.dock_repo.count() >= 200


def test_seed_is_idempotent(seeded_services):
    assert seed_database(seeded_services.db) == {}


def test_seed_is_deterministic(tmp_path):
    from deviceadmin.database import Database

    a, b = Database(str(tmp_path / "a.db")), Database(str(tmp_path / "b.db"))
    seed_database(a)
    seed_database(b)
    with a.connection() as ca, b.connection() as cb:
        q = "SELECT device_name, ip_address, user_id FROM phones ORDER BY id"
        assert [tuple(r) for r in ca.execute(q)] == [tuple(r) for r in cb.execute(q)]


def test_seed_data_is_provably_fictional(seeded_services):
    """Only reserved documentation ranges / domains / numbers may appear."""
    docs_nets = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
    with seeded_services.db.connection() as c:
        for (ip,) in c.execute("SELECT ip_address FROM phones"):
            assert any(ipaddress.ip_address(ip) in net for net in docs_nets), ip
        for (mac,) in c.execute("SELECT mac_address FROM phones"):
            assert mac.startswith("02:00:5E:"), mac
        for (email,) in c.execute("SELECT email FROM users"):
            assert email.endswith("@democorp.example"), email
        for table in ("alert_contacts", "page_notify_contacts"):
            for (phone,) in c.execute(f"SELECT phone FROM {table}"):  # noqa: S608
                assert phone.startswith("+120255501") and len(phone) == 12, phone


def test_every_phone_has_unique_ip_mac_and_extension(seeded_services):
    with seeded_services.db.connection() as c:
        for col in ("ip_address", "mac_address", "extension"):
            n, distinct = c.execute(f"SELECT COUNT(*), COUNT(DISTINCT {col}) FROM phones").fetchone()  # noqa: S608
            assert n == distinct, col


def test_dashboard_stats_reflect_the_seed(seeded_services):
    stats = seeded_services.stats.summary()
    assert stats["total_phones"] == stats["online_phones"] + stats["offline_phones"] == N_PHONES
    assert stats["offline_phones"] > 0  # the demo includes unhealthy devices on purpose
    assert sum(d["phones"] for d in stats["by_department"]) == N_PHONES
