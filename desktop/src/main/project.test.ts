import { join } from "node:path"

import { describe, expect, it } from "vitest"

import { findProjectRoot, needsSetup, readPanelPort } from "./project"

const FILES = ["docker-compose.yml", "start.sh", join("src", "healer", "__init__.py")]

function projectAt(...folders: string[]): (path: string) => boolean {
  const present = new Set(folders.flatMap((folder) => FILES.map((file) => join(folder, file))))
  return (path) => present.has(path)
}

describe("finding the project", () => {
  it("uses HEALER_HOME when it is the project", () => {
    expect(findProjectRoot("/home/pc/healer", "/elsewhere", projectAt("/home/pc/healer"))).toBe(
      "/home/pc/healer",
    )
  })

  it("does not guess when HEALER_HOME is not the project", () => {
    expect(
      findProjectRoot("/nowhere", "/home/pc/healer/desktop", projectAt("/home/pc/healer")),
    ).toBeNull()
  })

  it("looks above the app when there is no HEALER_HOME", () => {
    const exists = projectAt("/home/pc/healer")
    expect(findProjectRoot(undefined, "/home/pc/healer/desktop/dist/cjs/main", exists)).toBe(
      "/home/pc/healer",
    )
    expect(findProjectRoot("", "/home/pc/healer/desktop", exists)).toBe("/home/pc/healer")
    expect(findProjectRoot(undefined, "/home/pc/other", exists)).toBeNull()
  })

  it("asks for all three marks of the project, not only one", () => {
    const onlyCompose = (path: string) => path === join("/p", "docker-compose.yml")
    expect(findProjectRoot("/p", "/p", onlyCompose)).toBeNull()
  })
})

describe("reading .env", () => {
  it("gives the panel's port, or 8765", () => {
    expect(readPanelPort(null)).toBe(8765)
    expect(readPanelPort("MQTT_HOST=x\n")).toBe(8765)
    expect(readPanelPort("PANEL_PORT=9000\n")).toBe(9000)
    expect(readPanelPort("PANEL_PORT=9000\nPANEL_PORT=9100")).toBe(9100)
    expect(readPanelPort("# PANEL_PORT=9000")).toBe(8765)
  })

  it.each(["PANEL_PORT=abc", "PANEL_PORT=0", "PANEL_PORT=70000", "PANEL_PORT=", "PANEL_PORT=80.5"])(
    "ignores a port that is not one (%s)",
    (line) => {
      expect(readPanelPort(line)).toBe(8765)
    },
  )

  it("needs start.sh until .env has its settings", () => {
    expect(needsSetup(null)).toBe(true)
    expect(needsSetup("ANTHROPIC_API_KEY=\n")).toBe(true)
    expect(needsSetup("ANTHROPIC_API_KEY=\nHOST_UID=1000\nHOST_GID=1000\n")).toBe(false)
  })
})
