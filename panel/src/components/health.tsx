import {
  Activity01Icon,
  InboxIcon,
  Key01Icon,
  LaptopIcon,
  Share08Icon,
  Shield01Icon,
  UserGroupIcon,
  Wifi01Icon,
} from "@hugeicons/core-free-icons"
import type { IconSvgElement } from "@hugeicons/react"

import { IconBubble, SectionTitle, type Tint } from "@/components/section"
import { StatusBadge } from "@/components/status"
import { Card, CardContent } from "@/components/ui/card"
import { formatFull, formatMoment, timeAgo } from "@/lib/format"
import type { Check, ListenerStatus } from "@/lib/types"

const LOOKS: Record<string, { icon: IconSvgElement; tint: Tint }> = {
  node: { icon: LaptopIcon, tint: "lavender" },
  broker: { icon: Wifi01Icon, tint: "sky" },
  sandbox: { icon: Shield01Icon, tint: "mint" },
  sharing: { icon: Share08Icon, tint: "peach" },
  api_key: { icon: Key01Icon, tint: "butter" },
  listener: { icon: UserGroupIcon, tint: "lavender" },
  outbox: { icon: InboxIcon, tint: "rose" },
}

export function Health({
  checks,
  listener,
  now,
}: {
  checks: Check[]
  listener: ListenerStatus | null
  now: Date
}) {
  return (
    <section aria-labelledby="health" className="flex flex-col gap-4">
      <SectionTitle
        icon={Activity01Icon}
        tint="mint"
        title="Health"
        description="The same checks as healer doctor, asked again on every refresh."
      />
      <div className="grid gap-3 sm:grid-cols-2">
        {checks.map((check) => (
          <CheckCard
            key={check.key}
            check={check}
            listener={check.key === "listener" ? listener : null}
            now={now}
          />
        ))}
      </div>
    </section>
  )
}

function CheckCard({
  check,
  listener,
  now,
}: {
  check: Check
  listener: ListenerStatus | null
  now: Date
}) {
  const look = LOOKS[check.key] ?? { icon: Activity01Icon, tint: "lavender" as const }
  const counts = listener && !listener.stopped && check.state !== "fail" ? listener : null
  return (
    <Card size="sm" className="gap-3">
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <IconBubble icon={look.icon} tint={look.tint} />
          <p className="min-w-0 flex-1 font-heading font-bold">{check.name}</p>
          <StatusBadge state={check.state} />
        </div>
        <p className="text-sm break-words text-muted-foreground">{check.detail}</p>
        {check.since ? (
          <p className="text-xs text-muted-foreground" title={formatMoment(check.since)}>
            since {timeAgo(check.since, now)}
          </p>
        ) : null}
        {counts ? (
          <dl className="grid grid-cols-3 gap-2 rounded-2xl bg-muted/60 p-2 text-center text-xs">
            <Count label="received" value={counts.received} />
            <Count label="ignored" value={counts.ignored} />
            <Count label="rejected" value={counts.rejected} />
          </dl>
        ) : null}
      </CardContent>
    </Card>
  )
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-heading text-base font-bold tabular-nums">{formatFull(value)}</dd>
    </div>
  )
}
