import socket
import threading

from healer.adapters.mqtt_subscriber import MqttSubscriber


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def test_an_unreachable_broker_is_reported_once_and_stop_ends_the_run() -> None:
    statuses: list[str] = []
    announced = threading.Event()

    def on_status(status: str) -> None:
        statuses.append(status)
        announced.set()

    port = closed_port()
    subscriber = MqttSubscriber(
        host="127.0.0.1", port=port, topic="healer-test/solutions", client_id="healer-test"
    )
    thread = threading.Thread(target=subscriber.run, args=(lambda _: None, on_status))
    thread.start()
    assert announced.wait(5)
    subscriber.stop()
    thread.join(5)
    assert not thread.is_alive()
    assert statuses == [f"cannot reach 127.0.0.1:{port}; retrying"]
