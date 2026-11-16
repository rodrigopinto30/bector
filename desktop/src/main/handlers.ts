import type {
  Bounds,
  DockerStatus,
  KeySave,
  KeyStatus,
  Section,
  StackResult,
  StackStatus,
  WindowAction,
} from "../shared/api"
import { checkKey, isUsable, normalizeKey, type Fetch } from "./key"
import { isSection, isWindowAction, parseBounds } from "./layout"
import { isAllowedExternal } from "./policy"
import type { Stack } from "./stack"
import { VaultError, type KeyVault } from "./vault"

const MAX_KEY_CHARS = 512
const MAX_URL_CHARS = 200

export interface HandlerDeps {
  docker(): Promise<DockerStatus>
  vault: Pick<KeyVault, "status" | "save" | "remove">
  stack: Pick<Stack, "status" | "start" | "stop">
  fetchFn: Fetch
  /** The window: which section it shows, where its content is, and its own buttons. */
  window: {
    show(section: Section): Promise<void>
    setBounds(bounds: Bounds): void
    control(action: WindowAction): void
  }
  openExternal(url: string): Promise<void>
}

function text(value: unknown, limit: number, what: string): string {
  if (typeof value !== "string" || value.length > limit) {
    throw new Error(`${what} must be a text of at most ${limit} characters`)
  }
  return value
}

/**
 * What the setup page may ask. Each handler checks its arguments, and none of them returns or
 * throws the key. The page is trusted only because of where it is loaded from; these checks
 * keep a mistake in it from doing more than its job.
 */
export function createHandlers(deps: HandlerDeps) {
  return {
    "docker:check": (): Promise<DockerStatus> => deps.docker(),
    "key:status": (): Promise<KeyStatus> => deps.vault.status(),
    "key:save": async (key: unknown): Promise<KeySave> => {
      const normalized = normalizeKey(text(key, MAX_KEY_CHARS, "the key"))
      const check = await checkKey(normalized, deps.fetchFn)
      if (!isUsable(check)) {
        return { saved: false, check, status: null }
      }
      try {
        await deps.vault.save(normalized)
      } catch (error) {
        if (error instanceof VaultError) {
          // The store just failed: asking it again would make the user wait a second time.
          const status: KeyStatus = {
            vault: "unavailable",
            reason: error.message,
            stored: false,
            masked: "",
          }
          return { saved: false, check, status }
        }
        throw error
      }
      return { saved: true, check, status: await deps.vault.status() }
    },
    "key:remove": async (): Promise<KeyStatus> => {
      await deps.vault.remove()
      return deps.vault.status()
    },
    "stack:status": (): Promise<StackStatus> => deps.stack.status(),
    "stack:start": (restart?: unknown): Promise<StackResult> => deps.stack.start(restart === true),
    "stack:stop": (): Promise<StackResult> => deps.stack.stop(),
    "section:show": async (section: unknown): Promise<void> => {
      if (!isSection(section)) {
        throw new Error("that section does not exist")
      }
      await deps.window.show(section)
    },
    "layout:content": async (bounds: unknown): Promise<void> => {
      deps.window.setBounds(parseBounds(bounds))
    },
    "window:control": async (action: unknown): Promise<void> => {
      if (!isWindowAction(action)) {
        throw new Error("that window action does not exist")
      }
      deps.window.control(action)
    },
    "link:open": async (url: unknown): Promise<void> => {
      const link = text(url, MAX_URL_CHARS, "the link")
      if (!isAllowedExternal(link)) {
        throw new Error("that link may not be opened")
      }
      await deps.openExternal(link)
    },
  }
}

export type Handlers = ReturnType<typeof createHandlers>
export type Channel = keyof Handlers
