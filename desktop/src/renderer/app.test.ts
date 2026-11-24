import { readFile } from "node:fs/promises"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import { checkOptions } from "../shared/options"
import type {
  DockerStatus,
  FixOutcome,
  FixResult,
  FixReview,
  KeyCheck,
  KeySave,
  KeyStatus,
  ProjectChoice,
  ProjectInfo,
  StackStatus,
} from "../shared/api"
import {
  canFix,
  canStart,
  checkChoice,
  checkMessage,
  CHECKING_NOTICE,
  ACTION_FAILED,
  controlLabel,
  diffLines,
  errorChoices,
  ICONS,
  dockerCard,
  EMPTY_NOTICE,
  FIX_FAILED,
  fixCard,
  fixNotice,
  folderName,
  KEY_CHECK_FAILED,
  Message,
  OPTIONS_HELP,
  keyCard,
  PANEL_DOWN,
  PROJECT_CHECK_FAILED,
  PROJECT_RESET_NOTICE,
  projectCard,
  projectNotice,
  removeNotice,
  reviewCard,
  reviewSummary,
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

const RUNNING: StackStatus = {
  running: true,
  services: ["cli", "panel"],
  keySeen: true,
  workspaceMatches: true,
}

describe("the services card", () => {
  const running = RUNNING

  it("shows that it is working, running or stopped", () => {
    expect(stackCard(null, true, "").title).toBe("Starting the services…")
    expect(stackCard(running, false, "")).toMatchObject({
      tone: "good",
      lines: ["The system sees your Anthropic key."],
    })
    expect(stackCard({ ...running, keySeen: false }, false, "").lines[0]).toBe(
      "The system does not see an Anthropic key.",
    )
    expect(
      stackCard(
        { running: false, services: ["cli"], keySeen: null, workspaceMatches: null },
        false,
        "",
      ),
    ).toEqual({
      tone: "warn",
      title: "The services are stopped",
      lines: ["Running: cli."],
    })
    expect(
      stackCard({ running: false, services: [], keySeen: null, workspaceMatches: null }, false, "")
        .lines,
    ).toEqual(["Running: none."])
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
    const message = new Message()
    expect(message.notice).toBeNull()
    expect(message.dismiss()).toBe(false)
    message.show(wrong)
    expect(message.notice).toBe(wrong)
    expect(message.dismiss()).toBe(true)
    expect(message.notice).toBeNull()
    expect(message.dismiss()).toBe(false)
  })

  it("is replaced at once when a new attempt starts, so an old message never stays", () => {
    const message = new Message()
    message.show(wrong)
    message.begin()
    expect(message.checking).toBe(true)
    expect(message.notice).toBe(CHECKING_NOTICE)
    expect(message.notice).not.toBe(wrong)
  })

  it("cannot be dismissed while a key is being checked, because its answer is due", () => {
    const message = new Message()
    message.begin()
    expect(message.dismiss()).toBe(false)
    expect(message.notice).toBe(CHECKING_NOTICE)
  })

  it("gives way to the answer, and stops checking", () => {
    const message = new Message()
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

const SHOP = "/home/ana/shop"
const DEFAULT: ProjectInfo = { chosen: null, folder: "/repo/workspace", problem: "", hints: [] }
const CHOSEN: ProjectInfo = { chosen: SHOP, folder: SHOP, problem: "", hints: [] }

describe("the project card", () => {
  it("says it is checking until it knows", () => {
    expect(projectCard(null, null)).toEqual({
      tone: "idle",
      title: "Checking your project…",
      lines: [],
    })
  })

  it("shows the default folder, and says to put the project there or choose another", () => {
    const card = projectCard(DEFAULT, null)
    expect(card).toMatchObject({ tone: "idle", title: "The default folder" })
    expect(card.lines[0]).toBe("/repo/workspace")
    expect(card.lines.join(" ")).toMatch(/choose the folder where it already is/)
  })

  it("shows a chosen folder by its name, with its full path", () => {
    const card = projectCard(CHOSEN, RUNNING)
    expect(card).toEqual({
      tone: "good",
      title: "Your project: shop",
      lines: [SHOP, "The services are working on it."],
    })
  })

  it("says what is worth knowing about the folder without refusing it", () => {
    const hint = "It does not look like a Python project."
    const card = projectCard({ ...CHOSEN, hints: [hint] }, RUNNING)
    expect(card.tone).toBe("warn")
    expect(card.lines).toContain(hint)
  })

  it("says that the services stopped, or have another folder, and what to do", () => {
    const stopped: StackStatus = {
      running: false,
      services: [],
      keySeen: null,
      workspaceMatches: null,
    }
    expect(projectCard(CHOSEN, stopped).lines).toEqual([SHOP, "The services are stopped."])
    expect(projectCard(CHOSEN, null).lines).toContain("The services are stopped.")
    const other: StackStatus = { ...RUNNING, workspaceMatches: false }
    const chosen = projectCard(CHOSEN, other)
    expect(chosen.tone).toBe("warn")
    expect(chosen.lines).toContain(
      "The services still use another folder: restart them to use this one.",
    )
    const back = projectCard(DEFAULT, other)
    expect(back.tone).toBe("warn")
    expect(back.lines).toContain(
      "The services still use another folder: restart them to go back to this one.",
    )
  })

  it("says nothing about the services when it cannot be told which folder they have", () => {
    const unknown: StackStatus = { ...RUNNING, workspaceMatches: null }
    expect(projectCard(CHOSEN, unknown).lines).toEqual([SHOP])
  })

  it("says why a folder that was chosen cannot be used now", () => {
    const gone: ProjectInfo = {
      chosen: SHOP,
      folder: SHOP,
      problem: "The folder does not exist.",
      hints: [],
    }
    expect(projectCard(gone, RUNNING)).toEqual({
      tone: "bad",
      title: "The project folder cannot be used",
      lines: [
        SHOP,
        "The folder does not exist.",
        "Choose it again, or go back to the default folder.",
      ],
    })
  })

  it("names a folder by its last part", () => {
    expect(folderName("/home/ana/shop")).toBe("shop")
    expect(folderName("/home/ana/shop/")).toBe("shop")
    expect(folderName("/")).toBe("/")
    expect(folderName("shop")).toBe("shop")
  })
})

describe("what is said after the folder dialog", () => {
  const choice = (patch: Partial<ProjectChoice>): ProjectChoice => ({
    changed: false,
    message: "",
    project: DEFAULT,
    ...patch,
  })

  it("says the project was chosen, and what comes next", () => {
    const chosen = choice({ changed: true, project: CHOSEN })
    expect(projectNotice(chosen, true)).toEqual({
      tone: "good",
      title: "Project chosen",
      text: `${SHOP} is now the project. Restart the services to use it.`,
    })
    expect(projectNotice(chosen, false)?.text).toMatch(/Start the services to use it\.$/)
  })

  it("says why a folder was refused, and says nothing when the dialog was closed", () => {
    expect(projectNotice(choice({ message: "That is the root of the disk." }), true)).toEqual({
      tone: "bad",
      title: "That folder was not chosen",
      text: "That is the root of the disk.",
    })
    expect(projectNotice(choice({}), true)).toBeNull()
  })

  it("has a message for going back to the default folder, and for a failure nobody planned", () => {
    expect(PROJECT_RESET_NOTICE.tone).toBe("good")
    expect(PROJECT_CHECK_FAILED.tone).toBe("bad")
  })
})

describe("what a restart of the services is waiting for", () => {
  it("says it for a new key and for a folder that is not in use yet, together", () => {
    const lines = stackCard(RUNNING, false, "", ["key", "project"]).lines
    expect(lines).toContain("A new key is waiting: restart the services to use it.")
    expect(lines).toContain(
      "The project folder chosen here is not in use yet: restart the services to use it.",
    )
  })
})

const DOCKER_OK: DockerStatus = { ok: true, version: "27.3.1" }

describe("whether the tests can be run", () => {
  it("needs Docker, the services on the chosen folder, and a usable folder", () => {
    expect(canFix(DOCKER_OK, RUNNING, CHOSEN)).toBe(true)
    expect(canFix(null, RUNNING, CHOSEN)).toBe(false)
    expect(canFix({ ...DOCKER_PROBLEM }, RUNNING, CHOSEN)).toBe(false)
    expect(canFix(DOCKER_OK, null, CHOSEN)).toBe(false)
    expect(canFix(DOCKER_OK, { ...RUNNING, running: false }, CHOSEN)).toBe(false)
    expect(canFix(DOCKER_OK, { ...RUNNING, workspaceMatches: false }, CHOSEN)).toBe(false)
    expect(canFix(DOCKER_OK, RUNNING, null)).toBe(false)
    expect(canFix(DOCKER_OK, RUNNING, { ...CHOSEN, problem: "It does not exist." })).toBe(false)
  })

  it("does not wait for a folder that Docker cannot tell", () => {
    expect(canFix(DOCKER_OK, { ...RUNNING, workspaceMatches: null }, CHOSEN)).toBe(true)
  })
})

const DOCKER_PROBLEM: DockerStatus = {
  ok: false,
  problem: "daemon",
  message: "Docker is installed, but it is not running.",
  hint: "Start it.",
}

describe("the card of the fix", () => {
  it("is ready on the chosen project, and says nothing is changed", () => {
    const card = fixCard(DOCKER_OK, RUNNING, CHOSEN, null)
    expect(card.tone).toBe("good")
    expect(card.title).toBe("Ready: shop")
    expect(card.lines).toContain(SHOP)
    expect(card.lines.join(" ")).toContain("does not change your files either")
  })

  it("says what is running, and that the files are not changed", () => {
    expect(fixCard(DOCKER_OK, RUNNING, CHOSEN, "test")).toMatchObject({
      tone: "idle",
      title: "Running the tests…",
    })
    const looking = fixCard(DOCKER_OK, RUNNING, CHOSEN, "fix")
    expect(looking.title).toBe("Looking for a fix…")
    expect(looking.lines).toContain("Your files are not changed.")
  })

  it("is waiting while the page is still asking", () => {
    expect(fixCard(null, null, null, null)).toMatchObject({ tone: "idle" })
    expect(fixCard(DOCKER_OK, RUNNING, null, null)).toMatchObject({ tone: "idle" })
  })

  it("says what is wrong, and where to go", () => {
    expect(fixCard(DOCKER_PROBLEM, null, CHOSEN, null).title).toBe("Docker is not working")
    expect(fixCard(DOCKER_OK, null, CHOSEN, null).title).toBe("The services are stopped")
    expect(
      fixCard(DOCKER_OK, { ...RUNNING, running: false }, CHOSEN, null).lines.join(" "),
    ).toMatch(/Settings/)
    const other = fixCard(DOCKER_OK, { ...RUNNING, workspaceMatches: false }, CHOSEN, null)
    expect(other).toMatchObject({ tone: "warn", title: "The services use another folder" })
    expect(other.lines.join(" ")).toContain("shop")
    const broken = fixCard(DOCKER_OK, RUNNING, { ...CHOSEN, problem: "It does not exist." }, null)
    expect(broken).toMatchObject({ tone: "bad" })
    expect(broken.lines).toContain("It does not exist.")
  })
})

describe("the fix on screen while it is applied", () => {
  it("says it tests the fix again first, and that the files are written only if the tests pass", () => {
    const card = fixCard(DOCKER_OK, RUNNING, CHOSEN, "apply")
    expect(card).toMatchObject({ tone: "idle", title: "Applying the fix…" })
    expect(card.lines.join(" ")).toContain("writes your files only if the tests pass")
    expect(card.lines).not.toContain("Your files are not changed.")
  })

  it("says in plain words how an application ended", () => {
    const said = (outcome: FixOutcome) => fixNotice({ kind: "apply", outcome, message: "m" })?.title
    expect(said("applied")).toBe("Fix applied")
    expect(said("blocked")).toBe("Not applied")
    expect(said("declined")).toBeUndefined()
  })
})

const REVIEW: FixReview = {
  id: "a".repeat(64),
  description: "sumar must add its two numbers",
  edits: [{ path: "app.py", old: "    return a - b", new: "    return a + b" }],
}

describe("the fix that is about to be applied", () => {
  it("shows what each change takes out, then what it puts in", () => {
    expect(diffLines(REVIEW.edits[0] as FixReview["edits"][number])).toEqual([
      { kind: "removed", text: "    return a - b" },
      { kind: "added", text: "    return a + b" },
    ])
  })

  it("shows every line of a text of many lines, without the last line break", () => {
    const lines = diffLines({ path: "a.py", old: "one\ntwo\n", new: "x\ny\nz\n" })
    expect(lines.map((line) => `${line.kind}:${line.text}`)).toEqual([
      "removed:one",
      "removed:two",
      "added:x",
      "added:y",
      "added:z",
    ])
  })

  it("shows nothing for a side that is empty, as in a new file or a deletion", () => {
    expect(diffLines({ path: "new.py", old: "", new: "x = 1\n" })).toEqual([
      { kind: "added", text: "x = 1" },
    ])
    expect(diffLines({ path: "old.py", old: "x = 1\n", new: "" })).toEqual([
      { kind: "removed", text: "x = 1" },
    ])
  })

  it("keeps blank lines and indentation, which can matter", () => {
    const lines = diffLines({ path: "a.py", old: "a\n\n    b", new: "" })
    expect(lines.map((line) => line.text)).toEqual(["a", "", "    b"])
  })

  it("counts files, and the lines taken out and put in", () => {
    expect(reviewSummary(REVIEW)).toBe("1 file, 1 line removed and 1 added")
    const more: FixReview = {
      ...REVIEW,
      edits: [
        { path: "a.py", old: "1\n2\n", new: "x\n" },
        { path: "b.py", old: "", new: "y\nz\n" },
        { path: "a.py", old: "q", new: "r" },
      ],
    }
    expect(reviewSummary(more)).toBe("2 files, 3 lines removed and 4 added")
  })

  it("warns, because it will change files, and says nothing is changed yet", () => {
    const card = reviewCard(REVIEW)
    expect(card.tone).toBe("warn")
    expect(card.title).toBe("Review the fix before it is applied")
    expect(card.lines).toEqual([
      "sumar must add its two numbers",
      "1 file, 1 line removed and 1 added",
      "Your files are not changed yet.",
    ])
  })

  it("leaves out a description that is not there", () => {
    expect(reviewCard({ ...REVIEW, description: "" }).lines).toHaveLength(2)
  })
})

describe("what is said after a run", () => {
  const all: FixOutcome[] = [
    "passed",
    "failed",
    "fixed",
    "applied",
    "blocked",
    "nothing",
    "unfixed",
    "cancelled",
    "declined",
    "refused",
    "error",
  ]

  it("says something about every outcome but a refusal in the dialog", () => {
    for (const outcome of all) {
      const result: FixResult = { kind: "fix", outcome, message: "Because." }
      const notice = fixNotice(result)
      if (outcome === "declined") {
        expect(notice).toBeNull()
      } else {
        expect(notice?.text).toBe("Because.")
        expect(notice?.title.length).toBeGreaterThan(0)
      }
    }
  })

  it("is good only when the tests pass or a fix was found, or there was nothing to fix", () => {
    const tone = (outcome: FixOutcome) =>
      fixNotice({ kind: "fix", outcome, message: "m" })?.tone ?? "none"
    const tones = (outcomes: FixOutcome[]) => outcomes.map(tone)
    expect(tones(["passed", "fixed", "applied", "nothing"])).toEqual([
      "good",
      "good",
      "good",
      "good",
    ])
    expect(tones(["failed", "unfixed", "blocked", "cancelled", "refused"])).toEqual([
      "warn",
      "warn",
      "warn",
      "warn",
      "warn",
    ])
    expect(tone("error")).toBe("bad")
  })

  it("has a plain failure for when the main process breaks in the middle", () => {
    expect(FIX_FAILED.outcome).toBe("error")
    expect(FIX_FAILED.message).toBe("The app could not do that. Try again.")
  })
})

describe("what the page checks before it asks", () => {
  const CHOICES = [
    { command: "", attempts: "", error: 1 },
    { command: "pytest tests/unit -x", attempts: "3", error: 2 },
    { command: "  python -m pytest  ", attempts: " 10 ", error: 100 },
    { command: "--json", attempts: "", error: 1 },
    { command: "-x", attempts: "", error: 1 },
    { command: "pytest\nrm -rf /", attempts: "", error: 1 },
    { command: "pytest\u0000", attempts: "", error: 1 },
    { command: "pytest\tx", attempts: "", error: 1 },
    { command: `p${"x".repeat(199)}`, attempts: "", error: 1 },
    { command: `p${"x".repeat(200)}`, attempts: "", error: 1 },
    { command: "pytest", attempts: "0", error: 1 },
    { command: "pytest", attempts: "11", error: 1 },
    { command: "pytest", attempts: "many", error: 1 },
    { command: "pytest", attempts: "2.5", error: 1 },
    { command: "pytest", attempts: "1e1", error: 1 },
    { command: "pytest", attempts: "+3", error: 1 },
    { command: "pytest", attempts: "-1", error: 1 },
    { command: "pytest", attempts: "", error: 0 },
    { command: "pytest", attempts: "", error: 101 },
    { command: "pytest", attempts: "", error: 1.5 },
    { command: "pytest", attempts: "", error: Number.NaN },
  ]

  it("are the rules of the main process, so that what the page lets through is not refused there", () => {
    for (const choice of CHOICES) {
      expect(checkChoice(choice), JSON.stringify(choice)).toEqual(checkOptions(choice))
    }
  })

  it("say what is wrong in words, and give the options cleaned up when all is well", () => {
    expect(checkChoice({ command: " pytest -x ", attempts: "4", error: 2 })).toEqual({
      ok: true,
      options: { command: "pytest -x", attempts: 4, error: 2 },
    })
    expect(checkChoice({ command: "-x", attempts: "", error: 1 })).toMatchObject({
      ok: false,
      problem: expect.stringContaining("one line") as string,
    })
  })
})

describe("choosing which error to fix", () => {
  it("offers only the first error before any tests ran", () => {
    expect(errorChoices([])).toEqual([{ value: 1, label: "The first error" }])
  })

  it("offers each error the tests showed, by its number and the start of what it says", () => {
    expect(errorChoices(["AssertionError: assert -2 == 10", "KeyError: 'name'"])).toEqual([
      { value: 1, label: "1 · AssertionError: assert -2 == 10" },
      { value: 2, label: "2 · KeyError: 'name'" },
    ])
  })

  it("cuts what an error says when it is long", () => {
    const [choice] = errorChoices(["x".repeat(300)])
    expect(choice?.label).toBe(`1 · ${"x".repeat(90)}…`)
  })

  it("explains each option, and says which commands the system allows", () => {
    expect(OPTIONS_HELP.command).toContain("pytest and python -m pytest")
    expect(OPTIONS_HELP.command).toContain("200")
    expect(OPTIONS_HELP.attempts).toContain("from 1 to 10")
    expect(OPTIONS_HELP.error).toContain("Run the tests to list them")
    expect(OPTIONS_HELP.error).toContain("every test passes with it")
  })
})

describe("the window", () => {
  it("has its sections in the menu, the panel first and the settings", () => {
    expect(SECTIONS.map((section) => section.id)).toEqual(["panel", "project", "fix", "settings"])
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
    expect(stackCard(RUNNING, false, "", ["key"]).lines).toContain(
      "A new key is waiting: restart the services to use it.",
    )
    expect(stackCard(RUNNING, false, "", []).lines.join(" ")).not.toMatch(/waiting/)
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

  it("asks nothing of the user for a run, because the main process asks", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).not.toMatch(/confirm\(|window\.confirm|prompt\(/)
  })

  it("gives back only the id of the fix to apply it, never the fix", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).toMatch(/api\.fixApply\(review\.id, options\)/)
    expect(source.match(/api\.fixApply\(/g)).toHaveLength(1)
  })

  it("asks the system, with the choices the user made, only after checking them", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).toMatch(/api\.fixTest\(options\)/)
    expect(source).toMatch(/api\.fixFind\(options\)/)
    expect(source).toMatch(/const options = takeOptions\(\)\s+if \(options === null\)/)
  })

  it("keeps what the user typed about the test command in the browser, if it can", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).toMatch(/localStorage\.setItem\(\s*OPTIONS_KEY/)
    expect(source).toMatch(/try \{[^}]*localStorage\.getItem\(OPTIONS_KEY\)/s)
  })

  it("shows the changes as text, with a sign that does not depend on color", async () => {
    const source = await readFile(join(__dirname, "app.ts"), "utf8")
    expect(source).toMatch(/"removed" \? "-" : "\+"/)
    const css = await readFile(join(__dirname, "styles.css"), "utf8")
    expect(css).toMatch(/\.diff-line\.removed/)
    expect(css).toMatch(/\.diff-line\.added/)
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
