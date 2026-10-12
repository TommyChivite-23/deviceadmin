"""Application settings.

Settings come from environment variables (12-factor style) so the same image
runs everywhere and no secret ever has to live in the repository. See
``.env.example`` for every variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable, typed application configuration."""

    app_name: str = "DemoCorp Device Admin"
    secret_key: str = "dev-only-not-a-secret"
    debug: bool = False
    testing: bool = False
    log_level: str = "INFO"
    database_path: str = str(BASE_DIR / "data" / "deviceadmin.db")

    # Which connector implementation to use for each integration.
    # Only "mock" ships in this public edition; see connectors/factory.py.
    connector_mode: str = "mock"
    mock_latency_ms: int = 0  # artificial delay to make the demo feel real

    page_monitor_enabled: bool = True
    page_monitor_poll_interval: int = 15
    page_monitor_cooldown: int = 60

    seed_demo_data: bool = True
    min_volume: int = 0
    max_volume: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            app_name=env.get("DEVICEADMIN_APP_NAME", cls.app_name),
            secret_key=env.get("DEVICEADMIN_SECRET_KEY", cls.secret_key),
            debug=_bool(env.get("DEVICEADMIN_DEBUG"), False),
            log_level=env.get("DEVICEADMIN_LOG_LEVEL", "INFO").upper(),
            database_path=env.get("DEVICEADMIN_DATABASE_PATH", cls.database_path),
            connector_mode=env.get("DEVICEADMIN_CONNECTOR_MODE", "mock").lower(),
            mock_latency_ms=int(env.get("DEVICEADMIN_MOCK_LATENCY_MS", "0")),
            page_monitor_enabled=_bool(env.get("DEVICEADMIN_PAGE_MONITOR"), True),
            page_monitor_poll_interval=int(env.get("DEVICEADMIN_PAGE_POLL_SECONDS", "15")),
            page_monitor_cooldown=int(env.get("DEVICEADMIN_PAGE_COOLDOWN_SECONDS", "60")),
            seed_demo_data=_bool(env.get("DEVICEADMIN_SEED_DEMO_DATA"), True),
        )

    def public_view(self) -> dict:
        """Settings safe to display in the UI (never includes the secret key)."""
        return {
            "App name": self.app_name,
            "Connector mode": self.connector_mode,
            "Mock latency (ms)": self.mock_latency_ms,
            "Log level": self.log_level,
            "Page monitor enabled": self.page_monitor_enabled,
            "Page monitor poll (s)": self.page_monitor_poll_interval,
            "Page monitor cooldown (s)": self.page_monitor_cooldown,
            "Volume range": f"{self.min_volume}-{self.max_volume}",
            "Database": Path(self.database_path).name,
        }
