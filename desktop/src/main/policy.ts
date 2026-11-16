/** What the windows may load and open. Kept apart from Electron so it can be tested. */

/** Links that may open in the user's browser, and only these. */
const EXTERNAL_HOSTS = new Set(["console.anthropic.com", "docs.docker.com"])

export const CONSOLE_URL = "https://console.anthropic.com/settings/keys"

function parse(url: string): URL | null {
  try {
    return new URL(url)
  } catch {
    return null
  }
}

/** The address of the panel: this computer, over http, at its port. */
export function panelUrl(port: number): string {
  return `http://localhost:${port}/`
}

export function isPanelUrl(url: string, port: number): boolean {
  const parsed = parse(url)
  return parsed !== null && parsed.origin === `http://localhost:${port}`
}

/** Whether ``url`` is the page of the window, whatever its #fragment. */
export function isShellPage(url: string, pageUrl: string): boolean {
  const parsed = parse(url)
  const page = parse(pageUrl)
  if (parsed === null || page === null) {
    return false
  }
  parsed.hash = ""
  page.hash = ""
  return parsed.href === page.href
}

export function isAllowedExternal(url: string): boolean {
  const parsed = parse(url)
  return parsed !== null && parsed.protocol === "https:" && EXTERNAL_HOSTS.has(parsed.hostname)
}
