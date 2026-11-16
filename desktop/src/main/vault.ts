import { chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises"
import { dirname } from "node:path"

import type { KeyStatus } from "../shared/api"
import { maskKey } from "./key"

/** The part of Electron's safeStorage that the vault uses. */
export interface SafeStorageLike {
  isAsyncEncryptionAvailable(): Promise<boolean>
  /** Only on Linux: which secret store protects the data. */
  getSelectedStorageBackend?(): string
  encryptStringAsync(plainText: string): Promise<Buffer>
  decryptStringAsync(data: Buffer): Promise<{ shouldReEncrypt: boolean; result: string }>
}

/** Where the encrypted key is kept: one file. */
export interface VaultFile {
  read(): Promise<Buffer | null>
  write(data: Buffer): Promise<void>
  remove(): Promise<void>
}

export class VaultError extends Error {}

const NO_STORE =
  "No secret store was found. On Linux, install and unlock gnome-keyring or KWallet: " +
  "without one the key would be saved as plain text, so it is not saved."

const NO_ANSWER =
  "The secret store did not answer. If your system asks you to unlock it, unlock it and check again."

/** A locked secret store can wait for a password with no end; the app does not wait with it. */
const ANSWER_TIMEOUT_MS = 15_000

/** The files of a vault, written so that only this user can read them. */
export function vaultFile(path: string): VaultFile {
  return {
    async read() {
      try {
        return await readFile(path)
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") {
          return null
        }
        throw error
      }
    },
    async write(data) {
      await mkdir(dirname(path), { recursive: true, mode: 0o700 })
      const temporary = `${path}.tmp`
      await writeFile(temporary, data, { mode: 0o600 })
      await chmod(temporary, 0o600)
      await rename(temporary, path)
    },
    async remove() {
      await rm(path, { force: true })
    },
  }
}

/**
 * The Anthropic key, encrypted with the secret store of the operating system (its keychain)
 * and kept in a file only this user can read. On Linux without a secret store, Electron would
 * fall back to plain text: the vault refuses to save then, instead of pretending.
 */
export class KeyVault {
  constructor(
    private readonly storage: SafeStorageLike,
    private readonly file: VaultFile,
    private readonly timeoutMs = ANSWER_TIMEOUT_MS,
  ) {}

  /** ``work``, or a VaultError if the secret store does not answer in time. */
  private within<T>(work: Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new VaultError(NO_ANSWER)), this.timeoutMs)
      work.then(
        (value) => {
          clearTimeout(timer)
          resolve(value)
        },
        (error: unknown) => {
          clearTimeout(timer)
          reject(error)
        },
      )
    })
  }

  /** Empty when the key can be kept safely; otherwise why not. */
  async unavailableReason(): Promise<string> {
    let available: boolean
    try {
      available = await this.within(this.storage.isAsyncEncryptionAvailable())
    } catch (error) {
      if (error instanceof VaultError) {
        return error.message
      }
      throw error
    }
    if (!available) {
      return "The secret store of this system is not available."
    }
    const backend = this.storage.getSelectedStorageBackend?.()
    if (backend === "basic_text" || backend === "unknown") {
      return NO_STORE
    }
    return ""
  }

  async status(): Promise<KeyStatus> {
    const reason = await this.unavailableReason()
    if (reason) {
      return { vault: "unavailable", reason, stored: false, masked: "" }
    }
    try {
      const key = await this.load()
      return {
        vault: "ready",
        reason: "",
        stored: key !== null,
        masked: key === null ? "" : maskKey(key),
      }
    } catch (error) {
      if (error instanceof VaultError) {
        return { vault: "ready", reason: error.message, stored: false, masked: "" }
      }
      throw error
    }
  }

  async save(key: string): Promise<void> {
    const reason = await this.unavailableReason()
    if (reason) {
      throw new VaultError(reason)
    }
    await this.file.write(await this.within(this.storage.encryptStringAsync(key)))
  }

  /** The key, or null when none is saved. It is for the main process only. */
  async load(): Promise<string | null> {
    const data = await this.file.read()
    if (data === null) {
      return null
    }
    try {
      const { result, shouldReEncrypt } = await this.within(this.storage.decryptStringAsync(data))
      if (shouldReEncrypt) {
        await this.file.write(await this.within(this.storage.encryptStringAsync(result)))
      }
      return result
    } catch (error) {
      if (error instanceof VaultError) {
        throw error
      }
      throw new VaultError("The saved key could not be read. Enter it again.")
    }
  }

  async remove(): Promise<void> {
    await this.file.remove()
  }
}
