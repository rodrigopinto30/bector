import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from healer.adapters.mqtt_publisher import MqttPublisher
from healer.adapters.mqtt_subscriber import MqttSubscriber

HOST = os.environ.get("MQTT_HOST", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "1883"))


def broker_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not broker_reachable(), reason="no MQTT broker reachable"),
]


def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition()


@contextmanager
def listening(topic: str, client_id: str, received: list[bytes]) -> Iterator[None]:
    statuses: list[str] = []
    subscriber = MqttSubscriber(host=HOST, port=PORT, topic=topic, client_id=client_id)
    thread = threading.Thread(target=subscriber.run, args=(received.append, statuses.append))
    thread.start()
    try:
        wait_until(lambda: f"connected to {HOST}:{PORT}" in statuses)
        yield
    finally:
        subscriber.stop()
        thread.join(5)
        assert not thread.is_alive()


def forget_session(client_id: str) -> None:
    """Ask the broker to drop the durable session the test created."""
    connected = threading.Event()
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    client.on_connect = lambda *_: connected.set()
    client.connect(HOST, PORT)
    client.loop_start()
    connected.wait(5)
    client.disconnect()
    client.loop_stop()


def test_what_is_sent_while_the_node_is_off_arrives_when_it_returns() -> None:
    topic, client_id = f"healer-test/{uuid.uuid4().hex}", f"healer-test-{uuid.uuid4().hex}"
    publisher = MqttPublisher(host=HOST, port=PORT, topic=topic, client_id="healer-test")
    online: list[bytes] = []
    later: list[bytes] = []
    try:
        with listening(topic, client_id, online):
            publisher.publish(b"while online")
            wait_until(lambda: online == [b"while online"])
        publisher.publish(b"while off")
        with listening(topic, client_id, later):
            wait_until(lambda: later == [b"while off"])
    finally:
        forget_session(client_id)
