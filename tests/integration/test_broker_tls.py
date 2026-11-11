"""The broker's TLS listener, when ./broker.sh tls turned it on (7.5)."""

import socket
import ssl
import threading
import uuid
import warnings
from pathlib import Path

import pytest

from healer.adapters.mqtt_publisher import MqttPublisher
from healer.adapters.mqtt_subscriber import MqttSubscriber
from healer.domain.errors import SharingError
from tests.integration.mqtt_broker import HOST, PASSWORD, SKIP_REASON, USERNAME, topic_for

TLS_PORT = 8883
TEAM_CA = Path(__file__).resolve().parents[2] / "team-ca.crt"


def tls_reachable() -> bool:
    try:
        with socket.create_connection((HOST, TLS_PORT), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON),
    pytest.mark.skipif(
        not TEAM_CA.exists() or not tls_reachable(),
        reason="TLS is off: run ./broker.sh tls ADDRESS",
    ),
]


def publisher(
    topic: str, *, host: str = HOST, ca_cert: Path | None = TEAM_CA, tls: bool = True
) -> MqttPublisher:
    return MqttPublisher(
        host=host,
        port=TLS_PORT,
        topic=topic,
        client_id="healer-test",
        username=USERNAME,
        password=PASSWORD,
        tls=tls,
        ca_cert=ca_cert,
        timeout_seconds=3,
        max_retries=0,
    )


def test_a_message_travels_over_tls() -> None:
    topic = topic_for(uuid.uuid4().hex)
    received: list[bytes] = []
    statuses: list[str] = []
    subscriber = MqttSubscriber(
        host=HOST,
        port=TLS_PORT,
        topics=(topic,),
        client_id=f"healer-test-{uuid.uuid4().hex}",
        username=USERNAME,
        password=PASSWORD,
        tls=True,
        ca_cert=TEAM_CA,
    )
    arrived = threading.Event()

    def on_message(data: bytes) -> None:
        received.append(data)
        arrived.set()

    thread = threading.Thread(target=subscriber.run, args=(on_message, statuses.append))
    thread.start()
    try:
        connected = threading.Event()
        for _ in range(100):
            if f"connected to {HOST}:{TLS_PORT}" in statuses:
                connected.set()
                break
            arrived.wait(0.05)
        assert connected.is_set()
        publisher(topic).publish(b"over tls")
        assert arrived.wait(5)
    finally:
        subscriber.stop()
        thread.join(5)
    assert received == [b"over tls"]


def test_a_broker_signed_by_another_ca_is_refused() -> None:
    with pytest.raises(SharingError, match="could not be verified"):
        publisher(topic_for("system-cas"), ca_cert=None).check()


def test_a_host_the_certificate_does_not_name_is_refused() -> None:
    address = socket.gethostbyname(HOST)
    with pytest.raises(SharingError, match="could not be verified .*mismatch"):
        publisher(topic_for("mismatch"), host=address).check()


def test_a_client_without_tls_gets_no_answer_on_the_tls_port() -> None:
    with pytest.raises(SharingError, match="no answer from the broker"):
        publisher(topic_for("plain"), tls=False).check()


@pytest.mark.parametrize(
    ("version", "accepted"), [("TLSv1_1", False), ("TLSv1_2", True), ("TLSv1_3", True)]
)
def test_only_tls_1_2_and_later_are_accepted(version: str, accepted: bool) -> None:
    context = ssl.create_default_context(cafile=str(TEAM_CA))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        context.minimum_version = context.maximum_version = getattr(ssl.TLSVersion, version)
    if version == "TLSv1_1":
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    try:
        with (
            socket.create_connection((HOST, TLS_PORT), timeout=3) as raw,
            context.wrap_socket(raw, server_hostname=HOST),
        ):
            handshake = True
    except ssl.SSLError:
        handshake = False
    assert handshake is accepted
