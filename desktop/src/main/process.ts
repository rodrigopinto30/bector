import { spawn } from "node:child_process"

export interface RunOptions {
  cwd?: string
  /** Added to the environment of the program. Never put in its arguments. */
  env?: Record<string, string>
  timeoutMs?: number
  /** Written to the program's standard input, which is then closed. Without it, it is closed. */
  input?: string
  /** Called with each line the program prints, on either output. */
  onLine?: (line: string) => void
}

export interface RunResult {
  code: number | null
  stdout: string
  stderr: string
  /** Empty when the program ran; otherwise why it did not, such as ENOENT or timeout. */
  failed: string
}

export type Run = (
  file: string,
  args: readonly string[],
  options?: RunOptions,
) => Promise<RunResult>

const OUTPUT_LIMIT = 1_000_000

/** Runs a program without a shell, so nothing in its arguments is ever interpreted. */
export const run: Run = (file, args, options = {}) =>
  new Promise((resolve) => {
    let stdout = ""
    let stderr = ""
    let failed = ""
    let done = false
    const finish = (code: number | null) => {
      if (!done) {
        done = true
        resolve({ code, stdout, stderr, failed })
      }
    }
    const child = spawn(file, [...args], {
      cwd: options.cwd,
      env: { ...process.env, ...options.env },
      stdio: ["pipe", "pipe", "pipe"],
      shell: false,
    })
    // The input is closed at once when there is none, as if the program had no input at all. A
    // program that ends without reading its input is not an error of ours.
    child.stdin.on("error", () => undefined)
    child.stdin.end(options.input)
    const timer =
      options.timeoutMs === undefined
        ? null
        : setTimeout(() => {
            failed = "timeout"
            child.kill("SIGTERM")
            setTimeout(() => child.kill("SIGKILL"), 2_000).unref()
          }, options.timeoutMs)
    const pending: Array<() => void> = []
    const lines = (sink: (text: string) => void) => {
      let partial = ""
      pending.push(() => {
        if (partial !== "") {
          options.onLine?.(partial)
          partial = ""
        }
      })
      return (chunk: Buffer) => {
        const text = chunk.toString("utf8")
        sink(text)
        if (options.onLine === undefined) {
          return
        }
        const parts = (partial + text).split(/\r?\n|\r/)
        partial = parts.pop() ?? ""
        for (const line of parts) {
          options.onLine(line)
        }
      }
    }
    child.stdout.on(
      "data",
      lines((text) => (stdout = (stdout + text).slice(-OUTPUT_LIMIT))),
    )
    child.stderr.on(
      "data",
      lines((text) => (stderr = (stderr + text).slice(-OUTPUT_LIMIT))),
    )
    child.on("error", (error: NodeJS.ErrnoException) => {
      failed = error.code ?? "error"
      finish(null)
    })
    child.on("close", (code) => {
      if (timer !== null) {
        clearTimeout(timer)
      }
      for (const flush of pending) {
        flush()
      }
      finish(code)
    })
  })
