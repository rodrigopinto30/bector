"""The bundled broker only lets known nodes in, and only on the team topic (7.5)."""

import threading
import time
import uuid

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from healer.adapters.mqtt_publisher import MqttPublisher
from healer.domain.errors import SharingError
from tests.integration.mqtt_broker import HOST, PASSWORD, PORT, SKIP_REASON, USERNAME, topic_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON),
]


def publisher(topic: str, username: str | None, password: str | None) -> MqttPublisher:
    return MqttPublisher(
        host=HOST,
        port=PORT,
        topic=topic,
        client_id="healer-test",
        username=username,
        password=password,
        timeout_seconds=5,
        max_retries=0,
    )


@pytest.mark.parametrize(
    ("username", "password"),
    [(None, None), (USERNAME, f"{PASSWORD}-wrong"), (f"unknown-{uuid.uuid4().hex[:8]}", "x")],
    ids=["anonymous", "wrong password", "unknown node"],
)
def test_the_broker_refuses_who_is_not_a_node(username: str | None, password: str | None) -> None:
    with pytest.raises(SharingError, match="the broker refused the connection: Not authorized"):
        publisher(topic_for("refused"), username, password).check()


def test_a_known_node_is_accepted() -> None:
    publisher(topic_for("accepted"), USERNAME, PASSWORD).check()


def test_a_node_only_sends_and_receives_on_the_team_topic() -> None:
    run = uuid.uuid4().hex
    inside, outside = topic_for(run), f"healer-test-outside/{run}"
    received: list[str] = []
    subscribed, arrived = threading.Event(), threading.Event()
    spy = mqtt.Client(CallbackAPIVersion.VERSION2)
    spy.username_pw_set(USERNAME, PASSWORD)
    spy.on_connect = lambda c, *_: c.subscribe([("#", 1), ("$SYS/#", 1)])
    spy.on_subscribe = lambda *_: subscribed.set()

    def on_message(_c: mqtt.Client, _u: object, message: mqtt.MQTTMessage) -> None:
        if run in message.topic:
            received.append(message.topic)
            if message.topic == inside:
                arrived.set()

    spy.on_message = on_message
    spy.connect(HOST, PORT)
    spy.loop_start()
    try:
        assert subscribed.wait(5)
        publisher(outside, USERNAME, PASSWORD).publish(b"must be dropped")
        publisher(inside, USERNAME, PASSWORD).publish(b"allowed")
        assert arrived.wait(5)
        time.sleep(0.5)
    finally:
        spy.disconnect()
        spy.loop_stop()
    assert received == [inside]
