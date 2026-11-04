import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from healer.adapters.mqtt_publisher import MqttPublisher
from healer.adapters.mqtt_subscriber import MqttSubscriber
from healer.adapters.mqtt_tls import tls_failure, use_tls
from healer.domain.errors import SharingError


@contextmanager
def not_a_tls_server() -> Iterator[int]:
    """A port that answers a TLS handshake with plain text, like a broker without TLS."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port: int = server.getsockname()[1]

    def answer() -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with connection:
                connection.sendall(b"this is not TLS\r\n" * 8)

    threading.Thread(target=answer, daemon=True).start()
    try:
        yield port
    finally:
        server.close()


def test_a_missing_ca_file_is_explained(tmp_path: Path) -> None:
    client = mqtt.Client(CallbackAPIVersion.VERSION2)
    with pytest.raises(
        SharingError, match=r"MQTT_CA_CERT .*missing\.crt cannot be used: No such file"
    ):
        use_tls(client, tmp_path / "missing.crt")


def test_a_certificate_that_cannot_be_verified_points_to_the_ca_and_the_host() -> None:
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    error.verify_message = "IP address mismatch"
    message = str(tls_failure("10.0.0.5", 8883, error))
    assert "the broker at 10.0.0.5:8883 could not be verified (IP address mismatch)" in message
    assert "MQTT_CA_CERT is the team's CA" in message
    assert "MQTT_HOST is one of the names" in message


def test_another_handshake_failure_points_to_the_port() -> None:
    message = str(tls_failure("broker", 1883, ssl.SSLEOFError(8, "EOF occurred")))
    assert "the TLS connection to broker:1883 failed" in message
    assert "check that MQTT_PORT is the broker's TLS port" in message


def test_the_publisher_does_not_retry_a_failed_handshake() -> None:
    waits: list[float] = []
    with not_a_tls_server() as port:
        publisher = MqttPublisher(
            host="127.0.0.1",
            port=port,
            topic="healer-test/solutions",
            client_id="healer-test",
            tls=True,
            timeout_seconds=2,
            max_retries=2,
            sleep=waits.append,
        )
        with pytest.raises(SharingError, match="the TLS connection to 127.0.0.1"):
            publisher.publish(b"{}")
    assert waits == []


def test_the_listener_stops_when_the_first_handshake_fails() -> None:
    statuses: list[str] = []
    with not_a_tls_server() as port:
        subscriber = MqttSubscriber(
            host="127.0.0.1",
            port=port,
            topic="healer-test/solutions",
            client_id="healer-test",
            tls=True,
        )
        with pytest.raises(SharingError, match="the TLS connection to 127.0.0.1"):
            subscriber.run(lambda _: None, statuses.append)
    assert statuses == []
