import { mkdtemp, realpath } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import { run } from "./process"

describe("running a program", () => {
  it("gives its code and both outputs", async () => {
    const result = await run("sh", ["-c", "echo out; echo err >&2; exit 3"])
    expect(result).toEqual({ code: 3, stdout: "out\n", stderr: "err\n", failed: "" })
  })

  it("says when the program does not exist", async () => {
    const result = await run("healer-no-such-program", [])
    expect(result).toMatchObject({ code: null, failed: "ENOENT" })
  })

  it("stops a program that takes too long", async () => {
    const result = await run("sleep", ["10"], { timeoutMs: 100 })
    expect(result.failed).toBe("timeout")
  })

  it("passes the environment through, apart from the arguments", async () => {
    const secret = "value-that-must-stay-in-the-environment"
    const result = await run("sh", ["-c", 'printf %s "$HEALER_TEST_VALUE"'], {
      env: { HEALER_TEST_VALUE: secret },
    })
    expect(result.stdout).toBe(secret)
  })

  it("runs in the folder it is given", async () => {
    const folder = await realpath(await mkdtemp(join(tmpdir(), "healer-run-")))
    expect((await run("pwd", [], { cwd: folder })).stdout.trim()).toBe(folder)
  })

  it("never interprets its arguments", async () => {
    const result = await run("echo", ["$(echo hacked); echo also"])
    expect(result.stdout).toBe("$(echo hacked); echo also\n")
  })

  it("gives each line as it comes, including a last one with no line break", async () => {
    const lines: string[] = []
    await run("sh", ["-c", "echo one; echo two >&2; printf last"], {
      onLine: (line) => lines.push(line),
    })
    expect(lines.sort()).toEqual(["last", "one", "two"])
  })
})
