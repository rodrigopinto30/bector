"""TLS for the MQTT adapters: verify the broker, and say clearly why a handshake failed."""

import ssl
from pathlib import Path

import paho.mqtt.client as mqtt

from healer.domain.errors import SharingError


def use_tls(client: mqtt.Client, ca_cert: Path | None) -> None:
    """Make ``client`` verify the broker with ``ca_cert``, or with the system's CAs without one.

    The broker's certificate must also name the host the client connects to.
    """
    try:
        client.tls_set(ca_certs=str(ca_cert) if ca_cert else None)
    except (OSError, ValueError) as exc:
        detail = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise SharingError(f"MQTT_CA_CERT {ca_cert} cannot be used: {detail}") from exc


def tls_failure(host: str, port: int, exc: ssl.SSLError) -> SharingError:
    """The error for a failed TLS handshake. Retrying would not help."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return SharingError(
            f"the broker at {host}:{port} could not be verified ({exc.verify_message}); "
            "check that MQTT_CA_CERT is the team's CA and that MQTT_HOST is one of the names "
            "in the broker's certificate"
        )
    return SharingError(
        f"the TLS connection to {host}:{port} failed ({getattr(exc, 'reason', None) or exc}); "
        "check that MQTT_PORT is the broker's TLS port"
    )
