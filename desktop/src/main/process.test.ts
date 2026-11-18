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

  it("gives the program the text it is given as its input, as it is", async () => {
    const text = 'one\ntwo ñ "quoted" $(not run)\n'
    const result = await run("cat", [], { input: text })
    expect(result).toEqual({ code: 0, stdout: text, stderr: "", failed: "" })
  })

  it("gives a program that reads its input nothing, and no wait, when there is none", async () => {
    const result = await run("sh", ["-c", "cat; echo done"], { timeoutMs: 5_000 })
    expect(result).toMatchObject({ code: 0, stdout: "done\n", failed: "" })
  })

  it("does not fail when the program ends without reading what it was given", async () => {
    const result = await run("true", [], { input: "x".repeat(1_000_000) })
    expect(result).toMatchObject({ code: 0, failed: "" })
  })

  it("gives each line as it comes, including a last one with no line break", async () => {
    const lines: string[] = []
    await run("sh", ["-c", "echo one; echo two >&2; printf last"], {
      onLine: (line) => lines.push(line),
    })
    expect(lines.sort()).toEqual(["last", "one", "two"])
  })
})
