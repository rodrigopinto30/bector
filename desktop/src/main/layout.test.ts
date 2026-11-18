import { describe, expect, it } from "vitest"

import { fitBounds, isSection, isWindowAction, parseBounds } from "./layout"

describe("the layout of the window", () => {
  it("knows its sections and its window actions", () => {
    expect(["panel", "project", "fix", "settings"].every(isSection)).toBe(true)
    expect(["minimize", "toggleMaximize", "close"].every(isWindowAction)).toBe(true)
    for (const value of ["", "Panel", "admin", 1, null, undefined, {}]) {
      expect(isSection(value)).toBe(false)
      expect(isWindowAction(value)).toBe(false)
    }
  })

  it("reads a rectangle as whole pixels", () => {
    expect(parseBounds({ x: 0.4, y: 43.6, width: 1000.5, height: 20, extra: "ignored" })).toEqual({
      x: 0,
      y: 44,
      width: 1001,
      height: 20,
    })
  })

  it("accepts the largest rectangle and nothing beyond", () => {
    expect(parseBounds({ x: 0, y: 0, width: 20_000, height: 20_000 }).width).toBe(20_000)
    expect(() => parseBounds({ x: 0, y: 0, width: 20_000.6, height: 1 })).toThrow(/width/)
  })

  it("cuts a rectangle to fit the window, so the panel can never go beyond it", () => {
    const window = { width: 1000, height: 700 }
    expect(fitBounds({ x: 232, y: 44, width: 768, height: 656 }, window)).toEqual({
      x: 232,
      y: 44,
      width: 768,
      height: 656,
    })
    expect(fitBounds({ x: 232, y: 44, width: 5000, height: 5000 }, window)).toEqual({
      x: 232,
      y: 44,
      width: 768,
      height: 656,
    })
    expect(fitBounds({ x: 2000, y: 900, width: 10, height: 10 }, window)).toEqual({
      x: 1000,
      y: 700,
      width: 0,
      height: 0,
    })
  })
})
