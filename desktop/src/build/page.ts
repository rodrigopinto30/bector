import { createHash } from "node:crypto"

const MARKERS = ["<!--CSP-->", "<!--STYLE-->", "<!--SCRIPT-->"] as const

function hash(text: string): string {
  return `'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`
}

/**
 * The setup page as one file: its styles and its script written inline, and a
 * Content-Security-Policy that allows exactly those two by their hash. Nothing else may run or
 * load, and nothing may connect anywhere: the page talks to the app only through its bridge.
 */
export function buildPage(template: string, css: string, js: string): string {
  for (const marker of MARKERS) {
    if (template.split(marker).length !== 2) {
      throw new Error(`the page template must have ${marker} exactly once`)
    }
  }
  if (/<\/script/i.test(js) || /<\/style/i.test(css)) {
    throw new Error("the script or the styles would end their own tag")
  }
  if (/^\s*import\s/m.test(js)) {
    throw new Error("the script imports other files: it must be self-contained")
  }
  const policy = [
    "default-src 'none'",
    `script-src ${hash(js)}`,
    `style-src ${hash(css)}`,
    "img-src 'none'",
    "connect-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ")
  return template
    .replace("<!--CSP-->", `<meta http-equiv="Content-Security-Policy" content="${policy}" />`)
    .replace("<!--STYLE-->", () => `<style>${css}</style>`)
    .replace("<!--SCRIPT-->", () => `<script type="module">${js}</script>`)
}
