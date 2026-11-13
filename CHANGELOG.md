# Changelog

Every notable change to this project, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). ADR 0024 says what a version number promises.

## [1.0.0] - 2026-11-13

The first release. It covers everything the plan commits to: its five phases and the gaps it listed. It also includes the first of its future ideas, ranking the team's fixes by success, and a local panel. Before it, the project had no version of its own, so every earlier commit counts as 0.x.

### Fix failing tests

- `healer run` runs the tests on an isolated copy, diagnoses the first error and looks for a fix until the tests pass, within a limit of attempts and tokens (`REPAIR_MAX_ATTEMPTS`, `REPAIR_MAX_TOKENS`). It tries the cheapest source first: the local cache, then the team's fixes, then Claude, who is told why the earlier patches failed. (#8, #9, #13)
- A fix is shown as a diff and written to your files only after you accept it. `--save` keeps it, to apply later with `healer apply`. (#10)
- Every patch is checked before it runs: paths inside the project, protected folders, and size limits. (#6)

### Understand the code and the error

- `healer index`, `search` and `map`: a map of the code built with Tree-sitter, searchable by meaning in Chroma, and updated only where files changed. (#1, #3)
- `healer diagnose` turns a traceback into a structured diagnosis. Its signature ignores paths, line numbers and concrete values, so the same error matches on every machine.
- `healer test` and `healer patch` run the tests on an isolated copy, with or without a patch. (#4, #6)
- `healer propose` asks Claude for one fix for one error. (#7)

### Never pay twice

- Fixes that worked are kept in a local cache and replayed for 0 tokens when the same error comes back. (#9)
- `healer savings` shows the tokens spent and saved, per day or per project, from a ledger that keeps metrics only. (#9)

### Work as a team

- With `SHARE_SOLUTIONS=true`, an applied fix is sent to the team over MQTT. It is redacted, signed with the team key (HMAC-SHA256), and carries the evidence that the tests failed before and passed after. (#11)
- `healer listen` keeps the team's fixes as suggestions and refuses forged, malformed or unsafe messages. `healer team` lists them. (#12)
- `healer run` offers them between the local cache and Claude, and tests each one in the sandbox first. (#13)
- An error keeps several fixes from the team, up to `TEAM_FIXES_PER_ERROR` (5). (#22)
- Each node tells the team which of its fixes worked there, and fixes are tried by how often they worked. (#23)
- Fixes and results the broker did not take wait in an outbox and are sent later. `healer outbox` lists them. (#20, #23)
- The bundled broker accepts only the nodes added with `./broker.sh`, each with its own password, and can use TLS. (#14, #15)

### See everything

- A local panel at http://localhost:8765, built with Next.js, TypeScript, Tailwind and shadcn, in pastel colors with a dark mode. It shows health, recent activity, savings, incidents, projects, the team's fixes and the outbox. It only reads, and only answers to this computer. (#21)
- Each run can be opened step by step from the Incidents tab. (#24)
- `./panel.sh demo` makes real events happen one at a time on a demo panel with its own data. (#21)

### Security

- Secrets are hidden before anything is sent to Claude or to the team, and before a run is kept. `healer redact` shows how. (#5)
- The tests run in a sandbox container with no network, no secrets and no access to your files. (#16)
- The containers run as your own user, with no privileges. Only the sandbox's server keeps root, with the few capabilities it needs to run each test as another user, and the tests themselves never run as root. (#17)
- Every dependency, of Python and of the panel, is pinned with its hash and audited. CI checks every pull request, and a hook checks every commit. (#18, #19, #21)

### Upgrading to 1.0.0

From any earlier commit:

```bash
git pull
./stop.sh && ./start.sh
docker compose --profile team up -d listener   # only if you run the team listener
```

- The data in the volumes is kept, and nothing has to be converted by hand:
  - An outbox written before ADR 0022 is read as it is and moved to the new tables on its first change.
  - Team suggestions stored before ADR 0021 keep working.
  - The results of the team's fixes and the history of runs start empty, and their files are created on first use.
  - Runs from before the history show no steps in the panel.
- `./stop.sh` also stops the team listener, and `./start.sh` does not start it again: hence the last command.

### Compatibility within a team

- Every node since sharing (#11) sends and reads the same fix messages (schema 1), signed with the same team key. Since #14, a node also needs its own broker password, added with `./broker.sh`.
- Nodes from before #22 keep one fix per error.
- Nodes from before #23 neither send nor receive results. Results travel on their own topic, `MQTT_TOPIC/results`, so those nodes never see one.
- A team with nodes of different versions still works; upgrading every node brings the ranking to all of them.

[1.0.0]: https://github.com/rodrigopinto30/bector/releases/tag/v1.0.0
