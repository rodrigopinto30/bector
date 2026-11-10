# 0021. Several fixes per error

## Context

ADR 0011 kept one suggestion per error signature, as section 7.6 of the plan asks: a solution's id was a hash of its signature. A newer valid fix replaced the earlier one, even when the earlier one suited another project better, and the README listed that as a known limitation. The signature ignores concrete values on purpose, so one signature can stand for errors that need different fixes in different projects. Section 6.4 of the plan proposes ranking fixes by how often reusing them worked; that needs several fixes per error to rank, so keeping them is its first part.

## Decision

- **A fix is identified by its error signature and its edits.** `SharedSolution.fix_id` is a SHA-256 of the canonical JSON of both. The node, the times, the explanation and the tokens do not count, so the same fix sent by two nodes, or sent again, is still one. This replaces the rule of 7.6 that the id is a hash of the signature; ingestion stays idempotent.
- **An error keeps several fixes, up to `TEAM_FIXES_PER_ERROR` (5).** A limit is needed because every message can be a new fix: without it, a leaked team key could fill an error with any number of them.
  - The most recently found are kept. A newer fix for a full error makes room by dropping the oldest (`REPLACED`).
  - A fix found earlier than every fix kept, or at the same moment as the oldest, is ignored (`OLDER`). So replaying old signed messages still cannot push out newer fixes, the guarantee ADR 0011 gave.
  - A lower limit takes effect on the next fix kept for that error.
- **The policy lives in `SolutionIngester`; the store only stores.** `TeamSolutionRepository` lists the fixes of one signature (`for_signature`), saves and removes one fix, and searches by meaning while leaving out one signature (`similar(..., other_than=...)`).
- **Records saved before this keep working.** Their id is the signature, but every record also keeps its signature in its metadata, and the store finds and removes fixes through it, comparing the edits. No migration is needed.
- **`healer run` offers every fix of the same signature first, the most recently found first,** and then fixes for a nearly identical error, as before. The search by meaning leaves out the same signature with a Chroma filter, so those fixes do not use up its candidates. The repair loop already tested suggestions one after another until one passed, so it did not change.
- **What people see:**
  - `healer listen` prints `received ..., kept; the oldest suggestion for this error made room` and `ignored ...: older than every suggestion kept for this error`.
  - `healer team` says how many errors the suggestions cover.
  - In the panel, each Team card is keyed by its fix id, so two fixes for one error are two cards, and each says how many fixes its error has.
  - `./panel.sh demo` has a new event: a second teammate shares a different fix for the same bug, which the demo project's tests reject.
- **The outbox keeps one fix per error, unchanged.** Changing it means a new table and a migration of each node's SQLite file, and the panel reads that file read only. The loss is narrow: two different fixes for one error found by this node while the broker is down. It is listed as a known limitation.

## Consequences

- A fix that fails in one project no longer hides another that works there. A suggestion that fails is still only skipped, and the next one is tried.
- Each error stores up to five fixes, but a run offers at most `TEAM_MAX_SUGGESTIONS` (3), so the oldest kept may never be offered while newer ones exist. They are ordered by the time they were found, not by how well they work; counting which ones work, and ranking by it, is the second part of 6.4.
- Every suggestion needs its own yes when the tests run outside the sandbox, so several fixes for one error can mean several questions.
- Measured with `./panel.sh demo`: two teammates shared different fixes for the same `KeyError`, and `healer team` showed `2 suggestions from the team for 1 error`. `healer run` tried laptop-eva's fix first, the newest, which failed with `AssertionError: assert None == 'postgres'`. It then applied laptop-ana's, which passed: 2 attempts and 0 tokens.
