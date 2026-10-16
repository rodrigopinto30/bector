# 0008. Local solution cache and token savings ledger

## Context

The plan's guiding principle is that the LLM is the last resort (6.1): a node that already solved an error should not pay for it again. It also asks for a ledger of tokens spent and avoided from the first working pipeline (6.3), because reuse is the main assumption behind the shared network of Phase 5 and must be measured. Phase 4 ended with a loop that fixes errors with Claude but forgets every fix.

## Decision

- **Exact match on the error signature.** The Diagnoser's signature (ADR 0002) already ignores paths, line numbers and values, so the same bug matches across runs and entry points. Semantic matching with a similarity threshold is left for solutions received from other nodes in Phase 5.
- **Solutions live in Chroma behind a `SolutionRepository` port,** one collection per workspace, with the signature as the record id: storing a new fix for the same error replaces the old one, so saving is idempotent (7.6). The error summary is embedded so Phase 5 can search by meaning. Each record keeps the evidence 6.2 requires: the command, its exit code before the patch, and that the tests passed after it.
- **The cache is a node of the graph.** `recall` runs after `reproduce`: it replays the stored patch on a fresh copy, at zero tokens. A pass ends the run. A failure or a patch that no longer fits discards the solution and continues to `propose`, with the replay included as an earlier attempt. Replays do not count toward the model attempt limit. `remember` stores a fix found by the model. Storage errors are logged and never hide the fix.
- **The ledger is a SQLite file behind a `Ledger` port,** with one row per incident: time (UTC), project, signature, error type, final status, resolution level (`cache`, `model`, `none`), model attempts, token usage and tokens saved. SQLite is in the standard library, is append-friendly and makes per-day and per-project summaries simple. It lives in its own Docker volume (`healer-data`). Runs where nothing was attempted are not recorded.
- **Metrics only.** Ledger rows never contain code, logs or error messages, as 6.3 requires.
- **Tokens saved is an estimate:** the tokens the reused solution cost when the model found it.
- `healer run --no-cache` skips the replay; `healer savings` prints the summary per day or per project.

## Consequences

- A repeated error costs one test run and no tokens. A changed codebase costs one wasted replay before the model is asked.
- Matching is exact: a slightly different error (another message shape or call path) is not reused yet.
- A stored solution that matches but fixes the tests for the wrong reason would be replayed; it is still only recommended, never applied.
- The ledger grows by one small row per incident; there is no rotation yet.
