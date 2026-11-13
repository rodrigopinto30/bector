# 0024. Versions and releases

## Context

Section 7.7 of the plan asks for Semantic Versioning, but the project stayed at 0.1.0 with no version marked. The nodes of a team are updated one at a time, so at any moment they may run different code. Without a version, nothing told whether two nodes could work together, or whether an update would leave a node's data unreadable. With everything the plan commits to done, the project needed a first release and a rule for the next ones.

## Decision

- **Semantic Versioning, from 1.0.0.** A version is MAJOR.MINOR.PATCH. What it promises is about the parts of the system that other nodes, people or scripts rely on.
- **What a MAJOR version may break, and only a MAJOR one:**
  - The commands, their options and their exit codes, and the settings with what they mean.
  - The messages nodes exchange: the fix and the result, their `schema_version` 1, how they are signed, and their topics, `MQTT_TOPIC` and `MQTT_TOPIC/results`. The schemas refuse unknown fields, so even adding a field to a message is a MAJOR change, or a new `schema_version` that older nodes refuse cleanly.
  - The data in the `healer-data` volume and the Chroma collections. A new version reads what an older one wrote, and converts it on its own when needed, as the outbox does since ADR 0022. A version that cannot is MAJOR.
- **Not part of the promise:** the Python code inside `healer`, the panel's JSON under `/api`, and the panel's look. Only the panel's own pages read that JSON, and they are built together with it.
- **MINOR** adds without breaking: commands, options, settings, data files, or parts of the panel. **PATCH** fixes without changing any of the above.
- **One version, written in four places.** `pyproject.toml`, `healer.__version__`, and `panel/package.json` with its lock. A test checks that they agree and that `CHANGELOG.md` has an entry for it. `healer version` and the panel's header show it.
- **A release is a pull request and a tag.** The pull request sets the version and adds its entry to `CHANGELOG.md`, in the format of Keep a Changelog. The entry is written for whoever uses the system: what it can do, how to upgrade, and which versions work together. Once it is merged, an annotated tag `vX.Y.Z` marks that commit of `main`, and a GitHub release carries the same entry.

## Consequences

- A team can tell which nodes work together, and whether an update needs anything by hand.
- Changing a message becomes expensive on purpose: it needs a MAJOR version, or a new schema that older nodes refuse.
- Each release needs its changelog entry written by hand, and the version written in four places; the test catches a place left behind.
- 1.0.0 lists everything done so far as one entry. Later versions list only what changed.
