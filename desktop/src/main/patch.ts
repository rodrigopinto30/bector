import { createHash } from "node:crypto"

import type { FixEdit } from "../shared/api"

const TEXT_LIMIT = 500_000
const EDITS_LIMIT = 100
const PATH_LIMIT = 1_000
const SIDE_LIMIT = 100_000
const DESCRIPTION_LIMIT = 5_000

export interface PatchView {
  description: string
  edits: FixEdit[]
}

/** Identifies exactly this text, so that what is applied is what was reviewed. */
export function patchId(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex")
}

/** Whether a value is what the page may give back as the id of a fix. */
export function isPatchId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value)
}

/**
 * What a saved fix changes, to be shown. It is only read for the review: what is applied is the
 * system's own reading of the same text. Null when it is not a fix the app can show.
 */
export function readPatch(text: string): PatchView | null {
  if (text.length > TEXT_LIMIT) {
    return null
  }
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof value !== "object" || value === null) {
    return null
  }
  const record = value as Record<string, unknown>
  const raw = record["edits"]
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > EDITS_LIMIT) {
    return null
  }
  const description = record["description"] ?? ""
  if (typeof description !== "string" || description.length > DESCRIPTION_LIMIT) {
    return null
  }
  const edits: FixEdit[] = []
  for (const item of raw as unknown[]) {
    if (typeof item !== "object" || item === null) {
      return null
    }
    const edit = item as Record<string, unknown>
    const path = edit["path"]
    const before = edit["old"]
    const after = edit["new"]
    if (
      typeof path !== "string" ||
      path === "" ||
      path.length > PATH_LIMIT ||
      path.includes("\0") ||
      typeof before !== "string" ||
      before.length > SIDE_LIMIT ||
      typeof after !== "string" ||
      after.length > SIDE_LIMIT
    ) {
      return null
    }
    edits.push({ path, old: before, new: after })
  }
  return { description, edits }
}

/** The files a fix changes, each once, in the order it first names them. */
export function filesOf(edits: readonly FixEdit[]): string[] {
  return [...new Set(edits.map((edit) => edit.path))]
}
