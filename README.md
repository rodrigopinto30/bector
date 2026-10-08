# Local-First Autonomous Code Remediation System

A local, modular CLI system that diagnoses, fixes, and tests code errors while spending as few tokens as possible. Fixes are never applied without the developer's approval.

## What problem does it solve?

- **Wasted tokens.** Asking an AI to fix a bug usually means sending it a lot of code. This system sends only the small piece of context that matters.
- **Repeated work.** When one developer solves a problem, the rest of the team often solves it again. Validated solutions are shared between workstations over MQTT and stored in each machine's local vector database, so the same problem is solved once.
- **Loss of control.** Every fix is tested locally and then recommended. The developer decides whether to adopt it.

At this stage the scope is **Python projects only**.

## How it works

```text
CLI / Logs -> Diagnoser -> CodeIndex & Chroma -> LangGraph -> Test Runner -> User
```

1. **Diagnoser** parses the failure into a structured diagnosis with a machine-independent signature.
2. **CodeIndex** (Tree-sitter + Chroma) retrieves the minimal relevant code.
3. **LangGraph** orchestrates the loop: it proposes a patch, applies it in an isolated copy, and runs the tests. If they fail, it retries.
4. The validated fix is **recommended** to the developer.
5. If accepted, it is **published over MQTT** and indexed by the other nodes.

## Requirements

- Linux (the start script can install Docker for you) or any system with Docker and Docker Compose already installed.
- Port `1883` free on your machine (used by the MQTT broker).

## Getting started

```bash
./start.sh
```

The script will:

1. Check that Docker and the Compose plugin are available, and install them if they are missing (Linux only, may ask for `sudo`).
2. Create `.env` from `.env.example` if it does not exist.
3. Build the image and start the services.

Then check the setup:

```bash
docker compose exec healer healer doctor
```

To stop everything:

```bash
./stop.sh            # stop the services, keep the data
./stop.sh --clean    # stop the services and delete all data (Chroma and Mosquitto)
```

> If Docker was just installed by the script, your user may not be in the `docker` group until you open a new session. Until then, use `sudo docker ...`.

### Services

| Service | Container | Purpose |
| --- | --- | --- |
| `healer` | `healer-app` | The application and CLI. |
| `mosquitto` | `healer-mosquitto` | MQTT broker used to share solutions between machines (used from Phase 5). |

The project you want to analyze goes in the `workspace/` folder, which is mounted at `/workspace` inside the container. To use another folder, set `WORKSPACE_PATH` in `.env`.

### Verify that everything works

```bash
docker compose ps                          # both containers should be "Up"
docker compose exec healer healer doctor   # the dependency checks should be [ok]
docker compose exec -w /app healer pytest  # run the test suite
```

## CodeIndex: map and search your code

CodeIndex parses every Python file with Tree-sitter, extracts modules, classes, functions and methods (signature, docstring, decorators, line range, source), and stores them in Chroma. Run the commands inside the container:

```bash
docker compose exec healer healer index              # index /workspace (only changed files are re-parsed)
docker compose exec healer healer search "read the db settings"   # find code by meaning
docker compose exec healer healer map app/config.py  # signatures and line ranges of one file
```

- `index` makes the index mirror the workspace: new and changed files are parsed, deleted files are removed. A second run with no changes parses nothing.
- Files with syntax errors are still indexed with every definition that could be recognised, because broken code is exactly what the Diagnoser points at.
- Each workspace has its own Chroma collection, so two projects with the same relative paths never mix.
- Symlinks that resolve outside the workspace are skipped, as are files over 1 MB, non-UTF-8 files, and folders such as `.git`, `.venv` and `node_modules`.
- `search` prints the cosine distance first (lower is more similar), then `file:lines` and the signature.

## Diagnoser: turn a failure into a diagnosis

`healer diagnose` reads raw output (a log file, or stdin) and prints one diagnosis per distinct error:

```bash
docker compose exec healer sh -c 'python main.py 2>&1 | healer diagnose'
docker compose exec healer sh -c 'python -m pytest 2>&1 | healer diagnose'
docker compose exec healer healer diagnose error.log --json
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
- `--json` prints the full diagnosis (frames, origin, enclosing symbol with its source, signature and its basis), the contract the later phases consume.

## Sandbox runner: run the tests on an isolated copy

`healer test` copies the workspace to a private temporary directory, runs the test command there, diagnoses the failures and deletes the copy. Your files are never modified.

```bash
docker compose exec healer healer test                                   # runs TEST_COMMAND (python -m pytest)
docker compose exec healer healer test "python -m pytest tests/test_app.py" --timeout 60
docker compose exec healer healer test --output                          # also print the captured output
docker compose exec healer healer test --json
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
- **Clean environment:** the tests only see `PATH`, `HOME`, locale and terminal variables. Credentials such as `ANTHROPIC_API_KEY` never reach them.
- **What is copied:** files and symlinks (as links, never followed). `.git`, virtual environments, `node_modules`, build output and caches are skipped.
- **Exit codes:** `0` when the tests pass, `1` when they fail or time out, `2` when the command is not allowed or cannot start.
- Prefer `python -m pytest` over `pytest`: it adds the project root to the import path, so `from app import ...` works without extra configuration.

## Patches: apply a change to the copy and test it

`healer patch` applies a patch to an isolated copy of the workspace, prints the diff, and runs the tests against it. It answers "does this change fix the tests?" without touching your files. In Phase 4 the patches will come from Claude; for now they are written by hand.

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
docker compose exec healer healer patch fix.json
docker compose exec healer healer patch fix.json "python -m pytest tests/test_app.py" --json
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

## Redaction: hide secrets before anything leaves the machine

`healer redact` prints a file (or stdin) with every credential replaced by a `[REDACTED:<kind>]` placeholder, and reports on stderr how many were hidden. The file itself is never modified. The same redactor runs on every diagnosis before it is sent to Claude (Phase 4) or published to other nodes (Phase 5).

```bash
docker compose exec healer healer redact settings.env
docker compose exec healer sh -c 'python main.py 2>&1 | healer redact'
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

The development tools run inside the container (the host does not need Python packages). The container starts in `/workspace`, so point it at the project in `/app` with `-w /app`:

```bash
docker compose exec -w /app healer ruff format src tests
docker compose exec -w /app healer ruff check src tests
docker compose exec -w /app healer mypy                      # strict
docker compose exec -w /app healer pytest --cov              # fails below 75% coverage
docker compose exec -w /app healer pytest -m "not integration"   # fast unit tests only
```

If the container was created before the dev tools were added to the image, run `./start.sh` again to rebuild it.

Tests use a fake embedding function, so they never download a model. Architectural decisions are recorded in `docs/adr/`.

## The Chroma database

Chroma is used in **embedded mode**: it is a Python library running inside the `healer` process, not a separate server, so it has no container or port. Its data lives in `/data/chroma` inside the container, backed by the `chroma-data` Docker volume, so it survives restarts.

Because it is not a server, you interact with it from Python, running inside the container. The following command stores a record and then reads it back. It uses a collection called `demo`, so it does not touch real data:

```bash
docker compose exec -T healer python - <<'EOF'
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

- The first time you save a document, Chroma downloads an embedding model (about 80 MB), so it needs internet access.
- `query` searches by **meaning**, not by exact text. This is what the system uses to find similar past fixes.
- You can also open an interactive session with `docker compose exec healer python`. If you paste code into it, make sure no line starts with a space, or Python will raise an `IndentationError`.
- Avoid opening the Chroma files from another process while the application is running.
- To delete the demo data, run `./stop.sh --clean` (this removes all data) or call `client.delete_collection("demo")`.
