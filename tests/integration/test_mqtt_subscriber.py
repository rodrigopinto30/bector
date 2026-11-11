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
from tests.integration.mqtt_broker import HOST, PASSWORD, PORT, SKIP_REASON, USERNAME, topic_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON),
]


def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition()


@contextmanager
def listening(topic: str, client_id: str, received: list[bytes]) -> Iterator[None]:
    statuses: list[str] = []
    subscriber = MqttSubscriber(
        host=HOST,
        port=PORT,
        topics=(topic, f"{topic}/results"),
        client_id=client_id,
        username=USERNAME,
        password=PASSWORD,
    )
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
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = lambda *_: connected.set()
    client.connect(HOST, PORT)
    client.loop_start()
    connected.wait(5)
    client.disconnect()
    client.loop_stop()


def test_what_is_sent_while_the_node_is_off_arrives_when_it_returns() -> None:
    topic, client_id = topic_for(uuid.uuid4().hex), f"healer-test-{uuid.uuid4().hex}"
    publisher = MqttPublisher(
        host=HOST,
        port=PORT,
        topic=topic,
        client_id="healer-test",
        username=USERNAME,
        password=PASSWORD,
    )
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


def test_results_arrive_through_their_own_topic() -> None:
    topic, client_id = topic_for(uuid.uuid4().hex), f"healer-test-{uuid.uuid4().hex}"
    publisher = MqttPublisher(
        host=HOST,
        port=PORT,
        topic=f"{topic}/results",
        client_id="healer-test",
        username=USERNAME,
        password=PASSWORD,
    )
    received: list[bytes] = []
    try:
        with listening(topic, client_id, received):
            publisher.publish(b"a result")
            wait_until(lambda: received == [b"a result"])
    finally:
        forget_session(client_id)
