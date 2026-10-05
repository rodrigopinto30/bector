# 0002. Error diagnosis and normalized signature

## Context

Phase 3 turns raw output into a structured error specification. Two consumers depend on it: the remediation pipeline (Phase 4), which needs the place to fix, and the team knowledge base (Phase 5, section 6.1 of the plan), which needs to recognise the same bug across machines without exchanging code.

## Decision

- **Input is text, not a command.** `healer diagnose` reads a log file or stdin. Running commands belongs to the test runner of Phase 4, which needs an allowlist and timeouts. Logs over 5 MB keep their tail, where the failure is.
- **One parser per language behind `TraceParser`.** `PythonTraceParser` handles CPython tracebacks (chained exceptions, syntax errors without a header) and pytest long, short, native and collection-error reports. Fixtures are real outputs captured from Python 3.11 and pytest 9.
- **Origin is the innermost workspace frame.** Frames are mapped to workspace files by the longest suffix of the printed path that exists in the workspace, so traces from CI or another laptop still resolve. Site-packages, standard-library and `<frozen>` frames are always external. `..` segments and symlinks that leave the workspace are rejected.
- **Signature basis:** error type, normalized message, and the frames from the origin inward, as `workspace_path:function` (or a portable library path). Quoted values, numbers, paths and memory addresses become placeholders. Line numbers are never part of it.
- **Callers above the origin are excluded.** The same bug reached from a script and from a test keeps one signature. Library frames below the origin are included, because they describe how the failure happened.
- **Repeated errors are grouped** by signature with an occurrence count.
- **The diagnosis carries the enclosing symbol** from the index (narrowest range containing the origin line), so the later phases get the surgical context without another lookup.

## Consequences

- Two different bugs in the same function with the same exception type and message shape share a signature. This favours reuse; validation in Phase 4 (section 6.2) guards against applying a wrong match.
- Renaming the function or moving it to another file changes the signature.
- A workspace file whose path suffix matches an unrelated external file (for example a root-level `utils.py`) can be taken as the origin. Library and standard-library paths are excluded to keep this rare.
- The enclosing symbol is only as fresh as the index; it is looked up by line number, so `healer index` should run after the code changes.
- Exception groups (`ExceptionGroup`, Python 3.11) are not parsed yet.
- Messages are not redacted here. Redaction of secrets happens before anything is sent to Claude or published (Phases 4 and 5).
