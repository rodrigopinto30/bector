import type { RepairStatus, Resolution } from "@/lib/types"

const full = new Intl.NumberFormat("en")
const compact = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
})

/** 1,284 in full under ten thousand; 12.9K and 4.2M above. */
export function formatCount(value: number): string {
  return Math.abs(value) < 10_000 ? full.format(value) : compact.format(value)
}

/** Always compact: 0, 5K, 12.5K. */
export function formatCompact(value: number): string {
  return compact.format(value)
}

export function formatFull(value: number): string {
  return full.format(value)
}

/** The share of `part` in `whole` as a whole percentage; 0 when there is no whole. */
export function percent(part: number, whole: number): number {
  return whole > 0 ? Math.round((100 * part) / whole) : 0
}

/** A moment in the reader's own time zone, such as "Nov 9, 14:32". */
export function formatMoment(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(new Date(iso))
}

/** A UTC day ("2026-11-09") as "Nov 9". */
export function formatDay(day: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`))
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" })
const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
]

/** How long ago `iso` was, from `now`: "just now", "5 minutes ago", "yesterday". */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds < 45) {
    return "just now"
  }
  for (const [unit, size] of STEPS) {
    if (seconds >= size) {
      return relative.format(-Math.floor(seconds / size), unit)
    }
  }
  return relative.format(-1, "minute")
}

export function shortSignature(signature: string): string {
  return signature.slice(0, 12)
}

/** The last folder of a project path: "/workspace/shop" is "shop". */
export function projectName(path: string): string {
  const parts = path.split("/").filter(Boolean)
  return parts.at(-1) ?? path
}

export const RESOLUTION_LABELS: Record<Resolution, string> = {
  cache: "Local cache",
  team: "Team",
  model: "Claude",
  none: "Unresolved",
}

export const STATUS_LABELS: Record<RepairStatus, string> = {
  fixed: "Fixed",
  passing: "Already passing",
  undiagnosed: "Not diagnosed",
  out_of_attempts: "Out of attempts",
  out_of_budget: "Out of budget",
  stopped: "Stopped",
}

function nodes(count: number): string {
  return count === 1 ? "1 node" : `${count} nodes`
}

/** For example "worked on 2 nodes, failed on 1 node", leaving out a zero. */
export function fixResults(worked: number, failed: number): string {
  return [worked && `worked on ${nodes(worked)}`, failed && `failed on ${nodes(failed)}`]
    .filter(Boolean)
    .join(", ")
}

/** 1st, 2nd, 3rd, 4th, ..., 11th, 12th, 13th, 21st. */
export function ordinal(place: number): string {
  const tens = place % 100
  const suffix =
    tens >= 11 && tens <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[place % 10] ?? "th")
  return `${place}${suffix}`
}
