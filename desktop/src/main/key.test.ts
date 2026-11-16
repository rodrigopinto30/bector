import { describe, expect, it } from "vitest"

import {
  API_VERSION,
  checkKey,
  isUsable,
  looksLikeKey,
  maskKey,
  MODELS_URL,
  normalizeKey,
  type Fetch,
  type FetchInit,
} from "./key"

const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`

function replying(status: number): {
  fetchFn: Fetch
  seen: Array<{ url: string; init: FetchInit }>
} {
  const seen: Array<{ url: string; init: FetchInit }> = []
  const fetchFn: Fetch = async (url, init) => {
    seen.push({ url, init })
    return { status }
  }
  return { fetchFn, seen }
}

describe("the text of a key", () => {
  it("loses the spaces, line breaks and quotes that come with a copy", () => {
    expect(normalizeKey(`  "${KEY}"\n`)).toBe(KEY)
    expect(normalizeKey(`'${KEY}'`)).toBe(KEY)
    expect(normalizeKey(KEY)).toBe(KEY)
  })

  it("is recognized by its shape, and nothing else is a key", () => {
    expect(looksLikeKey(KEY)).toBe(true)
    for (const text of [
      "",
      "hello",
      "sk-ant-",
      "sk-ant-short",
      `${KEY} extra`,
      `x${KEY}`,
      "sk-proj-" + "a".repeat(40),
    ]) {
      expect(looksLikeKey(text)).toBe(false)
    }
  })

  it("is masked to its start and its last four characters", () => {
    const masked = maskKey(KEY)
    expect(masked).toBe(`sk-ant-…${KEY.slice(-4)}`)
    expect(KEY).not.toContain(masked)
    expect(masked).not.toContain(KEY.slice(10, 30))
  })
})

describe("asking Anthropic about a key", () => {
  it.each([
    [200, "valid"],
    [429, "limited"],
    [401, "invalid"],
    [403, "forbidden"],
    [404, "unavailable"],
    [500, "unavailable"],
    [529, "unavailable"],
  ])("a %i answer means %s", async (status, state) => {
    const { fetchFn } = replying(status)
    expect(await checkKey(KEY, fetchFn)).toEqual({ state })
  })

  it("sends the key only to the models list, as a header, with no tokens spent", async () => {
    const { fetchFn, seen } = replying(200)
    await checkKey(KEY, fetchFn)
    expect(seen).toHaveLength(1)
    expect(seen[0]?.url).toBe(MODELS_URL)
    expect(new URL(MODELS_URL).origin).toBe("https://api.anthropic.com")
    expect(seen[0]?.init.method).toBe("GET")
    expect(seen[0]?.init.headers).toEqual({ "x-api-key": KEY, "anthropic-version": API_VERSION })
    expect(MODELS_URL).not.toContain(KEY)
  })

  it("does not send something that does not look like a key", async () => {
    const { fetchFn, seen } = replying(200)
    expect(await checkKey("hello world", fetchFn)).toEqual({ state: "malformed" })
    expect(seen).toEqual([])
  })

  it("is offline when the request fails, and the failure never shows the key", async () => {
    const failing: Fetch = async () => {
      throw new Error(`getaddrinfo failed for a request with ${KEY}`)
    }
    const check = await checkKey(KEY, failing)
    expect(check).toEqual({ state: "offline" })
    expect(JSON.stringify(check)).not.toContain(KEY)
  })

  it("gives up after the time it was given", async () => {
    const slow: Fetch = (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("aborted")))
      })
    expect(await checkKey(KEY, slow, 20)).toEqual({ state: "offline" })
  })

  it("accepts a key only when it works", () => {
    expect(isUsable({ state: "valid" })).toBe(true)
    expect(isUsable({ state: "limited" })).toBe(true)
    for (const state of ["invalid", "forbidden", "unavailable", "offline", "malformed"] as const) {
      expect(isUsable({ state })).toBe(false)
    }
  })
})
