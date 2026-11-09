"use client"

import { ArrowDown01Icon, CodeIcon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import { cn } from "cn"
import { useState, type ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { shortSignature } from "@/lib/format"
import type { DiffLine, Edit } from "@/lib/types"

const LINE_STYLES: Record<DiffLine["kind"], { sign: string; className: string }> = {
  context: { sign: " ", className: "text-muted-foreground" },
  added: {
    sign: "+",
    className: "bg-emerald-100/70 text-emerald-950 dark:bg-emerald-400/12 dark:text-emerald-100",
  },
  removed: {
    sign: "-",
    className: "bg-rose-100/70 text-rose-950 dark:bg-rose-400/12 dark:text-rose-100",
  },
  gap: { sign: "⋯", className: "text-muted-foreground/70" },
}

/** The lines an edit removes and adds; the text is shown as text, never as markup. */
export function DiffView({ edits }: { edits: Edit[] }) {
  return (
    <div className="flex flex-col gap-3">
      {edits.map((edit, index) => (
        <div key={`${edit.path}-${index}`} className="overflow-hidden rounded-2xl border">
          <div className="flex items-center gap-2 bg-muted/70 px-3 py-1.5 font-mono text-xs">
            <span className="truncate">{edit.path}</span>
            {edit.created ? <Badge variant="secondary">new file</Badge> : null}
          </div>
          <pre className="overflow-x-auto py-1 font-mono text-xs leading-5">
            {edit.lines.map((line, n) => {
              const look = LINE_STYLES[line.kind]
              return (
                <div key={n} className={cn("flex min-w-max px-3", look.className)}>
                  <span className="w-4 shrink-0 select-none" aria-hidden>
                    {look.sign}
                  </span>
                  <span className="sr-only">
                    {line.kind === "added" ? "added: " : line.kind === "removed" ? "removed: " : ""}
                  </span>
                  <span className="whitespace-pre">{line.kind === "gap" ? "" : line.text}</span>
                </div>
              )
            })}
          </pre>
        </div>
      ))}
    </div>
  )
}

/** One fix: its error, a few facts, the explanation and the change, folded until asked. */
export function FixCard({
  error,
  signature,
  facts,
  explanation,
  edits,
  aside,
}: {
  error: string
  signature: string
  facts: ReactNode[]
  explanation?: string
  edits: Edit[]
  aside?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const files = new Set(edits.map((edit) => edit.path)).size
  return (
    <Card size="sm">
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start gap-2">
          <p className="min-w-0 flex-1 font-mono text-sm font-semibold break-words">{error}</p>
          <code className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
            {shortSignature(signature)}
          </code>
        </div>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {facts.map((fact, index) => (
            <li key={index}>{fact}</li>
          ))}
        </ul>
        {aside}
        {explanation ? <p className="text-sm">{explanation}</p> : null}
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
            <DiffView edits={edits} />
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}

export function exitCode(code: number | null): string {
  return code === null ? "timed out" : `exit code ${code}`
}
