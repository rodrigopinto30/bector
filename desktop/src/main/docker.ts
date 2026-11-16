import type { DockerStatus } from "../shared/api"
import type { Run, RunResult } from "./process"

const PROBLEMS = {
  missing: {
    message: "Docker is not installed.",
    hint: "Install Docker Engine for your distribution (https://docs.docker.com/engine/install/), then press Check again.",
  },
  daemon: {
    message: "Docker is installed, but it is not running.",
    hint: "Start it with: sudo systemctl start docker. Then press Check again.",
  },
  permission: {
    message: "Your user is not allowed to use Docker.",
    hint: "Run: sudo usermod -aG docker $USER. Then log out, log back in and press Check again.",
  },
  compose: {
    message: "Docker Compose is missing.",
    hint: "Install its plugin, for example: sudo apt-get install docker-compose-plugin. Then press Check again.",
  },
} as const

function problem(kind: keyof typeof PROBLEMS): DockerStatus {
  return { ok: false, problem: kind, ...PROBLEMS[kind] }
}

function whyInfoFailed(info: RunResult): keyof typeof PROBLEMS {
  return /permission denied/i.test(info.stderr) ? "permission" : "daemon"
}

/**
 * Whether Docker works for this user without sudo. The app never runs sudo or installs
 * anything: when something is missing it says what to do, in words the user can follow.
 */
export async function checkDocker(run: Run): Promise<DockerStatus> {
  const version = await run("docker", ["--version"], { timeoutMs: 10_000 })
  if (version.failed === "ENOENT") {
    return problem("missing")
  }
  const info = await run("docker", ["info", "--format", "{{.ServerVersion}}"], {
    timeoutMs: 20_000,
  })
  if (info.code !== 0) {
    return problem(whyInfoFailed(info))
  }
  const compose = await run("docker", ["compose", "version", "--short"], { timeoutMs: 10_000 })
  if (compose.code !== 0) {
    return problem("compose")
  }
  return { ok: true, version: info.stdout.trim() }
}
