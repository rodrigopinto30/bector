import { describe, expect, it } from "vitest"

import type { Bounds, KeyStatus, Section, WindowAction } from "../shared/api"
import { createHandlers, type HandlerDeps } from "./handlers"
import { API_VERSION, type Fetch } from "./key"
import { VaultError } from "./vault"

const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`
const READY: KeyStatus = { vault: "ready", reason: "", stored: false, masked: "" }

function setup(options: { status?: number; saveError?: Error } = {}) {
  const saved: string[] = []
  const opened: string[] = []
  const requests: Array<Record<string, string>> = []
  const started: boolean[] = []
  const shown: Section[] = []
  const placed: Bounds[] = []
  const controlled: WindowAction[] = []
  let stored = false
  let statusCalls = 0
  const fetchFn: Fetch = async (_url, init) => {
    requests.push(init.headers)
    return { status: options.status ?? 200 }
  }
  const deps: HandlerDeps = {
    docker: async () => ({ ok: true, version: "27" }),
    vault: {
      status: async () => {
        statusCalls += 1
        return stored ? { ...READY, stored, masked: "sk-ant-…_-Ab" } : READY
      },
      save: async (key) => {
        if (options.saveError) {
          throw options.saveError
        }
        saved.push(key)
        stored = true
      },
      remove: async () => {
        stored = false
      },
    },
    stack: {
      status: async () => ({ running: false, services: [], keySeen: null }),
      start: async (restart) => {
        started.push(restart === true)
        return { ok: true, message: "ok" }
      },
      stop: async () => ({ ok: true, message: "stopped" }),
    },
    fetchFn,
    window: {
      show: async (section) => {
        shown.push(section)
      },
      setBounds: (bounds) => {
        placed.push(bounds)
      },
      control: (action) => {
        controlled.push(action)
      },
    },
    openExternal: async (url) => {
      opened.push(url)
    },
  }
  return {
    handlers: createHandlers(deps),
    saved,
    opened,
    requests,
    started,
    shown,
    placed,
    controlled,
    statusCalls: () => statusCalls,
  }
}

describe("saving a key", () => {
  it("checks it, keeps it cleaned up, and never gives it back", async () => {
    const { handlers, saved, requests } = setup()
    const outcome = await handlers["key:save"](`  "${KEY}"\n`)
    expect(outcome.saved).toBe(true)
    expect(outcome.check).toEqual({ state: "valid" })
    expect(outcome.status?.masked).toBe("sk-ant-…_-Ab")
    expect(saved).toEqual([KEY])
    expect(requests[0]).toEqual({ "x-api-key": KEY, "anthropic-version": API_VERSION })
    expect(JSON.stringify(outcome)).not.toContain(KEY)
  })

  it.each([401, 403, 500])(
    "does not keep a key that Anthropic answers %i about",
    async (status) => {
      const { handlers, saved } = setup({ status })
      const outcome = await handlers["key:save"](KEY)
      expect(outcome.saved).toBe(false)
      expect(saved).toEqual([])
      expect(JSON.stringify(outcome)).not.toContain(KEY)
    },
  )

  it("keeps a key that works even if its account is being limited", async () => {
    const { handlers, saved } = setup({ status: 429 })
    expect(await handlers["key:save"](KEY)).toMatchObject({
      saved: true,
      check: { state: "limited" },
    })
    expect(saved).toEqual([KEY])
  })

  it("sends nothing for text that is not a key", async () => {
    const { handlers, requests } = setup()
    expect(await handlers["key:save"]("hello")).toMatchObject({
      saved: false,
      check: { state: "malformed" },
    })
    expect(requests).toEqual([])
  })

  it("says the key was not saved when the vault refuses, and why, without failing", async () => {
    const { handlers, statusCalls } = setup({
      saveError: new VaultError("No secret store was found."),
    })
    expect(await handlers["key:save"](KEY)).toEqual({
      saved: false,
      check: { state: "valid" },
      status: {
        vault: "unavailable",
        reason: "No secret store was found.",
        stored: false,
        masked: "",
      },
    })
    expect(statusCalls()).toBe(0)
  })

  it("does not ask the vault anything when Anthropic does not accept the key", async () => {
    const { handlers, statusCalls, saved } = setup({ status: 401 })
    expect(await handlers["key:save"](KEY)).toEqual({
      saved: false,
      check: { state: "invalid" },
      status: null,
    })
    expect(await handlers["key:save"]("hello")).toMatchObject({ status: null })
    expect(statusCalls()).toBe(0)
    expect(saved).toEqual([])
  })

  it("lets an unexpected failure through", async () => {
    const { handlers } = setup({ saveError: new Error("disk full") })
    await expect(handlers["key:save"](KEY)).rejects.toThrow("disk full")
  })

  it.each([42, null, undefined, { key: KEY }, "k".repeat(513)])(
    "rejects a bad argument (%s)",
    async (value) => {
      const { handlers, saved } = setup()
      await expect(handlers["key:save"](value)).rejects.toThrow(/the key must be a text/)
      expect(saved).toEqual([])
    },
  )

  it("removes the key", async () => {
    const { handlers } = setup()
    await handlers["key:save"](KEY)
    expect((await handlers["key:remove"]()).stored).toBe(false)
  })
})

describe("the rest of what the page asks", () => {
  it("restarts the services only when it is asked with exactly true", async () => {
    const { handlers, started } = setup()
    await handlers["stack:start"]()
    await handlers["stack:start"](true)
    await handlers["stack:start"]("true")
    await handlers["stack:start"](1)
    expect(started).toEqual([false, true, false, false])
  })

  it("opens only the links it allows", async () => {
    const { handlers, opened } = setup()
    await handlers["link:open"]("https://console.anthropic.com/settings/keys")
    for (const link of ["https://evil.example/", "file:///etc/passwd", "javascript:alert(1)"]) {
      await expect(handlers["link:open"](link)).rejects.toThrow("that link may not be opened")
    }
    await expect(handlers["link:open"](42)).rejects.toThrow(/the link must be a text/)
    await expect(
      handlers["link:open"]("https://console.anthropic.com/" + "a".repeat(200)),
    ).rejects.toThrow()
    expect(opened).toEqual(["https://console.anthropic.com/settings/keys"])
  })

  it("answers the checks of Docker and of the services", async () => {
    const { handlers } = setup()
    expect(await handlers["docker:check"]()).toEqual({ ok: true, version: "27" })
    expect(await handlers["stack:status"]()).toMatchObject({ running: false })
    expect(await handlers["stack:stop"]()).toEqual({ ok: true, message: "stopped" })
  })
})

describe("the window", () => {
  it("shows a section it knows, and no other", async () => {
    const { handlers, shown } = setup()
    await handlers["section:show"]("panel")
    await handlers["section:show"]("settings")
    for (const section of ["admin", "", 7, null, undefined, { id: "panel" }]) {
      await expect(handlers["section:show"](section)).rejects.toThrow("that section does not exist")
    }
    expect(shown).toEqual(["panel", "settings"])
  })

  it("places the panel where the page says its content is, in whole pixels", async () => {
    const { handlers, placed } = setup()
    await handlers["layout:content"]({ x: 232.4, y: 44, width: 1047.6, height: 760.2 })
    expect(placed).toEqual([{ x: 232, y: 44, width: 1048, height: 760 }])
  })

  it.each([
    null,
    "232",
    [],
    { x: 1, y: 2, width: 3 },
    { x: -1, y: 0, width: 5, height: 5 },
    { x: 0, y: 0, width: Number.NaN, height: 5 },
    { x: 0, y: 0, width: 5, height: Number.POSITIVE_INFINITY },
    { x: "0", y: 0, width: 5, height: 5 },
    { x: 0, y: 0, width: 20_001, height: 5 },
  ])("refuses bounds that are not a rectangle (%j)", async (bounds) => {
    const { handlers, placed } = setup()
    await expect(handlers["layout:content"](bounds)).rejects.toThrow(/bounds/)
    expect(placed).toEqual([])
  })

  it("does only the window actions it knows", async () => {
    const { handlers, controlled } = setup()
    for (const action of ["minimize", "toggleMaximize", "close"]) {
      await handlers["window:control"](action)
    }
    for (const action of ["quit", "destroy", "", 1, null, undefined]) {
      await expect(handlers["window:control"](action)).rejects.toThrow("window action")
    }
    expect(controlled).toEqual(["minimize", "toggleMaximize", "close"])
  })
})
