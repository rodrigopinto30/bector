# 0017. Locked and audited dependencies

## Context

Section 7.5 of the plan asks for dependencies pinned through a lock file and audited with `pip-audit`, and for pinned base images. `pyproject.toml` only had minimum versions and the image used `python:3.11-slim`, so every rebuild could install different versions. That already happened: paho-mqtt moved from 2.1 to 2.2 on a rebuild and changed its types, which broke mypy. An audit of the installed packages also found known vulnerabilities in the pip and setuptools of the base image, and four in chromadb.

## Decision

- **`requirements.lock` pins every package with its hashes.** It is generated from `pyproject.toml`, including the `dev` extra, by `uv pip compile --universal --python-version 3.11 --generate-hashes`. `pip-compile` from pip-tools produced the same pins and hashes, 120 packages before the tools below were added, but took 922 seconds against 2. The lock is universal, so it also covers other platforms, such as Apple Silicon, through markers.
- **The image installs only from the lock.** `pip install --require-hashes --no-deps` checks every file against its hashes and resolves nothing. The project itself is then installed with `--no-deps --no-build-isolation`, so not even the build backend is fetched unpinned: `hatchling` and `editables` are in the lock. `wheel`, which the base image brings and nothing uses, is removed, so what is installed is exactly what is locked.
- **pip and setuptools come from the lock as well,** at versions that fix the vulnerabilities of the ones in the base image.
- **The base image is pinned** to `python:3.11.18-slim-trixie`, a Python version and a Debian release.
- **`./deps.sh` runs the locking tools in the cli container:** `lock` after editing `pyproject.toml`, keeping the versions already locked; `upgrade` to move everything to the newest allowed version; and `audit`.
- **The audit fails on any known vulnerability not listed in `audit-exceptions.txt`.** It runs `pip-audit` on the lock with `--require-hashes --disable-pip`, so it checks exactly the locked versions without installing anything. Every exception has a reason. The four chromadb 1.5.9 vulnerabilities (GHSA-f4j7-r4q5-qw2c, GHSA-36p7-vc44-83pf, GHSA-2wm9-hf6c-p5cr, GHSA-xph7-9rjv-w5fr) have no fix. They are all in Chroma's HTTP server, its endpoints, tenants and authorization providers, and this project uses Chroma only embedded, through `PersistentClient`, with no server.

## Consequences

- Rebuilding the images gives the same packages until the lock changes, and the change can be reviewed in `git diff requirements.lock`.
- A package that does not match its hash stops the build. Measured: a lock with a wrong hash for idna made `pip` refuse the file and save nothing.
- Updating is deliberate: `./deps.sh upgrade`, then a rebuild and the tests.
- The audit needs the network, because `pip-audit` asks the vulnerability database.
- The exceptions must be reviewed on every upgrade, and an entry removed once a fixed chromadb is locked.
- The system packages of the image, `git` and `build-essential`, still come from Debian without pinned versions.
- `uv`, `pip-audit`, `hatchling` and `editables` are now part of the image, through the `dev` extra.
