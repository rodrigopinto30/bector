"use client"

import {
  Alert02Icon,
  Bug01Icon,
  CancelCircleIcon,
  CheckmarkCircle02Icon,
  InboxIcon,
  MinusSignCircleIcon,
  Notification03Icon,
  UserGroupIcon,
  Wifi01Icon,
} from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import type { IconSvgElement } from "@hugeicons/react"
import { cn } from "cn"
import { useEffect, useRef, useState } from "react"

import { IconBubble, SectionTitle, type Tint } from "@/components/section"
import { Card, CardContent } from "@/components/ui/card"
import { formatMoment, timeAgo } from "@/lib/format"
import type { ActivityItem } from "@/lib/types"

const LOOKS: Record<ActivityItem["kind"], { icon: IconSvgElement; tint: Tint }> = {
  incident: { icon: Bug01Icon, tint: "rose" },
  suggestion: { icon: UserGroupIcon, tint: "lavender" },
  waiting: { icon: InboxIcon, tint: "peach" },
  connection: { icon: Wifi01Icon, tint: "sky" },
  ignored: { icon: MinusSignCircleIcon, tint: "butter" },
  rejected: { icon: CancelCircleIcon, tint: "rose" },
  result: { icon: CheckmarkCircle02Icon, tint: "mint" },
}

const STATE_WORDS: Record<ActivityItem["state"], string> = {
  ok: "OK",
  warning: "attention",
  fail: "problem",
  off: "info",
}

export function activityKey(item: ActivityItem): string {
  return `${item.at}|${item.kind}|${item.title}|${item.detail}`
}

/**
 * The keys that were not in the previous snapshot. The first snapshot marks nothing, so
 * only what happens while the page is open is highlighted.
 */
function useFreshKeys(items: ActivityItem[]): Set<string> {
  const seen = useRef<Set<string> | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  useEffect(() => {
    const keys = items.map(activityKey)
    const previous = seen.current
    seen.current = new Set(keys)
    const added = previous === null ? [] : keys.filter((key) => !previous.has(key))
    const update = window.setTimeout(() => setFresh(new Set(added)), 0)
    return () => window.clearTimeout(update)
  }, [items])
  return fresh
}

export function Activity({ items, now }: { items: ActivityItem[]; now: Date }) {
  const fresh = useFreshKeys(items)
  return (
    <section aria-labelledby="activity" className="flex flex-col gap-4">
      <SectionTitle
        icon={Notification03Icon}
        tint="lavender"
        title="Recent activity"
        description="What just happened on this node, newest first. New events light up for a moment."
      />
      <Card size="sm">
        <CardContent>
          {items.length ? (
            <ol className="flex flex-col" aria-live="polite">
              {items.map((item) => {
                const look = LOOKS[item.kind]
                const isNew = fresh.has(activityKey(item))
                return (
                  <li
                    key={activityKey(item)}
                    className={cn(
                      "flex items-start gap-3 rounded-2xl px-2 py-2.5",
                      isNew ? "bg-tint-mint" : "transition-colors duration-1000",
                    )}
                  >
                    <IconBubble icon={look.icon} tint={look.tint} className="size-8 rounded-xl" />
                    <div className="min-w-0 flex-1">
                      <p className="flex flex-wrap items-center gap-x-2 font-semibold">
                        {item.title}
                        {item.state === "fail" || item.state === "warning" ? (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
                            <HugeiconsIcon
                              icon={Alert02Icon}
                              className="size-3.5"
                              strokeWidth={2}
                              aria-hidden
                            />
                            {STATE_WORDS[item.state]}
                          </span>
                        ) : null}
                        {isNew ? (
                          <span className="rounded-full bg-card px-2 py-0.5 text-[11px] font-bold">
                            new
                          </span>
                        ) : null}
                      </p>
                      <p className="text-sm break-words text-muted-foreground">{item.detail}</p>
                    </div>
                    <time
                      dateTime={item.at}
                      title={formatMoment(item.at)}
                      className="shrink-0 pt-0.5 text-xs text-muted-foreground"
                    >
                      {timeAgo(item.at, now)}
                    </time>
                  </li>
                )
              })}
            </ol>
          ) : (
            <p className="p-4 text-center text-sm text-muted-foreground">
              Nothing yet. Incidents, fixes from the team and what the listener receives show up
              here as they happen.
            </p>
          )}
        </CardContent>
      </Card>
    </section>
  )
}
