import type { Bounds, Section, WindowAction } from "../shared/api"

/** No window is larger than this, so a number beyond it is a mistake. */
const LIMIT = 20_000

export function isSection(value: unknown): value is Section {
  return value === "panel" || value === "project" || value === "fix" || value === "settings"
}

export function isWindowAction(value: unknown): value is WindowAction {
  return value === "minimize" || value === "toggleMaximize" || value === "close"
}

/** A rectangle from the page: four numbers from 0 to LIMIT, rounded to whole pixels. */
export function parseBounds(value: unknown): Bounds {
  if (typeof value !== "object" || value === null) {
    throw new Error("the bounds must be an object")
  }
  const record = value as Record<string, unknown>
  const read = (key: "x" | "y" | "width" | "height"): number => {
    const number = record[key]
    if (typeof number !== "number" || !Number.isFinite(number) || number < 0 || number > LIMIT) {
      throw new Error(`the bounds ${key} must be a number from 0 to ${LIMIT}`)
    }
    return Math.round(number)
  }
  return { x: read("x"), y: read("y"), width: read("width"), height: read("height") }
}

/** ``bounds`` cut to fit inside an area, so the panel can never go beyond the window. */
export function fitBounds(bounds: Bounds, area: { width: number; height: number }): Bounds {
  const x = Math.min(bounds.x, area.width)
  const y = Math.min(bounds.y, area.height)
  return {
    x,
    y,
    width: Math.max(0, Math.min(bounds.width, area.width - x)),
    height: Math.max(0, Math.min(bounds.height, area.height - y)),
  }
}
