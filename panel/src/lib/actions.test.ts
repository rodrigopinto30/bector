import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

type Call = { url: string; init?: RequestInit }

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("actions", () => {
  let calls: Call[]

  beforeEach(() => {
    vi.resetModules()
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function server(...replies: Response[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init })
        const reply = replies.shift()
        if (reply === undefined) {
          throw new Error("no more replies")
        }
        return reply
      }),
    )
  }

  it("carry the session's token as JSON, asking for the token once", async () => {
    server(
      answer(200, { token: "secret-token", actions: true }),
      answer(200, { ok: true, message: "Forgotten." }),
      answer(200, { ok: true, message: "Sent 1 fix." }),
    )
    const { act } = await import("@/lib/actions")
    expect(await act("team/forget", { fix: "f".repeat(64) })).toEqual({
      ok: true,
      message: "Forgotten.",
    })
    expect(await act("outbox/send")).toEqual({ ok: true, message: "Sent 1 fix." })
    expect(calls.map((call) => call.url)).toEqual([
      "/api/session",
      "/api/actions/team/forget",
      "/api/actions/outbox/send",
    ])
    const sent = calls[1].init
    expect(sent?.method).toBe("POST")
    expect(sent?.headers).toEqual({
      "Content-Type": "application/json",
      "X-Healer-Token": "secret-token",
    })
    expect(sent?.body).toBe(JSON.stringify({ fix: "f".repeat(64) }))
    expect(calls[2].init?.body).toBe("{}")
  })

  it("say why the server refused", async () => {
    server(
      answer(200, { token: "t", actions: true }),
      answer(403, { error: "an action needs the token of this panel's session" }),
    )
    const { act } = await import("@/lib/actions")
    expect(await act("outbox/send")).toEqual({
      ok: false,
      message: "an action needs the token of this panel's session",
    })
  })

  it("do nothing when the panel takes no actions", async () => {
    server(answer(200, { token: null, actions: false }))
    const { act } = await import("@/lib/actions")
    expect(await act("outbox/send")).toEqual({
      ok: false,
      message: "This panel does not take actions.",
    })
    expect(calls).toHaveLength(1)
  })

  it("ask for the session again after it could not be read", async () => {
    server(
      answer(503, {}),
      answer(200, { token: "t", actions: true }),
      answer(200, { ok: false, message: "That fix is no longer waiting." }),
    )
    const { act } = await import("@/lib/actions")
    expect(await act("outbox/send")).toEqual({
      ok: false,
      message: "the panel server answered 503",
    })
    expect(await act("outbox/discard", { topic: "t", fix: "f" })).toEqual({
      ok: false,
      message: "That fix is no longer waiting.",
    })
  })
})
