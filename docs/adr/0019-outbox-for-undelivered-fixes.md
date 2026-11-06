# 0019. An outbox for the fixes the broker did not take

## Context

When a developer accepted a fix and the broker could not be reached, `healer run` printed a warning and the fix was never sent: the team lost it, even though it was valid and tested. The README listed this as a known limitation. Section 7.6 of the plan asks for bounded retries on every external call, which the publisher already had, but a broker that is down for minutes outlasts any retry inside one command.

## Decision

- **A fix the broker does not take waits in an outbox.** `SolutionSharer` builds and checks the message as before. If publishing raises `SharingError` (the broker is unreachable, refuses this node, or fails the TLS handshake), it keeps the solution in a `ShareOutbox` and returns a receipt that says why. Problems with the content, such as a secret in the patch or a message over `MAX_SHARE_BYTES`, are not kept: sending later would not change them.
- **SQLite in the `healer-data` volume** (`OUTBOX_PATH`, `/data/healer/outbox.sqlite3`), like the token ledger. `healer run` in the cli container and `healer listen` in the listener container both use it. SQLite locks the file for each change.
- **What is kept is what would have left the machine:** the `SharedSolution`, already redacted, with its topic, the time it was queued, the attempts and the last error. It is sealed only when sent, with the team key of that moment, so rotating `SHARE_KEY` does not leave it unreadable.
- **One entry per topic and error signature.** A newer fix for the same error replaces the one waiting, and an older one never replaces a newer one. A fix that is delivered removes an older one waiting for the same error, which receivers would ignore anyway. Each solution keeps its `published_at`, so receivers still prefer whichever fix for the error was found last, not the one delivered last.
- **Only to its own topic.** A pass sends only the entries for the current `MQTT_TOPIC`. The others wait, so a node that moves to another team never sends it the old team's code.
- **Three ways out:**
  - `healer listen`, with `SHARE_SOLUTIONS=true`, sends the outbox from a thread of its own, so receiving never waits for it. It sends right after each connection, which is when a broker that was down comes back, and every `OUTBOX_RETRY_SECONDS` (300) while connected.
  - `healer run` sends it right after one of its fixes is delivered, since the broker is then known to be up.
  - `healer outbox --send` sends it by hand.
- **A pass ends at the first failure,** because the rest would fail the same way. The entry keeps the error and counts the attempt. An entry over the size limit can never be sent, so it is dropped.
- **`healer outbox`** lists what waits and `--discard` forgets it after asking. `healer doctor` says how many fixes are waiting.

## Consequences

- An accepted fix reaches the team even if the broker was down when it was accepted. Measured with the real broker:
  - with Mosquitto stopped, `healer run` kept the fix;
  - `healer outbox --send` then failed and counted a second attempt;
  - with Mosquitto started again, `healer outbox --send` delivered it with its original `published_at`;
  - in a second run, `healer listen` sent it about 3 seconds after the broker came back.
- A fix can be sent twice, when `healer run` and `healer listen` pass over the outbox at the same moment. Receiving is idempotent, so the second copy is ignored as already known.
- With sharing off, `healer listen` leaves the outbox alone. Turning sharing off stops what is waiting, too, until `healer outbox --send` is run.
- The outbox has no size cap or expiry. It holds at most one entry per error fixed while the broker was down, each under `MAX_SHARE_BYTES`.
- `SolutionPublisher` now exposes its `topic`, so the sharer can tell which entries are for it.
