"""Where the real broker of the integration tests is, and whether they can use it."""

import os
import socket

HOST = os.environ.get("MQTT_HOST", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
USERNAME = os.environ.get("MQTT_USERNAME")
PASSWORD = os.environ.get("MQTT_PASSWORD")
TEAM_TOPIC = os.environ.get("MQTT_TOPIC", "healer/solutions/v1")


def reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


SKIP_REASON = (
    "no MQTT broker reachable"
    if not reachable()
    else "MQTT_USERNAME and MQTT_PASSWORD are not set"
    if not (USERNAME and PASSWORD)
    else ""
)


def topic_for(name: str) -> str:
    """A topic under the team topic, which the broker lets this node use, but no listener reads."""
    return f"{TEAM_TOPIC}/test/{name}"
