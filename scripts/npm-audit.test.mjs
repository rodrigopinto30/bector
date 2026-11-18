import assert from "node:assert/strict"
import { test } from "node:test"

import { advisoriesOf, evaluate, parseExceptions } from "./npm-audit.mjs"

const PICOMATCH = "GHSA-fc5q-39xc-vjpf"
const OTHER = "GHSA-aaaa-bbbb-cccc"

const clean = { auditReportVersion: 2, vulnerabilities: {} }

/** The shape of the report of `npm audit --json`, as npm 11 prints it. */
function report(...advisories) {
  const vulnerabilities = {}
  for (const [id, name, depends] of advisories) {
    vulnerabilities[name] = {
      name,
      severity: "high",
      via: [
        {
          source: 1247710,
          name,
          title: `${name} has a problem`,
          url: `https://github.com/advisories/${id}`,
          severity: "high",
          range: "<=4.0.7",
        },
      ],
    }
    for (const dependent of depends ?? []) {
      vulnerabilities[dependent] = { name: dependent, severity: "high", via: [name] }
    }
  }
  return { auditReportVersion: 2, vulnerabilities }
}

const EXCEPTED = `# one per line
${PICOMATCH}  dev tools only, no fixed version yet
`

test("an exception is an id and the reason, and comments and blank lines are skipped", () => {
  const found = parseExceptions(`# comment\n\n${PICOMATCH}  only in the dev tools\n${OTHER} x y z\n`)
  assert.deepEqual([...found.keys()], [PICOMATCH, OTHER])
  assert.equal(found.get(PICOMATCH), "only in the dev tools")
  assert.equal(found.get(OTHER), "x y z")
})

test("an exception without a reason is refused", () => {
  assert.throws(() => parseExceptions(`${PICOMATCH}\n`), /has no reason/)
  assert.throws(() => parseExceptions(`${PICOMATCH}   \n`), /has no reason/)
})

test("an id twice, or that is not an id, is refused", () => {
  assert.throws(() => parseExceptions(`${PICOMATCH} a\n${PICOMATCH} b\n`), /listed twice/)
  assert.throws(() => parseExceptions("--ignore-all because\n"), /not an advisory id/)
})

test("a package that is vulnerable only through another is not an advisory", () => {
  const found = advisoriesOf(report([PICOMATCH, "picomatch", ["fdir", "tinyglobby", "vite"]]))
  assert.deepEqual([...found.keys()], [PICOMATCH])
  assert.deepEqual(found.get(PICOMATCH).packages, ["picomatch"])
})

test("the same advisory in two packages is one advisory", () => {
  const two = {
    vulnerabilities: {
      a: { via: [{ source: 1, title: "t", url: `https://github.com/advisories/${OTHER}` }] },
      b: { via: [{ source: 1, title: "t", url: `https://github.com/advisories/${OTHER}` }] },
    },
  }
  const found = advisoriesOf(two)
  assert.equal(found.size, 1)
  assert.deepEqual(found.get(OTHER).packages, ["a", "b"])
})

test("a report with an error, or without a list, is never taken as clean", () => {
  assert.throws(() => advisoriesOf({ error: { code: "ENOAUDIT", summary: "offline" } }), /offline/)
  assert.throws(() => advisoriesOf({ error: { code: "E404" } }), /E404/)
  assert.throws(() => advisoriesOf({}), /no list of vulnerabilities/)
  assert.throws(() => advisoriesOf(null), /did not give a report/)
})

test("nothing reported passes", () => {
  const { code, lines } = evaluate({ shipped: clean, everything: clean, exceptionsText: "" })
  assert.equal(code, 0)
  assert.equal(lines.at(-1), "[audit] found 0 vulnerabilities")
})

test("an advisory in the dev tools that is excepted passes, and says why", () => {
  const { code, lines } = evaluate({
    shipped: clean,
    everything: report([PICOMATCH, "picomatch", ["fdir"]]),
    exceptionsText: EXCEPTED,
  })
  assert.equal(code, 0)
  assert.match(lines.join("\n"), /excepted: GHSA-fc5q-39xc-vjpf/)
  assert.match(lines.join("\n"), /because: dev tools only, no fixed version yet/)
  assert.match(lines.at(-1), /OK/)
})

test("an advisory that is not in the list fails, even beside one that is", () => {
  const { code, lines } = evaluate({
    shipped: clean,
    everything: report([PICOMATCH, "picomatch"], [OTHER, "left-pad"]),
    exceptionsText: EXCEPTED,
  })
  assert.equal(code, 1)
  const text = lines.join("\n")
  assert.match(text, /1 advisory that is not in audit-exceptions.txt/)
  assert.match(text, new RegExp(`${OTHER}.*left-pad`))
  assert.equal(lines.at(-1), "[audit] FAILED")
})

test("an advisory in what ships fails, even when it is in the list", () => {
  const broken = report([PICOMATCH, "picomatch"])
  const { code, lines } = evaluate({ shipped: broken, everything: broken, exceptionsText: EXCEPTED })
  assert.equal(code, 1)
  assert.match(lines.join("\n"), /in what ships. Nothing that ships can be an exception/)
})

test("an exception that is no longer reported is pointed out, and does not fail", () => {
  const { code, lines } = evaluate({ shipped: clean, everything: clean, exceptionsText: EXCEPTED })
  assert.equal(code, 0)
  assert.match(lines.join("\n"), /GHSA-fc5q-39xc-vjpf is in audit-exceptions.txt but is no longer reported: remove it/)
})

test("a broken list of exceptions, or an audit that could not run, fails", () => {
  const noReason = evaluate({ shipped: clean, everything: clean, exceptionsText: `${PICOMATCH}\n` })
  assert.equal(noReason.code, 1)
  const offline = evaluate({
    shipped: clean,
    everything: { error: { summary: "no network" } },
    exceptionsText: "",
  })
  assert.equal(offline.code, 1)
  assert.match(offline.lines[0], /could not run: no network/)
})
