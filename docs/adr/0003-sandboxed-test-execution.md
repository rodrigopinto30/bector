# 0003. Sandboxed test execution

## Context

Phase 4 validates patches by running the project's tests. Section 7.5 of the plan requires that patches are applied only to an isolated copy, that commands run without a shell from an allowlist, and that they have a timeout and an output limit. The command may later come from configuration shared across nodes or be suggested by the LLM, so it is untrusted.

## Decision

- **Isolation by temporary copy, not Git worktree.** The workspace is copied into a private (`0700`) directory created with `mkdtemp`, and deleted after the run. A worktree only contains committed content, but the failure being fixed usually lives in uncommitted changes, and the workspace may not be a Git repository at all.
- **What is copied.** Regular files are copied; symlinks are recreated as links and never followed, so no file outside the workspace is pulled into the copy. Vendor and cache folders (the same list the indexer skips) are left out. Workspaces over 500 MB are refused.
- **Allowlist by argv prefix.** The command is split with `shlex` and must start with an allowed entry (`pytest`, `python -m pytest` by default). It runs with `subprocess.Popen` and no shell. Arguments with absolute paths, Windows drives, `~` or `..` are rejected. The command is validated before the copy is made.
- **Limits.** The process runs in its own session; on timeout the whole process group is killed, so child processes cannot outlive it. Output (stdout and stderr merged) is read in a background thread and only its last 1 MB is kept, where pytest prints the failures. Stdin is closed.
- **Minimal environment.** Only `PATH`, `HOME`, locale, `TZ` and `TERM` are passed, plus `PYTHONDONTWRITEBYTECODE=1`. Secrets in the container environment never reach the code under test.
- **Diagnosis reuse.** A failed or timed-out run is passed to the Diagnoser; paths inside the copy resolve to workspace files by their suffix.

## Consequences

- The copy costs time and disk proportional to the workspace size. Acceptable for typical repositories; a copy-on-write or incremental strategy can come later if needed.
- Tests run as the container user (currently root, see 7.10) with full access to the container. The sandbox protects the developer's files, not the container; stronger isolation would need a separate container or user namespace.
- Projects that rely on extra environment variables (for example a database URL) fail in the sandbox until an explicit, reviewed allowlist of variables is added.
- Dependencies of the project under test must be installed in the `healer` image.
