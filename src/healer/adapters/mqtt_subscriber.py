"""``SolutionSubscriber`` over MQTT (paho): a durable subscription that keeps itself alive."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from healer.domain.errors import SharingError

logger = logging.getLogger(__name__)


class MqttSubscriber:
    """Receives with QoS 1 under a fixed client id and without cleaning the session.

    That makes the subscription durable: once this node has subscribed, the broker keeps
    what is published while it is off and delivers it on the next connection. Lost
    connections are retried with growing delays (7.6); a broker that refuses this node
    ends ``run``, because retrying would not help.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        topic: str,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
        ca_cert: Path | None = None,
        keepalive_seconds: int = 60,
        min_delay_seconds: int = 1,
        max_delay_seconds: int = 60,
    ) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._client_id = client_id
        self._username = username
        self._password = password
        self._tls = tls
        self._ca_cert = ca_cert
        self._keepalive = keepalive_seconds
        self._min_delay = min_delay_seconds
        self._max_delay = max_delay_seconds
        self._stopped = threading.Event()
        self._refused: str | None = None
        self._last_status: str | None = None

    @property
    def topic(self) -> str:
        return self._topic

    def run(self, on_message: Callable[[bytes], None], on_status: Callable[[str], None]) -> None:
        def report(status: str) -> None:
            if status != self._last_status:
                self._last_status = status
                on_status(status)

        client = self._client()

        def connected(_c: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any) -> None:
            if reason.is_failure:
                self._refused = str(reason)
                self._stopped.set()
                return
            client.subscribe(self._topic, qos=1)

        def subscribed(_c: mqtt.Client, _u: Any, _m: int, reasons: Any, _p: Any) -> None:
            if any(reason.is_failure for reason in reasons):
                self._refused = f"not allowed to subscribe to {self._topic}"
                self._stopped.set()
                return
            report(f"connected to {self._host}:{self._port}")

        def lost(_c: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any) -> None:
            if not self._stopped.is_set():
                report("connection lost; retrying")

        def failed(_c: mqtt.Client, _u: Any) -> None:
            report(f"cannot reach {self._host}:{self._port}; retrying")

        def received(_c: mqtt.Client, _u: Any, message: mqtt.MQTTMessage) -> None:
            try:
                on_message(message.payload)
            except Exception:
                logger.exception("A received message could not be handled")

        client.on_connect = connected
        client.on_subscribe = subscribed
        client.on_disconnect = lost
        client.on_connect_fail = failed
        client.on_message = received
        client.connect_async(self._host, self._port, keepalive=self._keepalive)
        client.loop_start()
        try:
            self._stopped.wait()
        finally:
            self._stopped.set()
            client.disconnect()
            client.loop_stop()
        if self._refused is not None:
            raise SharingError(f"the broker refused the connection: {self._refused}")

    def stop(self) -> None:
        self._stopped.set()

    def _client(self) -> mqtt.Client:
        client = mqtt.Client(
            CallbackAPIVersion.VERSION2, client_id=self._client_id, clean_session=False
        )
        client.reconnect_delay_set(self._min_delay, self._max_delay)
        if self._username is not None:
            client.username_pw_set(self._username, self._password)
        if self._tls:
            client.tls_set(ca_certs=str(self._ca_cert) if self._ca_cert else None)
        return client
