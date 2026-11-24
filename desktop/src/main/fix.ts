import type { FixKind, FixResult, FixReviewResult, FixStatus, StackStatus } from "../shared/api"
import { DEFAULT_OPTIONS, type FixOptions } from "../shared/options"
import { filesOf, patchId, readPatch } from "./patch"
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
const APPLY_TIMEOUT_MS = 15 * 60_000
const CANCEL_TRIES = 10
const CANCEL_WAIT_MS = 300
const LINE_LIMIT = 2_000

/** What the user is asked before Claude may be asked for patches, or a fix is applied. */
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
  /** The folder of this computer that is the project, to say where a fix would be written. */
  project(): Promise<string>
  /** Asks in a dialog of the app; true only if the user accepts. */
  confirm(question: Question): Promise<boolean>
  sleep(ms: number): Promise<void>
  onLine(line: string): void
}

/**
 * How the system numbers the errors that the tests show: ``[1] AssertionError: ...``. Only the
 * numbers that follow one another from 1 count, so no other line can pass for one.
 */
const ERROR_LINE = /^\[(\d{1,3})\] (.+)$/

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
 * Runs the tests of the project, looks for a fix and applies it, with the system's own commands
 * in the cli container. Looking for a fix only saves it, in the data volume. Looking for it may
 * spend tokens, and applying it writes to the project, so both ask first, in a dialog of the app
 * that the page cannot answer. What is applied is exactly the saved text that was reviewed.
 */
export class Fixer {
  private current: Current | null = null

  constructor(private readonly deps: FixDeps) {}

  async status(): Promise<FixStatus> {
    const running = this.current?.kind ?? null
    if (running !== null) {
      return { running, saved: false }
    }
    return { running, saved: (await this.quiet(["test", "-s", PATCH_FILE])).code === 0 }
  }

  test(options: FixOptions = DEFAULT_OPTIONS): Promise<FixResult> {
    return this.go("test", "", options)
  }

  find(options: FixOptions = DEFAULT_OPTIONS): Promise<FixResult> {
    return this.go("fix", "", options)
  }

  /** The saved fix, as it will be applied. Nothing is changed. */
  async review(): Promise<FixReviewResult> {
    if (this.current !== null) {
      return noReview("Something is already running. Wait for it, or stop it.")
    }
    const problem = (await this.notReady()).problem
    if (problem !== "") {
      return noReview(problem)
    }
    const saved = await this.readSaved()
    if (saved === null) {
      return noReview("There is no saved fix. Look for one first.")
    }
    const patch = readPatch(saved)
    if (patch === null) {
      return noReview("The saved fix could not be read. Look for a fix again.")
    }
    return { review: { id: patchId(saved), ...patch }, message: "" }
  }

  /**
   * Applies the saved fix, only if it is the one with that id. It is tested again with the test
   * command of ``options``.
   */
  apply(id: string, options: FixOptions = DEFAULT_OPTIONS): Promise<FixResult> {
    return this.go("apply", id, options)
  }

  /** The saved fix belonged to the project that was chosen before: it goes. */
  async forget(): Promise<void> {
    if (this.current === null) {
      await this.quiet(["rm", "-f", PATCH_FILE])
    }
  }

  /**
   * Stops what is running. It does nothing when nothing runs, and it cannot stop an application
   * of a fix, which is over in a moment and is not to be left half done.
   */
  async cancel(): Promise<void> {
    const current = this.current
    if (current === null || current.cancelled || current.kind === "apply") {
      return
    }
    current.cancelled = true
    if (current.started) {
      await this.stopInside(current)
    }
  }

  private async go(kind: FixKind, id: string, options: FixOptions): Promise<FixResult> {
    if (this.current !== null) {
      return refused(kind, "Something is already running. Wait for it, or stop it.")
    }
    const current: Current = { kind, cancelled: false, started: false, sandboxBusy: false }
    this.current = current
    try {
      return await this.perform(current, id, options)
    } finally {
      this.current = null
    }
  }

  /** Why nothing can be run on the project now, or an empty text; and the services as they are. */
  private async notReady(): Promise<{ problem: string; stack: StackStatus }> {
    const stack = await this.deps.stack()
    if (!stack.running) {
      return { problem: "The services are stopped. Start them in Settings first.", stack }
    }
    if (stack.workspaceMatches === false) {
      return {
        problem:
          "The services work on another folder than the one chosen. Restart them in Settings first.",
        stack,
      }
    }
    return { problem: "", stack }
  }

  /** The text of the saved fix, or null when there is none. */
  private async readSaved(): Promise<string | null> {
    const read = await this.deps.run(
      "docker",
      ["compose", "exec", "-T", "cli", "cat", PATCH_FILE],
      { cwd: this.deps.root, timeoutMs: 30_000 },
    )
    return read.code === 0 && read.stdout.trim() !== "" ? read.stdout : null
  }

  private async perform(current: Current, id: string, options: FixOptions): Promise<FixResult> {
    const { kind } = current
    const { problem, stack } = await this.notReady()
    if (problem !== "") {
      return refused(kind, problem)
    }
    if (kind === "apply") {
      return this.runApply(current, id, options)
    }
    if (kind === "fix" && stack.keySeen === true) {
      const accepted = await this.deps.confirm({
        title: "Look for a fix",
        message: "Look for a fix for the tests of your project?",
        detail:
          `The system may ask Claude for patches with your Anthropic key, which spends tokens, up to ${
            options.attempts === null
              ? "the limits in its settings"
              : `${options.attempts === 1 ? "1 attempt" : `${options.attempts} attempts`} and the token limit in its settings`
          }. ` +
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
    return kind === "test" ? this.runTests(current, options) : this.runFix(current, options)
  }

  private async runTests(current: Current, options: FixOptions): Promise<FixResult> {
    const outcome = await this.inside(
      current,
      ["healer", "test", ...commandOf(options)],
      TEST_TIMEOUT_MS,
    )
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
          ...(outcome.errors.length > 0 ? { errors: outcome.errors } : {}),
        }
      default:
        return failedToRun("test", "", outcome.error)
    }
  }

  private async runFix(current: Current, options: FixOptions): Promise<FixResult> {
    await this.quiet(["rm", "-f", PATCH_FILE])
    const outcome = await this.inside(
      current,
      [
        "healer",
        "run",
        ...commandOf(options),
        "--no-apply",
        "--save",
        PATCH_FILE,
        "--error",
        String(options.error),
        ...(options.attempts === null ? [] : ["--attempts", String(options.attempts)]),
      ],
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

  private async runApply(current: Current, id: string, options: FixOptions): Promise<FixResult> {
    const saved = await this.readSaved()
    if (saved === null) {
      return refused("apply", "There is no saved fix. Look for one first.")
    }
    if (patchId(saved) !== id) {
      return refused("apply", "The saved fix is not the one you reviewed. Review it again.")
    }
    const patch = readPatch(saved)
    if (patch === null) {
      return refused("apply", "The saved fix could not be read. Look for a fix again.")
    }
    const files = filesOf(patch.edits)
    const accepted = await this.deps.confirm({
      title: "Apply the fix",
      message: "Apply this fix to your project?",
      detail:
        `It changes ${files.length === 1 ? "1 file" : `${files.length} files`} in ${await this.deps.project()}: ${listed(files)}. ` +
        "The system tests the fix again on a copy first, and writes your files only if the tests pass and the files are as they were when it was tested. " +
        "It does not keep a copy of the originals.",
      ok: "Apply the fix",
    })
    if (!accepted) {
      return { kind: "apply", outcome: "declined", message: "Your files were not changed." }
    }
    // The text that was reviewed goes in as it is: the program never reads the file again, so
    // nothing can change the fix between the review and the application.
    const outcome = await this.inside(
      current,
      ["healer", "apply", "-", ...commandOf(options), "--yes"],
      APPLY_TIMEOUT_MS,
      saved,
    )
    if (outcome.failed !== "") {
      return failedToRun("apply", outcome.failed, outcome.error)
    }
    if (outcome.code === 0) {
      await this.quiet(["rm", "-f", PATCH_FILE])
      return {
        kind: "apply",
        outcome: "applied",
        message: `The fix was applied to your project: ${listed(files)}. Run the tests to see them pass.`,
      }
    }
    if (outcome.code === 1) {
      return {
        kind: "apply",
        outcome: "blocked",
        message:
          "The tests do not pass with this fix now, so your files were not changed. The output below says why.",
      }
    }
    return failedToRun("apply", "", outcome.error)
  }

  /** Runs a command of the system in the cli container, line by line. */
  private async inside(current: Current, command: string[], timeoutMs: number, input?: string) {
    let error = ""
    const errors: string[] = []
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
        ...(input === undefined ? {} : { input }),
        onLine: (line) => {
          const shown = clean(line)
          if (shown.startsWith("error:")) {
            error = shown.slice("error:".length).trim()
          }
          const listed = ERROR_LINE.exec(shown)
          if (listed !== null && Number(listed[1]) === errors.length + 1) {
            errors.push((listed[2] ?? "").trim())
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
    return { code: result.code, failed: result.failed, error, errors }
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

/** The test command as the program's argument, or nothing for the system's own. */
function commandOf(options: FixOptions): string[] {
  return options.command === "" ? [] : [options.command]
}

function noReview(message: string): FixReviewResult {
  return { review: null, message }
}

/** The files, as one text: at most three names, then how many more. */
function listed(files: readonly string[]): string {
  const shown = files.slice(0, 3).join(", ")
  return files.length > 3 ? `${shown} and ${files.length - 3} more` : shown
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
  const what =
    kind === "test"
      ? "The tests could not be run"
      : kind === "fix"
        ? "The search for a fix could not run"
        : "The fix could not be applied"
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
