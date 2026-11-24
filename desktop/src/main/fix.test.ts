import { describe, expect, it } from "vitest"

import type { StackStatus } from "../shared/api"
import { DEFAULT_OPTIONS, type FixOptions } from "../shared/options"
import { clean, explain, Fixer, PATCH_FILE, type FixDeps, type Question } from "./fix"
import { patchId } from "./patch"
import type { Run, RunOptions, RunResult } from "./process"

const ROOT = "/home/pc/healer"
const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`
const UP: StackStatus = {
  running: true,
  services: ["cli", "mosquitto", "panel", "sandbox"],
  keySeen: true,
  workspaceMatches: true,
}

function result(code: number | null, failed = "", stdout = ""): RunResult {
  return { code, stdout, stderr: "", failed }
}

/** A fix as the system saves it. */
function saved(...paths: string[]): string {
  return JSON.stringify({
    edits: paths.map((path) => ({ path, old: "return a - b", new: "return a + b" })),
    description: "sumar must add",
  })
}

const PATCH = saved("app.py")

interface Options {
  stack?: StackStatus
  /** What the program in the container prints, and how it ends. */
  prints?: string[]
  code?: number | null
  failed?: string
  /** Whether the fix file exists once the search ends. */
  saved?: boolean
  answer?: boolean
  /** The id file is written only after this many attempts to read it. */
  killMisses?: number
  /** The sandbox cannot be restarted. */
  sandboxFails?: boolean
  /** The text of the saved fix, or null when there is none. */
  patch?: string | null
  /** The program in the container waits until the test opens the gate. */
  gate?: boolean
}

/** A computer with the services as given, and a container that runs what it is told. */
function computer(options: Options = {}) {
  const calls: string[] = []
  const argvs: Array<readonly string[]> = []
  const lines: string[] = []
  const questions: Question[] = []
  const slept: number[] = []
  let killMisses = options.killMisses ?? 0
  let release: (() => void) | null = null
  let started: (() => void) | null = null
  let removed = 0
  let patch = options.patch ?? null
  const inputs: string[] = []
  let open: () => void = () => undefined
  const gate = options.gate
    ? new Promise<void>((resolve) => {
        open = resolve
      })
    : null
  const hold =
    options.code === undefined && options.prints === undefined && options.failed === undefined
  const run: Run = async (file, args, runOptions: RunOptions = {}) => {
    const line = [file, ...args].join(" ")
    calls.push(line)
    argvs.push(args)
    if (line.includes(" rm -f /data/healer/fix.json")) {
      removed += 1
      return result(0)
    }
    if (line.includes(" rm -f /data/healer/fix.pid")) {
      return result(0)
    }
    if (line.includes("test -s /data/healer/fix.json")) {
      return result(options.saved ? 0 : 1)
    }
    if (line.endsWith(" cat /data/healer/fix.json")) {
      return patch === null ? result(1) : result(0, "", patch)
    }
    if (line === "docker compose restart -t 2 sandbox") {
      return result(options.sandboxFails ? 1 : 0)
    }
    if (line.includes('kill -INT "$(cat "$1")"')) {
      if (killMisses > 0) {
        killMisses -= 1
        return result(1)
      }
      release?.()
      return result(0)
    }
    if (runOptions.input !== undefined) {
      inputs.push(runOptions.input)
    }
    for (const text of options.prints ?? []) {
      runOptions.onLine?.(text)
    }
    started?.()
    if (gate !== null) {
      await gate
    }
    if (hold) {
      await new Promise<void>((resolve) => {
        release = resolve
      })
      return result(143)
    }
    return result(options.code ?? 0, options.failed ?? "")
  }
  const deps: FixDeps = {
    root: ROOT,
    run,
    stack: async () => options.stack ?? UP,
    project: async () => "/home/ana/shop",
    confirm: async (question) => {
      questions.push(question)
      return options.answer ?? true
    },
    sleep: async (ms) => {
      slept.push(ms)
    },
    onLine: (line) => lines.push(line),
  }
  const waitForStart = new Promise<void>((resolve) => {
    started = resolve
  })
  return {
    fixer: new Fixer(deps),
    calls,
    lines,
    questions,
    slept,
    waitForStart,
    argvs,
    /** What was handed to the program of the system, one argument each. */
    healerArgs: () =>
      argvs
        .map((args) => args.slice(args.indexOf("healer")))
        .filter((args) => args[0] === "healer"),
    inputs,
    open: () => open(),
    /** The saved fix is replaced by another, as when someone looks for a fix in a terminal. */
    replace: (text: string | null) => {
      patch = text
    },
    removed: () => removed,
    execs: () => calls.filter((call) => call.includes(" healer ")),
  }
}

describe("clean", () => {
  it("hides anything shaped like an Anthropic key", () => {
    expect(clean(`error: bad key ${KEY} here`)).toBe("error: bad key [hidden] here")
  })

  it("cuts a very long line", () => {
    expect(clean("x".repeat(5_000))).toHaveLength(2_000)
  })

  it("leaves other lines as they are", () => {
    expect(clean("tests      failed (exit 1) in 1.2s")).toBe("tests      failed (exit 1) in 1.2s")
  })
})

describe("what the system says about the key", () => {
  it("tells the app's way, not the way of editing .env", () => {
    const missing = explain(
      "ANTHROPIC_API_KEY is not set: add it to .env and recreate the container",
    )
    expect(missing).toContain("Save a key in Settings and restart the services")
    expect(missing).not.toContain(".env")
    expect(explain("Anthropic rejected the API key; check ANTHROPIC_API_KEY")).toContain(
      "Replace it in Settings",
    )
  })

  it("leaves any other error as it is", () => {
    expect(explain("the sandbox is not running")).toBe("the sandbox is not running")
  })

  it("shows up in the result of a search that could not run for lack of a key", async () => {
    const { fixer } = computer({
      code: 2,
      prints: ["error: ANTHROPIC_API_KEY is not set: add it to .env and recreate the container"],
      stack: { ...UP, keySeen: false },
    })
    const outcome = await fixer.find()
    expect(outcome.outcome).toBe("error")
    expect(outcome.message).toContain("Save a key in Settings")
  })
})

describe("running the tests", () => {
  it("runs healer test in the cli container and passes on exit 0", async () => {
    const { fixer, execs } = computer({ prints: ["result     passed (exit 0) in 1.0s"], code: 0 })
    const outcome = await fixer.test()
    expect(outcome).toEqual({
      kind: "test",
      outcome: "passed",
      message: "Every test passed, on a copy of your project.",
    })
    expect(execs()).toEqual([
      'docker compose exec -T -e PYTHONUNBUFFERED=1 cli sh -c echo $$ > "$1"; shift; exec "$@" sh /data/healer/fix.pid healer test',
    ])
  })

  it("passes each line of output on, as it is printed", async () => {
    const { fixer, lines } = computer({ prints: ["one", "two"], code: 1 })
    await fixer.test()
    expect(lines).toEqual(["one", "two"])
  })

  it("says the tests fail on exit 1", async () => {
    const { fixer } = computer({ code: 1 })
    const outcome = await fixer.test()
    expect(outcome.outcome).toBe("failed")
    expect(outcome.message).toContain("which ones, and why")
  })

  it("reports an error line of the system on any other exit", async () => {
    const { fixer } = computer({ code: 2, prints: ["error: the sandbox is not running"] })
    const outcome = await fixer.test()
    expect(outcome).toEqual({
      kind: "test",
      outcome: "error",
      message: "The tests could not be run: the sandbox is not running",
    })
  })

  it("says where to look when there is no error line", async () => {
    const { fixer } = computer({ code: 3 })
    expect((await fixer.test()).message).toBe(
      "The tests could not be run: The output below may say why.",
    )
  })

  it("never asks the user, and writes nothing to the project", async () => {
    const { fixer, questions, calls } = computer({ code: 0 })
    await fixer.test()
    expect(questions).toEqual([])
    expect(calls.join("\n")).not.toContain("apply")
    expect(calls.join("\n")).not.toContain("--save")
  })

  it("says so when Docker cannot be run", async () => {
    const { fixer } = computer({ code: null, failed: "ENOENT" })
    expect((await fixer.test()).message).toBe(
      "The tests could not be run: Docker could not be run.",
    )
  })

  it("stops a run that takes too long, in the container too", async () => {
    const { fixer, calls } = computer({ code: null, failed: "timeout" })
    const outcome = await fixer.test()
    expect(outcome.outcome).toBe("error")
    expect(outcome.message).toContain("It took too long")
    expect(calls.some((call) => call.includes('kill -INT "$(cat "$1")"'))).toBe(true)
  })

  it("removes the id file when it ends", async () => {
    const { fixer, calls } = computer({ code: 0 })
    await fixer.test()
    expect(calls[calls.length - 1]).toContain("rm -f /data/healer/fix.pid")
  })
})

describe("what is needed first", () => {
  it("refuses when the services are stopped", async () => {
    const stopped: StackStatus = { ...UP, running: false, keySeen: null, workspaceMatches: null }
    const { fixer, calls } = computer({ stack: stopped })
    const outcome = await fixer.test()
    expect(outcome).toEqual({
      kind: "test",
      outcome: "refused",
      message: "The services are stopped. Start them in Settings first.",
    })
    expect(calls).toEqual([])
  })

  it("refuses when the services work on another folder", async () => {
    const { fixer, calls } = computer({ stack: { ...UP, workspaceMatches: false } })
    const outcome = await fixer.find()
    expect(outcome.outcome).toBe("refused")
    expect(outcome.message).toContain("another folder")
    expect(calls).toEqual([])
  })

  it("does not refuse when the folder cannot be told", async () => {
    const { fixer } = computer({ stack: { ...UP, workspaceMatches: null }, code: 0 })
    expect((await fixer.test()).outcome).toBe("passed")
  })
})

describe("looking for a fix", () => {
  it("asks first, with the dialog of the app, and says what it costs", async () => {
    const { fixer, questions } = computer({ code: 0, saved: true })
    await fixer.find()
    expect(questions).toHaveLength(1)
    expect(questions[0]?.detail).toContain("spends tokens")
    expect(questions[0]?.detail).toContain("Your files are not changed")
  })

  it("runs nothing when the user says no", async () => {
    const { fixer, calls } = computer({ answer: false })
    const outcome = await fixer.find()
    expect(outcome).toEqual({ kind: "fix", outcome: "declined", message: "Nothing was run." })
    expect(calls).toEqual([])
  })

  it("does not ask when there is no key, because nothing can be spent", async () => {
    const { fixer, questions } = computer({
      stack: { ...UP, keySeen: false },
      code: 0,
      saved: true,
    })
    const outcome = await fixer.find()
    expect(questions).toEqual([])
    expect(outcome.outcome).toBe("fixed")
  })

  it("saves the patch outside the project and never applies it", async () => {
    const { fixer, execs, removed } = computer({ code: 0, saved: true })
    await fixer.find()
    expect(execs()).toHaveLength(1)
    expect(execs()[0]).toContain(`healer run --no-apply --save ${PATCH_FILE}`)
    expect(PATCH_FILE.startsWith("/data/")).toBe(true)
    expect(removed()).toBe(1)
  })

  it("says a fix was found when it was saved", async () => {
    const { fixer } = computer({ code: 0, saved: true })
    const outcome = await fixer.find()
    expect(outcome.outcome).toBe("fixed")
    expect(outcome.message).toContain("Your files were not changed: the fix is only saved")
  })

  it("says there is nothing to fix when the tests already pass", async () => {
    const { fixer } = computer({ code: 0, saved: false })
    expect((await fixer.find()).outcome).toBe("nothing")
  })

  it("says no fix was found on exit 1", async () => {
    const { fixer } = computer({ code: 1 })
    const outcome = await fixer.find()
    expect(outcome.outcome).toBe("unfixed")
    expect(outcome.message).toContain("Your files were not changed")
  })

  it("reports an error of the system on exit 2", async () => {
    const { fixer } = computer({ code: 2, prints: ["error: no Anthropic key"] })
    expect((await fixer.find()).message).toBe(
      "The search for a fix could not run: no Anthropic key",
    )
  })

  it("hides a key that shows up in the output", async () => {
    const { fixer, lines } = computer({ code: 2, prints: [`error: refused ${KEY}`] })
    const outcome = await fixer.find()
    expect(lines.join("\n")).not.toContain(KEY)
    expect(outcome.message).not.toContain(KEY)
    expect(outcome.message).toContain("[hidden]")
  })
})

describe("one run at a time, and stopping it", () => {
  it("says what is running", async () => {
    const { fixer, waitForStart } = computer()
    expect(await fixer.status()).toEqual({ running: null, saved: false })
    const running = fixer.test()
    await waitForStart
    expect(await fixer.status()).toEqual({ running: "test", saved: false })
    await fixer.cancel()
    await running
    expect(await fixer.status()).toEqual({ running: null, saved: false })
  })

  it("refuses a second run while one is going", async () => {
    const { fixer, waitForStart } = computer()
    const first = fixer.test()
    await waitForStart
    const second = await fixer.find()
    expect(second.outcome).toBe("refused")
    expect(second.message).toContain("already running")
    await fixer.cancel()
    await first
  })

  it("stops the program in the container and says it was stopped", async () => {
    const { fixer, waitForStart, calls } = computer()
    const running = fixer.test()
    await waitForStart
    await fixer.cancel()
    expect(await running).toEqual({
      kind: "test",
      outcome: "cancelled",
      message: "Your files were not changed.",
    })
    expect(calls.filter((call) => call.includes('kill -INT "$(cat "$1")"'))).toHaveLength(1)
  })

  it("frees the test sandbox when a run is stopped, because it would go on with its copy", async () => {
    const { fixer, waitForStart, calls } = computer()
    const running = fixer.test()
    await waitForStart
    await fixer.cancel()
    await running
    expect(calls.filter((call) => call === "docker compose restart -t 2 sandbox")).toHaveLength(1)
  })

  it("says so when the sandbox could not be freed", async () => {
    const { fixer, waitForStart } = computer({ sandboxFails: true })
    const running = fixer.test()
    await waitForStart
    await fixer.cancel()
    expect((await running).message).toBe(
      "Your files were not changed. The test sandbox could not be freed and may stay busy for a few minutes.",
    )
  })

  it("frees the sandbox after a run that took too long, too", async () => {
    const { fixer, calls } = computer({ code: null, failed: "timeout" })
    await fixer.test()
    expect(calls).toContain("docker compose restart -t 2 sandbox")
  })

  it("leaves the sandbox alone when a run ends by itself", async () => {
    const { fixer, calls } = computer({ code: 1 })
    await fixer.test()
    expect(calls.some((call) => call.includes("restart"))).toBe(false)
  })

  it("warns that tokens already spent are not returned when a fix search is stopped", async () => {
    const { fixer, waitForStart } = computer()
    const running = fixer.find()
    await waitForStart
    await fixer.cancel()
    const outcome = await running
    expect(outcome.outcome).toBe("cancelled")
    expect(outcome.message).toContain("Tokens already used by Claude are not returned")
  })

  it("tries again when the program has not written its id yet", async () => {
    const { fixer, waitForStart, slept, calls } = computer({ killMisses: 2 })
    const running = fixer.test()
    await waitForStart
    await fixer.cancel()
    await running
    expect(calls.filter((call) => call.includes('kill -INT "$(cat "$1")"'))).toHaveLength(3)
    expect(slept).toEqual([300, 300])
  })

  it("does nothing to cancel when nothing runs", async () => {
    const { fixer, calls } = computer()
    await fixer.cancel()
    expect(calls).toEqual([])
  })

  it("can run again after one was stopped", async () => {
    const first = computer()
    const running = first.fixer.test()
    await first.waitForStart
    await first.fixer.cancel()
    await running
    const second = computer({ code: 0 })
    expect((await second.fixer.test()).outcome).toBe("passed")
  })

  it("does not start a run that was stopped while the services were being asked", async () => {
    const gate = { open: () => undefined as void }
    const calls: string[] = []
    const deps: FixDeps = {
      root: ROOT,
      run: async (file, args) => {
        calls.push([file, ...args].join(" "))
        return result(0)
      },
      stack: () =>
        new Promise((resolve) => {
          gate.open = () => resolve(UP)
        }),
      project: async () => "/home/ana/shop",
      confirm: async () => true,
      sleep: async () => undefined,
      onLine: () => undefined,
    }
    const fixer = new Fixer(deps)
    const running = fixer.test()
    await fixer.cancel()
    gate.open()
    expect((await running).outcome).toBe("cancelled")
    expect(calls).toEqual([])
  })
})

describe("the saved fix", () => {
  it("says whether there is one, when nothing runs", async () => {
    expect(await computer({ saved: true }).fixer.status()).toEqual({ running: null, saved: true })
    expect(await computer({ saved: false }).fixer.status()).toEqual({ running: null, saved: false })
  })

  it("says what runs, and does not look at the fix meanwhile", async () => {
    const { fixer, waitForStart, calls } = computer({ saved: true })
    const running = fixer.test()
    await waitForStart
    const before = calls.length
    expect(await fixer.status()).toEqual({ running: "test", saved: false })
    expect(calls.length).toBe(before)
    await fixer.cancel()
    await running
  })

  it("is shown as it will be applied, with an id that names exactly it", async () => {
    const { fixer, questions } = computer({ patch: PATCH })
    const shown = await fixer.review()
    expect(shown).toEqual({
      review: {
        id: patchId(PATCH),
        description: "sumar must add",
        edits: [{ path: "app.py", old: "return a - b", new: "return a + b" }],
      },
      message: "",
    })
    expect(questions).toEqual([])
  })

  it("is not shown when there is none, when it cannot be read, or when the services are not ready", async () => {
    expect((await computer({ patch: null }).fixer.review()).message).toBe(
      "There is no saved fix. Look for one first.",
    )
    expect((await computer({ patch: "{" }).fixer.review()).message).toBe(
      "The saved fix could not be read. Look for a fix again.",
    )
    const stopped: StackStatus = { ...UP, running: false, keySeen: null, workspaceMatches: null }
    const down = computer({ patch: PATCH, stack: stopped })
    expect((await down.fixer.review()).message).toBe(
      "The services are stopped. Start them in Settings first.",
    )
    expect(down.calls).toEqual([])
    const other = computer({ patch: PATCH, stack: { ...UP, workspaceMatches: false } })
    expect((await other.fixer.review()).message).toContain("another folder")
  })

  it("is not shown while something runs", async () => {
    const { fixer, waitForStart } = computer({ patch: PATCH })
    const running = fixer.test()
    await waitForStart
    expect((await fixer.review()).message).toContain("already running")
    await fixer.cancel()
    await running
  })

  it("goes when the project changes, but not while something runs", async () => {
    const idle = computer()
    await idle.fixer.forget()
    expect(idle.calls).toEqual(["docker compose exec -T cli rm -f /data/healer/fix.json"])
    const busy = computer()
    const running = busy.fixer.test()
    await busy.waitForStart
    const before = busy.calls.length
    await busy.fixer.forget()
    expect(busy.calls.length).toBe(before)
    await busy.fixer.cancel()
    await running
  })
})

describe("applying the fix", () => {
  const id = patchId(PATCH)

  it("asks first, in a dialog of the app, saying what changes, where, and that it keeps no copy", async () => {
    const { fixer, questions } = computer({ patch: PATCH, code: 0 })
    await fixer.apply(id)
    expect(questions).toHaveLength(1)
    expect(questions[0]).toMatchObject({
      title: "Apply the fix",
      message: "Apply this fix to your project?",
      ok: "Apply the fix",
    })
    const detail = questions[0]?.detail ?? ""
    expect(detail).toContain("1 file in /home/ana/shop: app.py")
    expect(detail).toContain("tests the fix again on a copy first")
    expect(detail).toContain("does not keep a copy of the originals")
  })

  it("counts the files, and does not list too many", async () => {
    const many = saved("a.py", "b.py", "c.py", "d.py", "e.py", "a.py")
    const { fixer, questions } = computer({ patch: many, code: 0 })
    await fixer.apply(patchId(many))
    expect(questions[0]?.detail).toContain("5 files in /home/ana/shop: a.py, b.py, c.py and 2 more")
  })

  it("changes nothing when the user says no", async () => {
    const { fixer, execs, calls } = computer({ patch: PATCH, answer: false })
    expect(await fixer.apply(id)).toEqual({
      kind: "apply",
      outcome: "declined",
      message: "Your files were not changed.",
    })
    expect(execs()).toEqual([])
    expect(calls.some((call) => call.includes("rm -f"))).toBe(false)
  })

  it("gives the system the text that was reviewed, and never a file to read again", async () => {
    const { fixer, execs, inputs } = computer({ patch: PATCH, code: 0 })
    await fixer.apply(id)
    expect(execs()).toHaveLength(1)
    expect(execs()[0]).toMatch(/ healer apply - --yes$/)
    expect(execs()[0]).not.toContain("fix.json")
    expect(inputs).toEqual([PATCH])
  })

  it("applies only the fix that was reviewed", async () => {
    const { fixer, execs, questions, replace } = computer({ patch: PATCH, code: 0 })
    replace(saved("other.py"))
    expect(await fixer.apply(id)).toEqual({
      kind: "apply",
      outcome: "refused",
      message: "The saved fix is not the one you reviewed. Review it again.",
    })
    expect(execs()).toEqual([])
    expect(questions).toEqual([])
  })

  it("refuses when there is none, or it cannot be read, or the services are not ready", async () => {
    expect((await computer({ patch: null }).fixer.apply(id)).message).toBe(
      "There is no saved fix. Look for one first.",
    )
    const broken = computer({ patch: "{" })
    expect((await broken.fixer.apply(patchId("{"))).message).toBe(
      "The saved fix could not be read. Look for a fix again.",
    )
    const stopped: StackStatus = { ...UP, running: false, keySeen: null, workspaceMatches: null }
    const down = computer({ patch: PATCH, stack: stopped })
    expect((await down.fixer.apply(id)).outcome).toBe("refused")
    expect(down.calls).toEqual([])
    const other = computer({ patch: PATCH, stack: { ...UP, workspaceMatches: false } })
    expect((await other.fixer.apply(id)).outcome).toBe("refused")
    expect(other.questions).toEqual([])
  })

  it("asks even when the services have no key, because it writes to the project", async () => {
    const { fixer, questions } = computer({
      patch: PATCH,
      code: 0,
      stack: { ...UP, keySeen: false },
    })
    await fixer.apply(id)
    expect(questions).toHaveLength(1)
  })

  it("says it was applied, and the saved fix goes", async () => {
    const { fixer, calls, removed } = computer({ patch: PATCH, code: 0 })
    const outcome = await fixer.apply(id)
    expect(outcome.kind).toBe("apply")
    expect(outcome.outcome).toBe("applied")
    expect(outcome.message).toBe(
      "The fix was applied to your project: app.py. Run the tests to see them pass.",
    )
    expect(removed()).toBe(1)
    expect(calls.findIndex((call) => call.includes("healer apply"))).toBeLessThan(
      calls.findIndex((call) => call.includes("rm -f /data/healer/fix.json")),
    )
  })

  it("writes nothing and keeps the saved fix when the tests do not pass", async () => {
    const { fixer, removed } = computer({ patch: PATCH, code: 1 })
    const outcome = await fixer.apply(id)
    expect(outcome.outcome).toBe("blocked")
    expect(outcome.message).toContain("your files were not changed")
    expect(removed()).toBe(0)
  })

  it("says why when the system could not apply it, and keeps the saved fix", async () => {
    const { fixer, removed } = computer({
      patch: PATCH,
      code: 2,
      prints: [
        "error: not applied, your files were not changed: app.py: changed since the patch was tested",
      ],
    })
    expect(await fixer.apply(id)).toEqual({
      kind: "apply",
      outcome: "error",
      message:
        "The fix could not be applied: not applied, your files were not changed: app.py: changed since the patch was tested",
    })
    expect(removed()).toBe(0)
  })

  it("does not say it was applied when the program could not run", async () => {
    const { fixer, removed } = computer({ patch: PATCH, code: null, failed: "ENOENT" })
    const outcome = await fixer.apply(id)
    expect(outcome.outcome).toBe("error")
    expect(outcome.message).toBe("The fix could not be applied: Docker could not be run.")
    expect(removed()).toBe(0)
  })

  it("passes its output on, with a key hidden", async () => {
    const { fixer, lines } = computer({
      patch: PATCH,
      code: 1,
      prints: ["verdict    the tests still fail", `error ${KEY}`],
    })
    await fixer.apply(id)
    expect(lines).toEqual(["verdict    the tests still fail", "error [hidden]"])
  })

  it("cannot be stopped, because it is not to be left half done", async () => {
    const { fixer, waitForStart, calls, open } = computer({ patch: PATCH, code: 0, gate: true })
    const applying = fixer.apply(id)
    await waitForStart
    expect(await fixer.status()).toEqual({ running: "apply", saved: false })
    await fixer.cancel()
    expect(calls.some((call) => call.includes("kill"))).toBe(false)
    expect(calls.some((call) => call.includes("restart"))).toBe(false)
    open()
    expect((await applying).outcome).toBe("applied")
  })

  it("refuses a second run while it applies", async () => {
    const { fixer, waitForStart, open } = computer({ patch: PATCH, code: 0, gate: true })
    const applying = fixer.apply(id)
    await waitForStart
    expect((await fixer.test()).outcome).toBe("refused")
    expect((await fixer.apply(id)).outcome).toBe("refused")
    open()
    await applying
  })
})

describe("what the user chose", () => {
  const chosen = (change: Partial<FixOptions>): FixOptions => ({ ...DEFAULT_OPTIONS, ...change })

  it("gives the system its own choices when the user chose nothing", async () => {
    const tests = computer({ code: 0 })
    await tests.fixer.test()
    expect(tests.healerArgs()).toEqual([["healer", "test"]])
    const search = computer({ code: 0, saved: true })
    await search.fixer.find()
    expect(search.healerArgs()).toEqual([
      ["healer", "run", "--no-apply", "--save", PATCH_FILE, "--error", "1"],
    ])
  })

  it("gives the test command as one argument, which no shell reads, to the tests", async () => {
    const { fixer, healerArgs } = computer({ code: 0 })
    await fixer.test(chosen({ command: "pytest tests/unit -x; rm -rf /" }))
    expect(healerArgs()).toEqual([["healer", "test", "pytest tests/unit -x; rm -rf /"]])
  })

  it("gives the command, the error and the attempts to the search", async () => {
    const { fixer, healerArgs } = computer({ code: 0, saved: true })
    await fixer.find(chosen({ command: "pytest tests/unit", error: 2, attempts: 4 }))
    expect(healerArgs()).toEqual([
      [
        "healer",
        "run",
        "pytest tests/unit",
        "--no-apply",
        "--save",
        PATCH_FILE,
        "--error",
        "2",
        "--attempts",
        "4",
      ],
    ])
  })

  it("tests the fix again with the command, when it is applied", async () => {
    const patch = saved("app.py")
    const { fixer, healerArgs } = computer({ patch, code: 0 })
    await fixer.apply(
      patchId(patch),
      chosen({ command: "pytest tests/unit", attempts: 5, error: 3 }),
    )
    expect(healerArgs()).toEqual([["healer", "apply", "-", "pytest tests/unit", "--yes"]])
  })

  it("never gives the error or the attempts to what does not use them", async () => {
    const tests = computer({ code: 0 })
    await tests.fixer.test(chosen({ error: 3, attempts: 7 }))
    expect(tests.healerArgs()).toEqual([["healer", "test"]])
    const patch = saved("app.py")
    const applying = computer({ patch, code: 0 })
    await applying.fixer.apply(patchId(patch), chosen({ error: 3, attempts: 7 }))
    expect(applying.healerArgs()).toEqual([["healer", "apply", "-", "--yes"]])
  })

  it("says in the question how many attempts the user allowed", async () => {
    const detail = async (options: FixOptions) => {
      const { fixer, questions } = computer({ code: 0, saved: true })
      await fixer.find(options)
      return questions[0]?.detail ?? ""
    }
    expect(await detail(chosen({}))).toContain("up to the limits in its settings")
    expect(await detail(chosen({ attempts: 1 }))).toContain(
      "up to 1 attempt and the token limit in its settings",
    )
    expect(await detail(chosen({ attempts: 6 }))).toContain(
      "up to 6 attempts and the token limit in its settings",
    )
  })

  it("shows the system's own reason when it does not allow the command", async () => {
    const { fixer } = computer({
      code: 2,
      prints: ["error: Command not allowed: 'ls'. Allowed: pytest, python -m pytest"],
    })
    expect((await fixer.test(chosen({ command: "ls" }))).message).toBe(
      "The tests could not be run: Command not allowed: 'ls'. Allowed: pytest, python -m pytest",
    )
  })
})

describe("the errors the tests show", () => {
  const FAILING = [
    "sandbox    2 files copied to an isolated copy, then deleted",
    "command    python -m pytest",
    "result     tests failed (exit 1) in 1.9s",
    "",
    "[1] AssertionError: assert -2 == 10",
    "    signature  70983271ad01",
    "    origin     test_app.py:5 in test_sumar",
    "",
    "[2] ZeroDivisionError: division by zero",
    "    signature  1111111111aa",
    "",
    "[3] KeyError: 'name'",
  ]

  it("are listed with what the system says of each, in its order", async () => {
    const { fixer } = computer({ code: 1, prints: FAILING })
    const outcome = await fixer.test()
    expect(outcome.outcome).toBe("failed")
    expect(outcome.errors).toEqual([
      "AssertionError: assert -2 == 10",
      "ZeroDivisionError: division by zero",
      "KeyError: 'name'",
    ])
  })

  it("count only the lines that follow the numbering from 1", async () => {
    const { fixer } = computer({
      code: 1,
      prints: [
        "[2] a line that is not the first",
        "[1] the first",
        "    [2] indented, so it is not one",
        "[3] a number that skips one",
        "[2] the second",
        "[2] the second again",
      ],
    })
    expect((await fixer.test()).errors).toEqual(["the first", "the second"])
  })

  it("leave no list when the tests show none, or pass", async () => {
    const none = await computer({
      code: 1,
      prints: ["result     tests failed (exit 1)"],
    }).fixer.test()
    expect(none.outcome).toBe("failed")
    expect("errors" in none).toBe(false)
    const passed = await computer({ code: 0, prints: ["[1] this is not an error"] }).fixer.test()
    expect(passed.outcome).toBe("passed")
    expect("errors" in passed).toBe(false)
  })

  it("hide a key that an error says", async () => {
    const { fixer } = computer({ code: 1, prints: [`[1] ValueError: bad key ${KEY}`] })
    expect((await fixer.test()).errors).toEqual(["ValueError: bad key [hidden]"])
  })
})
