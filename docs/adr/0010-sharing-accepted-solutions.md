# 0010. Sharing accepted solutions over MQTT

## Context

Section 4 of the plan has every node broadcast a fix once it is validated and the developer accepts it, so other nodes never solve the same error again. Section 6.2 lets a solution into the shared knowledge only with evidence that the tests failed before the patch and passed after it. Section 7.5 requires redacting before publishing, sharing only the signature and the diff (opt-in), signing every message, and authentication with TLS on untrusted networks. Section 7.6 asks for timeouts and bounded retries on MQTT. ADR 0009 made acceptance an explicit step.

## Decision

- **Publish on acceptance, from `healer run` only.** The message is sent after the accepted patch is written to the workspace. Declining, `--no-apply` or `--no-share` send nothing. `healer apply` does not share, because it never runs the tests without the patch and so has no evidence.
- **Opt-in.** `SHARE_SOLUTIONS` is off by default; `--share`/`--no-share` override it for one run.
- **The evidence comes from the run.** `RepairResult.solution` holds the fix with this run's command and exit code before the patch. For a fix replayed from the local cache, the evidence is refreshed, and the tokens are what the model spent originally.
- **One JSON message, schema version 1** (`domain/sharing.py`): node, publish time, error signature and summary, the patch's edits, explanation, model, tokens spent, and the evidence (command, exit code before, `passed_after: true`, verification time). Pydantic models forbid unknown fields so the format stays strict on both sides.
- **Redaction.** The summary, explanation and command are redacted. A patch whose edits contain anything the redactor flags is not shared, because a masked patch would no longer apply. The patch's description, a copy of the explanation, is dropped.
- **HMAC-SHA256 with a team key.** `seal` signs the canonical JSON of the solution (sorted keys, no spaces) with `SHARE_KEY`, which must have at least 16 characters. A shared key proves a message came from the team and was not altered. It does not tell team members apart; per-node keys or signatures can be added under a new schema version.
- **Delivery.** `MqttPublisher` (paho-mqtt 2) connects per message, publishes with QoS 1 and waits for the broker's confirmation, with a timeout on every step. Network failures are retried with exponential backoff; a refused connection is not. Each connection adds a random suffix to the client ID, so concurrent runs never take over each other's session. Username, password and TLS are configurable.
- **A failure never undoes a fix.** Sharing errors are printed as warnings, and the exit code still reflects the fix.

## Consequences

- Accepted fixes reach every node subscribed to `MQTT_TOPIC`. Receiving and validating them is the next part of Phase 5.
- Messages are not queued when the broker is down; a solution that was not sent stays in the local cache but is not retried later.
- The bundled Mosquitto still accepts anonymous connections, which is acceptable only on a trusted local network; the README explains how to secure it.
- Anyone with the team key can publish valid messages. That is why receivers must also re-validate every solution with their own tests before recommending it (6.2).
