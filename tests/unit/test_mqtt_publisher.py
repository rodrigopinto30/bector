import socket

import pytest

from healer.adapters.mqtt_publisher import MqttPublisher
from healer.domain.errors import SharingError


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def test_an_unreachable_broker_is_retried_with_backoff_and_then_reported() -> None:
    waits: list[float] = []
    publisher = MqttPublisher(
        host="127.0.0.1",
        port=closed_port(),
        topic="healer-test/solutions",
        client_id="healer-test",
        timeout_seconds=2,
        max_retries=2,
        sleep=waits.append,
    )
    with pytest.raises(SharingError, match=r"the broker at 127\.0\.0\.1:\d+ could not be reached"):
        publisher.publish(b"{}")
    assert waits == [1.0, 2.0]
