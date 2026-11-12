import { formatFull, STATUS_LABELS } from "@/lib/format"
import type { AttemptOutcome, AttemptSource, Run, RunAttempt, RunOfTests } from "@/lib/types"

export const SOURCE_LABELS: Record<AttemptSource, string> = {
  cache: "Local cache",
  team: "Team",
  model: "Claude",
}

export const OUTCOME_WORDS: Record<AttemptOutcome, string> = {
  passed: "passed",
  failed: "failed",
  rejected: "did not apply",
  repeated: "repeated",
}

function seconds(value: number): string {
  return `${value.toFixed(1)} s`
}

/** What a run of the tests said, in one line. */
export function testsSummary(tests: RunOfTests): string {
  if (tests.timed_out) {
    return `the tests timed out after ${seconds(tests.duration_seconds)}`
  }
  if (tests.passed) {
    return `the tests pass (${seconds(tests.duration_seconds)})`
  }
  const [first] = tests.failures
  if (first === undefined) {
    return `the tests fail with exit code ${tests.exit_code} (${seconds(tests.duration_seconds)})`
  }
  const more = tests.failures.length - 1 + tests.more_failures
  return `the tests fail: ${first}${more ? `, and ${more} more` : ""}`
}

/** How the first run of the tests ended, without repeating its error. */
export function baselineSummary(tests: RunOfTests): string {
  if (tests.timed_out) {
    return `timed out after ${seconds(tests.duration_seconds)}`
  }
  const errors = tests.failures.length + tests.more_failures
  const more = errors > 1 ? `, ${errors} errors` : ""
  return `exit code ${tests.exit_code} in ${seconds(tests.duration_seconds)}${more}`
}

/** The last step: how the run ended, and with which attempt when it was fixed. */
export function verdictTitle(run: Run): string {
  const fix = run.attempts.find((attempt) => attempt.outcome === "passed")
  return run.status === "fixed" && fix
    ? `Fixed by attempt ${fix.number}`
    : STATUS_LABELS[run.status]
}

export function attemptTitle(attempt: RunAttempt): string {
  const from = attempt.source === "team" && attempt.origin ? `, from ${attempt.origin}` : ""
  return `Attempt ${attempt.number} · ${SOURCE_LABELS[attempt.source]}${from}`
}

/** What the attempt cost, and what reusing a fix saved when it worked. */
export function attemptCost(attempt: RunAttempt): string {
  if (attempt.source === "model") {
    return `${formatFull(attempt.input_tokens)} tokens in, ${formatFull(attempt.output_tokens)} out`
  }
  return attempt.outcome === "passed" && attempt.saved_tokens
    ? `0 tokens, about ${formatFull(attempt.saved_tokens)} saved`
    : "0 tokens"
}

/** What happened once the run ended: applying, sharing and telling the team. */
export function afterwards(run: Run): string[] {
  const fix = run.attempts.find((attempt) => attempt.outcome === "passed")
  const lines: string[] = []
  if (run.applied === true) {
    lines.push("Applied to your files")
  } else if (run.applied === false) {
    lines.push("Not applied to your files")
  }
  if (run.shared === "sent") {
    lines.push("Sent to the team")
  } else if (run.shared === "waiting") {
    lines.push("Waiting in the outbox to be sent to the team")
  } else if (run.shared === "failed") {
    lines.push("Could not be sent to the team")
  } else if (run.shared === "off") {
    lines.push("Not sent to the team: sharing is off")
  } else if (run.applied && fix?.source === "team") {
    lines.push("Not sent again: it came from the team")
  }
  if (run.results_told) {
    const results = run.results_told === 1 ? "1 result" : `${run.results_told} results`
    lines.push(`The team was told ${results} of its fixes`)
  }
  return lines
}
