import type { KeyCheck } from "../shared/api"

/** Listing the models spends no tokens: it only asks Anthropic whether the key is accepted. */
export const MODELS_URL = "https://api.anthropic.com/v1/models?limit=1"
export const API_VERSION = "2023-06-01"

const SHAPE = /^sk-ant-[A-Za-z0-9_-]{20,}$/
const PREFIX = "sk-ant-"

export interface FetchInit {
  method: string
  headers: Record<string, string>
  signal: AbortSignal
}

export type Fetch = (url: string, init: FetchInit) => Promise<{ status: number }>

/** What was pasted, without the spaces, line breaks or quotes that come along with a copy. */
export function normalizeKey(text: string): string {
  return text
    .trim()
    .replace(/^["']|["']$/g, "")
    .trim()
}

export function looksLikeKey(key: string): boolean {
  return SHAPE.test(key)
}

/** For example sk-ant-…ab12: enough to recognize the key, never enough to use it. */
export function maskKey(key: string): string {
  return `${PREFIX}…${key.slice(-4)}`
}

/**
 * Asks Anthropic whether it accepts ``key``. The key is sent only to MODELS_URL, as a header,
 * and never appears in what this returns or throws. A text that does not look like a key is
 * not sent anywhere.
 */
export async function checkKey(key: string, fetchFn: Fetch, timeoutMs = 10_000): Promise<KeyCheck> {
  if (!looksLikeKey(key)) {
    return { state: "malformed" }
  }
  let status: number
  try {
    const answer = await fetchFn(MODELS_URL, {
      method: "GET",
      headers: { "x-api-key": key, "anthropic-version": API_VERSION },
      signal: AbortSignal.timeout(timeoutMs),
    })
    status = answer.status
  } catch {
    return { state: "offline" }
  }
  if (status === 200) {
    return { state: "valid" }
  }
  if (status === 429) {
    // Anthropic checks the key before it limits the requests, so the key is accepted.
    return { state: "limited" }
  }
  if (status === 401) {
    return { state: "invalid" }
  }
  if (status === 403) {
    return { state: "forbidden" }
  }
  return { state: "unavailable" }
}

export function isUsable(check: KeyCheck): boolean {
  return check.state === "valid" || check.state === "limited"
}
