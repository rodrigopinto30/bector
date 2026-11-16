import { readFile } from "node:fs/promises"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import type { DockerStatus, KeyCheck, KeySave, KeyStatus, StackStatus } from "../shared/api"
import {
  canStart,
  checkMessage,
  CHECKING_NOTICE,
  ACTION_FAILED,
  controlLabel,
  ICONS,
  dockerCard,
  EMPTY_NOTICE,
  KEY_CHECK_FAILED,
  KeyMessage,
  keyCard,
  PANEL_DOWN,
  removeNotice,
  SAVE_FAILED_NOTICE,
  saveNotice,
  SECTIONS,
  stackCard,
  STATE_ICONS,
  STATE_LABELS,
} from "./app"

const READY: KeyStatus = { vault: "ready", reason: "", stored: false, masked: "" }

describe("the Docker card", () => {
  it("shows what is going on", () => {
    expect(dockerCard(null)).toMatchObject({ tone: "idle" })
    expect(dockerCard({ ok: true, version: "27.3.1" })).toEqual({
      tone: "good",
      title: "Docker is ready",
      lines: ["Version 27.3.1."],
    })
    expect(dockerCard({ ok: true, version: "" }).lines).toEqual(["It answers without sudo."])
  })

  it("says what is wrong and what to do", () => {
    const status: DockerStatus = {
      ok: false,
      problem: "daemon",
      message: "Docker is installed, but it is not running.",
      hint: "Start it with: sudo systemctl start docker.",
    }
    expect(dockerCard(status)).toEqual({ tone: "bad", title: status.message, lines: [status.hint] })
  })
})

describe("what Anthropic said about a key", () => {
  const states: KeyCheck["state"][] = [
    "valid",
    "limited",
    "invalid",
    "forbidden",
    "unavailable",
    "offline",
    "malformed",
  ]

  it("has words for every answer, and good only for a key that works", () => {
    for (const state of states) {
      const said = checkMessage({ state })
      expect(said.text.length).toBeGreaterThan(10)
      expect(said.tone === "good").toBe(state === "valid" || state === "limited")
    }
  })

  it("separates a bad key from a problem that is not the key's", () => {
    expect(checkMessage({ state: "invalid" }).tone).toBe("bad")
    expect(checkMessage({ state: "offline" }).tone).toBe("warn")
    expect(checkMessage({ state: "unavailable" }).tone).toBe("warn")
  })
})

describe("the key card", () => {
  it("covers each situation", () => {
    expect(keyCard(null).tone).toBe("idle")
    expect(
      keyCard({
        vault: "unavailable",
        reason: "No secret store was found.",
        stored: false,
        masked: "",
      }),
    ).toMatchObject({
      tone: "warn",
      lines: ["No secret store was found.", "You can still use the app without a key."],
    })
    expect(keyCard({ ...READY, stored: true, masked: "sk-ant-…ab12" })).toEqual({
      tone: "good",
      title: "Your Anthropic key is saved",
      lines: ["sk-ant-…ab12", "It is kept in this computer's secret store."],
    })
    const none = keyCard(READY)
    expect(none.tone).toBe("warn")
    expect(none.lines.join(" ")).toMatch(/cannot ask Claude/)
  })

  it("says when the services already have a key that this app did not save", () => {
    const card = keyCard(READY, true)
    expect(card).toMatchObject({ tone: "good", title: "The services already have a key" })
    expect(card.lines[0]).toMatch(/did not save it/)
    expect(keyCard(READY, false).title).toBe("No Anthropic key yet")
    expect(keyCard({ ...READY, stored: true, masked: "sk-ant-…ab12" }, true).title).toBe(
      "Your Anthropic key is saved",
    )
    expect(keyCard({ ...READY, reason: "The saved key could not be read." }, true).title).toBe(
      "No Anthropic key yet",
    )
  })

  it("explains a saved key that could not be read", () => {
    const card = keyCard({ ...READY, reason: "The saved key could not be read. Enter it again." })
    expect(card.lines[0]).toBe("The saved key could not be read. Enter it again.")
  })

  it("never shows a whole key", () => {
    const text = JSON.stringify(keyCard({ ...READY, stored: true, masked: "sk-ant-…ab12" }))
    expect(text).not.toMatch(/sk-ant-[A-Za-z0-9]/)
  })
})

describe("the services card", () => {
  const running: StackStatus = { running: true, services: ["cli", "panel"], keySeen: true }

  it("shows that it is working, running or stopped", () => {
    expect(stackCard(null, true, "").title).toBe("Starting the services…")
    expect(stackCard(running, false, "")).toMatchObject({
      tone: "good",
      lines: ["The system sees your Anthropic key."],
    })
    expect(stackCard({ ...running, keySeen: false }, false, "").lines[0]).toBe(
      "The system does not see an Anthropic key.",
    )
    expect(stackCard({ running: false, services: ["cli"], keySeen: null }, false, "")).toEqual({
      tone: "warn",
      title: "The services are stopped",
      lines: ["Running: cli."],
    })
    expect(stackCard({ running: false, services: [], keySeen: null }, false, "").lines).toEqual([
      "Running: none.",
    ])
  })

  it("adds what the last start said", () => {
    expect(stackCard(running, false, "The services did not start.").lines).toContain(
      "The services did not start.",
    )
    expect(stackCard(null, false, "Something happened.").lines).toEqual(["Something happened."])
  })

  it("lets the user start only when Docker works and nothing else is going on", () => {
    expect(canStart({ ok: true, version: "27" }, false)).toBe(true)
    expect(canStart({ ok: true, version: "27" }, true)).toBe(false)
    expect(canStart(null, false)).toBe(false)
    expect(canStart({ ok: false, problem: "missing", message: "", hint: "" }, false)).toBe(false)
  })
})

describe("what is said after saving a key", () => {
  const saved: KeyStatus = { vault: "ready", reason: "", stored: true, masked: "sk-ant-…ab12" }
  const unsaved: KeyStatus = { vault: "ready", reason: "", stored: false, masked: "" }

  it("says clearly that the key was saved, and what happens next", () => {
    const outcome: KeySave = { saved: true, check: { state: "valid" }, status: saved }
    const running = saveNotice(outcome, true)
    expect(running).toMatchObject({ tone: "good", title: "Key saved" })
    expect(running.text).toMatch(/stored encrypted in this computer's secret store as sk-ant-…ab12/)
    expect(running.text).toMatch(/Restart the services below/)
    expect(saveNotice(outcome, false).text).toMatch(/Start the services below/)
  })

  it("mentions that Anthropic is limiting a key that works", () => {
    const outcome: KeySave = { saved: true, check: { state: "limited" }, status: saved }
    expect(saveNotice(outcome, false).text).toMatch(/limiting its requests/)
  })

  it("tells a key that works but was not saved apart from a key that was saved", () => {
    const refused: KeySave = {
      saved: false,
      check: { state: "valid" },
      status: { ...unsaved, vault: "unavailable", reason: "No secret store was found." },
    }
    expect(saveNotice(refused, true)).toEqual({
      tone: "bad",
      title: "The key works, but it was not saved",
      text: "No secret store was found.",
    })
    const without = saveNotice({ ...refused, status: unsaved }, true)
    expect(without.text).toBe("This computer's secret store refused it.")
  })

  it.each<[KeyCheck["state"], string]>([
    ["invalid", "bad"],
    ["forbidden", "bad"],
    ["malformed", "bad"],
    ["offline", "warn"],
    ["unavailable", "warn"],
  ])("explains why a key was not saved when Anthropic said %s", (state, tone) => {
    const notice = saveNotice({ saved: false, check: { state }, status: unsaved }, false)
    expect(notice).toMatchObject({ tone, title: "The key was not saved" })
    expect(notice.text.length).toBeGreaterThan(10)
  })

  it("says that the key already saved is unchanged when a new one is refused", () => {
    const stored: KeyStatus = { vault: "ready", reason: "", stored: true, masked: "sk-ant-…ab12" }
    const refused = saveNotice({ saved: false, check: { state: "invalid" }, status: stored }, true)
    expect(refused.text).toMatch(/The key you had saved is unchanged\./)
    const none = saveNotice({ saved: false, check: { state: "invalid" }, status: unsaved }, true)
    expect(none.text).not.toMatch(/unchanged/)
  })

  it("never claims success for something that was not saved", () => {
    for (const state of ["invalid", "offline", "malformed", "forbidden", "unavailable"] as const) {
      const notice = saveNotice({ saved: false, check: { state }, status: unsaved }, true)
      expect(notice.tone).not.toBe("good")
      expect(notice.title).toMatch(/not saved/)
    }
  })

  it("never leaves the page stuck when something unplanned breaks", () => {
    expect(SAVE_FAILED_NOTICE.tone).toBe("bad")
    expect(ACTION_FAILED.ok).toBe(false)
    expect(keyCard(KEY_CHECK_FAILED).tone).toBe("warn")
    expect(keyCard(KEY_CHECK_FAILED).lines).toContain(KEY_CHECK_FAILED.reason)
  })

  it("says while it checks, and when the key is removed", () => {
    expect(CHECKING_NOTICE.tone).toBe("idle")
    expect(removeNotice()).toMatchObject({ tone: "good", title: "Key removed" })
  })
})

describe("a refused key, when the vault was not asked", () => {
  it("is explained without a status, and keeps saying a saved key is unchanged", () => {
    const refused: KeySave = { saved: false, check: { state: "invalid" }, status: null }
    const none = saveNotice(refused, true)
    expect(none).toMatchObject({ tone: "bad", title: "The key was not saved" })
    expect(none.text).not.toMatch(/unchanged/)
    const stored: KeyStatus = { vault: "ready", reason: "", stored: true, masked: "sk-ant-…ab12" }
    expect(saveNotice(refused, true, stored).text).toMatch(/The key you had saved is unchanged\./)
  })

  it("asks for a key when the field is empty, instead of doing nothing", () => {
    expect(EMPTY_NOTICE.tone).toBe("warn")
    expect(EMPTY_NOTICE.text).toMatch(/Paste your Anthropic key/)
  })
})

describe("the message about the last attempt with the key", () => {
  const wrong = saveNotice({ saved: false, check: { state: "malformed" }, status: null }, false)

  it("is shown until something else happens, and then it goes", () => {
    const message = new KeyMessage()
    expect(message.notice).toBeNull()
    expect(message.dismiss()).toBe(false)
    message.show(wrong)
    expect(message.notice).toBe(wrong)
    expect(message.dismiss()).toBe(true)
    expect(message.notice).toBeNull()
    expect(message.dismiss()).toBe(false)
  })

  it("is replaced at once when a new attempt starts, so an old message never stays", () => {
    const message = new KeyMessage()
    message.show(wrong)
    message.begin()
    expect(message.checking).toBe(true)
    expect(message.notice).toBe(CHECKING_NOTICE)
    expect(message.notice).not.toBe(wrong)
  })

  it("cannot be dismissed while a key is being checked, because its answer is due", () => {
    const message = new KeyMessage()
    message.begin()
    expect(message.dismiss()).toBe(false)
    expect(message.notice).toBe(CHECKING_NOTICE)
  })

  it("gives way to the answer, and stops checking", () => {
    const message = new KeyMessage()
    message.begin()
    const saved = saveNotice(
      {
        saved: true,
        check: { state: "valid" },
        status: { vault: "ready", reason: "", stored: true, masked: "sk-ant-…ab12" },
      },
      false,
    )
    message.show(saved)
    expect(message.checking).toBe(false)
    expect(message.notice).toBe(saved)
    expect(message.dismiss()).toBe(true)
  })
})

describe("the window", () => {
  it("has its sections in the menu, the panel first and the settings", () => {
    expect(SECTIONS.map((section) => section.id)).toEqual(["panel", "settings"])
    expect(SECTIONS.every((section) => section.label.length > 0)).toBe(true)
  })

  it("names the button that maximizes or restores the window", () => {
    expect(controlLabel(false)).toBe("Maximize")
    expect(controlLabel(true)).toBe("Restore")
  })

  it("says where to go when the panel is not running", () => {
    expect(PANEL_DOWN.tone).toBe("warn")
    expect(PANEL_DOWN.lines.join(" ")).toMatch(/Open Settings/)
  })

  it("says that a new key is waiting for the services to restart", () => {
    const running: StackStatus = { running: true, services: ["cli"], keySeen: true }
    expect(stackCard(running, false, "", true).lines).toContain(
      "A new key is waiting: restart the services to use it.",
    )
    expect(stackCard(running, false, "", false).lines.join(" ")).not.toMatch(/waiting/)
  })
})

describe("state is never shown by color alone", () => {
  it("gives each tone its own icon, and a name for those who cannot see it", () => {
    const shapes = Object.values(STATE_ICONS).map((paths) => paths.join(" "))
    expect(new Set(shapes).size).toBe(4)
    expect(new Set(Object.values(STATE_LABELS)).size).toBe(4)
  })

  it("never uses a cross for a state, because the cross is the button that closes", () => {
    const cross = new Set<string>(ICONS.close)
    for (const paths of Object.values(STATE_ICONS)) {
      expect(paths.some((path) => cross.has(path))).toBe(false)
    }
  })
})

describe("the page's own code", () => {
  it("builds the page without markup from text, and never evaluates anything", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).not.toMatch(
      /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/,
    )
    expect(source).not.toMatch(/\.style\.|setAttribute\(["']style/)
  })

  it("gives a message a button to close it, except while its answer is due", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).toMatch(/"banner-close"/)
    expect(source).toMatch(/aria-label",\s*"Dismiss this message"/)
    expect(source).toMatch(/keyMessage\.checking\s*\?\s*undefined\s*:\s*closeMessage/)
  })

  it("has a title bar the window can be dragged by, with its buttons apart from the drag", async () => {
    const css = await readFile(join(__dirname, "styles.css"), "utf8")
    expect(css).toMatch(/\.titlebar\s*\{[^}]*-webkit-app-region:\s*drag/)
    expect(css).toMatch(/\.controls\s*\{[^}]*-webkit-app-region:\s*no-drag/)
  })

  it("puts the window buttons on the right of the title bar", async () => {
    const css = await readFile(join(__dirname, "styles.css"), "utf8")
    expect(css).toMatch(/\.titlebar\s*\{[^}]*justify-content:\s*space-between/)
    const template = await readFile(join(__dirname, "index.html"), "utf8")
    expect(template.indexOf('id="brand"')).toBeLessThan(template.indexOf('id="controls"'))
  })

  it("does not animate the layout, because the panel is a view that can only follow it", async () => {
    const css = await readFile(join(__dirname, "styles.css"), "utf8")
    expect(css).not.toMatch(/transition\s*:/)
    expect(css).not.toMatch(/@keyframes|animation\s*:/)
  })

  it("keeps the scrollbar space of the content, so changing section moves nothing", async () => {
    const css = await readFile(join(__dirname, "styles.css"), "utf8")
    expect(css).toMatch(/\.content\s*\{[^}]*scrollbar-gutter:\s*stable/)
  })

  it("keeps the template free of what the security policy would block", async () => {
    const template = await readFile(join(__dirname, "index.html"), "utf8")
    expect(template).not.toMatch(/\son[a-z]+=|\sstyle=|<script[^>]*\ssrc=|<link/i)
  })
})
