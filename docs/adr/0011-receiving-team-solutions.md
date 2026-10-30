# 0011. Receiving team solutions

## Context

ADR 0010 made nodes publish accepted fixes, but nothing received them. Section 4.2 of the plan calls for a background listener that stores incoming solutions in the local Chroma database. Section 7.5 treats them as untrusted: validated against the schema, size-limited, deduplicated, signed, kept as suggestions with their origin and timestamp, and never applied automatically. Section 7.6 asks for idempotent ingestion, with the record id derived from the signature, and for a listener that reconnects on its own and shuts down gracefully.

## Decision

- **Checks run from cheapest and least trusting to richest.** `unseal` checks the size, parses JSON into plain values, and verifies the HMAC of their canonical form with `hmac.compare_digest`. Only then does Pydantic validate the schema, so a message without the team key never reaches it. Malformed input (bad UTF-8, lone surrogates, deep nesting, wrong shapes) ends in `RejectedMessageError`, never in an unexpected exception.
- **A valid signature is not enough.** `SolutionIngester` also refuses evidence with exit code `0` before the patch and any patch that breaks `PatchPolicy`. A team key can leak, so the patch rules apply to signed messages too.
- **One suggestion per error signature,** as 7.6 requires: the signature is the record id. The same patch changes nothing; a newer `published_at` replaces; an older one is ignored, so replaying an old signed message cannot overwrite a newer fix. A node ignores its own messages.
- **Suggestions belong to the node, not to a workspace.** Signatures are portable, so one collection per topic (`team-<hash>`) serves every project. Each record keeps the origin node and the publish and receive times. The error summary is embedded for the search by meaning of the next part.
- **Durable subscription.** `MqttSubscriber` uses a fixed client id derived from `NODE_ID`, `clean_session=False` and QoS 1, so the broker keeps what is published after the node first subscribed. `healer listen` warns when `NODE_ID` is not set, because the default changes with the container.
- **Staying alive.** The paho thread retries lost connections with delays from 1 to 60 seconds. `connected` is reported only after the broker confirms the subscription. A refused connection or subscription ends the command with exit code `2`. The message handler catches every exception, because one bad message must not kill the loop thread.
- **Clean shutdown.** `SIGTERM` is turned into `KeyboardInterrupt`, which interrupts the main thread's wait and runs the normal disconnect. The handler itself touches no locks.
- **Terminal safety.** Text from other nodes is printed without control characters, so a message cannot move the cursor or forge output lines.
- **`healer team` lists the suggestions,** and an optional `listener` service with the `team` profile keeps `healer listen` running. The embedding model moved to a volume, shared by both containers.

## Consequences

- Suggestions are stored but not used yet; replaying them before asking Claude is the next part.
- Two processes now use the same embedded Chroma path. A check with one long-lived writer and eight short-lived processes on other collections, and the real demo, showed no errors, and each collection has one writer. Chroma does not document this as a guaranteed mode, so it stays a known risk.
- Messages are acknowledged once handled. If storing one fails, a warning is printed and that message is not redelivered.
- Two nodes with the same `NODE_ID` ignore each other's messages and fight over one broker session.
- One suggestion per signature means a later valid fix replaces an earlier one, even if the earlier one suited another project better.
- The broker keeps queued messages until the node returns, within Mosquitto's limits; a node that never returns leaves a stored session behind.
