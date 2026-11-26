import type { SessionKeyInfo } from "../shared/api"
import { maskKey } from "./key"

/**
 * The key the services get: the one of this session if there is one, and then the secret store
 * is not asked at all, since it may be locked and asking would make the user wait; otherwise the
 * saved key, or null.
 */
export async function keyForServices(
  session: Pick<SessionKey, "get">,
  vault: { load(): Promise<string | null> },
): Promise<string | null> {
  return session.get() ?? (await vault.load())
}

/**
 * An Anthropic key that is used for this session only: it lives in the memory of this program,
 * is never written anywhere, and is gone when the app closes. It is for when the secret store
 * cannot keep a key safely, or for someone who does not want it kept. Only the main process can
 * read it; the page can only see it masked.
 */
export class SessionKey {
  private key: string | null = null

  set(key: string): void {
    this.key = key
  }

  clear(): void {
    this.key = null
  }

  /** The key, or null when none is in use. It is for the main process only. */
  get(): string | null {
    return this.key
  }

  /** What the page may know of it: that there is one, and its first and last characters. */
  info(): SessionKeyInfo | null {
    return this.key === null ? null : { masked: maskKey(this.key) }
  }
}
