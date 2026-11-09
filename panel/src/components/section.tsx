import { HugeiconsIcon } from "@hugeicons/react"
import type { IconSvgElement } from "@hugeicons/react"
import { cn } from "cn"
import type { ReactNode } from "react"

export type Tint = "lavender" | "sky" | "mint" | "peach" | "butter" | "rose"

const TINTS: Record<Tint, string> = {
  lavender: "bg-tint-lavender",
  sky: "bg-tint-sky",
  mint: "bg-tint-mint",
  peach: "bg-tint-peach",
  butter: "bg-tint-butter",
  rose: "bg-tint-rose",
}

/** A soft round bubble that carries an icon in one of the pastel tints. */
export function IconBubble({
  icon,
  tint,
  className,
}: {
  icon: IconSvgElement
  tint: Tint
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex size-9 shrink-0 items-center justify-center rounded-2xl text-foreground/80",
        TINTS[tint],
        className,
      )}
    >
      <HugeiconsIcon icon={icon} className="size-[18px]" strokeWidth={1.8} aria-hidden />
    </span>
  )
}

export function SectionTitle({
  icon,
  tint,
  title,
  description,
  action,
}: {
  icon: IconSvgElement
  tint: Tint
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <IconBubble icon={icon} tint={tint} />
      <div className="min-w-0 flex-1">
        <h2 className="font-heading text-lg font-bold">{title}</h2>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {action}
    </div>
  )
}

/** What a list shows when it has nothing yet: what it is for and how it fills up. */
export function EmptyState({
  icon,
  tint,
  title,
  children,
}: {
  icon: IconSvgElement
  tint: Tint
  title: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-3xl border border-dashed border-border px-6 py-10 text-center">
      <IconBubble icon={icon} tint={tint} className="size-12 rounded-3xl" />
      <p className="font-heading font-bold">{title}</p>
      <p className="max-w-md text-sm text-muted-foreground">{children}</p>
    </div>
  )
}
