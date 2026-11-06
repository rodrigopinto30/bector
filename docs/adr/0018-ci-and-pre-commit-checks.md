# 0018. Continuous integration and pre-commit checks

## Context

Section 7.7 of the plan asks for CI on every pull request: `ruff`, `mypy`, `pytest` with coverage, `pip-audit` and a Docker build. Section 7.3 asks for pre-commit hooks that run ruff, mypy and the tests on the changed files, and 7.4 asks for 85% coverage on `domain` and `services`, 75% overall, with CI failing below that. Until now every check was run by hand before each pull request, and the 85% was never checked.

## Decision

- **One GitHub Actions job that runs the same commands as a developer's machine.** `.github/workflows/ci.yml` runs on every pull request and every push to `main`. It starts the stack with `./start.sh`, which also checks the Docker build, and runs every check inside the cli container, against the real broker and the real sandbox. Python is not set up on the runner: the image is the environment, so CI checks exactly what developers run.
- **The checks, in order:**
  - the lock is current: `./deps.sh lock` followed by `git diff --exit-code requirements.lock`;
  - `ruff check` and `ruff format --check`;
  - `mypy`;
  - `pytest` with coverage, which fails below 75% (`pyproject.toml`);
  - `coverage report` on `domain` and `services`, which fails below 85%;
  - `./deps.sh audit`.

  If a step fails, the job prints the last logs of the services.
- **Minimal permissions.**
  - The job only reads the repository (`contents: read`).
  - The checkout does not keep the token (`persist-credentials: false`).
  - `actions/checkout` is pinned to the commit of v7.0.1, like the Python dependencies (ADR 0017), and the runner to `ubuntu-24.04`.
  - A new push cancels the older run of the same branch.
  - A run stops after 30 minutes.
- **No secrets.** The tests use a fake model and a fake embedding function, so `ANTHROPIC_API_KEY` is not needed.
- **The pre-commit hook is a plain script, `.githooks/pre-commit`, not the `pre-commit` framework.** The framework downloads its hooks and their environments from the internet on first use, outside the lock, and runs the tools on the host, which does not have them. The script uses the tools already locked in the cli container. `./start.sh` turns it on with `git config core.hooksPath .githooks`, unless `core.hooksPath` is already set.
- **What the hook checks.** It runs only when the commit has Python files:
  - ruff, lint and format, on those files;
  - mypy on the whole project, since types cross files;
  - the fast tests (`-m "not integration"`), because a change in one file can break a test of another;
  - the test files of the commit, integration tests included.

  On success it prints only the summary line of the tests; on failure, all of it.
- **If the cli container is not running, the hook stops the commit** and says how to start it, instead of letting the commit through unchecked. `git commit --no-verify` skips it once.

## Consequences

- A change that breaks lint, format, types, tests, coverage, the lock or the audit gets a red check in its pull request. Measured on a fresh clone, running each step of the workflow in order:
  - all steps passed;
  - `./start.sh` took 63 s, mypy 13 s and the tests 124 s;
  - the tests gave 504 passed and 8 skipped (7 that need TLS turned on, 1 that needs root);
  - coverage was 95.4% overall and 99% on `domain` and `services`.
- The workflow does not block a merge by itself. A ruleset on GitHub must require its check. The plan's "at least one review" cannot be met by a single developer, because GitHub does not let the author approve their own pull request. With one developer, only the check is required.
- The audit runs on every pull request, so a vulnerability published for a locked package makes a pull request fail even if it did not touch the dependencies. This is on purpose: someone has to decide between an upgrade and an exception.
- The hook takes about 12 seconds and needs the stack running for any commit with Python files. Measured:
  - a lint error, a format error, a type error and a failing test each stopped the commit;
  - a commit with only documentation was not checked;
  - with the container stopped, the commit stopped with the message.
- To turn the hook off for good, `git config core.hooksPath .git/hooks`; `./start.sh` leaves a set value alone.
