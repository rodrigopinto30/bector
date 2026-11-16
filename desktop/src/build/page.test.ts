import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import { buildPage } from "./page"

const TEMPLATE = "<head><!--CSP--><!--STYLE--></head><body><!--SCRIPT--></body>"

function hash(text: string): string {
  return `'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`
}

function policyOf(page: string): string {
  return /Content-Security-Policy" content="([^"]+)"/.exec(page)?.[1] ?? ""
}

describe("building the setup page", () => {
  const css = "body { margin: 0 }"
  const js = "export const x = 1\nconsole.log(x)"
  const page = buildPage(TEMPLATE, css, js)

  it("inlines the styles and the script", () => {
    expect(page).toContain(`<style>${css}</style>`)
    expect(page).toContain(`<script type="module">${js}</script>`)
  })

  it("allows exactly the styles and the script it holds, by their hash", () => {
    const policy = policyOf(page)
    expect(policy).toContain(`script-src ${hash(js)}`)
    expect(policy).toContain(`style-src ${hash(css)}`)
    expect(policy).toContain("default-src 'none'")
    expect(policy).toContain("connect-src 'none'")
    expect(policy).toContain("form-action 'none'")
    expect(policy).not.toMatch(/unsafe|\*|https?:/)
  })

  it("changes the policy when the script changes", () => {
    expect(policyOf(buildPage(TEMPLATE, css, js + "\n// changed"))).not.toBe(policyOf(page))
  })

  it("keeps the script exactly as it is, even with characters that replace() reads", () => {
    const tricky = 'const a = "$&"; const b = "$1"; const c = `$$`'
    expect(buildPage(TEMPLATE, css, tricky)).toContain(`<script type="module">${tricky}</script>`)
  })

  it.each([
    ["no marker", "<head></head>"],
    ["a repeated marker", TEMPLATE + "<!--SCRIPT-->"],
  ])("refuses a template with %s", (_name, template) => {
    expect(() => buildPage(template, css, js)).toThrow(/exactly once/)
  })

  it("refuses a script or styles that would end their own tag", () => {
    expect(() => buildPage(TEMPLATE, css, 'x = "</script><script>alert(1)"')).toThrow(
      /end their own tag/,
    )
    expect(() => buildPage(TEMPLATE, "</STYLE>", js)).toThrow(/end their own tag/)
  })

  it("refuses a script that imports other files", () => {
    expect(() => buildPage(TEMPLATE, css, 'import { x } from "./other.js"\nx()')).toThrow(
      /self-contained/,
    )
  })

  it("works with the real template and styles", async () => {
    const dir = join(__dirname, "..", "renderer")
    const [template, styles] = await Promise.all([
      readFile(join(dir, "index.html"), "utf8"),
      readFile(join(dir, "styles.css"), "utf8"),
    ])
    const built = buildPage(template, styles, "export {}")
    expect(built).toContain("<h1>Settings</h1>")
    expect(built).toContain('id="controls"')
    expect(built).toContain('id="sidebar"')
    expect(policyOf(built)).toContain(`style-src ${hash(styles)}`)
  })
})
