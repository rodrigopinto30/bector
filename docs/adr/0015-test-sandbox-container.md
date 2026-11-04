# 0015. A separate container for the tests

## Context

Every patch is tested by running the project's tests, and that executes the patch's code. ADR 0003 isolated the files with a temporary copy and the credentials with a clean environment, but the tests still ran in the `cli` container, as root, next to the `.env` file, the workspace, the data volumes and the network. Measured with a hostile patch, its code read the parent process's environment, including `ANTHROPIC_API_KEY`. It also read `/app/.env`, connected to the broker and to the internet, and wrote to the real workspace. ADR 0012 made team suggestions wait for approval for that reason, and section 7.5 of the plan asks for containers without root, privileges or unneeded mounts.

## Decision

- **A `sandbox` service runs every test command.** The `cli` container writes the copy to a volume shared with it, and sends the command over a Unix socket in that volume. Each request is one JSON line, and each answer is one JSON line with the result. `SandboxServiceRunner` implements `CommandRunner` on the `cli` side. Without `SANDBOX_SOCKET`, as outside Docker, the tests run locally as before.
- **The container reaches nothing it does not need.** It has no network, no `.env` and no workspace, data or code mounts, only the copies volume. Its root filesystem is read-only, with a `tmpfs` for `/tmp`. It runs under `init`, with `no-new-privileges`.
- **A run has a ceiling for everything it can consume.** The container gets at most 512 processes, `SANDBOX_CPUS` cores and `SANDBOX_MEMORY` of memory. The copies volume is a `tmpfs` capped at `SANDBOX_DISK`, so a run cannot fill the host's disk: measured with a 64 MB cap, a run that tried to write 200 MB stopped at 63 MB, and the host's disk did not change. Docker keeps an existing volume as it was created, so `stop.sh` deletes this one, which holds nothing worth keeping, and a new size applies on the next start. It has a fixed name for that reason.
- **Its output cannot rewrite the terminal.** What the tests print reaches the developer through `--output` and the diagnoses, so control characters are masked there, as they already were for text from other nodes.
- **The server keeps only four capabilities: `CHOWN`, `SETUID`, `SETGID` and `KILL`.** It runs each command as user 10001 with a fixed environment, and Python's user site is disabled.
- **A run cannot reach the server or the other copies.** The socket's folder belongs to root with mode 700, so the code under test can neither connect to the socket nor replace it with a fake server that reports passing tests. The `cli` container creates each copy for root only, and the server hands it to user 10001 only when its run starts, so a run cannot change copies waiting for theirs.
- **Nothing outlives a run.** When the command ends, the server kills every process of the container except itself and init, before it collects the output, and empties `/tmp` as user 10001. A background process can therefore neither keep running nor plant files that a later run would load.
- **Nothing the server receives is trusted.** Requests have a size limit, a strict schema, and limits on arguments, time and output. The working folder must be a real folder directly inside the copies folder, with a copy's name. The client checks the size before sending, and errors come back as messages, not as a dead server.
- **The image's code, not the project.** Mounting the project into the sandbox would expose `.env`, so the sandbox uses the code baked into its image, and `./start.sh` rebuilds it.
- **In the sandbox, team suggestions are tested without asking.** ADR 0012 required approval because their code could reach everything the cli container can. In the sandbox it cannot, so each suggestion is tested at once, as section 6.2 of the plan asks, and `healer run` says where it ran. Writing the fix to the workspace still needs its own yes, and `--apply` still does not cover it. Without the sandbox, ADR 0012 applies unchanged: every edit is shown and nothing runs without a yes.

## Consequences

- Measured with the same hostile patch, the code under test runs as user 10001. It cannot read other processes' environments, reach the network, open `.env`, write to the workspace, the code or the system, or open the socket or a waiting copy. What it starts in the background is killed, and what it leaves in `/tmp` is deleted.
- The sandbox does not judge the code: a patch can still make the tests pass without fixing anything, so the diff must still be read before applying it.
- A hostile suggestion can still use the sandbox up to its limits until the time limit, at most `TEAM_MAX_SUGGESTIONS` times per run, and runs without a terminal now test suggestions too.
- The tests run with the Python packages of the image, as before. A project that needs more must have them installed in the image.
- Each run adds a few milliseconds for the socket and the hand-over of the copy.
- The copies take memory instead of disk, up to `SANDBOX_DISK`. A project larger than that cannot be tested until it is raised; it should stay above `MAX_SANDBOX_BYTES`.
- The `cli` container still runs as root, and so does the sandbox's server, with only four capabilities. Non-root users for both are the next step.
- Changes to the code reach the sandbox only after `./start.sh` rebuilds its image.
