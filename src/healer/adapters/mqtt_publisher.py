"""``SolutionPublisher`` over MQTT (paho), with timeouts and bounded retries (7.6)."""

import logging
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from healer.domain.errors import SharingError

logger = logging.getLogger(__name__)


class MqttPublisher:
    """Connects, publishes one message with QoS 1 (the broker confirms it) and disconnects.

    Each connection uses ``client_id`` plus a random suffix, so two runs on the same node
    never take over each other's session.

    Network failures are retried with exponential backoff; a broker that refuses the
    connection (bad credentials, ACLs) is not, because retrying would not help.
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
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._client_id = client_id
        self._username = username
        self._password = password
        self._tls = tls
        self._ca_cert = ca_cert
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    @property
    def topic(self) -> str:
        return self._topic

    def publish(self, message: bytes) -> None:
        for attempt in range(self._max_retries + 1):
            try:
                self._publish_once(message)
                return
            except _RetryableError as exc:
                if attempt == self._max_retries:
                    raise SharingError(
                        f"the broker at {self._host}:{self._port} could not be reached: {exc}"
                    ) from exc
                delay = self._backoff * 2**attempt
                logger.info("MQTT publish failed (%s); retrying in %.1fs", exc, delay)
                self._sleep(delay)

    def _publish_once(self, message: bytes) -> None:
        client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=f"{self._client_id}-{secrets.token_hex(4)}",
            reconnect_on_failure=False,
        )
        client.connect_timeout = self._timeout
        if self._username is not None:
            client.username_pw_set(self._username, self._password)
        if self._tls:
            client.tls_set(ca_certs=str(self._ca_cert) if self._ca_cert else None)
        connected = threading.Event()
        refused: list[str] = []

        def on_connect(_c: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any) -> None:
            if reason.is_failure:
                refused.append(str(reason))
            connected.set()

        client.on_connect = on_connect
        try:
            client.connect(self._host, self._port, keepalive=max(int(self._timeout), 5))
        except (OSError, ValueError) as exc:
            raise _RetryableError(str(exc) or type(exc).__name__) from exc
        client.loop_start()
        try:
            if not connected.wait(self._timeout):
                raise _RetryableError("no answer from the broker")
            if refused:
                raise SharingError(f"the broker refused the connection: {refused[0]}")
            info = client.publish(self._topic, message, qos=1)
            try:
                info.wait_for_publish(self._timeout)
            except (RuntimeError, ValueError) as exc:
                raise _RetryableError(str(exc)) from exc
            if not info.is_published():
                raise _RetryableError("the broker did not confirm the message")
        finally:
            client.disconnect()
            client.loop_stop()


class _RetryableError(Exception):
    """A transient network failure."""
