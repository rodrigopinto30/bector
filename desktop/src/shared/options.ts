/**
 * What the user may choose about a run of the tests, a search for a fix and its application. Both
 * sides use these rules: the page to say what is wrong before asking, and the main process to
 * refuse anything else, since it is the one that starts the program.
 */

export interface FixOptions {
  /** The test command, or an empty text for the system's own. */
  command: string
  /** The most patches to try, or null for the system's own number. */
  attempts: number | null
  /** Which of the errors the tests show to fix; the first by default. */
  error: number
}

export const DEFAULT_OPTIONS: FixOptions = { command: "", attempts: null, error: 1 }

export const COMMAND_LIMIT = 200
export const ATTEMPTS_RANGE = { min: 1, max: 10 } as const
export const ERROR_RANGE = { min: 1, max: 100 } as const

export type OptionsCheck = { ok: true; options: FixOptions } | { ok: false; problem: string }

/** A control character would break the command into lines, or hide part of it. */
function hasControl(text: string): boolean {
  return [...text].some((character) => {
    const code = character.codePointAt(0) ?? 0
    return code < 32 || code === 127
  })
}
const INTEGER = /^\d{1,6}$/

function whole(value: unknown, min: number, max: number): number | null {
  const text = typeof value === "string" ? value.trim() : ""
  const number = INTEGER.test(text) ? Number(text) : value
  return typeof number === "number" && Number.isInteger(number) && number >= min && number <= max
    ? number
    : null
}

/**
 * Whether what was chosen can be used, and the options cleaned up. Unknown keys are dropped, an
 * absent value is the default, and the system itself still decides which commands are allowed.
 */
export function checkOptions(value: unknown): OptionsCheck {
  if (value === undefined || value === null) {
    return { ok: true, options: DEFAULT_OPTIONS }
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, problem: "The options are not a list of choices." }
  }
  const record = value as Record<string, unknown>
  const rawCommand = record["command"] ?? ""
  if (typeof rawCommand !== "string") {
    return { ok: false, problem: "The test command must be text." }
  }
  const command = rawCommand.trim()
  if (command.length > COMMAND_LIMIT || hasControl(command) || command.startsWith("-")) {
    return {
      ok: false,
      problem: `The test command must be one line of at most ${COMMAND_LIMIT} characters that starts with a command, such as pytest, and not with an option.`,
    }
  }
  const rawAttempts = record["attempts"] ?? null
  const none =
    rawAttempts === null || (typeof rawAttempts === "string" && rawAttempts.trim() === "")
  const attempts = none ? null : whole(rawAttempts, ATTEMPTS_RANGE.min, ATTEMPTS_RANGE.max)
  if (!none && attempts === null) {
    return {
      ok: false,
      problem: `The attempts must be a whole number from ${ATTEMPTS_RANGE.min} to ${ATTEMPTS_RANGE.max}.`,
    }
  }
  const error = whole(record["error"] ?? 1, ERROR_RANGE.min, ERROR_RANGE.max)
  if (error === null) {
    return {
      ok: false,
      problem: `The error must be a whole number from ${ERROR_RANGE.min} to ${ERROR_RANGE.max}.`,
    }
  }
  return { ok: true, options: { command, attempts, error } }
}
