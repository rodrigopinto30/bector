# 0014. TLS for the broker

## Context

ADR 0013 made the broker require a password per node, but left encryption for later. Without it, passwords and messages travel in clear text on the network, and a machine on the same network can pretend to be the broker to collect the nodes' passwords. Section 7.5 of the plan requires TLS outside a trusted local network. The MQTT adapters already accepted `MQTT_TLS` and `MQTT_CA_CERT`, but nothing in the project produced certificates, and the bundled broker had no TLS listener.

## Decision

- **A private CA per broker, created by `./broker.sh tls ADDRESS...`.** The broker image has no `openssl`, so the certificates are made in a throwaway container of the `cli` image, with the broker's security volume mounted. Keys are EC P-256. The CA lasts 10 years and the broker's certificate 825 days, and that certificate names the given addresses plus `mosquitto`, `localhost` and `127.0.0.1`.
- **The CA is created once and kept.** Running `tls` again only renews the broker's certificate, so the other machines keep their `team-ca.crt`. The CA's key stays in the volume, readable only by root. The broker's key belongs to the mosquitto user, which needs it. `tls` copies the CA's certificate to `team-ca.crt` in the project folder (ignored by git), which is what the other machines need. `ca` writes it again, and `tls-off` deletes the certificates and the CA.
- **The TLS listener lives in an included file.** `mosquitto.conf` includes `conf.d/` from the security volume, and `tls` writes `tls.conf` there. Without certificates the broker starts exactly as before. Mosquitto only opens listeners when it starts, so `tls` restarts the broker instead of reloading it.
- **TLS 1.2 or later.** `tls_version tlsv1.2` works as a minimum. Measured on the real broker, TLS 1.1 is refused, and TLS 1.2 and 1.3 are accepted.
- **Each port has its own bind.** `BROKER_TLS_BIND` and `BROKER_TLS_PORT` publish port 8883, on `127.0.0.1` by default, apart from `BROKER_BIND` for the plain port. A network can therefore reach TLS only. The broker machine's own containers keep the plain port inside Docker.
- **Clients verify the CA and the host name** with paho's defaults. Measured: a broker signed by another CA, the system's CAs and an address missing from the certificate are all refused.
- **A failed handshake is explained, not retried.** `MqttPublisher` does not retry a TLS error, and `healer listen` ends with exit code 2 when its first handshake fails, instead of retrying forever under "cannot reach". The messages point to `MQTT_CA_CERT` and `MQTT_HOST` for a certificate that cannot be verified, and to `MQTT_PORT` for a port without TLS. A CA file that cannot be read is reported as such.
- **An empty setting means its default.** Without this, `MQTT_CA_CERT=` in `.env` was read as the folder `.`.

## Consequences

- Passwords and messages are encrypted, and the nodes know they are talking to the team's broker. The team can share fixes over a network it does not trust.
- Every machine needs `team-ca.crt`. A new CA, after `tls-off` or `./stop.sh --clean`, must be copied to all of them again.
- Nodes still authenticate with passwords inside TLS, not with client certificates; mutual TLS could replace them later.
- There is no revocation list for the broker's certificate. It is replaced by running `tls` again, and it must be renewed before it expires, when verification would start to fail.
- A client without TLS on the TLS port gets no answer, so it fails after `MQTT_TIMEOUT_SECONDS` with "no answer from the broker" rather than at once.
- `healer listen` keeps retrying a TLS failure that appears after it has connected, such as a certificate replaced by one from another CA.
