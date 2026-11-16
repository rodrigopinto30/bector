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
}

export interface StackResult {
  ok: boolean
  message: string
}

/** The two sections of the window: the panel, and the settings of the app. */
export type Section = "panel" | "settings"

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
  /** Shows a section. The panel is shown by the app on top of the page, in the content area. */
  showSection(section: Section): Promise<void>
  /** Where the content area is, so the app can put the panel exactly there. */
  setContentBounds(bounds: Bounds): Promise<void>
  windowControl(action: WindowAction): Promise<void>
  openLink(url: string): Promise<void>
  /** Lines of output while the services start. Returns a function that stops listening. */
  onLine(listener: (line: string) => void): () => void
  onWindowState(listener: (state: WindowState) => void): () => void
  /** The panel could not be loaded: the services are stopped or still starting. */
  onPanelFailed(listener: () => void): () => void
  /** A keyboard shortcut of the app, such as Ctrl+, for the settings. */
  onShortcut(listener: (name: "settings") => void): () => void
}
