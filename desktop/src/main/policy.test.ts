import { describe, expect, it } from "vitest"

import { isAllowedExternal, isPanelUrl, isShellPage, panelUrl } from "./policy"

describe("what the windows may load", () => {
  it("knows the panel only at localhost, over http, on its port", () => {
    expect(panelUrl(8765)).toBe("http://localhost:8765/")
    for (const url of [
      "http://localhost:8765/",
      "http://localhost:8765/#team",
      "http://localhost:8765/api/snapshot",
    ]) {
      expect(isPanelUrl(url, 8765)).toBe(true)
    }
    for (const url of [
      "http://localhost:8766/",
      "https://localhost:8765/",
      "http://127.0.0.1:8765/",
      "http://localhost:8765.evil.example/",
      "http://evil.example/?http://localhost:8765/",
      "http://user@evil.example:8765/",
      "file:///etc/passwd",
      "javascript:alert(1)",
      "not a url",
    ]) {
      expect(isPanelUrl(url, 8765)).toBe(false)
    }
  })

  it("knows the page of the window whatever its fragment", () => {
    const page = "file:///home/pc/healer/desktop/dist/renderer/index.html"
    expect(isShellPage(page, page)).toBe(true)
    expect(isShellPage(`${page}#settings`, page)).toBe(true)
    expect(isShellPage("file:///etc/passwd", page)).toBe(false)
    expect(isShellPage(`${page}?x=1`, page)).toBe(false)
    expect(isShellPage("not a url", page)).toBe(false)
    expect(isShellPage(page, "not a url")).toBe(false)
  })

  it("opens in the browser only the Anthropic console and the Docker guide, over https", () => {
    for (const url of [
      "https://console.anthropic.com/settings/keys",
      "https://docs.docker.com/engine/install/",
    ]) {
      expect(isAllowedExternal(url)).toBe(true)
    }
    for (const url of [
      "http://console.anthropic.com/settings/keys",
      "https://console.anthropic.com.evil.example/",
      "https://evil.example/https://console.anthropic.com/",
      "https://api.anthropic.com/",
      "file:///etc/passwd",
      "javascript:alert(1)",
      "not a url",
    ]) {
      expect(isAllowedExternal(url)).toBe(false)
    }
  })
})
