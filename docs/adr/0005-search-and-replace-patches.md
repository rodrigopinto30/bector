# 0005. Patches as search-and-replace edits applied to the sandbox

## Context

Phase 4 asks Claude for a fix and validates it by running the tests. The fix has to be expressed in a format the model produces reliably, that Healer can check for safety (plan 7.5: paths inside the workspace, capped size and number of files), and that can be shown to the developer before anything is applied.

## Decision

- **Search-and-replace edits instead of unified diffs.** Each edit names a file, the exact text to replace and its replacement; an empty search text creates a file. Language models frequently get line numbers and diff context wrong, while quoting an existing snippet is something they do well. The search text must be unique in the file.
- **Tolerance for trailing whitespace only.** If the exact text is missing, a line-by-line match that ignores trailing whitespace is accepted when it is unique. Indentation and content must match, so a fuzzy match never lands in the wrong place.
- **Validation before anything happens.** `PatchPolicy` (domain) rejects unsafe paths (absolute, `..`, `~`, drives, `.git`/`.hg`/`.svn`/`.healer`), non-text content, whitespace-only search text, too many files and too many bytes. This runs before the sandbox is created.
- **All or nothing.** `FilesystemPatcher` reads the affected files, applies every edit in memory, checks the changed-lines limit, and only then writes. A conflict in the last edit leaves every file untouched. Writing through symlinks, into directories or into non-UTF-8 files is refused.
- **Applied to the sandbox, never to the workspace.** `SandboxRunner.run_tests(command, patch)` validates the command and the patch, copies the workspace, applies the patch to the copy, runs the tests and deletes the copy. The report includes the unified diff for review.
- **Manual entry point.** `healer patch` takes a JSON patch so the whole path can be exercised before the LLM exists.

## Consequences

- The model has to quote code exactly; a slightly wrong quote is a conflict that the pipeline can report back to the model for a retry.
- Deleting files and renaming are not supported yet.
- Patches only apply to UTF-8 text files.
- Applying an accepted patch to the real workspace is a separate, explicit step that comes with the recommendation in Phase 5.
