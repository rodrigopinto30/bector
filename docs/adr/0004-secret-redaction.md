# 0004. Secret redaction before data leaves the machine

## Context

From Phase 4 on, diagnoses and code are sent to the Anthropic API, and in Phase 5 solutions are published to other nodes over MQTT. Section 7.5 of the plan requires scanning code, logs and traces for secrets and masking them before both. Tracebacks often contain connection strings, and the code around an error often contains configuration with credentials.

## Decision

- **Over-redact.** When a value might be a secret, it is hidden. A leaked credential cannot be recalled; a hidden harmless value only removes a little context from the prompt. This was chosen explicitly over a "high confidence only" policy.
- **Pure domain logic, no third-party scanner.** The redactor is a set of regular expressions plus an entropy check in `healer.domain.redaction`. It runs offline, is deterministic, and is easy to test. A dedicated scanner (for example `detect-secrets`) can replace it later behind the same interface.
- **Layered rules, most specific first:** known credential formats, then credentials in URLs and authorization headers, then values assigned to secret-looking names, then random-looking strings.
- **Only the value is replaced.** Names, users, hosts and header names stay, so the model still understands the code (`DB_PASSWORD=[REDACTED:secret_assignment]`).
- **Placeholders are protected.** No rule may rewrite text that is already a placeholder, so redaction is idempotent and placeholders are never corrupted.
- **Random-string heuristic:** at least 24 characters, letters and digits, an unbroken run of 16+ characters (paths with two or more `/` count `/` as a separator) and Shannon entropy of 3.5 bits per character or more. Measured on real data: base64 keys score 4.2 to 4.8; identifiers and pytest lines are excluded by the run length; UUIDs by their short segments.
- **Counts, never values.** The result reports how many secrets of each kind were hidden, so the pipeline and the token ledger can log it safely.
- **Diagnosis support.** `redact_diagnosis` cleans every free-text field (message, causes, frame code, enclosing source, signature basis) and keeps the signature, paths and line numbers intact.

## Consequences

- Hex hashes such as Git SHAs are hidden even though they are not secrets.
- Some code that assigns a plain variable to a secret-looking name (`password = user_input`) is hidden.
- Secrets with no recognisable format, no secret-looking name and low entropy (for example `password123` in free text) are not detected.
- Base64 secrets that contain a single `/` are evaluated as one token; with two or more `/` they are treated like a path and may be missed unless a name or format rule catches them.
- New credential formats need a new rule.
