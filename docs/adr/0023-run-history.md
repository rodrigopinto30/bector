# 0023. Keeping each run step by step

## Context

The panel of ADR 0020 shows each incident as one row of the ledger: what solved it and what it cost. To understand a run, its terminal output was the only record: the error found, each patch tried and why it failed, and the fix applied. Once the terminal was closed, all of it was lost. The ledger cannot hold it: section 6.3 of the plan keeps it to metrics, never code or logs. Stage 2 of the panel asked for the steps of each run.

## Decision

- **A separate history, local and redacted.** `healer run` keeps a `RunRecord` for each run the ledger counts, in a SQLite file of its own, `HISTORY_PATH`. The ledger stays metrics only. A record holds:
  - the error, where it comes from in the project, and how the first run of the tests ended;
  - each attempt: its source (local cache, team or Claude) and origin node, the model, tokens in, out and saved, the explanation, its edits, and what its tests said, or why the patch did not run;
  - the verdict;
  - whether the fix was applied, what happened when it was shared, and how many results of team fixes were told.
- **Everything is redacted first.** The command, the error, every explanation and problem, the errors of each test run, and the old and new text of each edit go through the same `Redactor` as everything that leaves the machine. Nothing of the history is ever sent: it is read only by the panel.
- **Bounded.** Texts are cut to 2,000 characters, and the errors of a test run to the first 5, each to 300. The edits of one attempt are kept whole up to 20,000 characters; beyond that, only the first ones are kept and the record says so. The raw output of the tests is never kept. Only the latest `HISTORY_MAX_RUNS` runs (200) are kept, the oldest dropped on each write.
- **A run and its incident find each other.** The pipeline now returns its project and the moment it wrote the ledger (`RepairResult.recorded_at`). The id of a run is a hash of both, so the panel knows which incidents have their steps without changing the ledger. The record is written by the CLI once the run ends, because applying and sharing happen there.
- **The steps are fetched only when opened.** The snapshot, fetched every 5 seconds, only says which incidents have steps. `GET /api/runs/<id>` gives one run, with each edit as removed and added lines.
  - It follows the panel's rules: localhost only, GET and HEAD only, no caching.
  - An id that is not 16 lowercase hex characters is refused before anything is read.
  - A history that cannot be read answers 500 with the reason, and the rest of the panel goes on.
- **In the panel,** the Incidents tab has a Steps column. **Show** opens the run under its row:
  - the failing tests;
  - each attempt, with an icon and a word for its outcome and its change on demand;
  - how it ended;
  - afterwards: applied or not, sent, waiting or not shared again.
- **The demo** points to it at event 5.

## Consequences

- Each incident can be understood later, in the browser, including the attempts that failed and why.
- The history holds code: the edits of every attempt, redacted. It stays on the machine, in the data volume, like the local cache, and the panel's container reads it read only.
- Up to 200 runs of up to a few attempts each: in the worst case some tens of megabytes, usually far less.
- Runs from before this decision, and runs whose record could not be written, show no steps. A record that cannot be written prints a warning and changes nothing else.
- Measured with `./panel.sh demo`:
  - The run of event 5 opened in the panel with the failing tests, laptop-eva's attempt, which failed with `AssertionError: assert None == 'postgres'`, and laptop-ana's, which passed, each with its change. It ended with `Fixed by attempt 2`, applied, not sent again, and 2 results told.
  - The run of event 10 showed the local cache's attempt, applied and sent to the team.
