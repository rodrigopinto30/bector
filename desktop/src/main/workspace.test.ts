import { mkdtemp, readFile, stat } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import {
  defaultFolder,
  inspectFolder,
  readWorkspacePath,
  textFile,
  Workspace,
  type FolderProbe,
  type TextFile,
} from "./workspace"

const HOME = "/home/ana"

interface Entry {
  names?: string[]
  /** Where a link leads. */
  link?: string
  file?: boolean
  locked?: boolean
}

/** A disk made of a few entries. A path that is not in it does not exist. */
function disk(entries: Record<string, Entry>): FolderProbe {
  const real = (path: string): string => {
    const entry = entries[path]
    if (entry === undefined) {
      throw new Error("ENOENT")
    }
    return entry.link === undefined ? path : real(entry.link)
  }
  return {
    realpath: async (path) => real(path),
    isDirectory: async (path) => entries[path]?.file !== true && entries[path] !== undefined,
    canReadAndWrite: async (path) => entries[path]?.locked !== true,
    names: async (path) => entries[path]?.names ?? [],
  }
}

const python = { names: ["app.py", "tests"] }
const base = {
  "/": {},
  [HOME]: {},
  "/home/ana/shop": python,
}

const check = (path: unknown, entries: Record<string, Entry> = base) =>
  inspectFolder(path, disk(entries), HOME)

describe("which folders can be the project", () => {
  it("accepts a folder of this user, and says its real path", async () => {
    expect(await check("/home/ana/shop")).toEqual({
      ok: true,
      folder: "/home/ana/shop",
      hints: [],
    })
  })

  it("follows a link to the folder it leads to, and keeps that one", async () => {
    const entries = { ...base, "/home/ana/link": { link: "/home/ana/shop" } }
    expect(await check("/home/ana/link", entries)).toMatchObject({
      ok: true,
      folder: "/home/ana/shop",
    })
  })

  it.each([
    [undefined, "That is not a folder path."],
    [42, "That is not a folder path."],
    [null, "That is not a folder path."],
    ["", "That is not a folder path."],
    ["/home/ana/a\0b", "That is not a folder path."],
    ["/home/ana/a\nb", "That is not a folder path."],
    [`/${"x".repeat(4096)}`, "That is not a folder path."],
    ["shop", "The folder must be given with its full path."],
    ["./shop", "The folder must be given with its full path."],
    ["~/shop", "The folder must be given with its full path."],
  ])("refuses %j", async (path, reason) => {
    expect(await check(path)).toEqual({ ok: false, reason })
  })

  it("refuses a folder that does not exist, and a file", async () => {
    expect(await check("/home/ana/missing")).toEqual({
      ok: false,
      reason: "The folder does not exist.",
    })
    const entries = { ...base, "/home/ana/notes.txt": { file: true } }
    expect(await check("/home/ana/notes.txt", entries)).toEqual({
      ok: false,
      reason: "That is not a folder.",
    })
  })

  it("refuses a path with a colon, which Docker cannot mount, also behind a link", async () => {
    const entries = {
      ...base,
      "/home/ana/a:b": {},
      "/home/ana/link": { link: "/home/ana/a:b" },
    }
    const expected = {
      ok: false,
      reason: expect.stringContaining("colon (:)") as unknown as string,
    }
    expect(await check("/home/ana/a:b", entries)).toEqual(expected)
    expect(await check("/home/ana/link", entries)).toEqual(expected)
  })

  it("refuses the whole disk, the whole home folder, and a link to either", async () => {
    const entries = {
      ...base,
      "/home/ana/to-home": { link: HOME },
      "/home/ana/to-root": { link: "/" },
    }
    expect(await check("/", entries)).toMatchObject({
      ok: false,
      reason: expect.stringMatching(/root of the disk/),
    })
    expect(await check(HOME, entries)).toMatchObject({
      ok: false,
      reason: expect.stringMatching(/whole home folder/),
    })
    expect(await check("/home/ana/to-home", entries)).toMatchObject({
      ok: false,
      reason: expect.stringMatching(/whole home folder/),
    })
    expect(await check("/home/ana/to-root", entries)).toMatchObject({
      ok: false,
      reason: expect.stringMatching(/root of the disk/),
    })
  })

  it.each([
    "/etc",
    "/usr",
    "/usr/local/src",
    "/bin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/run",
    "/lib64",
  ])("refuses a folder of the system: %s", async (path) => {
    expect(await check(path, { ...base, [path]: {} })).toMatchObject({
      ok: false,
      reason: expect.stringMatching(/folder of the system/) as unknown as string,
    })
  })

  it("does not take a folder for a system one just because its name starts the same", async () => {
    const entries = {
      ...base,
      "/usrlocal": python,
      "/etcetera": python,
      "/var/www/app": python,
      "/opt/app": python,
      "/home/bob/app": python,
    }
    for (const path of ["/usrlocal", "/etcetera", "/var/www/app", "/opt/app", "/home/bob/app"]) {
      expect(await check(path, entries)).toMatchObject({ ok: true })
    }
  })

  it("refuses a folder this user cannot read and write", async () => {
    const entries = { ...base, "/home/ana/locked": { ...python, locked: true } }
    expect(await check("/home/ana/locked", entries)).toMatchObject({
      ok: false,
      reason: expect.stringMatching(/read this folder and to write a fix/) as unknown as string,
    })
  })
})

describe("what is said about a folder", () => {
  const hints = async (names: string[]) => {
    const result = await check("/home/ana/p", { ...base, "/home/ana/p": { names } })
    return result.ok ? result.hints : ["REFUSED"]
  }

  it("says nothing of a folder that looks like a Python project", async () => {
    for (const names of [
      ["a.py"],
      ["pyproject.toml"],
      ["setup.py"],
      ["requirements.txt"],
      ["tests"],
      ["src"],
      ["README.md", "pytest.ini"],
    ]) {
      expect(await hints(names)).toEqual([])
    }
  })

  it("says that an empty folder is empty", async () => {
    expect(await hints([])).toEqual(["The folder is empty: put the project's code in it."])
  })

  it("says, without refusing, that a folder does not look like Python", async () => {
    const said = await hints(["package.json", "index.js", "README.md"])
    expect(said).toHaveLength(1)
    expect(said[0]).toMatch(/does not look like a Python project/)
    expect(said[0]).toMatch(/Python projects only/)
  })
})

function memory(initial: string | null = null): TextFile & { text: string | null } {
  const file = {
    text: initial,
    read: async () => file.text,
    write: async (text: string) => {
      file.text = text
    },
    remove: async () => {
      file.text = null
    },
  }
  return file
}

function workspace(entries: Record<string, Entry>, saved: string | null = null) {
  const store = memory(saved === null ? null : JSON.stringify({ folder: saved }))
  const probe = disk({ ...base, "/repo/workspace": {}, ...entries })
  return {
    store,
    workspace: new Workspace({
      store,
      probe,
      home: HOME,
      defaultFolder: async () => "/repo/workspace",
    }),
  }
}

describe("the project folder", () => {
  it("is the default one until another is chosen", async () => {
    const { workspace: w } = workspace({})
    expect(await w.info()).toEqual({
      chosen: null,
      folder: "/repo/workspace",
      problem: "",
      hints: [],
    })
    expect(await w.plan()).toEqual({ problem: "", env: null, expected: "/repo/workspace" })
  })

  it("keeps a folder that is chosen, and gives it to the services", async () => {
    const { workspace: w, store } = workspace({})
    const choice = await w.choose("/home/ana/shop")
    expect(choice).toMatchObject({ changed: true, message: "" })
    expect(choice.project).toEqual({
      chosen: "/home/ana/shop",
      folder: "/home/ana/shop",
      problem: "",
      hints: [],
    })
    expect(JSON.parse(store.text ?? "")).toEqual({ folder: "/home/ana/shop" })
    expect(await w.plan()).toEqual({
      problem: "",
      env: "/home/ana/shop",
      expected: "/home/ana/shop",
    })
  })

  it("keeps the real path of a link, so the same folder is never kept under two names", async () => {
    const { workspace: w } = workspace({ "/home/ana/link": { link: "/home/ana/shop" } })
    expect((await w.choose("/home/ana/link")).project.chosen).toBe("/home/ana/shop")
  })

  it("does not change the choice when a folder is refused, and says why", async () => {
    const { workspace: w } = workspace({}, "/home/ana/shop")
    const choice = await w.choose("/")
    expect(choice.changed).toBe(false)
    expect(choice.message).toMatch(/root of the disk/)
    expect(choice.project.chosen).toBe("/home/ana/shop")
  })

  it("goes back to the default folder", async () => {
    const { workspace: w, store } = workspace({}, "/home/ana/shop")
    expect((await w.reset()).chosen).toBeNull()
    expect(store.text).toBeNull()
  })

  it("says that a folder that was chosen is gone, and does not start the services with it", async () => {
    const { workspace: w } = workspace({}, "/home/ana/deleted")
    const info = await w.info()
    expect(info).toMatchObject({
      chosen: "/home/ana/deleted",
      problem: "The folder does not exist.",
    })
    const plan = await w.plan()
    expect(plan.env).toBeNull()
    expect(plan.problem).toMatch(
      /cannot be used: The folder does not exist\. Choose it again, or go back to the default folder\./,
    )
  })

  it.each(["", "not json", "[]", '{"folder": 5}', '{"folder": ""}', "null"])(
    "ignores a damaged choice (%j) and uses the default",
    async (text) => {
      const store = memory(text)
      const w = new Workspace({
        store,
        probe: disk(base),
        home: HOME,
        defaultFolder: async () => "/repo/workspace",
      })
      expect((await w.info()).chosen).toBeNull()
    },
  )

  it("compares two paths as folders: through links, and by text when one is not there", async () => {
    const { workspace: w } = workspace({ "/home/ana/link": { link: "/home/ana/shop" } })
    expect(await w.same("/home/ana/link", "/home/ana/shop")).toBe(true)
    expect(await w.same("/home/ana/shop", "/repo/workspace")).toBe(false)
    expect(await w.same("/gone/a", "/gone/a")).toBe(true)
    expect(await w.same("/gone/a", "/gone/b")).toBe(false)
  })
})

describe("the default folder", () => {
  it.each([
    ["WORKSPACE_PATH=./workspace\n", "/repo/workspace"],
    ["WORKSPACE_PATH=/data/code\n", "/data/code"],
    ["WORKSPACE_PATH=../other\n", "/other"],
    ['WORKSPACE_PATH="/with space"\n', "/with space"],
    ["WORKSPACE_PATH=\n", "/repo/workspace"],
    ["A=1\nWORKSPACE_PATH=/one\nWORKSPACE_PATH=/two\n", "/two"],
    ["A=1\n", "/repo/workspace"],
    [null, "/repo/workspace"],
  ])("reads %j as %s", (env, expected) => {
    expect(defaultFolder("/repo", env)).toBe(expected)
  })

  it("reads only WORKSPACE_PATH of the .env text", () => {
    expect(readWorkspacePath("NOT_WORKSPACE_PATH=/x\n # WORKSPACE_PATH=/y\n")).toBeNull()
  })
})

describe("the file that keeps the choice", () => {
  it("is written in one step, only this user can read it, and can be removed", async () => {
    const folder = await mkdtemp(join(tmpdir(), "healer-workspace-"))
    const file = textFile(join(folder, "nested", "project.json"))
    expect(await file.read()).toBeNull()
    await file.write('{"folder":"/x"}')
    expect(await file.read()).toBe('{"folder":"/x"}')
    expect((await stat(join(folder, "nested", "project.json"))).mode & 0o777).toBe(0o600)
    expect(await readFile(join(folder, "nested", "project.json"), "utf8")).toBe('{"folder":"/x"}')
    await file.write('{"folder":"/y"}')
    expect(await file.read()).toBe('{"folder":"/y"}')
    await file.remove()
    expect(await file.read()).toBeNull()
    await file.remove()
  })
})
