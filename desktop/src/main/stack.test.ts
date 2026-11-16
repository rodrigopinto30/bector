import { describe, expect, it } from "vitest"

import type { Run, RunOptions, RunResult } from "./process"
import { Stack, SERVICES, type StackDeps } from "./stack"
import { VaultError } from "./vault"

const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`
const ROOT = "/home/pc/healer"
const SET_UP = "ANTHROPIC_API_KEY=\nHOST_UID=1000\nHOST_GID=1000\n"

interface Call {
  line: string
  file: string
  args: readonly string[]
  options: RunOptions
}

function result(code: number | null, stdout = "", failed = ""): RunResult {
  return { code, stdout, stderr: "", failed }
}

/** A computer where ``up`` services run, and ``cliKey`` says whether the cli has a key. */
function computer(options: {
  up?: readonly string[]
  cliKey?: boolean
  env?: string | null
  key?: string | null
  keyFails?: Error
  startCode?: number
  startFailed?: string
  startOutput?: string[]
  panelAnswers?: boolean
}) {
  const { up = [], env = SET_UP, key = KEY, startCode = 0, panelAnswers = true } = options
  let cliKey = options.cliKey ?? false
  const calls: Call[] = []
  const lines: string[] = []
  const slept: number[] = []
  const running = new Set(up)
  const run: Run = async (file, args, runOptions = {}) => {
    const line = [file, ...args].join(" ")
    calls.push({ line, file, args, options: runOptions })
    if (line.startsWith("docker compose ps")) {
      return result(0, [...running].join("\n"))
    }
    if (line.startsWith("docker compose exec")) {
      return result(0, cliKey ? "set\n" : "unset\n")
    }
    if (line === "bash stop.sh") {
      running.clear()
      return result(0)
    }
    for (const output of options.startOutput ?? []) {
      runOptions.onLine?.(output)
    }
    if (startCode === 0) {
      SERVICES.forEach((name) => running.add(name))
      cliKey = key !== null
    }
    return result(startCode, "", options.startFailed ?? "")
  }
  const deps: StackDeps = {
    root: ROOT,
    run,
    readEnv: async () => env,
    key: async () => {
      if (options.keyFails) {
        throw options.keyFails
      }
      return key
    },
    ping: async () => panelAnswers,
    sleep: async (ms) => {
      slept.push(ms)
    },
    onLine: (line) => lines.push(line),
  }
  return { stack: new Stack(deps), calls, lines, slept, running }
}

const started = (calls: Call[]) =>
  calls.filter((call) => /^(bash start|docker compose up)/.test(call.line))

describe("the state of the services", () => {
  it("is running when the four services are up, and says whether the cli has a key", async () => {
    const { stack } = computer({ up: SERVICES, cliKey: true })
    expect(await stack.status()).toEqual({
      running: true,
      services: [...SERVICES].sort(),
      keySeen: true,
    })
    expect(await computer({ up: SERVICES, cliKey: false }).stack.status()).toMatchObject({
      keySeen: false,
    })
  })

  it("is not running when one is missing, and asks about the key only if the cli is up", async () => {
    const { stack, calls } = computer({ up: ["mosquitto", "panel", "sandbox"] })
    expect(await stack.status()).toMatchObject({ running: false, keySeen: null })
    expect(calls.some((call) => call.line.startsWith("docker compose exec"))).toBe(false)
  })

  it("asks only whether a key is set, never for its value", async () => {
    const { stack, calls } = computer({ up: SERVICES, cliKey: true })
    await stack.status()
    const asked = calls.find((call) => call.line.startsWith("docker compose exec"))
    expect(asked?.args.join(" ")).toContain("echo set || echo unset")
    expect(asked?.args.join(" ")).not.toMatch(/printenv|echo \$ANTHROPIC/)
  })
})

describe("starting the services", () => {
  it("runs start.sh the first time and gives it the key through the environment only", async () => {
    const { stack, calls } = computer({ env: null })
    const outcome = await stack.start()
    expect(outcome).toEqual({
      ok: true,
      message: "The services are running, and the system sees your Anthropic key.",
    })
    const [first] = started(calls)
    expect(first?.line).toBe("bash start.sh")
    expect(first?.options.cwd).toBe(ROOT)
    expect(first?.options.env).toEqual({ ANTHROPIC_API_KEY: KEY })
    expect(
      calls.some((call) => call.line.includes(KEY) || call.args.some((arg) => arg.includes(KEY))),
    ).toBe(false)
  })

  it("only brings the services up when .env is already set", async () => {
    const { stack, calls } = computer({ env: SET_UP })
    await stack.start()
    expect(started(calls).map((call) => call.line)).toEqual(["docker compose up -d"])
  })

  it("does nothing when everything runs and the cli already has the key", async () => {
    const { stack, calls } = computer({ up: SERVICES, cliKey: true })
    expect(await stack.start()).toMatchObject({ ok: true })
    expect(started(calls)).toEqual([])
  })

  it("recreates the services when they run without the key", async () => {
    const { stack, calls } = computer({ up: SERVICES, cliKey: false })
    await stack.start()
    expect(started(calls)).toHaveLength(1)
  })

  it("recreates them on demand, which is how a replaced key reaches them", async () => {
    const { stack, calls } = computer({ up: SERVICES, cliKey: true })
    await stack.start(true)
    expect(started(calls)).toHaveLength(1)
  })

  it("does not start, and says why, when the saved key cannot be read", async () => {
    const { stack, calls } = computer({
      keyFails: new VaultError("The secret store did not answer."),
    })
    expect(await stack.start()).toEqual({
      ok: false,
      message: "The secret store did not answer. The services were not started.",
    })
    expect(started(calls)).toEqual([])
    expect(calls).toEqual([])
    // It is free again: another try is not refused as "already being started".
    expect((await stack.start()).message).not.toMatch(/already being started/)
  })

  it("does not hide an error that is not about the key", async () => {
    const { stack } = computer({ keyFails: new Error("disk on fire") })
    await expect(stack.start()).rejects.toThrow("disk on fire")
  })

  it("starts with no key when none is saved, and says what that means", async () => {
    const { stack, calls } = computer({ key: null })
    const outcome = await stack.start()
    expect(outcome.ok).toBe(true)
    expect(outcome.message).toMatch(/no Anthropic key/)
    expect(started(calls)[0]?.options.env).toEqual({})
  })

  it("hides the key from every line the services print", async () => {
    const { stack, lines } = computer({ startOutput: [`using ${KEY} now`, "plain line"] })
    await stack.start()
    expect(lines).toEqual(["using [hidden] now", "plain line"])
    expect(lines.join("\n")).not.toContain(KEY)
  })

  it.each([
    [1, "", "See the output above."],
    [null, "timeout", "It took too long."],
  ])("says so when the start fails (code %s)", async (code, failed, reason) => {
    const { stack } = computer({ startCode: code ?? 1, startFailed: failed })
    expect(await stack.start()).toEqual({
      ok: false,
      message: `The services did not start. ${reason}`,
    })
  })

  it("waits for the panel, and gives up after two minutes", async () => {
    const { stack, slept } = computer({ panelAnswers: false })
    expect(await stack.start()).toEqual({
      ok: false,
      message: "The services started, but the panel did not answer in time.",
    })
    expect(slept).toHaveLength(120)
  })

  it("does not start twice at once", async () => {
    const { stack } = computer({})
    const [first, second] = await Promise.all([stack.start(), stack.start()])
    expect([first.ok, second.ok].sort()).toEqual([false, true])
    expect([first, second].find((outcome) => !outcome.ok)?.message).toMatch(/already/)
  })
})

describe("stopping the services", () => {
  it("runs stop.sh", async () => {
    const { stack, calls, running } = computer({ up: SERVICES })
    expect(await stack.stop()).toEqual({ ok: true, message: "The services are stopped." })
    expect(calls.map((call) => call.line)).toContain("bash stop.sh")
    expect(running.size).toBe(0)
  })
})
