# Local-First Autonomous Code Remediation System

A local, modular CLI system that diagnoses, fixes, and tests code errors while spending as few tokens as possible. Fixes are never applied without the developer's approval.

> **Status:** early development. Only Phase 1 (environment and base CLI) is done. See [`project.md`](project.md) for the full plan.

## What problem does it solve?

- **Wasted tokens.** Asking an AI to fix a bug usually means sending it a lot of code. This system sends only the small piece of context that matters.
- **Repeated work.** When one developer solves a problem, the rest of the team often solves it again. Validated solutions are shared between workstations over MQTT and stored in each machine's local vector database, so the same problem is solved once.
- **Loss of control.** Every fix is tested locally and then recommended. The developer decides whether to adopt it.

At this stage the scope is **Python projects only**.

## How it works

```text
CLI / Logs -> Healer -> Cartographer & Chroma -> LangGraph -> Test Runner -> User
```

1. **Healer** parses the failure into a structured diagnosis.
2. **Cartographer** (Tree-sitter + Chroma) retrieves the minimal relevant code.
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
docker compose exec healer pytest          # run the test suite
```

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
- To delete the demo data, run `./.stop.sh --clean` (this removes all data) or call `client.delete_collection("demo")`.
