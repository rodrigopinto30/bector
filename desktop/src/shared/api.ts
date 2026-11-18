/**
 * What the setup page and the main process say to each other. Types only: nothing here runs,
 * so the page and the main program can both import it.
 */

export type DockerProblem = "missing" | "daemon" | "permission" | "compose"

export type DockerStatus =
  | { ok: true; version: string }
  | { ok: false; problem: DockerProblem; message: string; hint: string }

/** What Anthropic said about a key. Only "valid" and "limited" mean it can be used. */
export type KeyCheck =
  | { state: "valid" }
  | { state: "limited" }
  | { state: "invalid" }
  | { state: "forbidden" }
  | { state: "unavailable" }
  | { state: "offline" }
  | { state: "malformed" }

export interface KeyStatus {
  vault: "ready" | "unavailable"
  /** Why the vault is unavailable, or why a saved key could not be read; empty otherwise. */
  reason: string
  stored: boolean
  /** The key without its middle, such as sk-ant-…ab12; empty when none is stored. */
  masked: string
}

export interface KeySave {
  saved: boolean
  check: KeyCheck
  /** The vault after the attempt, or null when nothing was asked of it and it did not change. */
  status: KeyStatus | null
}

export interface StackStatus {
  /** Every service of the system is up. */
  running: boolean
  services: string[]
  /** Whether the cli container has a key; null when it is not running. */
  keySeen: boolean | null
  /** Whether the cli container has the project folder that should be there; null when it is not running. */
  workspaceMatches: boolean | null
}

/** The folder with the code to fix, the one the services see as /workspace. */
export interface ProjectInfo {
  /** The folder chosen in the app, or null when the default one is used. */
  chosen: string | null
  /** The folder the services should have: the chosen one, or the default. */
  folder: string
  /** Why the chosen folder cannot be used now; empty when it can. */
  problem: string
  /** What is worth knowing about the folder. It never stops the folder from being used. */
  hints: string[]
}

export interface ProjectChoice {
  /** False when the dialog was closed or the folder was refused. */
  changed: boolean
  /** Why the folder was refused; empty otherwise. */
  message: string
  project: ProjectInfo
}

export interface StackResult {
  ok: boolean
  message: string
}

/** What the app can ask the services to do with the project. */
export type FixKind = "test" | "fix" | "apply"

/**
 * How a run ended. "declined" and "refused" mean nothing was run: the first because the user
 * said no in the dialog, the second because the services were not ready. "applied" is the only
 * outcome that wrote to the project, and "blocked" means the fix was tested again and did not
 * pass, so nothing was written.
 */
export type FixOutcome =
  | "passed"
  | "failed"
  | "fixed"
  | "applied"
  | "blocked"
  | "nothing"
  | "unfixed"
  | "cancelled"
  | "declined"
  | "refused"
  | "error"

export interface FixResult {
  kind: FixKind
  outcome: FixOutcome
  /** What to tell the user, in a sentence or two. */
  message: string
}

export interface FixStatus {
  /** What is running now, or null. */
  running: FixKind | null
  /** Whether a fix was found and is saved, waiting to be reviewed. */
  saved: boolean
}

/** One change of the saved fix: the text to replace in a file of the project, and what replaces it. */
export interface FixEdit {
  path: string
  old: string
  new: string
}

/** The saved fix, as it will be applied. */
export interface FixReview {
  /** Identifies exactly this fix, so that what is applied is what was reviewed. */
  id: string
  description: string
  edits: FixEdit[]
}

export interface FixReviewResult {
  /** The saved fix, or null when there is none to review. */
  review: FixReview | null
  /** Why there is none; empty otherwise. */
  message: string
}

/** The sections of the window: the panel, the project, the fix, and the settings of the app. */
export type Section = "panel" | "project" | "fix" | "settings"

/** A rectangle of the window, in pixels from its top left corner. */
export interface Bounds {
  x: number
  y: number
  width: number
  height: number
}

export type WindowAction = "minimize" | "toggleMaximize" | "close"

export interface WindowState {
  maximized: boolean
}

/** What the page can ask of the main process. The key is never given back to the page. */
export interface HealerApi {
  dockerCheck(): Promise<DockerStatus>
  keyStatus(): Promise<KeyStatus>
  keySave(key: string): Promise<KeySave>
  keyRemove(): Promise<KeyStatus>
  stackStatus(): Promise<StackStatus>
  /** With ``restart``, the services are recreated even if they run, to apply a changed key. */
  stackStart(restart?: boolean): Promise<StackResult>
  stackStop(): Promise<StackResult>
  projectStatus(): Promise<ProjectInfo>
  /** Opens the system's folder dialog. The page never says which folder: only the dialog does. */
  projectChoose(): Promise<ProjectChoice>
  /** Goes back to the default folder. */
  projectReset(): Promise<ProjectInfo>
  /** What runs now, and whether a fix is saved. */
  fixStatus(): Promise<FixStatus>
  /** The saved fix, to be shown before it is applied. Nothing is changed. */
  fixReview(): Promise<FixReviewResult>
  /**
   * Applies the fix with that id to the project. The app asks first, in its own dialog, and
   * applies only the fix that was reviewed: if the saved fix is another one, nothing happens.
   */
  fixApply(id: string): Promise<FixResult>
  /** Runs the tests of the project in the sandbox. Nothing is changed. */
  fixTest(): Promise<FixResult>
  /**
   * Looks for a fix and saves it, without applying it. The app asks first, in its own dialog,
   * because Claude may be asked for patches, which spends tokens.
   */
  fixFind(): Promise<FixResult>
  /** Stops what is running. */
  fixCancel(): Promise<void>
  /** Shows a section. The panel is shown by the app on top of the page, in the content area. */
  showSection(section: Section): Promise<void>
  /** Where the content area is, so the app can put the panel exactly there. */
  setContentBounds(bounds: Bounds): Promise<void>
  windowControl(action: WindowAction): Promise<void>
  openLink(url: string): Promise<void>
  /** Lines of output while the services start. Returns a function that stops listening. */
  onLine(listener: (line: string) => void): () => void
  onWindowState(listener: (state: WindowState) => void): () => void
  /** Lines of output while the tests run or a fix is looked for. */
  onFixLine(listener: (line: string) => void): () => void
  /** The panel could not be loaded: the services are stopped or still starting. */
  onPanelFailed(listener: () => void): () => void
  /** A keyboard shortcut of the app, such as Ctrl+, for the settings. */
  onShortcut(listener: (name: "settings") => void): () => void
}
