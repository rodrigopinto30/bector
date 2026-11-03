import threading
import uuid

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from healer.adapters.mqtt_publisher import MqttPublisher
from tests.integration.mqtt_broker import HOST, PASSWORD, PORT, SKIP_REASON, USERNAME, topic_for


@pytest.mark.integration
@pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON)
def test_a_subscriber_receives_the_published_message() -> None:
    topic = topic_for(uuid.uuid4().hex)
    received: list[bytes] = []
    subscribed, arrived = threading.Event(), threading.Event()
    subscriber = mqtt.Client(CallbackAPIVersion.VERSION2)
    subscriber.username_pw_set(USERNAME, PASSWORD)
    subscriber.on_connect = lambda c, *_: c.subscribe(topic, qos=1)
    subscriber.on_subscribe = lambda *_: subscribed.set()

    def on_message(_c: mqtt.Client, _u: object, message: mqtt.MQTTMessage) -> None:
        received.append(message.payload)
        arrived.set()

    subscriber.on_message = on_message
    subscriber.connect(HOST, PORT)
    subscriber.loop_start()
    try:
        assert subscribed.wait(5)
        MqttPublisher(
            host=HOST,
            port=PORT,
            topic=topic,
            client_id="healer-test",
            username=USERNAME,
            password=PASSWORD,
        ).publish(b'{"solution": 1}')
        assert arrived.wait(5)
    finally:
        subscriber.disconnect()
        subscriber.loop_stop()
    assert received == [b'{"solution": 1}']
