# 0009. Applying a verified fix to the real workspace

## Context

Section 3.5 of the plan ends the pipeline with a recommendation: the system shows the validated changes and the developer decides whether to adopt them. Section 7.5 allows only the developer to apply a patch to the real workspace (Human-in-the-Loop). Until now, `healer run` printed the working patch and stopped; applying it was a manual copy. Section 4.2 also makes acceptance the trigger for sharing a solution, which the next part of Phase 5 needs.

## Decision

- **Ask after the test, never before.** `healer run` shows the fix as a colored unified diff and asks `apply this patch ...? [y/N]`. The default is no, and closed input (no terminal) also means no. `--apply` and `--no-apply` skip the question. `healer apply FILE` tests a saved patch on a fresh copy and asks only if the tests pass; `--yes` skips the question.
- **A single writer.** `FixInstaller` (`services/fix_installer.py`) is the only code that writes the developer's files. It applies the `PatchPolicy` again and refuses a patch whose files differ from the tested one.
- **Nothing changed since the test.** The applied patch on the copy now records the SHA-256 of each file it touched (`AppliedPatch.checksums`). The installer passes them to the `PatchApplier` as `expected`, and nothing is written unless every file still has that content. This closes the gap between the test and the answer, which can be minutes.
- **All or nothing on disk.** `FilesystemPatcher` already computed every edit in memory first. It now also restores the files it wrote, and removes the folders it created, if a later write fails.
- **Ownership.** The container runs as root on a bind-mounted workspace. Existing files are rewritten in place, keeping owner and permissions; new files and folders get the owner of their parent folder (best effort).
- **No undo command.** The diff is printed before the answer, and projects are expected to be under version control.

## Consequences

- The plan's flow is complete: diagnose, fix on a copy, recommend, and apply on acceptance. Phase 5 can publish a solution at that point.
- Only the touched files are checked. Changes to other files after the test are not detected by `healer run`; `healer apply` covers this by testing again.
- Files are rewritten with the line endings Python normalizes on read, as on the copy.
- The `PatchApplier` port gained an optional `expected` argument; implementations must honour it.
