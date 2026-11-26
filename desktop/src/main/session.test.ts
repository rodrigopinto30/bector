import { describe, expect, it } from "vitest"

import { keyForServices, SessionKey } from "./session"
import { VaultError } from "./vault"

const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`
const OTHER = `sk-ant-api03-${"ZyXwVuTs0987654321-_".repeat(3)}`

describe("the key of this session", () => {
  it("is nothing until one is set, and then only its first and last characters can be told", () => {
    const session = new SessionKey()
    expect(session.get()).toBeNull()
    expect(session.info()).toBeNull()
    session.set(KEY)
    expect(session.get()).toBe(KEY)
    expect(session.info()).toEqual({ masked: "sk-ant-…" + KEY.slice(-4) })
    expect(JSON.stringify(session.info())).not.toContain(KEY)
  })

  it("is replaced by another, and forgotten when it is cleared", () => {
    const session = new SessionKey()
    session.set(KEY)
    session.set(OTHER)
    expect(session.get()).toBe(OTHER)
    session.clear()
    expect(session.get()).toBeNull()
    expect(session.info()).toBeNull()
  })

  it("is not written anywhere: it is a value in memory", () => {
    const session = new SessionKey()
    session.set(KEY)
    expect(Object.keys(session)).toEqual(["key"])
    expect(Object.getOwnPropertyNames(Object.getPrototypeOf(session) as object).sort()).toEqual([
      "clear",
      "constructor",
      "get",
      "info",
      "set",
    ])
  })
})

describe("the key the services get", () => {
  function vault(result: string | null | Error) {
    const calls = { loads: 0 }
    return {
      calls,
      load: async () => {
        calls.loads += 1
        if (result instanceof Error) {
          throw result
        }
        return result
      },
    }
  }

  it("is the key of the session, without asking the secret store, which may be locked", async () => {
    const session = new SessionKey()
    session.set(KEY)
    const store = vault(new VaultError("locked"))
    expect(await keyForServices(session, store)).toBe(KEY)
    expect(store.calls.loads).toBe(0)
  })

  it("is the key of the session even when a key is saved", async () => {
    const session = new SessionKey()
    session.set(KEY)
    const store = vault(OTHER)
    expect(await keyForServices(session, store)).toBe(KEY)
    expect(store.calls.loads).toBe(0)
  })

  it("is the saved key when the session has none", async () => {
    const store = vault(OTHER)
    expect(await keyForServices(new SessionKey(), store)).toBe(OTHER)
    expect(store.calls.loads).toBe(1)
  })

  it("is nothing when there is no key anywhere", async () => {
    expect(await keyForServices(new SessionKey(), vault(null))).toBeNull()
  })

  it("is the problem of the secret store when the session has none and it does not answer", async () => {
    await expect(keyForServices(new SessionKey(), vault(new VaultError("locked")))).rejects.toThrow(
      "locked",
    )
  })

  it("goes back to the saved key once the session key is cleared", async () => {
    const session = new SessionKey()
    session.set(KEY)
    session.clear()
    expect(await keyForServices(session, vault(OTHER))).toBe(OTHER)
  })
})
