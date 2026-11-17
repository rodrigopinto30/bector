import type { StackResult, StackStatus } from "../shared/api"
import { needsSetup, readPanelPort } from "./project"
import type { Run } from "./process"
import { VaultError } from "./vault"
import type { WorkspacePlan } from "./workspace"

/** The services that must be up for the system to work. The team listener is optional. */
export const SERVICES = ["cli", "mosquitto", "panel", "sandbox"] as const

const START_TIMEOUT_MS = 30 * 60_000
const PANEL_WAIT_MS = 120_000

/** Asks Docker which folder of this computer is mounted as /workspace in a container. */
const MOUNT_FORMAT = '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}'

export interface StackDeps {
  root: string
  run: Run
  /** The text of .env, or null when there is none. */
  readEnv(): Promise<string | null>
  /** The saved key, or null. It reaches the containers through the environment only. */
  key(): Promise<string | null>
  /** The project folder the services should have, and whether the choice can be used. */
  workspace(): Promise<WorkspacePlan>
  /** Whether two paths are the same folder. */
  sameFolder(a: string, b: string): Promise<boolean>
  /** Whether the panel answers at that address. */
  ping(url: string): Promise<boolean>
  sleep(ms: number): Promise<void>
  onLine(line: string): void
}

/**
 * Starts and stops the services of the system with the project's own scripts. The key goes to
 * the child process as ANTHROPIC_API_KEY, which docker-compose.yml passes on to the
 * containers: it is never written to a file or put in an argument, and it is hidden from
 * every line of output this passes on. The project folder chosen in the app goes the same way,
 * as WORKSPACE_PATH, and .env is never written.
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
    const cli = services.includes("cli")
    return {
      running,
      services,
      keySeen: cli ? await this.keySeen() : null,
      workspaceMatches: cli ? await this.workspaceMatches() : null,
    }
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

  /**
   * Whether the cli container has the folder that should be its project. Null when that cannot
   * be told: the chosen folder is unusable, or Docker does not say.
   */
  private async workspaceMatches(): Promise<boolean | null> {
    const plan = await this.deps.workspace()
    if (plan.problem !== "") {
      return null
    }
    const options = { cwd: this.deps.root, timeoutMs: 30_000 }
    const listed = await this.deps.run("docker", ["compose", "ps", "-q", "cli"], options)
    const id = listed.stdout.split(/\r?\n/).find(Boolean)?.trim()
    if (listed.code !== 0 || id === undefined) {
      return null
    }
    const mount = await this.deps.run("docker", ["inspect", "--format", MOUNT_FORMAT, id], options)
    const actual = mount.stdout.trim()
    if (mount.code !== 0 || actual === "") {
      return null
    }
    return this.deps.sameFolder(actual, plan.expected)
  }

  private async startServices(restart: boolean): Promise<StackResult> {
    const plan = await this.deps.workspace()
    if (plan.problem !== "") {
      return { ok: false, message: `${plan.problem} The services were not started.` }
    }
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
    const inSync = before.workspaceMatches !== false
    if (!restart && before.running && inSync && (key === null || before.keySeen === true)) {
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
      env: {
        ...(key === null ? {} : { ANTHROPIC_API_KEY: key }),
        ...(plan.env === null ? {} : { WORKSPACE_PATH: plan.env }),
      },
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
