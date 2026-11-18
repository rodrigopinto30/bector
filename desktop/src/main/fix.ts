import type { FixKind, FixResult, FixStatus, StackStatus } from "../shared/api"
import type { Run } from "./process"

/** Where the patch that was found is saved: the data volume, never the user's project. */
export const PATCH_FILE = "/data/healer/fix.json"

/** Holds the process id of the run in the container, so that it can be stopped from outside. */
const PID_FILE = "/data/healer/fix.pid"

/**
 * Records the id of the shell, then becomes the program, so the id is the program's own. Docker
 * does not stop what runs in a container when the client that started it goes away, so the app
 * stops it from inside, with the signal of Ctrl+C.
 */
const WRAPPER = 'echo $$ > "$1"; shift; exec "$@"'

const TEST_TIMEOUT_MS = 15 * 60_000
const FIND_TIMEOUT_MS = 45 * 60_000
const CANCEL_TRIES = 10
const CANCEL_WAIT_MS = 300
const LINE_LIMIT = 2_000

/** What the user is asked before Claude may be asked for patches. */
export interface Question {
  title: string
  message: string
  detail: string
  ok: string
}

export interface FixDeps {
  root: string
  run: Run
  /** The services, as they are now. */
  stack(): Promise<StackStatus>
  /** Asks in a dialog of the app; true only if the user accepts. */
  confirm(question: Question): Promise<boolean>
  sleep(ms: number): Promise<void>
  onLine(line: string): void
}

/** The text of an Anthropic key, which must never reach the page. */
const KEY_SHAPE = /sk-ant-[A-Za-z0-9_-]{8,}/g

export function clean(line: string): string {
  return line.replace(KEY_SHAPE, "[hidden]").slice(0, LINE_LIMIT)
}

interface Current {
  kind: FixKind
  cancelled: boolean
  started: boolean
  /** The test sandbox could not be freed after the run was stopped. */
  sandboxBusy: boolean
}

/**
 * Runs the tests of the project, and looks for a fix, with the system's own commands in the
 * cli container. Nothing here writes to the project: the fix is only saved, in the data volume,
 * and applying it is a different step. Looking for a fix may spend tokens, so it asks first, in
 * a dialog of the app that the page cannot answer.
 */
export class Fixer {
  private current: Current | null = null

  constructor(private readonly deps: FixDeps) {}

  status(): FixStatus {
    return { running: this.current?.kind ?? null }
  }

  test(): Promise<FixResult> {
    return this.go("test")
  }

  find(): Promise<FixResult> {
    return this.go("fix")
  }

  /** Stops what is running. It does nothing when nothing runs. */
  async cancel(): Promise<void> {
    const current = this.current
    if (current === null || current.cancelled) {
      return
    }
    current.cancelled = true
    if (current.started) {
      await this.stopInside(current)
    }
  }

  private async go(kind: FixKind): Promise<FixResult> {
    if (this.current !== null) {
      return refused(kind, "Something is already running. Wait for it, or stop it.")
    }
    const current: Current = { kind, cancelled: false, started: false, sandboxBusy: false }
    this.current = current
    try {
      return await this.perform(current)
    } finally {
      this.current = null
    }
  }

  private async perform(current: Current): Promise<FixResult> {
    const { kind } = current
    const stack = await this.deps.stack()
    if (!stack.running) {
      return refused(kind, "The services are stopped. Start them in Settings first.")
    }
    if (stack.workspaceMatches === false) {
      return refused(
        kind,
        "The services work on another folder than the one chosen. Restart them in Settings first.",
      )
    }
    if (kind === "fix" && stack.keySeen === true) {
      const accepted = await this.deps.confirm({
        title: "Look for a fix",
        message: "Look for a fix for the tests of your project?",
        detail:
          "The system may ask Claude for patches with your Anthropic key, which spends tokens, up to the limits in its settings. " +
          "A fix already stored on this computer, or one from your team, is tried first and costs nothing. " +
          "Your files are not changed: the fix is only saved.",
        ok: "Look for a fix",
      })
      if (!accepted) {
        return { kind, outcome: "declined", message: "Nothing was run." }
      }
    }
    if (current.cancelled) {
      return cancelled(kind, false)
    }
    return kind === "test" ? this.runTests(current) : this.runFix(current)
  }

  private async runTests(current: Current): Promise<FixResult> {
    const outcome = await this.inside(current, ["healer", "test"], TEST_TIMEOUT_MS)
    if (current.cancelled) {
      return cancelled("test", current.sandboxBusy)
    }
    if (outcome.failed !== "") {
      return failedToRun("test", outcome.failed, outcome.error)
    }
    switch (outcome.code) {
      case 0:
        return {
          kind: "test",
          outcome: "passed",
          message: "Every test passed, on a copy of your project.",
        }
      case 1:
        return {
          kind: "test",
          outcome: "failed",
          message: "The output below says which ones, and why.",
        }
      default:
        return failedToRun("test", "", outcome.error)
    }
  }

  private async runFix(current: Current): Promise<FixResult> {
    await this.quiet(["rm", "-f", PATCH_FILE])
    const outcome = await this.inside(
      current,
      ["healer", "run", "--no-apply", "--save", PATCH_FILE],
      FIND_TIMEOUT_MS,
    )
    if (current.cancelled) {
      return cancelled("fix", current.sandboxBusy)
    }
    if (outcome.failed !== "") {
      return failedToRun("fix", outcome.failed, outcome.error)
    }
    if (outcome.code === 0) {
      const saved = await this.quiet(["test", "-s", PATCH_FILE])
      return saved.code === 0
        ? {
            kind: "fix",
            outcome: "fixed",
            message:
              "The tests pass with it on a copy of your project. Your files were not changed: the fix is only saved.",
          }
        : {
            kind: "fix",
            outcome: "nothing",
            message: "The tests already pass.",
          }
    }
    if (outcome.code === 1) {
      return {
        kind: "fix",
        outcome: "unfixed",
        message: "The output below says why. Your files were not changed.",
      }
    }
    return failedToRun("fix", "", outcome.error)
  }

  /** Runs a command of the system in the cli container, line by line. */
  private async inside(current: Current, command: string[], timeoutMs: number) {
    let error = ""
    current.started = true
    const result = await this.deps.run(
      "docker",
      [
        "compose",
        "exec",
        "-T",
        "-e",
        "PYTHONUNBUFFERED=1",
        "cli",
        "sh",
        "-c",
        WRAPPER,
        "sh",
        PID_FILE,
        ...command,
      ],
      {
        cwd: this.deps.root,
        timeoutMs,
        onLine: (line) => {
          const shown = clean(line)
          if (shown.startsWith("error:")) {
            error = shown.slice("error:".length).trim()
          }
          this.deps.onLine(shown)
        },
      },
    )
    const stopped = current.cancelled || result.failed === "timeout"
    if (result.failed === "timeout") {
      await this.stopInside(current)
    }
    current.started = false
    await this.quiet(["rm", "-f", PID_FILE])
    if (stopped) {
      current.sandboxBusy = !(await this.freeSandbox())
    }
    return { code: result.code, failed: result.failed, error }
  }

  /**
   * The test sandbox goes on with the copy it was running until the tests end or hit their own
   * limit, and answers one request at a time, so the next run would wait for it. Restarting it
   * ends what it was running at once. It keeps nothing else.
   */
  private async freeSandbox(): Promise<boolean> {
    const restarted = await this.deps.run("docker", ["compose", "restart", "-t", "2", "sandbox"], {
      cwd: this.deps.root,
      timeoutMs: 60_000,
    })
    return restarted.code === 0
  }

  /** Asks the program in the container to end. The run may not have written its id yet. */
  private async stopInside(current: Current): Promise<void> {
    for (let tried = 0; tried < CANCEL_TRIES; tried += 1) {
      const sent = await this.quiet(["sh", "-c", 'kill -INT "$(cat "$1")"', "sh", PID_FILE])
      if (sent.code === 0 || !current.started) {
        return
      }
      await this.deps.sleep(CANCEL_WAIT_MS)
    }
  }

  private quiet(command: string[]) {
    return this.deps.run("docker", ["compose", "exec", "-T", "cli", ...command], {
      cwd: this.deps.root,
      timeoutMs: 30_000,
    })
  }
}

function refused(kind: FixKind, message: string): FixResult {
  return { kind, outcome: "refused", message }
}

function cancelled(kind: FixKind, sandboxBusy: boolean): FixResult {
  const said =
    kind === "fix"
      ? "Your files were not changed. Tokens already used by Claude are not returned."
      : "Your files were not changed."
  const busy = sandboxBusy
    ? " The test sandbox could not be freed and may stay busy for a few minutes."
    : ""
  return { kind, outcome: "cancelled", message: said + busy }
}

/**
 * What the system says about the key is advice for someone who edits .env by hand. In the app
 * the key is kept in the secret store and reaches the services when they restart.
 */
const KEY_ADVICE: ReadonlyArray<readonly [RegExp, string]> = [
  [
    /ANTHROPIC_API_KEY is not set/,
    "There is no Anthropic key in the services, and no fix stored here or from your team fits. Save a key in Settings and restart the services.",
  ],
  [
    /rejected the API key/,
    "Anthropic does not accept the key the services have. Replace it in Settings and restart the services.",
  ],
]

export function explain(error: string): string {
  return KEY_ADVICE.find(([shape]) => shape.test(error))?.[1] ?? error
}

function failedToRun(kind: FixKind, failed: string, error: string): FixResult {
  const what = kind === "test" ? "The tests could not be run" : "The search for a fix could not run"
  const why =
    failed === "timeout"
      ? "It took too long, so it was stopped."
      : failed === "ENOENT"
        ? "Docker could not be run."
        : error !== ""
          ? explain(error)
          : "The output below may say why."
  return { kind, outcome: "error", message: `${what}: ${why}` }
}
