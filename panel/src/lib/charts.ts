import { formatCompact, formatDay, formatFull, percent, RESOLUTION_LABELS } from "@/lib/format"
import type { DailyTokens, Resolution } from "@/lib/types"

/** The chart order of the levels, cheapest first. Each keeps its color everywhere. */
export const LEVELS: Resolution[] = ["cache", "team", "model", "none"]

export const LEVEL_COLORS: Record<Resolution, string> = {
  cache: "var(--chart-1)",
  team: "var(--chart-2)",
  model: "var(--chart-3)",
  none: "var(--chart-4)",
}

export type DayRow = {
  day: string
  label: string
  incidents: number
  cache: number
  team: number
  model: number
  total: number
}

/** One column per day: what the cache and the team saved, and what Claude cost. */
export function dayRows(days: DailyTokens[]): DayRow[] {
  return days.map((d) => ({
    day: d.day,
    label: formatDay(d.day),
    incidents: d.incidents,
    cache: d.saved_by_cache,
    team: d.saved_by_team,
    model: d.spent,
    total: d.saved_by_cache + d.saved_by_team + d.spent,
  }))
}

/** The day with the highest column, to label it; null when every day is empty. */
export function busiestDay(rows: DayRow[]): DayRow | null {
  return rows.reduce<DayRow | null>(
    (best, row) => (row.total > 0 && (best === null || row.total > best.total) ? row : best),
    null,
  )
}

export type Part = {
  key: Resolution
  label: string
  value: number
  share: number
}

/** How the incidents were resolved, in level order, without the levels that solved none. */
export function resolutionParts(counts: Record<Resolution, number>): Part[] {
  const whole = LEVELS.reduce((sum, key) => sum + counts[key], 0)
  return LEVELS.filter((key) => counts[key] > 0).map((key) => ({
    key,
    label: RESOLUTION_LABELS[key],
    value: counts[key],
    share: percent(counts[key], whole),
  }))
}

/** Round ticks from 0 that cover `max`: steps of 1, 2, 2.5 or 5 times a power of ten. */
export function axisTicks(max: number, count = 4): number[] {
  if (max <= 0) {
    return [0]
  }
  const rough = max / count
  const power = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power
  const steps = Math.ceil(max / step)
  return Array.from({ length: steps + 1 }, (_, index) => index * step)
}

/** One style per axis: compact (10K) when the axis reaches ten thousand, else in full. */
export function axisFormat(ticks: number[]): (value: number) => string {
  return (ticks.at(-1) ?? 0) >= 10_000 ? formatCompact : formatFull
}
