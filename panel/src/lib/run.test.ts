import { describe, expect, it } from "vitest"

import run from "@/lib/fixtures/run.json"
import {
  afterwards,
  attemptCost,
  attemptTitle,
  baselineSummary,
  testsSummary,
  verdictTitle,
} from "@/lib/run"
import type { Run, RunOfTests } from "@/lib/types"

const sample = run as Run

function tests(changes: Partial<RunOfTests>): RunOfTests {
  return {
    exit_code: 1,
    duration_seconds: 1.42,
    timed_out: false,
    passed: false,
    failures: [],
    more_failures: 0,
    ...changes,
  }
}

describe("a run, step by step", () => {
  it("says what each run of the tests found", () => {
    expect(testsSummary(tests({ passed: true, exit_code: 0 }))).toBe("the tests pass (1.4 s)")
    expect(testsSummary(tests({ timed_out: true, exit_code: null }))).toBe(
      "the tests timed out after 1.4 s",
    )
    expect(testsSummary(tests({}))).toBe("the tests fail with exit code 1 (1.4 s)")
    expect(
      testsSummary(tests({ failures: ["KeyError: 'db'", "KeyError: 'x'"], more_failures: 3 })),
    ).toBe("the tests fail: KeyError: 'db', and 4 more")
  })

  it("starts with how the tests failed and ends with how the run did", () => {
    expect(baselineSummary(tests({}))).toBe("exit code 1 in 1.4 s")
    expect(baselineSummary(tests({ failures: ["a", "b"], more_failures: 1 }))).toBe(
      "exit code 1 in 1.4 s, 3 errors",
    )
    expect(baselineSummary(tests({ timed_out: true }))).toBe("timed out after 1.4 s")
    expect(verdictTitle(sample)).toBe("Fixed by attempt 2")
    expect(verdictTitle({ ...sample, status: "out_of_attempts" })).toBe("Out of attempts")
  })

  it("names each attempt and what it cost", () => {
    const [first, second] = sample.attempts
    expect(attemptTitle(first)).toBe("Attempt 1 · Team, from laptop-eva")
    expect(attemptCost(first)).toBe("0 tokens")
    expect(attemptCost(second)).toBe("0 tokens, about 4,800 saved")
    expect(attemptCost({ ...first, source: "model", input_tokens: 1200, output_tokens: 300 })).toBe(
      "1,200 tokens in, 300 out",
    )
  })

  it("ends with what happened to the fix", () => {
    expect(afterwards(sample)).toEqual([
      "Applied to your files",
      "Not sent again: it came from the team",
      "The team was told 2 results of its fixes",
    ])
    expect(afterwards({ ...sample, applied: false, shared: null, results_told: 0 })).toEqual([
      "Not applied to your files",
    ])
    expect(afterwards({ ...sample, shared: "waiting", results_told: 1 })).toEqual([
      "Applied to your files",
      "Waiting in the outbox to be sent to the team",
      "The team was told 1 result of its fixes",
    ])
  })
})
