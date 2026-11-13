"use client"

import { Alert02Icon, CheckmarkCircle02Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import type { IconSvgElement } from "@hugeicons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import type { ActionOutcome } from "@/lib/types"

type Stage = "idle" | "confirming" | "working"

/**
 * A button that asks before it acts: the first press shows what will happen and a confirm
 * button, the second one runs `run`. A failure is shown below the button; what a success
 * said goes to `onDone`, since what it changed may leave the page.
 */
export function ActionButton({
  icon,
  label,
  question,
  confirm,
  run,
  onDone,
}: {
  icon: IconSvgElement
  label: string
  question: string
  confirm: string
  run: () => Promise<ActionOutcome>
  onDone: (outcome: ActionOutcome) => void
}) {
  const [stage, setStage] = useState<Stage>("idle")
  const [outcome, setOutcome] = useState<ActionOutcome | null>(null)

  async function go() {
    setStage("working")
    const said = await run()
    setStage("idle")
    if (said.ok) {
      onDone(said)
    } else {
      setOutcome(said)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {stage === "idle" ? (
        <Button
          variant="secondary"
          size="sm"
          className="w-fit"
          onClick={() => {
            setOutcome(null)
            setStage("confirming")
          }}
        >
          <HugeiconsIcon icon={icon} strokeWidth={2} aria-hidden />
          {label}
        </Button>
      ) : (
        <div
          className="flex flex-wrap items-center gap-2 rounded-2xl bg-tint-butter/70 px-3 py-2 text-sm"
          role="group"
          aria-label={question}
        >
          <span className="font-medium">{question}</span>
          <Button size="sm" onClick={go} disabled={stage === "working"}>
            {stage === "working" ? "Working…" : confirm}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setStage("idle")}
            disabled={stage === "working"}
          >
            Cancel
          </Button>
        </div>
      )}
      {outcome ? <Notice outcome={outcome} /> : null}
    </div>
  )
}

/** What an action said, with an icon and its words, never color alone. */
export function Notice({ outcome }: { outcome: ActionOutcome }) {
  return (
    <p className="flex items-start gap-1.5 text-sm text-muted-foreground" role="status">
      <HugeiconsIcon
        icon={outcome.ok ? CheckmarkCircle02Icon : Alert02Icon}
        className="mt-0.5 size-4 shrink-0"
        strokeWidth={2}
        aria-hidden
      />
      {outcome.message}
    </p>
  )
}
