# 0007. Self-correction loop as a LangGraph state graph

## Context

The earlier steps can diagnose an error, ask Claude for a patch and test that patch on an isolated copy, but only once. A first patch often fixes the reported error and uncovers another one, or does not fit the code. The plan (Phase 4, 7.6) asks for an automated loop orchestrated with LangGraph, with a maximum number of attempts and a token budget per incident, that stops and reports when either is reached.

## Decision

- **A small state graph in `pipeline/`:** `reproduce` runs the tests without a patch and picks the target error, `propose` asks Claude, `test` applies the patch on a copy and runs the tests. Conditional edges return to `propose` until a patch passes or a limit is reached. The state is a typed dictionary of immutable records; each node returns only what it changes. Services and adapters are injected, so the graph is tested with fakes.
- **Start from the reproduced failure,** not from a log. The baseline run gives a reliable diagnosis, checks the command against the allowlist before any token is spent, and records that the tests failed before the patch, which shared solutions will need later (6.2).
- **Every attempt is a complete patch against the original code.** Patches are not stacked: stacking would make each attempt depend on the earlier ones being right and would invalidate the code snippets Claude was shown. Claude is told this explicitly.
- **Feedback instead of a bigger prompt.** A retry adds the earlier patches with their results (up to three remaining errors with their location, the end of the output when nothing was diagnosed, or why the patch could not be applied) and the functions where the last patch broke, only in files it did not change, because line numbers in changed files no longer match the index. All of it is redacted.
- **Success means the whole test command passes.** A patch that fixes the targeted error but breaks another test is not a fix.
- **Limits:** `REPAIR_MAX_ATTEMPTS` (default 3) and `REPAIR_MAX_TOKENS` (default 100,000). The cost of a call is unknown until it ends, so before each new attempt the loop assumes it will cost as much as the last one and stops if that would exceed the budget. Rejected proposals, such as those containing a redaction marker, still count their tokens.
- **Cheap failures:** a patch identical to an earlier attempt is not tested again, and a patch that does not fit is reported back without running the tests. Model and infrastructure errors stop the loop and keep the attempts already made.
- **Recommendation only.** The working patch is printed (and optionally saved); applying it to the real workspace stays with the developer and is planned for Phase 5.

## Consequences

- The default limits cap a run at about three Claude calls; with Sonnet 5.5 a small error costs a few cents, and the stable system prompt is served from the prompt cache on retries.
- Each attempt runs the test command once more, so slow test suites make the loop slow; a narrower command helps.
- Context from the failing frames of files the last patch changed is not added; Claude still sees its own patch for those files.
- The loop does not check whether the error was already solved; the local cache (6.1) and the token ledger (6.3) will build on the attempt records.
