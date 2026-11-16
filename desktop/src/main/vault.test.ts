import { mkdtemp, readFile, readdir, stat } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import { KeyVault, VaultError, vaultFile, type SafeStorageLike, type VaultFile } from "./vault"

const KEY = `sk-ant-api03-${"AbCdEfGh1234567890_-".repeat(3)}`

function storage(
  options: {
    available?: boolean
    backend?: string
    fail?: boolean
    reEncrypt?: boolean
  } = {},
): SafeStorageLike {
  const { available = true, backend = "gnome_libsecret", fail = false, reEncrypt = false } = options
  return {
    isAsyncEncryptionAvailable: async () => available,
    getSelectedStorageBackend: backend === "none" ? undefined : () => backend,
    encryptStringAsync: async (text) => Buffer.from(`enc:${[...text].reverse().join("")}`),
    decryptStringAsync: async (data) => {
      if (fail) {
        throw new Error("could not decrypt")
      }
      const text = data.toString().slice("enc:".length)
      return { shouldReEncrypt: reEncrypt, result: [...text].reverse().join("") }
    },
  }
}

function memory(): VaultFile & { data: Buffer | null; writes: number } {
  const file = {
    data: null as Buffer | null,
    writes: 0,
    read: async () => file.data,
    write: async (data: Buffer) => {
      file.data = data
      file.writes += 1
    },
    remove: async () => {
      file.data = null
    },
  }
  return file
}

/** A secret store that is locked: it waits for a password and never answers. */
function locked(): SafeStorageLike {
  const never = <T>() => new Promise<T>(() => undefined)
  return {
    isAsyncEncryptionAvailable: () => never<boolean>(),
    getSelectedStorageBackend: () => "gnome_libsecret",
    encryptStringAsync: () => never<Buffer>(),
    decryptStringAsync: () => never<{ shouldReEncrypt: boolean; result: string }>(),
  }
}

describe("a secret store that does not answer", () => {
  it("is told as unavailable, with what to do, instead of waiting for ever", async () => {
    const vault = new KeyVault(locked(), memory(), 20)
    const status = await vault.status()
    expect(status).toMatchObject({ vault: "unavailable", stored: false, masked: "" })
    expect(status.reason).toMatch(/did not answer.*unlock it and check again/)
  })

  it("refuses to save, and nothing is written", async () => {
    const file = memory()
    const vault = new KeyVault(locked(), file, 20)
    await expect(vault.save(KEY)).rejects.toThrow(VaultError)
    await expect(vault.save(KEY)).rejects.toThrow(/did not answer/)
    expect(file.writes).toBe(0)
  })

  it("says it did not answer, not that the saved key is broken, when reading", async () => {
    const file = memory()
    file.data = Buffer.from("enc:something")
    const vault = new KeyVault(
      { ...storage(), decryptStringAsync: locked().decryptStringAsync },
      file,
      20,
    )
    await expect(vault.load()).rejects.toThrow(/did not answer/)
    await expect(vault.load()).rejects.not.toThrow(/could not be read/)
  })

  it("is waited for when it is only slow", async () => {
    const slow: SafeStorageLike = {
      ...storage(),
      isAsyncEncryptionAvailable: () =>
        new Promise((resolve) => setTimeout(() => resolve(true), 15)),
    }
    expect(await new KeyVault(slow, memory(), 2000).status()).toMatchObject({ vault: "ready" })
  })
})

describe("the key vault", () => {
  it("saves the key encrypted and says only a masked form of it", async () => {
    const file = memory()
    const vault = new KeyVault(storage(), file)
    expect(await vault.status()).toEqual({ vault: "ready", reason: "", stored: false, masked: "" })
    await vault.save(KEY)
    expect(file.data?.toString()).not.toContain(KEY)
    expect(await vault.status()).toEqual({
      vault: "ready",
      reason: "",
      stored: true,
      masked: `sk-ant-…${KEY.slice(-4)}`,
    })
    expect(await vault.load()).toBe(KEY)
    await vault.remove()
    expect(await vault.load()).toBeNull()
    expect((await vault.status()).stored).toBe(false)
  })

  it.each([["basic_text"], ["unknown"]])(
    "refuses to save when the only store is %s, which is plain text",
    async (backend) => {
      const file = memory()
      const vault = new KeyVault(storage({ backend }), file)
      const status = await vault.status()
      expect(status).toMatchObject({ vault: "unavailable", stored: false })
      expect(status.reason).toMatch(/gnome-keyring or KWallet/)
      await expect(vault.save(KEY)).rejects.toBeInstanceOf(VaultError)
      expect(file.writes).toBe(0)
    },
  )

  it("refuses when the system has no secret store", async () => {
    const vault = new KeyVault(storage({ available: false }), memory())
    expect((await vault.status()).vault).toBe("unavailable")
    await expect(vault.save(KEY)).rejects.toBeInstanceOf(VaultError)
  })

  it.each([["kwallet6"], ["kwallet5"], ["none"]])("trusts a real store (%s)", async (backend) => {
    const vault = new KeyVault(storage({ backend }), memory())
    await vault.save(KEY)
    expect(await vault.load()).toBe(KEY)
  })

  it("says so when the saved key cannot be read, instead of failing", async () => {
    const file = memory()
    await new KeyVault(storage(), file).save(KEY)
    const vault = new KeyVault(storage({ fail: true }), file)
    expect(await vault.status()).toMatchObject({ vault: "ready", stored: false })
    expect((await vault.status()).reason).toMatch(/Enter it again/)
    await expect(vault.load()).rejects.toBeInstanceOf(VaultError)
  })

  it("saves the key again when the store asks to", async () => {
    const file = memory()
    await new KeyVault(storage(), file).save(KEY)
    const writes = file.writes
    expect(await new KeyVault(storage({ reEncrypt: true }), file).load()).toBe(KEY)
    expect(file.writes).toBe(writes + 1)
  })
})

describe("the file of the vault", () => {
  it("is written for this user only, in one step", async () => {
    const folder = await mkdtemp(join(tmpdir(), "healer-vault-"))
    const path = join(folder, "inner", "key.bin")
    const file = vaultFile(path)
    expect(await file.read()).toBeNull()
    await file.write(Buffer.from("one"))
    await file.write(Buffer.from("two"))
    expect((await readFile(path)).toString()).toBe("two")
    expect((await stat(path)).mode & 0o777).toBe(0o600)
    expect((await stat(join(folder, "inner"))).mode & 0o777).toBe(0o700)
    expect(await readdir(join(folder, "inner"))).toEqual(["key.bin"])
    await file.remove()
    await file.remove()
    expect(await file.read()).toBeNull()
  })
})
