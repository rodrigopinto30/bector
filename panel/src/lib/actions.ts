import type { ActionOutcome, Session } from "@/lib/types"

let session: Promise<Session> | null = null

/**
 * The token of this panel's session, asked once. Only pages of this panel can read it:
 * the server answers only to localhost and never lets another site read its answers.
 */
export function getSession(): Promise<Session> {
  session ??= fetch("/api/session", { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`the panel server answered ${response.status}`)
      }
      return (await response.json()) as Session
    })
    .catch((reason: unknown) => {
      session = null
      throw reason
    })
  return session
}

/** Runs one action of the panel, such as "outbox/send", and says how it went. */
export async function act(name: string, body?: Record<string, string>): Promise<ActionOutcome> {
  try {
    const { token } = await getSession()
    if (token === null) {
      return { ok: false, message: "This panel does not take actions." }
    }
    const response = await fetch(`/api/actions/${name}`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Healer-Token": token },
      body: JSON.stringify(body ?? {}),
    })
    const answer = (await response.json()) as Partial<ActionOutcome> & { error?: string }
    if (!response.ok) {
      return { ok: false, message: answer.error ?? `the panel server answered ${response.status}` }
    }
    return { ok: Boolean(answer.ok), message: answer.message ?? "" }
  } catch (reason: unknown) {
    return { ok: false, message: reason instanceof Error ? reason.message : String(reason) }
  }
}
