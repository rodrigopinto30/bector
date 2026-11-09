import {
  Alert02Icon,
  CancelCircleIcon,
  CheckmarkCircle02Icon,
  MinusSignCircleIcon,
} from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import { cn } from "cn"

import type { CheckState } from "@/lib/types"

const STATES: Record<
  CheckState,
  { label: string; icon: typeof CheckmarkCircle02Icon; className: string }
> = {
  ok: {
    label: "OK",
    icon: CheckmarkCircle02Icon,
    className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-400/15 dark:text-emerald-200",
  },
  warning: {
    label: "Attention",
    icon: Alert02Icon,
    className: "bg-amber-100 text-amber-800 dark:bg-amber-400/15 dark:text-amber-200",
  },
  fail: {
    label: "Failing",
    icon: CancelCircleIcon,
    className: "bg-rose-100 text-rose-800 dark:bg-rose-400/15 dark:text-rose-200",
  },
  off: {
    label: "Off",
    icon: MinusSignCircleIcon,
    className: "bg-muted text-muted-foreground",
  },
}

/** A state always shows its icon and its word, never its color alone. */
export function StatusBadge({ state, className }: { state: CheckState; className?: string }) {
  const { label, icon, className: tone } = STATES[state]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold",
        tone,
        className,
      )}
    >
      <HugeiconsIcon icon={icon} className="size-3.5" strokeWidth={2} aria-hidden />
      {label}
    </span>
  )
}

export function worstState(states: CheckState[]): CheckState {
  for (const state of ["fail", "warning", "ok"] as const) {
    if (states.includes(state)) {
      return state
    }
  }
  return "off"
}
