import { constants } from "node:fs"
import {
  access,
  chmod,
  mkdir,
  readdir,
  readFile,
  realpath,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises"
import { dirname, isAbsolute, resolve } from "node:path"

import type { ProjectChoice, ProjectInfo } from "../shared/api"

const MAX_PATH_CHARS = 4096

/** Folders of the operating system: a project is never in them, and mounting one would be a mistake. */
const SYSTEM_FOLDERS = [
  "/bin",
  "/boot",
  "/dev",
  "/etc",
  "/lib",
  "/lib32",
  "/lib64",
  "/proc",
  "/run",
  "/sbin",
  "/sys",
  "/usr",
]

/** Names at the top of a folder that say it is a Python project. */
const PYTHON_MARKERS = [
  "pyproject.toml",
  "setup.py",
  "setup.cfg",
  "requirements.txt",
  "pytest.ini",
  "tox.ini",
  "tests",
  "test",
  "src",
]

/** What the app needs to know about a folder of the disk. Apart from Electron, so it can be tested. */
export interface FolderProbe {
  /** The real path, with links resolved; fails if the folder does not exist. */
  realpath(path: string): Promise<string>
  isDirectory(path: string): Promise<boolean>
  /** Whether this user can read the folder and write into it. */
  canReadAndWrite(path: string): Promise<boolean>
  /** The names directly inside the folder; none if it cannot be read. */
  names(path: string): Promise<string[]>
}

export function folderProbe(): FolderProbe {
  return {
    realpath: (path) => realpath(path),
    isDirectory: async (path) => {
      try {
        return (await stat(path)).isDirectory()
      } catch {
        return false
      }
    },
    canReadAndWrite: async (path) => {
      try {
        await access(path, constants.R_OK | constants.W_OK | constants.X_OK)
        return true
      } catch {
        return false
      }
    },
    names: async (path) => {
      try {
        return await readdir(path)
      } catch {
        return []
      }
    },
  }
}

export type FolderCheck =
  { ok: true; folder: string; hints: string[] } | { ok: false; reason: string }

function refuse(reason: string): FolderCheck {
  return { ok: false, reason }
}

const COLON =
  "Docker cannot mount a folder whose path has a colon (:) in it. Rename the folder, or choose another."

function isSystemFolder(path: string): boolean {
  return SYSTEM_FOLDERS.some((folder) => path === folder || path.startsWith(`${folder}/`))
}

function hintsFor(names: string[]): string[] {
  if (names.length === 0) {
    return ["The folder is empty: put the project's code in it."]
  }
  const looksPython = names.some((name) => name.endsWith(".py") || PYTHON_MARKERS.includes(name))
  return looksPython
    ? []
    : [
        "It does not look like a Python project: it has no .py files, pyproject.toml, requirements.txt or tests folder at its top. The system works on Python projects only.",
      ]
}

/**
 * Whether ``path`` can be the folder with the code to fix, and what is worth knowing about it.
 * The folder is mounted in the containers, so it must be a real folder of this user, not one
 * of the system, not the whole disk and not the whole home folder.
 */
export async function inspectFolder(
  path: unknown,
  probe: FolderProbe,
  home: string,
): Promise<FolderCheck> {
  if (
    typeof path !== "string" ||
    path === "" ||
    path.length > MAX_PATH_CHARS ||
    /[\0\r\n]/.test(path)
  ) {
    return refuse("That is not a folder path.")
  }
  if (!isAbsolute(path)) {
    return refuse("The folder must be given with its full path.")
  }
  let real: string
  try {
    real = await probe.realpath(path)
  } catch {
    return refuse("The folder does not exist.")
  }
  if (!(await probe.isDirectory(real))) {
    return refuse("That is not a folder.")
  }
  if (real.includes(":")) {
    return refuse(COLON)
  }
  if (real === "/") {
    return refuse("That is the root of the disk. Choose the folder of your project.")
  }
  if (real === home || real === (await probe.realpath(home).catch(() => home))) {
    return refuse("That is your whole home folder. Choose the folder of one project.")
  }
  if (isSystemFolder(real)) {
    return refuse("That is a folder of the system. Choose the folder of your project.")
  }
  if (!(await probe.canReadAndWrite(real))) {
    return refuse(
      "The system needs to read this folder and to write a fix into it, and it cannot do both here.",
    )
  }
  return { ok: true, folder: real, hints: hintsFor(await probe.names(real)) }
}

/** A small text file: the app's own choice, written in one step. */
export interface TextFile {
  read(): Promise<string | null>
  write(text: string): Promise<void>
  remove(): Promise<void>
}

export function textFile(path: string): TextFile {
  return {
    async read() {
      try {
        return await readFile(path, "utf8")
      } catch {
        return null
      }
    },
    async write(text) {
      await mkdir(dirname(path), { recursive: true, mode: 0o700 })
      const temporary = `${path}.${process.pid}.tmp`
      await writeFile(temporary, text, { mode: 0o600 })
      await chmod(temporary, 0o600)
      await rename(temporary, path)
    },
    async remove() {
      await rm(path, { force: true })
    },
  }
}

/** The text of .env's WORKSPACE_PATH, or null when it has none. */
export function readWorkspacePath(env: string | null): string | null {
  const line = env?.split(/\r?\n/).findLast((text) => /^WORKSPACE_PATH=/.test(text))
  const value = line
    ?.slice("WORKSPACE_PATH=".length)
    .trim()
    .replace(/^["']|["']$/g, "")
  return value === undefined || value === "" ? null : value
}

/** The folder the services use when none was chosen in the app: .env's, or ./workspace. */
export function defaultFolder(root: string, env: string | null): string {
  return resolve(root, readWorkspacePath(env) ?? "workspace")
}

/** What the services need, from the choice made in the app. */
export interface WorkspacePlan {
  /** Why the services cannot start with this choice; empty when they can. */
  problem: string
  /** The folder to give them as WORKSPACE_PATH, or null to leave the default one. */
  env: string | null
  /** The folder their container should have mounted. */
  expected: string
}

export interface WorkspaceDeps {
  store: TextFile
  probe: FolderProbe
  home: string
  defaultFolder(): Promise<string>
}

/**
 * The project folder: the one chosen in the app, kept in a file of the app's own, or the
 * default one. The choice is checked again each time, because a folder can be moved or deleted
 * after it was chosen.
 */
export class Workspace {
  constructor(private readonly deps: WorkspaceDeps) {}

  private async saved(): Promise<string | null> {
    const text = await this.deps.store.read()
    if (text === null) {
      return null
    }
    try {
      const folder = (JSON.parse(text) as { folder?: unknown }).folder
      return typeof folder === "string" && folder !== "" ? folder : null
    } catch {
      return null
    }
  }

  async info(): Promise<ProjectInfo> {
    const saved = await this.saved()
    if (saved === null) {
      return { chosen: null, folder: await this.deps.defaultFolder(), problem: "", hints: [] }
    }
    const seen = await inspectFolder(saved, this.deps.probe, this.deps.home)
    return seen.ok
      ? { chosen: seen.folder, folder: seen.folder, problem: "", hints: seen.hints }
      : { chosen: saved, folder: saved, problem: seen.reason, hints: [] }
  }

  /** Keeps ``path`` as the project folder if it can be one. Otherwise the choice does not change. */
  async choose(path: string): Promise<ProjectChoice> {
    const seen = await inspectFolder(path, this.deps.probe, this.deps.home)
    if (!seen.ok) {
      return { changed: false, message: seen.reason, project: await this.info() }
    }
    await this.deps.store.write(JSON.stringify({ folder: seen.folder }))
    return { changed: true, message: "", project: await this.info() }
  }

  async reset(): Promise<ProjectInfo> {
    await this.deps.store.remove()
    return this.info()
  }

  async plan(): Promise<WorkspacePlan> {
    const info = await this.info()
    if (info.chosen === null) {
      return { problem: "", env: null, expected: info.folder }
    }
    if (info.problem !== "") {
      return {
        problem: `The project folder cannot be used: ${info.problem} Choose it again, or go back to the default folder.`,
        env: null,
        expected: info.folder,
      }
    }
    return { problem: "", env: info.chosen, expected: info.chosen }
  }

  /** Whether two paths are the same folder, whatever links lead to it. */
  async same(a: string, b: string): Promise<boolean> {
    const real = (path: string) => this.deps.probe.realpath(path).catch(() => resolve(path))
    return (await real(a)) === (await real(b))
  }
}
