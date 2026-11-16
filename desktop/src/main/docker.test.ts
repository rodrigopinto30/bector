import { describe, expect, it } from "vitest"

import { checkDocker } from "./docker"
import type { Run, RunResult } from "./process"

function answer(code: number | null, stdout = "", stderr = "", failed = ""): RunResult {
  return { code, stdout, stderr, failed }
}

/** A run that answers by the start of the command line, and remembers what was run. */
function scripted(answers: Record<string, RunResult>): { run: Run; calls: string[] } {
  const calls: string[] = []
  const run: Run = async (file, args) => {
    const line = [file, ...args].join(" ")
    calls.push(line)
    const found = Object.entries(answers).find(([start]) => line.startsWith(start))
    return found === undefined ? answer(0) : found[1]
  }
  return { run, calls }
}

describe("checking Docker", () => {
  it("is ready when Docker answers without sudo and has Compose", async () => {
    const { run, calls } = scripted({ "docker info": answer(0, "27.3.1\n") })
    expect(await checkDocker(run)).toEqual({ ok: true, version: "27.3.1" })
    expect(calls.every((call) => call.startsWith("docker "))).toBe(true)
  })

  it("says when Docker is not installed, and asks nothing more", async () => {
    const { run, calls } = scripted({ "docker --version": answer(null, "", "", "ENOENT") })
    const status = await checkDocker(run)
    expect(status).toMatchObject({ ok: false, problem: "missing" })
    expect(calls).toEqual(["docker --version"])
  })

  it("tells a user who is not in the docker group", async () => {
    const { run } = scripted({
      "docker info": answer(
        1,
        "",
        "permission denied while trying to connect to the Docker daemon socket",
      ),
    })
    expect(await checkDocker(run)).toMatchObject({ ok: false, problem: "permission" })
  })

  it.each([
    ["Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is it running?", 1, ""],
    ["", null, "timeout"],
  ])("tells when the daemon is not running (%s)", async (stderr, code, failed) => {
    const { run } = scripted({ "docker info": answer(code, "", stderr, failed) })
    expect(await checkDocker(run)).toMatchObject({ ok: false, problem: "daemon" })
  })

  it("tells when Compose is missing", async () => {
    const { run } = scripted({ "docker info": answer(0, "27.3.1"), "docker compose": answer(1) })
    expect(await checkDocker(run)).toMatchObject({ ok: false, problem: "compose" })
  })

  it("gives a way forward for every problem, and never runs sudo itself", async () => {
    for (const failing of ["docker --version", "docker info", "docker compose"]) {
      const { run, calls } = scripted({ [failing]: answer(null, "", "", "ENOENT") })
      const status = await checkDocker(run)
      expect(status.ok).toBe(false)
      if (!status.ok) {
        expect(status.hint).toMatch(/Check again/)
        expect(status.message).not.toBe("")
      }
      expect(calls.some((call) => call.includes("sudo"))).toBe(false)
    }
  })
})
