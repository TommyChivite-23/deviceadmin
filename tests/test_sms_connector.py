"""MockSmsConnector: validation, opt-outs and rate limiting."""

import pytest

from deviceadmin.connectors.base import InvalidRequestError, RateLimitError
from deviceadmin.connectors.mock import MockSmsConnector

GOOD = "+12025550123"


def test_successful_send_returns_delivered_result(sms_connector):
    result = sms_connector.send(GOOD, "Hello")
    assert result.delivered and result.message_id.startswith("mock-")
    assert sms_connector.sent[-1]["to"] == GOOD


def test_message_ids_are_unique_and_sequential(sms_connector):
    ids = [sms_connector.send(GOOD, "x").message_id for _ in range(3)]
    assert ids == ["mock-000001", "mock-000002", "mock-000003"]


@pytest.mark.parametrize("number", ["", "2025550123", "+0123456", "+1202", "abc"])
def test_non_e164_numbers_are_rejected(sms_connector, number):
    with pytest.raises(InvalidRequestError):
        sms_connector.send(number, "Hello")


@pytest.mark.parametrize("text", ["", "   ", "x" * 1601])
def test_bad_message_bodies_are_rejected(sms_connector, text):
    with pytest.raises(InvalidRequestError):
        sms_connector.send(GOOD, text)


def test_opted_out_recipient_is_undeliverable_not_an_exception(sms_connector):
    """A real gateway accepts the request but reports the message as undeliverable."""
    result = sms_connector.send("+12025550100", "Hello")
    assert not result.delivered
    assert "opted out" in result.detail


def test_long_messages_are_split_into_segments(sms_connector):
    assert sms_connector.send(GOOD, "a" * 161).segments == 2


def test_rate_limit_kicks_in_with_a_retry_hint():
    gateway = MockSmsConnector(max_per_minute=2)
    gateway.send(GOOD, "1")
    gateway.send(GOOD, "2")
    with pytest.raises(RateLimitError) as caught:
        gateway.send(GOOD, "3")
    assert 0 < caught.value.retry_after <= 60
