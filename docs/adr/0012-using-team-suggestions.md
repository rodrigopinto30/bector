# 0012. Using team suggestions before asking the model

## Context

ADR 0011 stores what other nodes share, but nothing uses it. Section 6.1 of the plan puts team knowledge between the local cache and the LLM, with a minimum similarity below which a match is discarded. Section 6.2 requires the receiving node to re-validate a solution locally before recommending it. Sections 6.2 and 7.5 say solutions are never applied automatically, and that content from the network reaches Claude only as data.

Re-validating means running tests with someone else's patch applied, so what those tests can reach was measured in the container. They run as root, can read the parent process's environment through `/proc` (including `ANTHROPIC_API_KEY` and `SHARE_KEY`), and can write to `/workspace` and the data volumes. The clean environment of `SubprocessRunner` keeps credentials out of reach of ordinary code, not of code that looks for them. The isolated copy protects the real files from a wrong patch. It does not contain a hostile one. With a leaked team key, automatic validation would give anyone code execution on every node.

## Decision

- **Order:** local cache, then the team, then Claude, as a `consult_team` node between `recall` and `propose`.
- **Nothing from the team runs without approval.** `healer run` shows each suggestion (origin, error, explanation and every edit) and asks before testing it. No answer, as in a script, means no. This adds a human step to the re-validation of 6.2. It can be relaxed once tests run in a real sandbox: another user, no network and no access to the workspace or the data volumes.
- **A match needs the same signature, or the same error type and a cosine distance of at most `TEAM_MAX_DISTANCE` (0.1).** Measured with the real embeddings, distance alone does not separate errors well: `KeyError: 'db'` is 0.15 from `ConnectionError: could not connect to db`, and two unrelated assertions are also 0.15, while `KeyError: 'db'` and `KeyError: 'database'` are 0.06. An identical summary with another stack trace is 0, and that is the case worth catching. Signature matches ignore concrete values, so `KeyError: 'db'` can match `KeyError: 'cache'` at the same place. The prompt therefore always shows the suggestion's own error and the kind of match. At most `TEAM_MAX_SUGGESTIONS` (3) are offered per run.
- **`--apply` does not cover a fix from the team.** Approving a test run is not approving a write, so the patch is confirmed again. `--no-apply` still works.
- **A suggestion that fails is skipped, not deleted.** Suggestions belong to the node and serve all its workspaces, so a failure here says nothing about another project. It also keeps the listener as the only writer of the collection (ADR 0011). The cost is one more question and test run the next time, and only if the error is still unsolved.
- **Team text never reaches the model.** This is stricter than 7.5: failed suggestions are left out of the attempts shown to Claude, so a poisoned patch or explanation has no path to the prompt.
- **A team fix is not stored in the local cache or shared again.** Storing it would hide its origin behind the local tier, and the team already has it. The ledger records `team` as the resolution each time it is used.
- **Numbers and text from other nodes are not trusted.** Tokens saved are capped by `REPAIR_MAX_TOKENS`, because the sender's figure cannot be verified and a huge value would overflow SQLite. Diffs and patches are printed with control characters masked, because the developer decides from that text and a patch must not be able to hide lines from it. Only a real newline ends a line.

## Consequences

- Fixes known to the team cost zero tokens, and `healer savings` shows them in their own column.
- A human must read a patch to catch hostile code, and a plausible patch can still fool a reader. Approving a suggestion trusts its author as much as running a teammate's branch. The real fix is the sandbox described above, on top of the non-root container in 7.5.
- Runs without a terminal never use team suggestions.
- Each `healer run` also opens the team collection for reading while the listener may be writing to it. This is the multi-process use accepted as a known risk in ADR 0011.
- The savings for a team fix are an estimate reported by the sending node, within the cap.
