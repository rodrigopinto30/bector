import { describe, expect, it } from "vitest"

import type {
  Bounds,
  FixKind,
  FixResult,
  KeyStatus,
  ProjectInfo,
  Section,
  WindowAction,
} from "../shared/api"
import { createHandlers, type HandlerDeps } from "./handlers"
import { API_VERSION, type Fetch } from "./key"
import { VaultError } from "./vault"

const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`
const READY: KeyStatus = { vault: "ready", reason: "", stored: false, masked: "" }

const DEFAULT_PROJECT: ProjectInfo = {
  chosen: null,
  folder: "/repo/workspace",
  problem: "",
  hints: [],
}

const DONE = (kind: FixKind): FixResult => ({ kind, outcome: "passed", message: "ok" })

function setup(
  options: { status?: number; saveError?: Error; dialog?: string | null; refuse?: string } = {},
) {
  const { dialog = null } = options
  const asked: string[] = []
  const chosen: string[] = []
  let resets = 0
  let dialogs = 0
  const saved: string[] = []
  const opened: string[] = []
  const requests: Array<Record<string, string>> = []
  const started: boolean[] = []
  const fixed: string[] = []
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
      status: async () => ({
        running: false,
        services: [],
        keySeen: null,
        workspaceMatches: null,
      }),
      start: async (restart) => {
        started.push(restart === true)
        return { ok: true, message: "ok" }
      },
      stop: async () => ({ ok: true, message: "stopped" }),
    },
    workspace: {
      info: async () => DEFAULT_PROJECT,
      choose: async (path) => {
        chosen.push(path)
        if (options.refuse !== undefined) {
          return { changed: false, message: options.refuse, project: DEFAULT_PROJECT }
        }
        return {
          changed: true,
          message: "",
          project: { ...DEFAULT_PROJECT, chosen: path, folder: path },
        }
      },
      reset: async () => {
        resets += 1
        return DEFAULT_PROJECT
      },
    },
    fix: {
      status: async () => ({ running: null as FixKind | null, saved: false }),
      test: async (...given: unknown[]) => {
        fixed.push(`test(${JSON.stringify(given)})`)
        return DONE("test")
      },
      find: async (...given: unknown[]) => {
        fixed.push(`find(${JSON.stringify(given)})`)
        return DONE("fix")
      },
      review: async (...given: unknown[]) => {
        fixed.push(`review(${given.length})`)
        return { review: null, message: "There is no saved fix. Look for one first." }
      },
      apply: async (...given: unknown[]) => {
        fixed.push(`apply(${JSON.stringify(given)})`)
        return DONE("apply")
      },
      forget: async () => {
        fixed.push("forget")
      },
      cancel: async (...given: unknown[]) => {
        fixed.push(`cancel(${given.length})`)
      },
    },
    fetchFn,
    window: {
      chooseFolder: async () => {
        dialogs += 1
        return dialog
      },
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
    fixed,
    shown,
    placed,
    controlled,
    statusCalls: () => statusCalls,
    asked,
    chosen,
    resets: () => resets,
    dialogs: () => dialogs,
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

describe("the project folder", () => {
  it("says which folder the project is", async () => {
    const { handlers } = setup()
    expect(await handlers["project:status"]()).toEqual(DEFAULT_PROJECT)
  })

  it("keeps the folder the dialog gives, and nothing else", async () => {
    const { handlers, chosen, dialogs } = setup({ dialog: "/home/ana/shop" })
    const answer = await handlers["project:choose"]()
    expect(dialogs()).toBe(1)
    expect(chosen).toEqual(["/home/ana/shop"])
    expect(answer).toMatchObject({ changed: true, project: { chosen: "/home/ana/shop" } })
  })

  it("never takes a folder from the page, which can only ask for the dialog", async () => {
    const { handlers, chosen } = setup({ dialog: "/home/ana/shop" })
    for (const fromPage of ["/", "/etc", "/home/ana", { folder: "/" }, ["/"], null]) {
      await (handlers["project:choose"] as (...values: unknown[]) => Promise<unknown>)(fromPage)
    }
    expect(chosen).toEqual(Array(6).fill("/home/ana/shop"))
  })

  it("changes nothing when the dialog is closed", async () => {
    const { handlers, chosen } = setup({ dialog: null })
    expect(await handlers["project:choose"]()).toEqual({
      changed: false,
      message: "",
      project: DEFAULT_PROJECT,
    })
    expect(chosen).toEqual([])
  })

  it("passes on why a folder was refused", async () => {
    const { handlers } = setup({ dialog: "/", refuse: "That is the root of the disk." })
    expect(await handlers["project:choose"]()).toMatchObject({
      changed: false,
      message: "That is the root of the disk.",
    })
  })

  it("goes back to the default folder", async () => {
    const { handlers, resets } = setup()
    expect(await handlers["project:reset"]()).toEqual(DEFAULT_PROJECT)
    expect(resets()).toBe(1)
  })
})

describe("the fix", () => {
  it("says what is running", async () => {
    const { handlers } = setup()
    expect(await handlers["fix:status"]()).toEqual({ running: null, saved: false })
  })

  const NOTHING = '[{"command":"","attempts":null,"error":1}]'

  it("runs the tests and looks for a fix with the system's own choices when the page chooses nothing", async () => {
    const { handlers, fixed } = setup()
    const asPage = (name: "fix:test" | "fix:find" | "fix:cancel") =>
      (handlers[name] as (...values: unknown[]) => Promise<unknown>)()
    expect(await asPage("fix:test")).toEqual(DONE("test"))
    expect(await asPage("fix:find")).toEqual(DONE("fix"))
    await asPage("fix:cancel")
    expect(fixed).toEqual([`test(${NOTHING})`, `find(${NOTHING})`, "cancel(0)"])
  })

  it("gives the fixer what the user chose, checked and cleaned, and nothing else the page says", async () => {
    const { handlers, fixed } = setup()
    const chosen = {
      command: "  pytest tests/unit -x  ",
      attempts: "3",
      error: 2,
      apply: true,
      path: "/etc",
    }
    const asPage = handlers["fix:find"] as (...values: unknown[]) => Promise<unknown>
    await asPage(chosen)
    await (handlers["fix:test"] as (...values: unknown[]) => Promise<unknown>)({
      command: "python -m pytest",
      attempts: null,
    })
    expect(fixed).toEqual([
      'find([{"command":"pytest tests/unit -x","attempts":3,"error":2}])',
      'test([{"command":"python -m pytest","attempts":null,"error":1}])',
    ])
  })

  it.each([
    ["a command that is not text", { command: 7 }],
    ["a command that starts as an option", { command: "--json" }],
    ["a command of several lines", { command: "pytest\nrm -rf /" }],
    ["a command with a control character", { command: "pytest\u0000" }],
    ["a command that is too long", { command: `pytest ${"x".repeat(200)}` }],
    ["no attempts at all", { attempts: 0 }],
    ["too many attempts", { attempts: 11 }],
    ["attempts that are not a number", { attempts: "many" }],
    ["attempts that are not whole", { attempts: 1.5 }],
    ["an error number of zero", { error: 0 }],
    ["an error number that is too high", { error: 101 }],
    ["an error number that is not a number", { error: "first" }],
    ["choices that are a text", "pytest"],
    ["choices that are a list", ["pytest"]],
  ])("refuses %s, and runs nothing", async (_name, choice) => {
    const { handlers, fixed } = setup()
    for (const name of ["fix:test", "fix:find"] as const) {
      expect(() => (handlers[name] as (...values: unknown[]) => unknown)(choice)).toThrow()
    }
    expect(() =>
      (handlers["fix:apply"] as (...values: unknown[]) => unknown)("a".repeat(64), choice),
    ).toThrow()
    expect(fixed).toEqual([])
  })

  it("shows the saved fix without taking anything from the page", async () => {
    const { handlers, fixed } = setup()
    const review = handlers["fix:review"] as (...values: unknown[]) => Promise<unknown>
    expect(await review("--anything", { patch: "x" })).toEqual({
      review: null,
      message: "There is no saved fix. Look for one first.",
    })
    expect(fixed).toEqual(["review(0)"])
  })

  it("applies only the fix whose id the page gives back, and nothing else it says", async () => {
    const { handlers, fixed } = setup()
    const id = "a".repeat(64)
    const apply = handlers["fix:apply"] as (...values: unknown[]) => Promise<unknown>
    expect(await apply(id, { path: "/etc/passwd" }, "--yes")).toEqual(DONE("apply"))
    expect(fixed).toEqual([`apply(["${id}",{"command":"","attempts":null,"error":1}])`])
  })

  it("tests the fix again with the test command the user chose", async () => {
    const { handlers, fixed } = setup()
    const id = "b".repeat(64)
    const apply = handlers["fix:apply"] as (...values: unknown[]) => Promise<unknown>
    await apply(id, { command: "pytest tests/unit", attempts: 9, error: 4 })
    expect(fixed).toEqual([
      `apply(["${id}",{"command":"pytest tests/unit","attempts":9,"error":4}])`,
    ])
  })

  it.each([
    undefined,
    null,
    "",
    "latest",
    "A".repeat(64),
    "a".repeat(63),
    "a".repeat(65),
    `${"a".repeat(63)}g`,
    42,
    { id: "a".repeat(64) },
    ["a".repeat(64)],
  ])("refuses an id that is not one (%j)", async (id) => {
    const { handlers, fixed } = setup()
    expect(() => handlers["fix:apply"](id)).toThrow("not the id of a fix")
    expect(fixed).toEqual([])
  })

  it("forgets the saved fix when the project changes, and not otherwise", async () => {
    const { handlers, fixed } = setup({ dialog: "/home/ana/shop" })
    await handlers["project:choose"]()
    expect(fixed).toEqual(["forget"])
    const refused = setup({ dialog: "/", refuse: "That folder cannot be the project." })
    await refused.handlers["project:choose"]()
    expect(refused.fixed).toEqual([])
    const closed = setup({ dialog: null })
    await closed.handlers["project:choose"]()
    expect(closed.fixed).toEqual([])
    const reset = setup()
    await reset.handlers["project:reset"]()
    expect(reset.fixed).toEqual(["forget"])
  })

  it("is a section the page may show", async () => {
    const { handlers, shown } = setup()
    await handlers["section:show"]("fix")
    expect(shown).toEqual(["fix"])
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
