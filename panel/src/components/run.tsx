"use client"

import {
  Alert02Icon,
  ArrowDown01Icon,
  Bug01Icon,
  CancelCircleIcon,
  CheckmarkCircle02Icon,
  CodeIcon,
  DatabaseIcon,
  AiMagicIcon,
  RepeatIcon,
  Share08Icon,
  UserGroupIcon,
} from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import type { IconSvgElement } from "@hugeicons/react"
import { cn } from "cn"
import { useEffect, useState, type ReactNode } from "react"

import { DiffView } from "@/components/fixes"
import { IconBubble, type Tint } from "@/components/section"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Skeleton } from "@/components/ui/skeleton"
import {
  afterwards,
  attemptCost,
  attemptTitle,
  baselineSummary,
  OUTCOME_WORDS,
  testsSummary,
  verdictTitle,
} from "@/lib/run"
import type { AttemptOutcome, AttemptSource, Run, RunAttempt } from "@/lib/types"

const SOURCE_LOOKS: Record<AttemptSource, { icon: IconSvgElement; tint: Tint }> = {
  cache: { icon: DatabaseIcon, tint: "sky" },
  team: { icon: UserGroupIcon, tint: "lavender" },
  model: { icon: AiMagicIcon, tint: "peach" },
}

const OUTCOME_LOOKS: Record<AttemptOutcome, { icon: IconSvgElement; className: string }> = {
  passed: { icon: CheckmarkCircle02Icon, className: "bg-tint-mint" },
  failed: { icon: CancelCircleIcon, className: "bg-tint-rose" },
  rejected: { icon: Alert02Icon, className: "bg-tint-butter" },
  repeated: { icon: RepeatIcon, className: "bg-muted" },
}

type Loaded = { id: string; run: Run | null; error: string | null }

/** Fetches /api/runs/<id> once, when the run is opened. */
function useRun(id: string): Loaded | null {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  useEffect(() => {
    let live = true
    fetch(`/api/runs/${id}`, { cache: "no-store" })
      .then(async (response) => {
        const body = (await response.json()) as Run | { error?: string }
        if (!response.ok) {
          const said = "error" in body ? body.error : undefined
          throw new Error(said ?? `the panel server answered ${response.status}`)
        }
        return body as Run
      })
      .then((run) => live && setLoaded({ id, run, error: null }))
      .catch(
        (reason: unknown) =>
          live &&
          setLoaded({
            id,
            run: null,
            error: reason instanceof Error ? reason.message : String(reason),
          }),
      )
    return () => {
      live = false
    }
  }, [id])
  return loaded?.id === id ? loaded : null
}

/** A run of healer run, step by step: the failing tests, each attempt, and the end. */
export function RunSteps({ id }: { id: string }) {
  const loaded = useRun(id)
  if (loaded === null) {
    return <Skeleton className="h-28 rounded-2xl" aria-label="Loading the run" />
  }
  const { run, error } = loaded
  if (run === null) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <HugeiconsIcon icon={Alert02Icon} className="size-4" strokeWidth={2} aria-hidden />
        The run could not be shown: {error}
      </p>
    )
  }
  const fixed = run.status === "fixed"
  const after = afterwards(run)
  return (
    <ol className="flex flex-col gap-4" aria-label="The run, step by step">
      <Step icon={Bug01Icon} tint="rose" title="The tests failed">
        <p className="font-mono text-sm break-words">{run.error}</p>
        <Facts
          items={[run.location && `in ${run.location}`, run.command, baselineSummary(run.baseline)]}
        />
      </Step>
      {run.attempts.map((attempt) => (
        <AttemptStep key={attempt.number} attempt={attempt} />
      ))}
      <Step
        icon={fixed ? CheckmarkCircle02Icon : CancelCircleIcon}
        tint={fixed ? "mint" : "rose"}
        title={verdictTitle(run)}
      >
        {run.detail ? <p className="text-sm text-muted-foreground">{run.detail}</p> : null}
      </Step>
      {after.length ? (
        <Step icon={Share08Icon} tint="butter" title="Afterwards">
          <Facts items={after} />
        </Step>
      ) : null}
    </ol>
  )
}

function AttemptStep({ attempt }: { attempt: RunAttempt }) {
  const [open, setOpen] = useState(false)
  const look = SOURCE_LOOKS[attempt.source]
  const outcome = OUTCOME_LOOKS[attempt.outcome]
  const files = new Set(attempt.edits.map((edit) => edit.path)).size
  return (
    <Step
      icon={look.icon}
      tint={look.tint}
      title={attemptTitle(attempt)}
      badge={
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
            outcome.className,
          )}
        >
          <HugeiconsIcon icon={outcome.icon} className="size-3.5" strokeWidth={2} aria-hidden />
          {OUTCOME_WORDS[attempt.outcome]}
        </span>
      }
    >
      {attempt.explanation ? <p className="text-sm">{attempt.explanation}</p> : null}
      <Facts
        items={[
          attemptCost(attempt),
          attempt.tests ? testsSummary(attempt.tests) : attempt.problem,
        ]}
      />
      {attempt.edits.length ? (
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="secondary" size="sm" className="w-fit">
              <HugeiconsIcon icon={CodeIcon} strokeWidth={2} aria-hidden />
              {open
                ? "Hide the change"
                : `Show the change (${files} ${files === 1 ? "file" : "files"})`}
              <HugeiconsIcon
                icon={ArrowDown01Icon}
                strokeWidth={2}
                className={cn("transition-transform", open && "rotate-180")}
                aria-hidden
              />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-3">
            <DiffView edits={attempt.edits} />
            {attempt.edits_cut ? (
              <p className="pt-2 text-xs text-muted-foreground">
                The change was too long to keep whole; only its first edits are shown.
              </p>
            ) : null}
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </Step>
  )
}

function Step({
  icon,
  tint,
  title,
  badge,
  children,
}: {
  icon: IconSvgElement
  tint: Tint
  title: string
  badge?: ReactNode
  children?: ReactNode
}) {
  return (
    <li className="flex gap-3">
      <IconBubble icon={icon} tint={tint} className="size-8 rounded-xl" />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5 pt-1">
        <p className="flex flex-wrap items-center gap-2 font-semibold">
          {title}
          {badge}
        </p>
        {children}
      </div>
    </li>
  )
}

function Facts({ items }: { items: (string | false | null | undefined)[] }) {
  const shown = items.filter((item): item is string => Boolean(item))
  return shown.length ? (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {shown.map((item, index) => (
        <li key={index} className="break-words">
          {item}
        </li>
      ))}
    </ul>
  ) : null
}
