import type { StackResult, StackStatus } from "../shared/api"
import { needsSetup, readPanelPort } from "./project"
import type { Run } from "./process"
import { VaultError } from "./vault"

/** The services that must be up for the system to work. The team listener is optional. */
export const SERVICES = ["cli", "mosquitto", "panel", "sandbox"] as const

const START_TIMEOUT_MS = 30 * 60_000
const PANEL_WAIT_MS = 120_000

export interface StackDeps {
  root: string
  run: Run
  /** The text of .env, or null when there is none. */
  readEnv(): Promise<string | null>
  /** The saved key, or null. It reaches the containers through the environment only. */
  key(): Promise<string | null>
  /** Whether the panel answers at that address. */
  ping(url: string): Promise<boolean>
  sleep(ms: number): Promise<void>
  onLine(line: string): void
}

/**
 * Starts and stops the services of the system with the project's own scripts. The key goes to
 * the child process as ANTHROPIC_API_KEY, which docker-compose.yml passes on to the
 * containers: it is never written to a file or put in an argument, and it is hidden from
 * every line of output this passes on.
 */
export class Stack {
  private busy = false

  constructor(private readonly deps: StackDeps) {}

  async status(): Promise<StackStatus> {
    const listed = await this.deps.run(
      "docker",
      ["compose", "ps", "--services", "--status", "running"],
      {
        cwd: this.deps.root,
        timeoutMs: 30_000,
      },
    )
    const services = listed.code === 0 ? listed.stdout.split(/\r?\n/).filter(Boolean).sort() : []
    const running = SERVICES.every((name) => services.includes(name))
    return { running, services, keySeen: services.includes("cli") ? await this.keySeen() : null }
  }

  /**
   * Starts the services. With ``restart``, they are recreated even if they run, which is how a
   * key that was replaced or removed reaches the containers.
   */
  async start(restart = false): Promise<StackResult> {
    if (this.busy) {
      return { ok: false, message: "The services are already being started or stopped." }
    }
    this.busy = true
    try {
      return await this.startServices(restart)
    } finally {
      this.busy = false
    }
  }

  async stop(): Promise<StackResult> {
    if (this.busy) {
      return { ok: false, message: "The services are already being started or stopped." }
    }
    this.busy = true
    try {
      const stopped = await this.deps.run("bash", ["stop.sh"], {
        cwd: this.deps.root,
        timeoutMs: 5 * 60_000,
        onLine: this.deps.onLine,
      })
      return stopped.code === 0
        ? { ok: true, message: "The services are stopped." }
        : { ok: false, message: "The services could not be stopped." }
    } finally {
      this.busy = false
    }
  }

  private async keySeen(): Promise<boolean> {
    const seen = await this.deps.run(
      "docker",
      [
        "compose",
        "exec",
        "-T",
        "cli",
        "sh",
        "-c",
        '[ -n "$ANTHROPIC_API_KEY" ] && echo set || echo unset',
      ],
      { cwd: this.deps.root, timeoutMs: 30_000 },
    )
    return seen.stdout.trim() === "set"
  }

  private async startServices(restart: boolean): Promise<StackResult> {
    let key: string | null
    try {
      key = await this.deps.key()
    } catch (error) {
      if (error instanceof VaultError) {
        return { ok: false, message: `${error.message} The services were not started.` }
      }
      throw error
    }
    const before = await this.status()
    if (!restart && before.running && (key === null || before.keySeen === true)) {
      return { ok: true, message: this.ready(key, before.keySeen) }
    }
    const env = await this.deps.readEnv()
    const hide = (line: string) => (key === null ? line : line.split(key).join("[hidden]"))
    const first = needsSetup(env)
    const [file, args] = first
      ? (["bash", ["start.sh"]] as const)
      : (["docker", ["compose", "up", "-d"]] as const)
    const result = await this.deps.run(file, args, {
      cwd: this.deps.root,
      env: key === null ? {} : { ANTHROPIC_API_KEY: key },
      timeoutMs: START_TIMEOUT_MS,
      onLine: (line) => this.deps.onLine(hide(line)),
    })
    if (result.code !== 0) {
      const reason = result.failed === "timeout" ? "It took too long." : "See the output above."
      return { ok: false, message: `The services did not start. ${reason}` }
    }
    const port = readPanelPort(await this.deps.readEnv())
    if (!(await this.waitForPanel(`http://localhost:${port}/api/snapshot`))) {
      return { ok: false, message: "The services started, but the panel did not answer in time." }
    }
    return { ok: true, message: this.ready(key, (await this.status()).keySeen) }
  }

  private ready(key: string | null, keySeen: boolean | null): string {
    if (key === null) {
      return "The services are running, with no Anthropic key: only the local cache and the team's fixes can be used."
    }
    return keySeen === true
      ? "The services are running, and the system sees your Anthropic key."
      : "The services are running, but the system does not see the key yet."
  }

  private async waitForPanel(url: string): Promise<boolean> {
    for (let waited = 0; waited < PANEL_WAIT_MS; waited += 1_000) {
      if (await this.deps.ping(url)) {
        return true
      }
      await this.deps.sleep(1_000)
    }
    return false
  }
}
