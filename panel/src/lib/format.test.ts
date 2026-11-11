import { describe, expect, it } from "vitest"

import { axisFormat, axisTicks, busiestDay, dayRows, resolutionParts } from "@/lib/charts"
import {
  fixResults,
  formatCount,
  formatDay,
  formatMoment,
  ordinal,
  percent,
  projectName,
  shortSignature,
  timeAgo,
} from "@/lib/format"

describe("numbers", () => {
  it("are in full under ten thousand and compact above", () => {
    expect(formatCount(0)).toBe("0")
    expect(formatCount(9_999)).toBe("9,999")
    expect(formatCount(12_900)).toBe("12.9K")
    expect(formatCount(4_200_000)).toBe("4.2M")
  })

  it("give a share only when there is a whole", () => {
    expect(percent(1, 3)).toBe(33)
    expect(percent(5, 0)).toBe(0)
  })
})

describe("times", () => {
  it("are shown in the reader's time zone", () => {
    expect(formatMoment("2026-11-09T14:32:00Z", "UTC")).toBe("Nov 9, 14:32")
    expect(formatMoment("2026-11-09T01:05:00Z", "America/Argentina/Buenos_Aires")).toBe(
      "Nov 8, 22:05",
    )
  })

  it("name UTC days without moving them", () => {
    expect(formatDay("2026-11-09")).toBe("Nov 9")
  })

  it("say how long ago something happened", () => {
    const now = new Date("2026-11-09T12:00:00Z")
    expect(timeAgo("2026-11-09T11:59:30Z", now)).toBe("just now")
    expect(timeAgo("2026-11-09T11:55:00Z", now)).toBe("5 minutes ago")
    expect(timeAgo("2026-11-09T09:00:00Z", now)).toBe("3 hours ago")
    expect(timeAgo("2026-11-08T11:00:00Z", now)).toBe("yesterday")
  })
})

describe("names", () => {
  it("shorten signatures and project paths", () => {
    expect(shortSignature("a".repeat(64))).toBe("a".repeat(12))
    expect(projectName("/workspace/shop")).toBe("shop")
    expect(projectName("/workspace/")).toBe("workspace")
  })
})

describe("charts", () => {
  const days = [
    { day: "2026-11-08", incidents: 0, spent: 0, saved_by_cache: 0, saved_by_team: 0 },
    { day: "2026-11-09", incidents: 3, spent: 1_200, saved_by_cache: 800, saved_by_team: 400 },
  ]

  it("stack what was saved and what was spent each day", () => {
    const rows = dayRows(days)
    expect(rows[1]).toEqual({
      day: "2026-11-09",
      label: "Nov 9",
      incidents: 3,
      cache: 800,
      team: 400,
      model: 1_200,
      total: 2_400,
    })
    expect(busiestDay(rows)?.day).toBe("2026-11-09")
    expect(busiestDay(dayRows(days.slice(0, 1)))).toBeNull()
  })

  it("split the incidents by level, leaving out empty levels", () => {
    expect(resolutionParts({ cache: 2, team: 0, model: 1, none: 1 })).toEqual([
      { key: "cache", label: "Local cache", value: 2, share: 50 },
      { key: "model", label: "Claude", value: 1, share: 25 },
      { key: "none", label: "Unresolved", value: 1, share: 25 },
    ])
    expect(resolutionParts({ cache: 0, team: 0, model: 0, none: 0 })).toEqual([])
  })

  it("put round ticks on the axis, in one style", () => {
    expect(axisTicks(0)).toEqual([0])
    expect(axisTicks(21_000)).toEqual([0, 10_000, 20_000, 30_000])
    expect(axisTicks(8_000)).toEqual([0, 2_000, 4_000, 6_000, 8_000])
    expect(axisTicks(900)).toEqual([0, 250, 500, 750, 1_000])
    expect(axisTicks(21_000).map(axisFormat(axisTicks(21_000)))).toEqual(["0", "10K", "20K", "30K"])
    expect(axisTicks(8_000).map(axisFormat(axisTicks(8_000)))).toEqual([
      "0",
      "2,000",
      "4,000",
      "6,000",
      "8,000",
    ])
  })
})

describe("team results", () => {
  it("leave a zero out", () => {
    expect(fixResults(2, 1)).toBe("worked on 2 nodes, failed on 1 node")
    expect(fixResults(1, 0)).toBe("worked on 1 node")
    expect(fixResults(0, 3)).toBe("failed on 3 nodes")
  })

  it("say each place in the order", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 111].map(ordinal)).toEqual([
      "1st",
      "2nd",
      "3rd",
      "4th",
      "11th",
      "12th",
      "13th",
      "21st",
      "22nd",
      "111th",
    ])
  })
})
