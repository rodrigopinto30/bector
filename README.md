# Local-First Autonomous Code Remediation System

A local, modular CLI system that diagnoses, fixes, and tests code errors while spending as few tokens as possible. Fixes are never applied without the developer's approval.

## What problem does it solve?

- **Wasted tokens.** Asking an AI to fix a bug usually means sending it a lot of code. This system sends only the small piece of context that matters.
- **Repeated work.** When one developer solves a problem, the rest of the team often solves it again. Validated solutions are shared between workstations over MQTT and stored in each machine's local vector database, so the same problem is solved once.
- **Loss of control.** Every fix is tested locally and then recommended. The developer decides whether to adopt it.

At this stage the scope is **Python projects only**.

## Project status

All five development phases are implemented. The broker requires a password per machine and can use TLS, so the team's machines can share fixes over a network they do not trust. The tests of every patch run in a separate sandbox container, with no network, no secrets and no access to your files. The containers run as your own user, with no privileges, except the sandbox's server. Every dependency is locked with its hashes and audited. Every pull request is checked by a CI pipeline, and every commit by a local pre-commit check. A local panel shows all of it in the browser.

| Phase | What it delivers | Status |
| --- | --- | --- |
| 1. Environment and base CLI | Docker setup, `start.sh`, `stop.sh` and `healer doctor` | Done |
| 2. CodeIndex | Map and search the code with Tree-sitter and Chroma | Done |
| 3. Diagnoser | Turn a failure into a structured diagnosis with a portable signature | Done |
| 4. Self-correction loop | Isolated test runs, patches, secret redaction, proposals from Claude, retries, local cache and token ledger | Done |
| 5. Recommendation and team sync | Applying fixes with your approval, then sharing, receiving and reusing them across machines | Done |

**Known limitations:**

- Anyone with the team key can send results under any node name. Results only change the order in which the team's fixes are tried, and every fix is still tested here before it is offered.
- `healer listen` and the other commands use the same embedded Chroma database from separate processes. Each collection has one writer and the checks made showed no errors, but Chroma does not guarantee this mode.

## How it works

```text
tests fail
    |
Diagnoser            structured diagnosis and portable signature
    |
looks for a fix, cheapest source first:
    1. local cache   free
    2. team fixes    free, and each one runs only if you approve it
    3. Claude        last resort, with the smallest useful context
    |
isolated copy        each patch is tested here; Claude retries with feedback
    |
your decision        the tested patch is written to your files only if you accept it
    |
MQTT (optional)      other nodes receive it as a suggestion
```

1. **Diagnoser** parses the failure into a structured diagnosis with a machine-independent signature.
2. **CodeIndex** (Tree-sitter + Chroma) retrieves the minimal relevant code when Claude has to be asked.
3. **LangGraph** orchestrates the loop. It tries what is already known first and asks Claude only as a last resort. Each patch is applied in an isolated copy and tested. If the tests fail, Claude is told why and tries again, up to the attempt and token limits.
4. The validated fix is **recommended** to the developer, who decides whether to apply it.
5. With sharing on, an accepted fix is **published over MQTT**. Other nodes verify the message and keep it as a suggestion, which `healer run` offers the next time that error appears.

Every incident is recorded in a local ledger, so the tokens spent and saved can be measured.

## Requirements

- Linux (the start script can install Docker for you) or any system with Docker and Docker Compose already installed.
- Port `1883` free on your machine (the MQTT broker listens on it, only for this machine by default).
- An Anthropic API key, only for the fixes that Claude has to write.

## Getting started

```bash
./start.sh
```

The script will:

1. Check that Docker and the Compose plugin are available, and install them if they are missing (Linux only, may ask for `sudo`).
2. Create `.env` from `.env.example` if it does not exist.
3. Write your uid and gid at the end of `.env` the first time (`HOST_UID`, `HOST_GID`): the containers run as your own user.
4. Give this machine a user and password for its MQTT broker, written at the end of `.env` the first time.
5. Build the images, give your user the data volumes and the tool caches that older versions left owned by root, and start the services.
6. Turn on the checks that run before every commit (ruff, mypy and the fast tests), unless the clone already has hooks of its own.

Then check the setup:

```bash
docker compose exec cli healer doctor
```

To stop everything:

```bash
./stop.sh            # stop the services, keep the data
./stop.sh --clean    # stop the services and delete all data (Chroma, the ledger, Mosquitto and its users)
```

> If Docker was just installed by the script, your user may not be in the `docker` group until you open a new session. Until then, use `sudo docker ...`.

### Services

| Service | Container | Purpose |
| --- | --- | --- |
| `cli` | `healer-cli` | Where the `healer` command runs, as your own user and with no privileges. |
| `sandbox` | `healer-sandbox` | Runs the tests of each copy, with no network, no secrets and no access to your files. |
| `mosquitto` | `healer-mosquitto` | MQTT broker used to share solutions between machines. Only the nodes added with `./broker.sh` can connect. |
| `listener` | `healer-listener` | Optional. Runs `healer listen` in the background; it only starts with the `team` profile. |
| `panel` | `healer-panel` | The panel at http://localhost:8765, for this computer only. It only reads. |

In `docker compose exec cli healer doctor`, `cli` is the service and `healer doctor` is the command that runs inside it. If you get `service "healer" is not running`, the command is from before the service was renamed to `cli`; `./stop.sh` also removes the old container.

The project you want to analyze goes in the `workspace/` folder, which is mounted at `/workspace` inside the container. To use another folder, set `WORKSPACE_PATH` in `.env`.

### Verify that everything works

```bash
docker compose ps                          # cli, mosquitto, panel and sandbox should be "Up"
docker compose exec cli healer doctor      # the dependency checks should be [ok]
docker compose exec -w /app cli pytest     # run the test suite
```

Then open http://localhost:8765 to see the panel.

### Fix your first error

Put your project in `workspace/`. To let Claude write fixes, open `.env`, set `ANTHROPIC_API_KEY` and recreate the containers with `./stop.sh && ./start.sh`. Then:

```bash
docker compose exec cli healer run        # find a fix, test it on a copy, ask before applying it
docker compose exec cli healer savings    # tokens spent and saved
```

## Commands

Run each one inside the container, as `docker compose exec cli healer <command>`.

| Command | What it does |
| --- | --- |
| `run` | Finds a fix until the tests pass, tests it on a copy and offers to apply it. This is the main command. |
| `apply FILE` | Tests a saved patch on a copy and, if you accept, writes it to your files. |
| `savings` | Shows the tokens spent and saved, per day or per project. |
| `doctor` | Checks the dependencies, the API key and the sharing setup. |
| `version` | Shows the installed version. |
| `index` | Indexes the workspace. Only changed files are parsed again. |
| `search "text"` | Finds code by meaning. |
| `map FILE` | Shows the signatures and line ranges of a file. |
| `diagnose [LOG]` | Turns a failure log into structured diagnoses. |
| `test` | Runs the tests on an isolated copy and diagnoses the failures. |
| `patch FILE` | Applies a patch to a copy and runs the tests against it. |
| `propose [LOG]` | Asks Claude to fix one error from a log and tests the patch on a copy. |
| `redact [FILE]` | Prints a text with its secrets hidden. |
| `listen` | Receives the team's fixes and keeps them as suggestions, until stopped. |
| `team` | Lists the suggestions received, with the nodes where each one worked or failed. |
| `outbox` | Lists the accepted fixes, and the results of the team's fixes, that the broker did not take. `--send` sends them now; `--discard` forgets them. |
| `panel` | Serves the panel. The `panel` container already runs it at http://localhost:8765. |

Add `--help` to any command to see its options.

## CodeIndex: map and search your code

CodeIndex parses every Python file with Tree-sitter, extracts modules, classes, functions and methods (signature, docstring, decorators, line range, source), and stores them in Chroma. Run the commands inside the container:

```bash
docker compose exec cli healer index                 # index /workspace (only changed files are re-parsed)
docker compose exec cli healer search "read the db settings"      # find code by meaning
docker compose exec cli healer map app/config.py     # signatures and line ranges of one file
```

- `index` makes the index mirror the workspace: new and changed files are parsed, deleted files are removed. A second run with no changes parses nothing.
- Files with syntax errors are still indexed with every definition that could be recognised, because broken code is exactly what the Diagnoser points at.
- Each workspace has its own Chroma collection, so two projects with the same relative paths never mix.
- Symlinks that resolve outside the workspace are skipped, as are files over 1 MB, non-UTF-8 files, and folders such as `.git`, `.venv` and `node_modules`.
- `search` prints the cosine distance first (lower is more similar), then `file:lines` and the signature.

## Diagnoser: turn a failure into a diagnosis

`healer diagnose` reads raw output (a log file, or stdin) and prints one diagnosis per distinct error:

```bash
docker compose exec cli sh -c 'python main.py 2>&1 | healer diagnose'
docker compose exec cli sh -c 'python -m pytest 2>&1 | healer diagnose'
docker compose exec cli healer diagnose error.log --json
```

```text
[1] KeyError: 'db'
    signature  de1466b3373f
    origin     app/cfg.py:3 in read_db
    code       return config["db"]
    symbol     def read_db(config):  (app/cfg.py:1-3)
    frames     2 (2 in workspace)
```

- **Formats:** standard Python tracebacks (including chained exceptions and syntax errors) and pytest reports in the long, short, native and collection-error formats. ANSI colors are ignored. Exception groups are not supported yet.
- **Origin:** the innermost frame that belongs to the workspace, which is where the fix most likely goes. Paths printed on another machine or in CI are matched to workspace files by their longest existing suffix. Library, standard-library and `<frozen>` frames never count as workspace files, and paths that escape the workspace are rejected.
- **Symbol:** the narrowest indexed function, method or class that contains the origin line. Run `healer index` first, otherwise it shows `not indexed`.
- **Signature:** a SHA-256 of the error type, the message with values, paths, numbers and addresses replaced by placeholders, and the frames from the origin inward. The same bug gets the same signature on any machine and from any entry point (a script or a test), so repeated errors are grouped with `seen N times`.
- **Exit codes:** `0` when errors were found, `1` when the log has none, `2` when the log cannot be read.
- `--json` prints the full diagnosis (frames, origin, enclosing symbol with its source, signature and its basis), the same structure the repair loop uses.

## Sandbox runner: run the tests on an isolated copy

`healer test` copies the workspace to a private temporary directory, runs the test command there in the test sandbox, diagnoses the failures and deletes the copy. Your files are never modified.

```bash
docker compose exec cli healer test                                      # runs TEST_COMMAND (python -m pytest)
docker compose exec cli healer test "python -m pytest tests/test_app.py" --timeout 60
docker compose exec cli healer test --output                             # also print the captured output
docker compose exec cli healer test --json
```

```text
sandbox    5 files copied to an isolated copy, then deleted
command    python -m pytest
result     tests failed (exit 1) in 1.3s

[1] KeyError: 'db'
    ...
```

- **Allowlist:** only commands that start with an entry of `ALLOWED_TEST_COMMANDS` run (default `pytest` and `python -m pytest`). There is no shell, so `;`, `&&` or `|` are plain arguments. Arguments with absolute paths, `~` or `..` are rejected, because they would read or write outside the copy.
- **Limits:** the run is killed after `TEST_TIMEOUT_SECONDS` (default 300) together with every process it started; output is capped at `MAX_OUTPUT_BYTES` (default 1 MB, keeping the end); workspaces over `MAX_SANDBOX_BYTES` (default 500 MB) are refused.
- **The test sandbox.** The copy is placed in a volume shared with the `sandbox` container, and the command runs there, not in `cli`. That container has no network, no `.env`, no access to the workspace or the data volumes, and a read-only filesystem. A run gets at most 512 processes, `SANDBOX_CPUS` cores (2) and `SANDBOX_MEMORY` of memory (2 GB), and the copies live in memory, in at most `SANDBOX_DISK` of space (2 GB), so a run cannot fill the host's disk. The command runs as an unprivileged user with a clean environment. Copies waiting for their turn stay readable only by root, and after every run the processes the command left behind are killed and `/tmp` is emptied, so one run cannot affect the next. `healer doctor` says whether the sandbox answers.
- **Without the sandbox** (`SANDBOX_SOCKET` unset, as outside Docker), the command runs in the same container with a clean environment. That keeps credentials out of reach of ordinary code, not of code that looks for them: it runs as the same user and can read what that user can.
- **What the tests print is shown with control characters masked,** with `--output` and in the diagnoses, so the code under test cannot forge lines in your terminal.
- The sandbox runs the code baked into its image, not the mounted project, because mounting the project would expose `.env`. After changing the code, `./start.sh` rebuilds it. `./stop.sh` deletes the copies volume, so a new `SANDBOX_DISK` applies on the next start.
- **What is copied:** files and symlinks (as links, never followed). `.git`, virtual environments, `node_modules`, build output and caches are skipped.
- **Exit codes:** `0` when the tests pass, `1` when they fail or time out, `2` when the command is not allowed or cannot start.
- Prefer `python -m pytest` over `pytest`: it adds the project root to the import path, so `from app import ...` works without extra configuration.

## Patches: apply a change to the copy and test it

`healer patch` applies a patch to an isolated copy of the workspace, prints the diff, and runs the tests against it. It answers "does this change fix the tests?" without touching your files. Patches can be written by hand or produced by `healer propose --save`.

A patch is a JSON list of search-and-replace edits:

```json
{
  "description": "Default to postgres when the db key is missing",
  "edits": [
    {"path": "app/cfg.py", "old": "    return config[\"db\"]", "new": "    return config.get(\"db\", \"postgres\")"},
    {"path": "tests/test_defaults.py", "old": "", "new": "def test_default(): ...\n"}
  ]
}
```

```bash
docker compose exec cli healer patch fix.json
docker compose exec cli healer patch fix.json "python -m pytest tests/test_app.py" --json
```

```text
patch      2 edits in 2 files (+2 -2), applied to the copy only
...
verdict    the tests pass with this patch
```

- **Search and replace, not line numbers.** `old` must appear exactly once in the file; an empty `old` creates a new file. If the text is not found verbatim, a unique match that ignores trailing whitespace is accepted. Missing or ambiguous text is rejected with a clear message. This format is far more reliable for an LLM than a unified diff with line numbers.
- **All or nothing.** Every edit is computed in memory first; nothing is written unless all of them fit.
- **Safe paths.** Absolute paths, `..`, `~`, Windows drives, `.git`, `.hg`, `.svn` and `.healer` are rejected, and so is writing through a symlink.
- **Limits.** At most `MAX_PATCH_FILES` files (default 5), `MAX_PATCH_BYTES` of edit text (default 100 KB) and `MAX_PATCH_CHANGED_LINES` changed lines (default 300).
- **Exit codes:** `0` when the tests pass with the patch, `1` when they still fail (the failures are diagnosed), `2` when the patch is invalid or does not fit the code.

## Proposals: ask Claude for a fix

`healer propose` reads a failure log, diagnoses it, sends Claude the smallest useful context, and tests the proposed patch on an isolated copy. Your files are never modified. It needs `ANTHROPIC_API_KEY` in `.env` (recreate the container after editing it, for example with `./stop.sh && ./start.sh`).

```bash
docker compose exec cli sh -c 'python -m pytest > error.log 2>&1'
docker compose exec cli healer propose error.log --show-prompt    # see what would be sent; needs no key
docker compose exec cli healer propose error.log                    # ask Claude and test the patch
docker compose exec cli healer propose error.log --no-test --save fix.json
```

```text
error      KeyError: 'db'
context    2 snippets sent (app/cfg.py:read_db, tests/test_app.py:test_read_db)
secrets    1 hidden before sending
model      claude-sonnet-5-5, 1180 tokens in, 240 out
proposal   read_db raises KeyError when the key is missing; return a default instead.
...
verdict    the tests pass with this patch
```

- **Context, not files.** Claude receives the diagnosis plus a few functions: the one where the error originates, the functions of the call stack that led to it, and up to four more found by meaning when they are close enough (`CONTEXT_MAX_DISTANCE`, default 0.7), within `CONTEXT_MAX_CHARS` (default 12,000 characters). The index is refreshed first.
- **Secrets are hidden before sending,** and a proposal that would write a `[REDACTED:...]` marker into the code is rejected.
- **Project content is data.** The diagnosis and the code are wrapped in tags, and the system prompt tells the model to ignore any instruction inside them.
- **Structured output.** The answer is constrained to the patch format (explanation plus search-and-replace edits), so it is always valid.
- **Model and limits:** `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`), `ANTHROPIC_EFFORT` (default `high`), `ANTHROPIC_MAX_TOKENS` (16,000), `ANTHROPIC_TIMEOUT_SECONDS` (120) and `ANTHROPIC_MAX_RETRIES` (2; the SDK retries rate limits, overloads and network errors with backoff). Server-side refusal fallbacks are enabled.
- `--error N` picks which diagnosed error to fix, `--command` sets the test command, `--save` writes the patch so it can be replayed with `healer patch`.
- **Exit codes:** `0` when a proposal was produced (and passes the tests when tested), `1` when the log has no errors or the tests still fail, `2` on configuration, API or patch errors.

Automated tests never call the real API; they use a fake model.

## Self-correction loop: fix until the tests pass

`healer run` runs the tests on an isolated copy and, when they fail, looks for a fix in order: a solution already verified here for the same error, the team's suggestions if you approve them, and finally Claude. Each of Claude's patches is tested on a fresh copy and, if the tests still fail, Claude is told what went wrong and asked again. The loop stops as soon as a patch makes the tests pass, or when it reaches the attempt or token limit. The working patch is then shown, and it is written to your files only if you accept it.

`ANTHROPIC_API_KEY` is needed only when Claude has to be asked. Set it in `.env` and recreate the containers with `./stop.sh && ./start.sh`.

```bash
docker compose exec cli healer run                                      # runs TEST_COMMAND (python -m pytest)
docker compose exec cli healer run "python -m pytest tests/test_app.py" --attempts 5
docker compose exec cli healer run --save fix.json                      # keep the patch that worked
docker compose exec cli healer run --no-apply                           # only recommend, never ask
```

```text
tests      tests failed (exit 1) in 1.3s, on an isolated copy
error      KeyError: 'db'  (app/cfg.py:3)
limits     3 attempts, 100000 tokens

attempt 1  claude-sonnet-5-5, 1162 tokens in, 142 out
proposal   ... Using config.get with a default of "postgres" fixes the root cause.
patch      1 file (+1 -1)
result     the tests still fail: IndexError: list index out of range

attempt 2  claude-sonnet-5-5, 1356 tokens in (870 from cache), 246 out
proposal   The first patch fixed read_db ..., but the test then calls parse_port with "localhost" ...
patch      2 files (+3 -2)
result     the tests pass

verdict    fixed: the tests pass with the patch of attempt 2
spent      2 attempts, 2518 tokens in (870 from cache), 388 out
...
```

- **The loop is a LangGraph state graph** (`src/healer/pipeline/`): `reproduce` runs the tests without a patch, `recall` tries a stored solution, `consult_team` offers the team's suggestions, `propose` asks Claude, `test` tries the patch, and the graph goes back to `propose` until it is done; `remember` stores a new fix.
- **Every patch starts from the original code,** not from an earlier attempt, so a bad attempt never piles up. Claude is told this and returns the complete fix each time.
- **Feedback.** Each new request includes the earlier patches and what happened with them: the errors that remained (at most three, with their location), the end of the output when no error was recognised, or why the patch could not be applied. It also adds the functions where the last patch broke, in files that patch did not touch. Everything is redacted first.
- **Goal:** the whole test command passes, not only the targeted error. Use a narrower command (`python -m pytest tests/test_app.py::test_x`) to focus on one test.
- **Limits:** at most `REPAIR_MAX_ATTEMPTS` patches (default 3; `--attempts` overrides it) and `REPAIR_MAX_TOKENS` per run (default 100,000). The token check is made before each new attempt, assuming it will cost about as much as the last one.
- **Wasted attempts are cheap:** a patch identical to an earlier one is not tested again, and a patch that does not fit the code is reported back to Claude without running the tests. Both still count as attempts.
- `--error N` picks which diagnosed error to target, `--timeout` limits each test run, `--save` writes the working patch so it can be applied later with `healer apply`, `--no-cache` skips stored solutions (a new fix is still stored), `--no-team` skips the team's suggestions, `--apply`/`--no-apply` apply the fix without asking or never, `--share`/`--no-share` override `SHARE_SOLUTIONS` for one run.
- **Exit codes:** `0` when the tests pass (already or with the patch), whether or not you apply it; `1` when no patch fixed them or the failure could not be diagnosed; `2` on configuration, API or command errors, or when an accepted patch could not be written.

## Local cache and token ledger: never pay twice for the same error

The cheapest fix is the one already found. When `healer run` fixes an error with Claude, it stores the patch under the error's **signature** (the machine-independent hash from the Diagnoser). The next time the same error appears, the stored patch is replayed on an isolated copy before Claude is asked. If the tests pass, the fix costs **zero tokens**. Every incident is recorded in a local ledger, so the savings can be measured.

```text
attempt 1  local cache, 0 tokens (found by claude-sonnet-5-5 for 2909 tokens)
proposal   ...
result     the tests pass

verdict    fixed: the tests pass with a solution from the local cache
spent      1 attempt, 0 tokens in, 0 out; saved about 2909 tokens
```

```bash
docker compose exec cli healer savings                         # one row per day (UTC)
docker compose exec cli healer savings --by project --days 7
```

```text
day         incidents  fixed  from cache  from team  model calls  tokens used  tokens saved
2026-10-16          2      2           1          0            2         2909          2909

total               2      2           1          0            2         2909          2909

reuse avoided 50% of the tokens these incidents would have cost
```

- **Verified, never trusted.** A stored solution is always tested again. If it no longer fits the code or the tests still fail, it is discarded and Claude is asked, with the failed replay included as an earlier attempt.
- **What is stored:** one solution per signature in a per-workspace Chroma collection (`solutions-...`): the patch, the explanation, the model, the tokens it cost, and the evidence that the tests failed before it and passed after it. This is the record that is shared between machines.
- **The ledger** is a SQLite file (`LEDGER_PATH`, `/data/healer/ledger.sqlite3` in the `healer-data` volume). Each row is one incident with metrics only: time, project, signature, error type, how it ended, which level solved it (`cache`, `team`, `model` or `none`), model attempts, tokens used and tokens saved. It never holds code, logs or error messages. Runs with nothing to fix are not recorded. The steps of each incident, redacted, are kept apart, for the Incidents tab of the panel.
- **Tokens saved** is an estimate: what the reused solution cost when the model found it.
- `--no-cache` on `healer run` skips the stored solution, and a new answer from Claude then replaces it. The team's suggestions are still offered; add `--no-team` to skip them too.

## Applying a fix: you decide

Every fix is tested on a copy first. Only then is it shown to you as a colored diff, and your files change only if you answer yes. This is the only step that writes to your project.

```text
verdict    fixed: the tests pass with a solution from the local cache
spent      1 attempt, 0 tokens in, 0 out; saved about 2905 tokens

recommended patch: 2 files (+3 -2), tested on a copy

--- a/app/cfg.py
+++ b/app/cfg.py
@@ -1,3 +1,3 @@
 def read_db(config):
     """Return the database name from the configuration."""
-    return config["db"]
+    return config.get("db", "postgres")
...

apply this patch to your files in /workspace? [y/N]: y
applied    2 files changed in your workspace (+3 -2)
```

`healer apply` does the same with a saved patch: it tests it on a fresh copy and, if the tests pass, asks before writing it.

```bash
docker compose exec cli healer run --save fix.json        # answer "n" to keep it for later
docker compose exec cli healer apply fix.json             # test it again on a copy, then ask
docker compose exec cli healer apply fix.json --yes       # apply without asking if the tests pass
```

- **No by default.** Pressing Enter, or running without a terminal (closed input, CI), means no. `--apply` on `healer run` and `--yes` on `healer apply` skip the question; `--no-apply` never asks and never writes.
- **Nothing changed since the test.** The patch is written only if every file it touches still has exactly the content that was tested (compared by SHA-256). If you edited one of them in the meantime, nothing is written; run it again.
- **All or nothing.** The patch follows the same rules as on the copy (paths inside the project, size limits, no symlinks). If writing one file fails, the files already written are restored.
- **Your files stay yours.** The container runs as your own user, so new files and folders are yours, and existing files are rewritten in place, keeping their owner and permissions. Run as root, as outside Docker, new files get the owner of the folder they are created in.
- Only the files of the patch are checked. If you changed other files after the test, the tests may behave differently; `healer apply` tests the patch again against the current code.
- **Exit codes** of `healer apply`: `0` when the tests pass with the patch (applied or not), `1` when they still fail (nothing is written), `2` on invalid patches, patches that no longer fit the code, or write errors.

## The broker: who can connect

The bundled Mosquitto only accepts the machines (nodes) it knows, and each one may only use the team topic (`MQTT_TOPIC` and its subtopics). Without a user and password nobody can connect, not even to read.

```bash
./broker.sh add laptop-luis      # let a machine connect; prints the lines for its .env
./broker.sh list                 # the machines that may connect
./broker.sh remove laptop-luis   # revoke a machine; it is disconnected at once
```

```text
[broker] laptop-luis may now connect. Add these lines to the .env of that machine:

MQTT_HOST=<address of this machine>
MQTT_PORT=1883
MQTT_USERNAME=laptop-luis
MQTT_PASSWORD=...
NODE_ID=laptop-luis
```

- **This machine needs nothing.** The first time, `./start.sh` writes its `MQTT_USERNAME` and `MQTT_PASSWORD` at the end of `.env` and adds it to the broker. `healer doctor` says whether the broker accepts it.
- **Private by default.** The broker only listens on this machine (`127.0.0.1`): port 1883 without encryption and, once TLS is on, port 8883 with it. Opening a port to the network is an explicit choice: TLS, below, for any network, or `BROKER_BIND=0.0.0.0` for the plain port, only on a trusted network, because passwords and messages travel unencrypted there.
- **Using the broker of another machine.** On the machine with the broker, run `./broker.sh add` with the new machine's `NODE_ID`. Copy the lines it prints into the new machine's `.env` (with TLS off, set `MQTT_HOST` to the broker machine's address) and run `./stop.sh && ./start.sh` there. Its own broker is then left alone.
- **One password per machine.** One machine can be revoked without changing the others. Adding or removing a machine applies at once, and the rest stay connected.
- **What it does not prevent.** A node that may connect can still publish messages claiming to come from another node, or take over another node's durable session by reusing its client name. The team key (`SHARE_KEY`) still decides which messages are accepted.
- `./stop.sh --clean` also deletes the broker's users and its TLS certificates. `./start.sh` adds this machine again; the others must be added again, and TLS turned on again with a new team CA.

### Encryption with TLS

To let other machines in over a network you do not fully trust, turn TLS on, on the machine with the broker, with the address the others use to reach it:

```bash
./broker.sh tls 192.168.1.20     # create the team CA and the broker's certificate; writes team-ca.crt here
./broker.sh add laptop-luis      # now prints the TLS settings for that machine
```

Then set `BROKER_TLS_BIND=0.0.0.0` in `.env` and run `./stop.sh && ./start.sh`. On the other machine, copy `team-ca.crt` into its project folder and add the lines that `./broker.sh add` printed to its `.env`:

```text
MQTT_HOST=192.168.1.20
MQTT_PORT=8883
MQTT_TLS=true
MQTT_CA_CERT=/app/team-ca.crt
MQTT_USERNAME=laptop-luis
MQTT_PASSWORD=...
NODE_ID=laptop-luis
```

- **Only the team's broker is trusted.** Each machine checks the broker's certificate against `team-ca.crt`, and the address in `MQTT_HOST` must be one of those given to `./broker.sh tls` (plus `mosquitto`, `localhost` and `127.0.0.1`). A server that cannot prove it is the team's broker is refused, so nobody can collect the passwords by pretending to be it.
- **TLS 1.2 or later**, and the password is still required inside it.
- **Only TLS faces the network.** Keep `BROKER_BIND` unset so that the plain port stays private. The broker machine's own containers still use the plain port, inside Docker.
- **The team CA is created once.** Running `./broker.sh tls` again, for example for a new address, only renews the broker's certificate (valid for 825 days), and the other machines keep working. The CA's private key never leaves the broker's volume. `./broker.sh ca` writes `team-ca.crt` again if it is lost, and `./broker.sh tls-off` turns TLS off and deletes the certificates and the CA.
- **Clear errors.** `healer doctor` and `healer listen` say when the certificate cannot be verified, or when the port has no TLS, instead of retrying.
- `team-ca.crt` holds no secret, and git ignores it.

## Sharing fixes with the team

When sharing is on, a fix you accept in `healer run` is also sent to the team's other machines (nodes) through the MQTT broker, so nobody has to solve the same error again. It is off by default.

Generate the team key once:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Then add these lines to `.env` on every node of the team, with the same key everywhere and a different `NODE_ID` on each:

```text
SHARE_SOLUTIONS=true
SHARE_KEY=<the key you generated>
NODE_ID=laptop-ana
```

```text
apply this patch to your files in /workspace? [y/N]: y
applied    2 files changed in your workspace (+3 -2)
sharing    sending the fix to the team through mosquitto:1883...
shared     sent to the team as node laptop-ana on healer/solutions/v1 (... bytes)
```

`healer doctor` shows whether sharing is ready (`[ok]   sharing: on, as node laptop-ana on healer/solutions/v1`).

- **Only after you accept.** Nothing is sent when you answer no, with `--no-apply`, or with `--no-share`. `healer apply` does not share: it has no proof that the tests failed without the patch.
- **What is sent** (one JSON message, schema version 1): the error signature and its summary, the patch's edits, the explanation, the model, the tokens it cost, the node and the evidence: the command, its exit code without the patch, and that the tests passed with it. Never whole files.
- **Secrets.** The summary, explanation and command are redacted first. If the patch itself contains something that looks like a secret, it is not shared at all, because masking it would break it.
- **Signed.** Each message carries an HMAC-SHA256 of its content made with `SHARE_KEY` (at least 16 characters), so receivers can reject forged or altered messages.
- **Delivery.** QoS 1 (the broker confirms it), `MQTT_TIMEOUT_SECONDS` per step (10) and `MQTT_MAX_RETRIES` retries with backoff (2). If the broker still does not take it, the fix waits in the outbox and is sent later; it stays applied either way and the exit code does not change. Messages larger than `MAX_SHARE_BYTES` (256,000) are not sent.
- **Broker access.** Only the nodes added with `./broker.sh` can connect, each one with its own password. To use a broker outside this project, set `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME` and `MQTT_PASSWORD`, plus `MQTT_TLS=true` and `MQTT_CA_CERT` if it uses TLS.

### Fixes the broker did not take

If the broker cannot be reached, or refuses this node, the fix is kept in the outbox, a small SQLite file in the `healer-data` volume, and sent later:

```text
applied    2 files changed in your workspace (+3 -2)
sharing    sending the fix to the team through mosquitto:1883...
warning: the fix was applied but not sent yet: the broker at mosquitto:1883 could not be reached: [Errno -3] Temporary failure in name resolution
waiting    kept in the outbox: 'healer listen' sends it when the broker is back, or send it with 'healer outbox --send'
```

It is sent again on its own:

- **By `healer listen`, with `SHARE_SOLUTIONS=true`:** right after each connection to the broker, and every `OUTBOX_RETRY_SECONDS` (300) while connected.
- **By `healer run`:** once its next fix reaches the broker, the ones waiting are sent too.

```text
broker     cannot reach mosquitto:1883; retrying
broker     connected to mosquitto:1883
resent     1 fix waiting in the outbox
```

`healer outbox` lists what waits, `healer outbox --send` sends it now, and `healer outbox --discard` forgets it after asking. `healer doctor` says how many are waiting. The results of the team's fixes tested here wait in the same outbox when the broker does not take them, and are sent the same way.

- **What waits is what would have been sent:** already redacted. It is signed only when it is sent, with the team key of that moment, so a new `SHARE_KEY` does not leave it unreadable.
- **One per fix.** The same fix found again replaces its copy waiting, and once one copy is delivered, an older copy is dropped. Different fixes for the same error wait side by side, as receivers keep them. A result waits once per fix: a newer result of this node for that fix replaces it. An outbox from before this rule is moved to it on its first change.
- **Only to its team.** Each fix goes only to the topic it was meant for. If `MQTT_TOPIC` changes, it keeps waiting until the topic is that one again.
- **A fix can arrive twice,** when `healer run` and `healer listen` send it at the same moment. Receivers ignore the second one as already known.
- **A fix over `MAX_SHARE_BYTES`** can never be sent, so it is dropped when tried.

## Receiving fixes from the team

`healer listen` receives what the other nodes share and keeps it as **suggestions**. It never applies or runs anything. It needs the same `SHARE_KEY` as the team and its own fixed `NODE_ID`; `SHARE_SOLUTIONS` only controls sending.

```bash
docker compose --profile team up -d listener      # keep it running in the background
docker compose logs -f listener                   # see what arrives
docker compose exec cli healer team               # list the suggestions
```

```text
listening  as node laptop-luis on healer/solutions/v1; stop with Ctrl+C
broker     connected to mosquitto:1883
received   KeyError: 'db' from laptop-ana (2 files), kept as a suggestion
received   KeyError: 'db' from laptop-eva (1 file), kept as a suggestion
ignored    KeyError: 'db' from laptop-ana (2 files): already known
result     laptop-sol: the fix from laptop-ana for KeyError: 'db' worked there
rejected   the signature does not match: forged or altered message
rejected   the patch is not safe: Path '../../etc/passwd' is outside the project
```

```text
1 suggestion from the team for 1 error, newest first

2026-10-30 10:52  from laptop-ana
error      KeyError: 'db'
files      app/cfg.py, app/net.py
evidence   python -m pytest
results    worked on 2 nodes
```

- **Every message is untrusted.** In order: the size (`MAX_SHARE_BYTES`), the HMAC signature, the schema, that the evidence shows the tests failing before the patch, and that the patch follows the same rules as any other (paths inside the project, protected folders, size limits). A message that fails any check is refused and not stored.
- **Several fixes per error, each kept once** (ingestion is idempotent). A fix is identified by its error signature and its edits, so the same patch again changes nothing, while a different patch for the same error is kept beside it: what fails in one project may be what another needs. An error keeps at most `TEAM_FIXES_PER_ERROR` (5), the best ranked, in the order `healer run` tries them: a new fix makes room by dropping the one ranked last (`kept; the one ranked last for this error made room`), and one that ranks below all of them is ignored. A new fix has no results yet, so among fixes nobody tried the newest wins, and replaying old messages cannot push out newer fixes. A node ignores its own messages. Each suggestion keeps its origin node and times.
- **Results from other nodes.** They arrive on their own topic, `MQTT_TOPIC` plus `/results`, so nodes from before results never receive one. Each is signed and checked like a fix, and counts once per node and fix, its latest: `result     laptop-sol: the fix from laptop-ana for KeyError: 'db' worked there`. A result about a fix this node does not keep is ignored, so results cannot fill the disk. They are kept in a SQLite file (`RESULTS_PATH`, `/data/healer/results.sqlite3`).
- **Nothing is lost while the node is off.** With a fixed `NODE_ID`, the broker keeps what is published after the node's first connection and delivers it on the next one. Without `NODE_ID`, the name changes when the container is recreated, and a warning says so.
- **It stays connected.** Lost connections are retried with growing delays (up to 60 seconds). A broker that refuses the node ends the command with exit code `2`. `Ctrl+C` or `docker stop` shut it down cleanly.
- Run only one listener per `NODE_ID`: the broker allows one connection per name.
- Suggestions live in a Chroma collection of this node, shared by all its workspaces.

## Using the team's fixes

Between the local cache and Claude, `healer run` looks for what the team already solved. A fix that works here costs **zero tokens**. Each suggestion is tested in the test sandbox, and nothing is written to your files until you accept it:

```text
team       suggestion from laptop-ana, received 2026-11-04 12:28
fixes      KeyError: 'db' (same signature)
proposal   Default the database name and tolerate an address without a port.
sandbox    testing it in the test sandbox, with no network, secrets or files

attempt 1  suggestion from laptop-ana, 0 tokens
patch      2 files (+3 -2)
result     the tests pass

verdict    fixed: the tests pass here with the fix from laptop-ana
spent      1 attempt, 0 tokens in, 0 out; saved about 2040 tokens
```

- **Tested in the sandbox, at once.** Testing a suggestion executes code written on another machine. In the test sandbox that code has no network, no secrets and no access to your files, so it runs without asking. The sandbox cannot tell a fair fix from one that only fools the tests, so read the diff before applying it.
- **Without the sandbox, it asks first.** When the tests run in this container (`SANDBOX_SOCKET` unset, as outside Docker), every edit is shown first and the question warns that the code will run with your access: `test this suggestion on a copy? Its code will run on this machine with your access [y/N]`. Without an answer, as in a script, nothing from the team runs.
- **What counts as a match.** Every suggestion with the same error signature first, then those for an error of the same type whose summary is almost identical (`TEAM_MAX_DISTANCE`, 0.1). At most `TEAM_MAX_SUGGESTIONS` (3) are offered per run, one after another until one passes. The signature ignores concrete values, so check the error shown next to `fixes`.
- **The fixes that worked most go first.** The suggestions for the same error are ordered by the share of nodes where each one worked, counting one vote for and one against from the start so that a single result never decides alone; then by how many nodes it worked on; then the newest. A fix nobody tried ranks above one that failed once and below one that worked once. Each suggestion shows its record before it is tested: `results    worked on 2 nodes, failed on 1 node`.
- **Each result is told to the team.** When a suggestion's tests ran, this node records whether it worked here and, with sharing on, tells the team: `result     the fix from laptop-eva failed here; the team was told`. A result names the fix and its error, never code, a project or a path, and is signed with the team key. A suggestion that did not apply to this code, or that was declined, tells nothing. With sharing off, the result only counts on this node.
- **Applying is a separate decision.** The fix is shown again as a diff and needs its own yes. `--apply` does not cover fixes from the team; `--no-apply` does.
- **A suggestion that fails is not recommended,** and the next source is tried. It stays stored, because it may fit another project of this node. Declining one has the same effect.
- **Team text never reaches Claude.** Failed suggestions are left out of what the model sees.
- A fix from the team is not stored in the local cache or shared again. `healer savings` counts it under `from team`, with the tokens the sending node reported, capped by `REPAIR_MAX_TOKENS`.
- `--no-team` skips the team for one run.

## The panel: see everything in the browser

Open **http://localhost:8765**. `./start.sh` starts the panel with the rest of the services. It shows what this computer knows, refreshes every 5 seconds, and follows the light or dark mode of the system, or the one you pick.

| Tab | What it shows |
| --- | --- |
| **Overview** | The same checks as `healer doctor`, plus the node name, the team listener and the outbox. Beside them, the recent activity, newest first: incidents, fixes received or waiting, and what the listener saw, with each new event lit up for a moment. Then the savings: tokens saved and spent, a chart per day split by the source that solved each incident (local cache, team or Claude), and how many incidents each source solved. Each chart can be shown as a table. |
| **Incidents** | The last 50 incidents: when, which project, the error, what solved it and what it cost. **Show**, under Steps, opens the run step by step: the failing tests, each attempt with its source, its change and what the tests said, how it ended, and whether the fix was applied and shared. |
| **Projects** | For each project: how many symbols its index has, its incidents, and the fixes it keeps, each with its explanation, its evidence and the change, line by line. |
| **Team** | The suggestions received from other nodes, with the node that sent them, the change, and the nodes where it worked or failed. An error with several fixes says in which order `healer run` tries them. |
| **Outbox** | The fixes waiting to be sent, with their attempts and the last error. The Outbox card also counts the results waiting. |

Each tab has its own address, such as http://localhost:8765/#incidents, so it can be bookmarked.

- **It only reads.** It has no button that changes anything: fixes are still accepted, sent or discarded with the commands. Its container gets the code and the data volume read only, and never sees the workspace.
- **Only this computer can open it.** The port is published on `127.0.0.1` only, and the panel answers only to the names `localhost`, `127.0.0.1` and `[::1]`. A web page from another site that makes its own name point to `127.0.0.1` (DNS rebinding) gets a refusal. Each page may run only its own scripts and ask only this server for data.
- **A part that cannot be read does not hide the rest.** If, for example, the ledger is corrupt, the panel says so above the tabs and shows everything else.
- **Each run, step by step.** `healer run` keeps the steps of each incident in a SQLite file (`HISTORY_PATH`, `/data/healer/history.sqlite3`): the error and where it comes from, each attempt (local cache, team or Claude) with its explanation, its edits and the errors its tests showed, the verdict, and whether the fix was applied and shared. Everything is redacted before it is stored, texts are cut to 2,000 characters, and an attempt's edits to 20,000. The raw output of the tests is never kept. Only the latest `HISTORY_MAX_RUNS` (200) are kept. Nothing of it leaves the machine: the panel asks for one run, at `/api/runs/<id>`, only when it is opened. Runs from before this history have no steps.
- **The team listener reports itself.** `healer listen` writes its connection, what it received, ignored and rejected, and a sign of life every 30 seconds to `LISTENER_STATUS_PATH`. A listener silent for 90 seconds is shown as not answering.
- **Without `NODE_ID`,** each container takes its own name, so the panel warns about it.
- To use another port, set `PANEL_PORT` in `.env` and run `./stop.sh && ./start.sh`.

To watch the panel change, `./panel.sh demo` makes real events happen one at a time and shows them on a demo panel at http://localhost:8766: a teammate shares a fix, a second teammate shares a different fix for the same bug, a forged message is refused, `healer run` tries the newest fix, which fails here, fixes the bug with the other one and tells the team both results (its steps open from the Incidents tab), a third teammate tells that a fix worked there, the broker goes down and a fix waits in the outbox, the broker comes back and the listener sends it, and the local cache fixes a second bug. It waits for Enter between events. The demo has its own data volume and its own topic (`MQTT_TOPIC` plus `/demo`), so it never touches your data, your workspace or your team, and it never asks Claude, so it costs no tokens. It does stop the broker for a moment. `./panel.sh demo clean` removes everything it created.

The pages are written with Next.js, TypeScript, Tailwind and shadcn, in `panel/`. The image builds them into static files, and `healer panel` serves them together with the data at `/api/snapshot`, so Node never runs in the containers.

## Redaction: hide secrets before anything leaves the machine

`healer redact` prints a file (or stdin) with every credential replaced by a `[REDACTED:<kind>]` placeholder, and reports on stderr how many were hidden. The file itself is never modified. The same redactor runs on everything sent to Claude or published to other nodes.

```bash
docker compose exec cli healer redact settings.env
docker compose exec cli sh -c 'python main.py 2>&1 | healer redact'
```

```text
DB_PASSWORD=[REDACTED:secret_assignment]
DATABASE_URL=postgres://admin:[REDACTED:url_credentials]@db:5432/app
ANTHROPIC_API_KEY=[REDACTED:anthropic_key]
redacted 3 secrets: anthropic_key 1, secret_assignment 1, url_credentials 1
```

The policy hides too much rather than too little, because a leaked credential cannot be taken back. It applies, in order:

1. **Known formats:** private keys, Anthropic, OpenAI, AWS, GitHub, Slack, Stripe and Google API keys, and JWTs.
2. **Credentials in context:** passwords inside URLs (`scheme://user:password@host`) and `Authorization` / `Bearer` headers. The user, host and header name stay visible.
3. **Secret-looking names:** any value assigned to a name containing `password`, `secret`, `token`, `api_key`, `credential` and similar, quoted or not (`.env`, YAML, Python, JSON, `--flag=value`). Code that only refers to a secret (`os.environ[...]`, `settings.api_key`, `None`, type annotations, function calls) is kept.
4. **Random-looking strings:** 24+ characters with letters and digits, a long unbroken run and high entropy. Identifiers, paths and UUIDs are kept; hashes such as Git SHAs are hidden.

The counts never include the values. Redacting already redacted text changes nothing.

## Development

The development tools run inside the container (the host does not need Python packages), as your own user, so their caches in the project are yours. The container starts in `/workspace`, so point it at the project in `/app` with `-w /app`:

```bash
docker compose exec -w /app cli ruff format src tests
docker compose exec -w /app cli ruff check src tests
docker compose exec -w /app cli mypy                         # strict
docker compose exec -w /app cli pytest --cov                 # fails below 75% coverage
docker compose exec -w /app cli pytest -m "not integration"      # fast unit tests only
```

Tests use a fake embedding function and a fake model, so they never download anything or call the real API. Architectural decisions are recorded in `docs/adr/`.

### Continuous integration

`.github/workflows/ci.yml` runs on every pull request and every push to `main`. It builds the images and starts the services with `./start.sh`, and then runs these checks inside the cli container, against the real broker and sandbox:

1. `requirements.lock` matches `pyproject.toml`.
2. `ruff check` and `ruff format --check`.
3. `mypy`.
4. The whole test suite with coverage: at least 75% overall, and 85% for `domain` and `services`.
5. `./deps.sh audit`.
6. The checks of the panel, `./panel.sh check`: format, lint, types, tests and build.
7. `./panel.sh audit`.

If a step fails, the job also prints the logs of the services. It needs no secrets, because the tests never call the real API. The result appears in each pull request as the check **Build, lint, types, tests and audit**.

The workflow does not stop a merge by itself. To merge only when the check is green, the owner of the repository adds a rule on GitHub, once:

1. **Settings → Rules → Rulesets → New ruleset → New branch ruleset.**
2. Give it a name, set **Enforcement status** to **Active**, and under **Target branches** choose **Add target → Include default branch**.
3. Tick **Require status checks to pass**, then **Add checks** and pick **Build, lint, types, tests and audit**. It is listed only after the workflow has run at least once.
4. Tick **Require a pull request before merging**. With a single developer, leave **Required approvals** at 0: GitHub does not let the author of a pull request approve it.
5. **Create.**

### Pre-commit checks

`./start.sh` turns on `.githooks/pre-commit`, a check that runs before every commit. It uses the tools of the cli container, so the host needs nothing else, but the container must be running. If the commit has Python files, it runs:

- `ruff check` and `ruff format --check` on those files;
- `mypy` on the whole project, since types cross files;
- the fast tests (`-m "not integration"`), because a change can break a test in another file, and the test files of the commit.

```text
[pre-commit] ruff on 1 file(s)...
[pre-commit] mypy...
[pre-commit] fast tests...
432 passed, 1 skipped, 79 deselected in 7.96s
[pre-commit] All checks passed.
```

If the commit has files of `panel/`, it also runs `./panel.sh quick`: format, lint, types and tests, in a Node container.

It takes about 12 seconds for Python files. A commit with no Python and no panel files, such as one with only documentation, is not checked. If a check fails, the commit is not made and its output says why.

- **To skip it once:** `git commit --no-verify`.
- **To turn it off:** `git config core.hooksPath .git/hooks`. `./start.sh` only turns it on when `core.hooksPath` is not set, so it stays off.

### Dependencies

`pyproject.toml` says which packages the project needs and which versions it accepts. `requirements.lock` pins every package the images install, 145 in all, each with the hashes of its files, and is generated from `pyproject.toml` with `uv`. The images install only from the lock: `pip` checks every file against its hashes and refuses the build if one does not match, so a changed or replaced package cannot get in. The base image is pinned too, to `python:3.11.18-slim-trixie`.

```bash
./deps.sh lock      # after editing pyproject.toml: pin what it needs, keeping the other versions
./deps.sh upgrade   # move every package to the newest version pyproject.toml allows
./deps.sh audit     # check the locked versions against the known vulnerabilities
```

```text
No known vulnerabilities found, 5 ignored
```

- **After `lock` or `upgrade`,** review `git diff requirements.lock`, rebuild with `./stop.sh && ./start.sh` and run the tests. A version that changes on its own no longer happens: the images change only when the lock does.
- **The audit uses `pip-audit`** and fails on any known vulnerability, except those listed in `audit-exceptions.txt`, each with the reason it does not apply. Today these are the four vulnerabilities of chromadb 1.5.9, which has no fixed version yet: all of them are in Chroma's HTTP server, and this project uses Chroma only embedded, with no server. Remove an entry as soon as a fixed version is locked.
- The lock is universal: it also has the packages and hashes for other platforms, such as Apple Silicon, so the images build the same way there.
- The system packages of the image (`git`, `build-essential`) still come from Debian's repositories without pinned versions.

### The panel's code

The panel's tools run in a Node container (`node:26.13.0-trixie-slim`, the same image that builds the panel), as your own user, so the host does not need Node.

```bash
./panel.sh dev      # a panel that reloads on every change, at http://localhost:3000, with real data
./panel.sh check    # format, lint, types, tests and build, as CI runs them
./panel.sh format   # rewrite the files of panel/ in the project style
./panel.sh audit    # check the locked packages against the known vulnerabilities
./panel.sh demo     # real events, one at a time, on a demo panel at http://localhost:8766
./panel.sh npm ...  # any npm command, such as: ./panel.sh npm install --save-exact NAME
```

- **The packages come from `panel/package-lock.json`,** which pins each one with its hash. `npm ci` installs exactly that, with install scripts off, both here and in the image. The audit fails on any known vulnerability.
- **The CSS of shadcn is copied into `panel/src/styles/shadcn.css`,** so its command line, which brought vulnerable packages, is not a dependency. To add a component, run `./panel.sh npm exec --yes shadcn@4.23.0 add NAME`.
- **The types of `panel/src/lib/types.ts` mirror `PanelSnapshot`** (`src/healer/domain/panel.py`). The sample `panel/src/lib/fixtures/snapshot.json` is checked by a Python test against the model, so a field changed on one side and not the other makes a test fail.
- After changing `panel/`, run `./stop.sh && ./start.sh` so http://localhost:8765 serves the new build.

## The Chroma database

Chroma is used in **embedded mode**: it is a Python library running inside the `healer` process, not a separate server, so it has no container or port. Its data lives in `/data/chroma` inside the container, backed by the `chroma-data` Docker volume, so it survives restarts.

It holds three kinds of collections:

| Collection | What it holds | Scope |
| --- | --- | --- |
| `code-<hash>` | The functions, classes and methods of a project | One per workspace |
| `solutions-<hash>` | The fixes verified in a project (the local cache) | One per workspace |
| `team-<hash>` | The suggestions received from other nodes | One per node and topic |

Because it is not a server, you interact with it from Python, running inside the container. The following command stores a record and then reads it back. It uses a collection called `demo`, so it does not touch real data:

```bash
docker compose exec -T cli python - <<'EOF'
import os
import chromadb

client = chromadb.PersistentClient(path=os.environ["CHROMA_PATH"])
col = client.get_or_create_collection("demo")

# Save
col.upsert(
    ids=["fix-001"],
    documents=["KeyError in config['db']: fixed by using config.get('db')"],
    metadatas=[{"file": "app/config.py", "error": "KeyError"}],
)

# Inspect
print("count:", col.count())                                     # number of records
print("peek:", col.peek())                                       # first records
print("filter:", col.get(where={"error": "KeyError"}))           # filter by metadata
print("search:", col.query(query_texts=["config read failure"], n_results=1))  # semantic search
EOF
```

If it prints the record you saved, the database is working. Notes:

- The first time you save a document, Chroma downloads an embedding model (about 80 MB), so it needs internet access. The model is kept in the `chroma-models` volume, so recreating the container does not download it again.
- `query` searches by **meaning**, not by exact text. This is what the system uses to find similar past fixes.
- You can also open an interactive session with `docker compose exec cli python`. If you paste code into it, make sure no line starts with a space, or Python will raise an `IndentationError`.
- Avoid writing to the Chroma files from your own processes while the application is running.
- To delete the demo data, run `./stop.sh --clean` (this removes all data) or call `client.delete_collection("demo")`.
