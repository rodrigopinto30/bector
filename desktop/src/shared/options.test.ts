import { describe, expect, it } from "vitest"

import { checkOptions, COMMAND_LIMIT, DEFAULT_OPTIONS } from "./options"

const ok = (value: unknown) => {
  const checked = checkOptions(value)
  if (!checked.ok) {
    throw new Error(checked.problem)
  }
  return checked.options
}
const problem = (value: unknown) => {
  const checked = checkOptions(value)
  return checked.ok ? "" : checked.problem
}

describe("what the user may choose", () => {
  it("is the system's own when nothing is chosen", () => {
    expect(ok(undefined)).toEqual(DEFAULT_OPTIONS)
    expect(ok(null)).toEqual(DEFAULT_OPTIONS)
    expect(ok({})).toEqual({ command: "", attempts: null, error: 1 })
  })

  it("takes the command, the attempts and the error, and drops everything else", () => {
    expect(ok({ command: "pytest -x", attempts: 3, error: 2, apply: true, path: "/" })).toEqual({
      command: "pytest -x",
      attempts: 3,
      error: 2,
    })
  })

  it("cleans the space around the command", () => {
    expect(ok({ command: "  python -m pytest tests  " }).command).toBe("python -m pytest tests")
    expect(ok({ command: "   " }).command).toBe("")
  })

  it("takes numbers written in a field", () => {
    expect(ok({ attempts: "4", error: "2" })).toMatchObject({ attempts: 4, error: 2 })
    expect(ok({ attempts: "" }).attempts).toBeNull()
  })

  it("accepts the limits and refuses what is just beyond them", () => {
    expect(ok({ attempts: 1 }).attempts).toBe(1)
    expect(ok({ attempts: 10 }).attempts).toBe(10)
    expect(problem({ attempts: 0 })).toContain("from 1 to 10")
    expect(problem({ attempts: 11 })).toContain("from 1 to 10")
    expect(ok({ error: 100 }).error).toBe(100)
    expect(problem({ error: 0 })).toContain("from 1 to 100")
    expect(problem({ error: 101 })).toContain("from 1 to 100")
    expect(ok({ command: `p${"x".repeat(COMMAND_LIMIT - 1)}` }).command).toHaveLength(COMMAND_LIMIT)
    expect(problem({ command: `p${"x".repeat(COMMAND_LIMIT)}` })).toContain("at most 200")
  })

  it.each([
    ["an option", "--json"],
    ["a short option", "-x"],
    ["two lines", "pytest\nrm -rf /"],
    ["a carriage return inside", "pytest\rx"],
    ["a null character", "pytest\u0000"],
    ["a tab", "pytest\tx"],
    ["the delete character", "pytest\u007f"],
  ])("refuses a command that is %s", (_what, command) => {
    expect(problem({ command })).toContain("one line")
  })

  it.each([
    ["a text for the attempts", { attempts: "many" }],
    ["decimals for the attempts", { attempts: 2.5 }],
    ["a negative number of attempts", { attempts: -1 }],
    ["an exponent for the attempts", { attempts: "1e1" }],
    ["a number with a sign", { attempts: "+3" }],
    ["a boolean for the error", { error: true }],
    ["a text for the error", { error: "first" }],
    ["a list for the attempts", { attempts: [3] }],
    ["a command that is not text", { command: ["pytest"] }],
  ])("refuses %s", (_what, value) => {
    expect(problem(value)).not.toBe("")
  })

  it("refuses choices that are not a list of choices", () => {
    for (const value of ["pytest", 3, true, ["pytest"]]) {
      expect(problem(value)).toBe("The options are not a list of choices.")
    }
  })
})
