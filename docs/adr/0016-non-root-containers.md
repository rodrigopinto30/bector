# 0016. Containers that run as the developer

## Context

Section 7.5 of the plan asks for containers that run as a non-root user, with `no-new-privileges`, and section 7.10 lists the root `cli` container as a gap. ADR 0015 moved the tests to a sandbox, but `cli` and `listener` still ran as root, next to `.env` and the developer's files. Running as root had visible effects too. 56 tool caches in the project and the workspace (`.ruff_cache`, `.mypy_cache`, `.pytest_cache`, `.coverage`) were owned by root, and so were the data volumes. Writing to the workspace needed code that gave new files the owner of their folder.

## Decision

- **The image has a `healer` user with the developer's uid and gid.** They are build arguments, `HOST_UID` and `HOST_GID`, which `start.sh` writes to `.env` the first time; under `sudo` it uses the calling user's ids. What the containers write to the mounted project therefore belongs to the developer. `HOME` is `/home/healer`, and the embedding model's volume moved there.
- **`cli` and `listener` run as that user, with no capabilities and `no-new-privileges`.**
- **The data volumes are created for that user.** The image creates their folders owned by `healer`, so new volumes start with that owner. For existing ones, `start.sh` runs a one-off root container, with only `CHOWN` and `DAC_READ_SEARCH`, that gives the developer what earlier versions left owned by root: the data volumes, the model and the known tool caches in the project and the workspace. It only touches what is not already the developer's.
- **The sandbox's server keeps root, with six capabilities.** It must run each command as another user, which needs `SETUID` and `SETGID`, and kill what a run left behind, which needs `KILL`. A non-root server could not do either, and running the tests as its own user would let them reach the socket. The copies now belong to the developer, so the server also has `DAC_READ_SEARCH` and `FOWNER` to read them and change their mode, besides `CHOWN`. With `SETUID` it could already become the copy's owner, so these give it no new reach. The tests themselves still run as user 10001, with none of them.
- **The socket and the copies folder are shared with the developer's group.** The server learns the cli container's uid and gid from `SANDBOX_CLIENT_UID` and `SANDBOX_CLIENT_GID`. The socket's folder is `root:<gid>` with mode 750, and the socket 660, so the cli container can connect and user 10001 cannot. The copies folder is mode 771, so user 10001 can only pass through it to its own copy.
- **Each copy is handed over and back.** It stays readable only by the developer until its run. Then the server gives it to user 10001, and after the run, even when the run fails, gives it back, so the cli container can delete it.
- **Maintenance containers ask for what they need.** `broker.sh` makes certificates in a root container of the `cli` image with only `CHOWN`, `FOWNER` and `DAC_OVERRIDE`, because it writes the broker's files.

## Consequences

- No process with access to `.env`, the workspace or the network runs as root.
- Running as a normal user exposed a bug that root had hidden. Removing a copy with a read-only folder changed the mode of the entry instead of its folder, so the removal failed. It also followed links: removing a copy with a link to a file outside changed that file's mode. Removal now gives this user access to the folder and never follows links.
- `doctor` says which user the command runs as.
- Changing `HOST_UID` or `HOST_GID` needs `./stop.sh && ./start.sh`, which rebuilds the images and gives the volumes to the new user.
- If the developer's uid is 0, the containers still run as root, and `start.sh` warns about it.
- Mosquitto is unchanged: its image already drops to the `mosquitto` user.
