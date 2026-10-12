"""Measure how long fleet-wide operations take against the *mock* connectors.

What this does and does not prove
---------------------------------
It measures the application's orchestration (database + service layer +
threading) with a fixed simulated network delay per device call. It does NOT
measure real device latency, and it says nothing about how long the old manual
process took - that baseline is an estimate and is not measured here.

Usage:  python scripts/benchmark_fleet_ops.py [--latency-ms 150] [--phones 160]
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deviceadmin.config import Settings  # noqa: E402
from deviceadmin.connectors import build_connectors  # noqa: E402
from deviceadmin.database import Database  # noqa: E402
from deviceadmin.seed import seed_database  # noqa: E402
from deviceadmin.services.container import build_services  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latency-ms", type=int, default=150, help="simulated per-call network delay")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    logging.disable(logging.WARNING)  # hide expected retry warnings for unreachable mock phones

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(database_path=f"{tmp}/bench.db", mock_latency_ms=args.latency_ms, testing=True)
        db = Database(settings.database_path)
        seed_database(db)
        services = build_services(settings, db, build_connectors(settings))
        services.phones.max_workers = args.workers
        n = services.phone_repo.count()

        start = time.perf_counter()
        sync = services.phones.sync_fleet()
        sync_s = time.perf_counter() - start

        start = time.perf_counter()
        policy = services.phones.apply_volume_to_fleet(6, True, "Benchmark")
        policy_s = time.perf_counter() - start

    serial_estimate = n * args.latency_ms / 1000
    print(f"Phones: {n}   simulated latency per device call: {args.latency_ms} ms   workers: {args.workers}")
    print(
        f"  Fleet sync (parallel)       : {sync_s:6.1f} s  ({len(sync.succeeded)} online, {len(sync.failed)} offline)"
    )
    print(
        f"  Fleet volume policy (parallel): {policy_s:6.1f} s  ({len(policy.succeeded)} applied, {len(policy.failed)} failed)"
    )
    print(f"  Same sync done one-by-one   : ~{serial_estimate:5.1f} s (n x latency, for comparison)")
    print("Note: mock timings only. Real device latency and the manual baseline are not measured here.")


if __name__ == "__main__":
    main()
