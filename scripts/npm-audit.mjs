// The audit of the npm packages of an app (panel/ or desktop/), with documented exceptions.
//
// `npm audit` has no list of exceptions, so this reads its report and judges it:
//
//   1. What ships (`npm audit --omit=dev`) must have no advisory at all. Nothing can be excepted
//      here, so an exception can never hide a vulnerability in what people run.
//   2. Everything, development tools included, must have no advisory that is not in the app's
//      audit-exceptions.txt: one `GHSA-...` per line, then the reason it does not apply.
//
// Run it from the app's folder: node /scripts/npm-audit.mjs audit-exceptions.txt

import { spawnSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { pathToFileURL } from "node:url"

const ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

/** The exceptions of a file: an id and a reason on each line, `#` for comments. */
export function parseExceptions(text) {
  const found = new Map()
  const lines = text.split(/\r?\n/)
  lines.forEach((raw, index) => {
    const line = raw.trim()
    if (line === "" || line.startsWith("#")) {
      return
    }
    const at = `line ${index + 1}`
    const [id, ...words] = line.split(/\s+/)
    if (!ID.test(id)) {
      throw new Error(`${at}: "${id}" is not an advisory id`)
    }
    if (words.length === 0) {
      throw new Error(`${at}: ${id} has no reason. An exception says why it does not apply.`)
    }
    if (found.has(id)) {
      throw new Error(`${at}: ${id} is listed twice`)
    }
    found.set(id, words.join(" "))
  })
  return found
}

/**
 * The advisories in a report of `npm audit --json`, by id. A package that is vulnerable only
 * because it depends on a vulnerable one lists that package by name, and is not an advisory.
 */
export function advisoriesOf(report) {
  if (report === null || typeof report !== "object") {
    throw new Error("the audit did not give a report")
  }
  if (report.error !== undefined) {
    const said = report.error.summary ?? report.error.code ?? "unknown"
    throw new Error(`the audit could not run: ${said}`)
  }
  if (typeof report.vulnerabilities !== "object" || report.vulnerabilities === null) {
    throw new Error("the report of the audit has no list of vulnerabilities")
  }
  const found = new Map()
  for (const [name, entry] of Object.entries(report.vulnerabilities)) {
    for (const via of entry.via ?? []) {
      if (typeof via !== "object" || via === null) {
        continue
      }
      const id = String(via.url ?? via.source ?? "").split("/").pop() || String(via.source)
      const known = found.get(id) ?? {
        id,
        severity: via.severity ?? "unknown",
        title: via.title ?? "",
        url: via.url ?? "",
        packages: [],
      }
      if (!known.packages.includes(name)) {
        known.packages.push(name)
      }
      found.set(id, known)
    }
  }
  return found
}

const describe = (advisory) =>
  `  ${advisory.id}  ${advisory.severity}  ${advisory.title} (${advisory.packages.join(", ")})` +
  (advisory.url === "" ? "" : `\n    ${advisory.url}`)

/**
 * Judges the two reports. Pure: it takes the reports and the text of the exceptions, and gives
 * the exit code and the lines to print.
 */
export function evaluate({ shipped, everything, exceptionsText }) {
  const lines = []
  let exceptions
  let shippedFound
  let all
  try {
    exceptions = parseExceptions(exceptionsText)
    shippedFound = advisoriesOf(shipped)
    all = advisoriesOf(everything)
  } catch (error) {
    return { code: 1, lines: [`[audit] FAILED: ${error.message}`] }
  }
  let failed = false
  if (shippedFound.size > 0) {
    failed = true
    lines.push(
      `[audit] ${shippedFound.size} advisory in what ships. Nothing that ships can be an exception:`,
      ...[...shippedFound.values()].map(describe),
    )
  }
  const blocking = [...all.values()].filter((advisory) => !exceptions.has(advisory.id))
  if (blocking.length > 0) {
    failed = true
    lines.push(
      `[audit] ${blocking.length} advisory that is not in audit-exceptions.txt:`,
      ...blocking.map(describe),
    )
  }
  for (const advisory of all.values()) {
    if (exceptions.has(advisory.id)) {
      lines.push(`[audit] excepted: ${advisory.id}  ${advisory.title}`)
      lines.push(`  because: ${exceptions.get(advisory.id)}`)
    }
  }
  for (const id of exceptions.keys()) {
    if (!all.has(id)) {
      lines.push(`[audit] ${id} is in audit-exceptions.txt but is no longer reported: remove it.`)
    }
  }
  lines.push(
    failed
      ? "[audit] FAILED"
      : all.size === 0
        ? "[audit] found 0 vulnerabilities"
        : "[audit] OK: nothing that ships, and nothing outside audit-exceptions.txt.",
  )
  return { code: failed ? 1 : 0, lines }
}

/** `npm audit --json`. The audit exits with 1 when it finds something, so the status is not read. */
function audit(extra) {
  const ran = spawnSync("npm", ["audit", "--json", ...extra], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  })
  if (ran.error !== undefined) {
    return { error: { summary: ran.error.message } }
  }
  try {
    return JSON.parse(ran.stdout)
  } catch {
    return { error: { summary: `npm audit did not print a report. ${ran.stderr.trim()}`.trim() } }
  }
}

function main() {
  const file = process.argv[2]
  if (file === undefined) {
    console.error("Usage: node npm-audit.mjs audit-exceptions.txt")
    return 2
  }
  let exceptionsText
  try {
    exceptionsText = readFileSync(file, "utf8")
  } catch (error) {
    console.error(`[audit] FAILED: ${file} could not be read: ${error.message}`)
    return 1
  }
  const { code, lines } = evaluate({
    shipped: audit(["--omit=dev"]),
    everything: audit([]),
    exceptionsText,
  })
  console.log(lines.join("\n"))
  return code
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main()
}
