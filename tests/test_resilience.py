"""Retry/backoff helper: which errors are retried, how long we wait."""

import pytest

from deviceadmin.connectors.base import (
    ConnectorAuthError,
    ConnectorTimeout,
    InvalidRequestError,
    RateLimitError,
)
from deviceadmin.services.common import RetryPolicy, call_with_retry


class Flaky:
    """Fails ``failures`` times with ``error`` and then succeeds."""

    def __init__(self, failures, error):
        self.failures, self.error, self.calls = failures, error, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def policy(sleeps):
    return RetryPolicy(attempts=3, base_delay=0.1, max_delay=1.0, sleep=sleeps.append)


def test_transient_error_is_retried_until_it_succeeds():
    sleeps, flaky = [], Flaky(2, ConnectorTimeout("slow"))
    assert call_with_retry(flaky, policy=policy(sleeps)) == "ok"
    assert flaky.calls == 3


def test_backoff_doubles_each_attempt():
    sleeps = []
    call_with_retry(Flaky(2, ConnectorTimeout("slow")), policy=policy(sleeps))
    assert sleeps == [0.1, 0.2]


def test_gives_up_after_the_configured_attempts():
    flaky = Flaky(99, ConnectorTimeout("down"))
    with pytest.raises(ConnectorTimeout):
        call_with_retry(flaky, policy=policy([]))
    assert flaky.calls == 3


@pytest.mark.parametrize("error", [ConnectorAuthError("401"), InvalidRequestError("400")])
def test_permanent_errors_are_never_retried(error):
    flaky = Flaky(99, error)
    with pytest.raises(type(error)):
        call_with_retry(flaky, policy=policy([]))
    assert flaky.calls == 1


def test_rate_limit_retry_after_hint_is_respected_but_capped():
    sleeps = []
    call_with_retry(Flaky(1, RateLimitError(retry_after=0.7)), policy=policy(sleeps))
    assert sleeps == [0.7]
    sleeps.clear()
    call_with_retry(Flaky(1, RateLimitError(retry_after=30)), policy=policy(sleeps))
    assert sleeps == [1.0]  # capped at max_delay
