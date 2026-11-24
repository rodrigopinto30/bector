# 0029. Exceptions in the audit of the npm packages

## Context

The CI audits the locked packages of the panel and of the desktop app (`./panel.sh audit`, `./desktop.sh audit`), and fails on any known vulnerability. On 2026-11-18 a new advisory appeared, `picomatch` (`GHSA-fc5q-39xc-vjpf`, ReDoS through repeated extglobs, versions up to 4.0.7), and the audit of both apps began to fail, with 16 vulnerable packages in the panel and 11 in the desktop app, all through that one advisory. The latest published version of `picomatch` is 4.0.7 and is affected, so there is nothing to upgrade to, and the fix that `npm audit` suggests is to downgrade `vitest` to 3.1.4, which breaks the tests. A red audit that cannot be fixed hides the next one that can.

The Python audit already has a way out: `audit-exceptions.txt`, each entry with the reason it does not apply. `npm audit` has no such list.

## Decision

- **A filter over the report of `npm audit --json`,** in `scripts/npm-audit.mjs`, that both scripts run in their Node container. The shipped packages come first: `npm audit --omit=dev` must have no advisory at all, and nothing can be excepted there, so an exception can never hide a vulnerability in what people run. Then the full audit, development tools included, may have only the advisories listed in the app's `audit-exceptions.txt`. In both apps today, the audit of what ships finds nothing: `picomatch` is only in the test runner, the linter and the bundler.
- **One list per app,** `panel/audit-exceptions.txt` and `desktop/audit-exceptions.txt`, in the format of the Python one: the id, then the reason. A line without a reason, an id twice or a line that is not an id makes the audit fail. The two lists are separate because each app has its own lock, and an advisory can be in one and not in the other.
- **An advisory is judged, not a package.** A package that is vulnerable only because it depends on a vulnerable one is not an advisory of its own: the 16 packages of the panel are one advisory.
- **A stale exception is pointed out and does not fail.** When a fixed version is locked, the audit says that the entry is no longer reported and should be removed. It does not fail, so that a list can be cleaned in its own time.
- **An audit that could not run never passes.** A report with an error, no report, or without a list of vulnerabilities fails.
- **The filter's tests run before every audit,** with Node's own test runner and fixtures in the shape of the report that npm 11 prints, because the filter is the gate: if it were wrong, the audit would be green for the wrong reason.

## Consequences

- The audit of both apps is green again, and any other new advisory, in what ships or in the tools, still turns it red.
- Someone who adds an exception must write why, in the file, where it is reviewed with the change.
- `picomatch` has to be looked at again: when a version after 4.0.7 is published, upgrade the lock and remove the line.
- Update, 2026-11-24: `npm audit` stopped reporting `picomatch`, with the latest published version still 4.0.7 and the lock unchanged, and the audit pointed out both exceptions as no longer reported, as designed. They were removed, and the lists have no entry today.
- Measured with the real `npm audit` and the real lists, in the Node image the apps use:
  - Both audits passed with the exception, and said which advisory was excepted and why.
  - With an empty list, the audit of the desktop app failed with the advisory, its title and its link. With a list that named another advisory, it failed the same way and pointed out that the other one was no longer reported. With an exception without a reason, it failed and said so.
  - With a project that ships `lodash@4.17.15` (a lock made on purpose, in a scratch folder) and every one of its advisories in the list, the audit still failed, five advisories, because they were in what ships.
  - The filter's 12 tests pass.
- Not measured: a run in the CI itself, which is the first thing to look at on the pull request.
