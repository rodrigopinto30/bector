import { describe, expect, it } from "vitest"

import { PanelLoad } from "./panel-load"

function load(): PanelLoad {
  return new PanelLoad()
}

describe("what is known of the panel's page", () => {
  it("needs to be loaded at first, and not again while it is loading", () => {
    const panel = load()
    expect(panel.needsLoad()).toBe(true)
    panel.requested()
    expect(panel.needsLoad()).toBe(false)
  })

  it("is loaded once a load stops without failing, and then needs nothing", () => {
    const panel = load()
    panel.requested()
    panel.started()
    panel.stopped()
    expect(panel.loaded).toBe(true)
    expect(panel.needsLoad()).toBe(false)
  })

  it("is not loaded when the load failed, even though the error page finishes", () => {
    const panel = load()
    panel.requested()
    panel.started()
    panel.failedToLoad()
    panel.stopped()
    expect(panel.loaded).toBe(false)
    expect(panel.needsLoad()).toBe(true)
  })

  it("is not loaded when the page stops before the failure is told", () => {
    const panel = load()
    panel.requested()
    panel.started()
    panel.stopped()
    panel.failedToLoad()
    expect(panel.loaded).toBe(false)
    expect(panel.needsLoad()).toBe(true)
  })

  it("loads again after a failure, and a new load that works is loaded", () => {
    const panel = load()
    panel.requested()
    panel.started()
    panel.failedToLoad()
    panel.stopped()
    expect(panel.needsLoad()).toBe(true)
    panel.requested()
    panel.started()
    panel.stopped()
    expect(panel.loaded).toBe(true)
  })

  it("asks to be loaded again if a page that was loaded fails later", () => {
    const panel = load()
    panel.requested()
    panel.started()
    panel.stopped()
    panel.started()
    panel.failedToLoad()
    panel.stopped()
    expect(panel.needsLoad()).toBe(true)
  })
})
