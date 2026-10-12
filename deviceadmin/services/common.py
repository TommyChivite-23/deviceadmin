"""Shared service plumbing: result types and retry/backoff."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ..connectors.base import ConnectorError, RateLimitError

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class OperationResult:
    """Outcome of one operation. Services return this instead of raising."""

    ok: bool
    message: str
    data: Any = None

    @classmethod
    def success(cls, message: str, data: Any = None) -> "OperationResult":
        return cls(True, message, data)

    @classmethod
    def failure(cls, message: str, data: Any = None) -> "OperationResult":
        return cls(False, message, data)


@dataclass
class BulkResult:
    """Outcome of an operation applied to many devices."""

    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (device, reason)
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self, verb: str) -> str:
        text = f"{verb}: {len(self.succeeded)} succeeded, {len(self.failed)} failed"
        if self.skipped:
            text += f", {len(self.skipped)} skipped"
        return text


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 2.0
    sleep: Callable[[float], None] = time.sleep


def call_with_retry(fn: Callable[..., T], *args: Any, policy: RetryPolicy | None = None, **kwargs: Any) -> T:
    """Call ``fn`` and retry *transient* connector failures with backoff.

    - Only errors with ``retryable = True`` (timeouts, outages, rate limits)
      are retried. Bad requests and auth failures are raised immediately,
      because repeating them cannot succeed.
    - Delay doubles each attempt (exponential backoff) up to ``max_delay``;
      a rate-limit error's ``retry_after`` hint wins when present.
    """
    policy = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(*args, **kwargs)
        except ConnectorError as exc:
            if not exc.retryable or attempt >= policy.attempts:
                raise
            delay = min(policy.base_delay * 2 ** (attempt - 1), policy.max_delay)
            if isinstance(exc, RateLimitError):
                delay = min(max(delay, exc.retry_after), policy.max_delay)
            logger.warning("Transient failure (%s); retry %d/%d in %.2fs", exc, attempt, policy.attempts - 1, delay)
            policy.sleep(delay)
