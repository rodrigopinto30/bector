# 0013. Broker authentication and per-node access

## Context

Section 7.5 of the plan requires authentication, per-node ACLs and TLS on the broker outside a trusted local network, and section 7.10 lists the anonymous Mosquitto as a gap to close before sharing across machines. In practice the gap was wider. Docker published port 1883 on every interface of the host, so anyone on the same network could subscribe without a password and read every patch the team shared, which contains code. In addition, `docker-compose.yml` fixed `MQTT_HOST` to the bundled broker, so a machine could not use another machine's broker at all.

This ADR covers authentication and access. TLS is the next step.

## Decision

- **No anonymous clients.** `allow_anonymous false`, with a password file and an ACL file in their own volume (`mosquitto-security`). The broker starts with empty files, which let nobody in.
- **One user per node, managed with `./broker.sh`.** `add NODE` creates the node or gives it a new random password, and prints the lines for that node's `.env`. `remove NODE` revokes it, and `list` shows the nodes. Names are restricted to letters, digits, `.`, `_` and `-`, so they cannot inject lines into the ACL.
- **Each node may only use the team topic.** The ACL gives every user `readwrite <MQTT_TOPIC>/#`. Measured on the real broker, Mosquitto still grants a subscription to `#` or `$SYS/#`, but it only delivers messages on the allowed topics and drops what a node publishes elsewhere. The subtopics let the integration tests run on the real broker without reaching any listener.
- **This machine is prepared automatically.** `./broker.sh local`, run by `start.sh`, writes `MQTT_USERNAME` and a random `MQTT_PASSWORD` at the end of `.env` if they are missing, and adds that user to the bundled broker. It does nothing when `MQTT_HOST` points to another machine.
- **Private by default.** The broker port is published on `127.0.0.1`. `BROKER_BIND=0.0.0.0` opens it to the network, and `BROKER_PORT` changes the port. `MQTT_HOST` and `MQTT_PORT` from `.env` now reach the containers, so a node can use another machine's broker.
- **The files belong to the mosquitto user, mode 600.** The pinned image (`eclipse-mosquitto:2.1.2-alpine`) drops privileges before reading them and warns unless it owns them. `mosquitto_passwd` advises root instead; following it stopped the broker from starting, and with an earlier build it made a reload stop the broker. `broker.sh` drops only those ownership warnings.
- **Changes apply with a reload.** A `SIGHUP` applies a new or removed node at once, disconnects a removed node, and keeps the other nodes connected.
- **`healer doctor` checks the broker.** It connects with this node's credentials and reports whether the broker accepts them.

## Consequences

- Nobody can read or publish without a node's password, and a lost machine can be revoked alone.
- Without TLS, passwords and messages still travel unencrypted. `BROKER_BIND=0.0.0.0` is only acceptable on a trusted network until TLS is added.
- ACLs cannot bind the `node` field of a message to the user who sent it, and MQTT lets a client take over another session by reusing its client id. Any node can therefore claim to be another one or steal its durable session. The team key still decides which messages are accepted.
- `./stop.sh --clean` deletes the nodes. `start.sh` adds this machine again; the others must be added again.
- The broker image is pinned, as 7.5 requires; updating it is a deliberate change that must be checked again.
