import { dirname, join } from "node:path"

const MARKERS = ["docker-compose.yml", "start.sh", join("src", "healer", "__init__.py")]
const DEFAULT_PANEL_PORT = 8765

/** Whether ``folder`` is the root of the healer project: its Compose file, scripts and code. */
function isProject(folder: string, exists: (path: string) => boolean): boolean {
  return MARKERS.every((marker) => exists(join(folder, marker)))
}

/**
 * The folder with docker-compose.yml, start.sh and the code: ``home`` when it is one (the
 * HEALER_HOME variable), otherwise the first folder above ``start`` that is.
 */
export function findProjectRoot(
  home: string | undefined,
  start: string,
  exists: (path: string) => boolean,
): string | null {
  if (home !== undefined && home !== "") {
    return isProject(home, exists) ? home : null
  }
  let folder = start
  for (let step = 0; step < 8; step += 1) {
    if (isProject(folder, exists)) {
      return folder
    }
    const parent = dirname(folder)
    if (parent === folder) {
      return null
    }
    folder = parent
  }
  return null
}

/** The panel's port: PANEL_PORT in the .env text, or 8765. Nothing else of it is read. */
export function readPanelPort(env: string | null): number {
  const line = env?.split(/\r?\n/).findLast((text) => /^PANEL_PORT=/.test(text))
  const port = Number(line?.slice("PANEL_PORT=".length).trim())
  return Number.isInteger(port) && port >= 1 && port <= 65_535 ? port : DEFAULT_PANEL_PORT
}

/** Whether start.sh has to run: the first time, when .env has none of its settings yet. */
export function needsSetup(env: string | null): boolean {
  return env === null || !/^HOST_UID=/m.test(env)
}
