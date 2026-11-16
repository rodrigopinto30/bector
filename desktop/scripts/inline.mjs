// Joins the setup page, its styles and its compiled script into dist/renderer/index.html.
import { mkdir, readFile, writeFile } from "node:fs/promises"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

import { buildPage } from "../dist/cjs/build/page.js"

const root = join(dirname(fileURLToPath(import.meta.url)), "..")
const [template, css, js] = await Promise.all([
  readFile(join(root, "src/renderer/index.html"), "utf8"),
  readFile(join(root, "src/renderer/styles.css"), "utf8"),
  readFile(join(root, "dist/esm/renderer/app.js"), "utf8"),
])
const out = join(root, "dist/renderer/index.html")
await mkdir(dirname(out), { recursive: true })
await writeFile(out, buildPage(template, css, js))
console.log("built", out)
