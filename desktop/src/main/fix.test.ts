import { describe, expect, it } from "vitest"

import type { StackStatus } from "../shared/api"
import { clean, explain, Fixer, PATCH_FILE, type FixDeps, type Question } from "./fix"
import type { Run, RunOptions, RunResult } from "./process"

const ROOT = "/home/pc/healer"
const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`
const UP: StackStatus = {
  running: true,
  services: ["cli", "mosquitto", "panel", "sandbox"],
  keySeen: true,
  workspaceMatches: true,
}

function result(code: number | null, failed = ""): RunResult {
  return { code, stdout: "", stderr: "", failed }
}

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
}

/** A computer with the services as given, and a container that runs what it is told. */
function computer(options: Options = {}) {
  const calls: string[] = []
  const lines: string[] = []
  const questions: Question[] = []
  const slept: number[] = []
  let killMisses = options.killMisses ?? 0
  let release: (() => void) | null = null
  let started: (() => void) | null = null
  let removed = 0
  const hold =
    options.code === undefined && options.prints === undefined && options.failed === undefined
  const run: Run = async (file, args, runOptions: RunOptions = {}) => {
    const line = [file, ...args].join(" ")
    calls.push(line)
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
    for (const text of options.prints ?? []) {
      runOptions.onLine?.(text)
    }
    started?.()
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
    expect(fixer.status()).toEqual({ running: null })
    const running = fixer.test()
    await waitForStart
    expect(fixer.status()).toEqual({ running: "test" })
    await fixer.cancel()
    await running
    expect(fixer.status()).toEqual({ running: null })
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
