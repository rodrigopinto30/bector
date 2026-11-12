"use client"

import {
  Alert02Icon,
  Bug01Icon,
  Folder01Icon,
  InboxIcon,
  ListViewIcon,
  UserGroupIcon,
} from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import { Fragment, useState } from "react"

import { exitCode, FixCard } from "@/components/fixes"
import { RunSteps } from "@/components/run"
import { EmptyState, SectionTitle } from "@/components/section"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { LEVEL_COLORS } from "@/lib/charts"
import {
  fixResults,
  formatCount,
  formatFull,
  formatMoment,
  ordinal,
  projectName,
  RESOLUTION_LABELS,
  shortSignature,
  STATUS_LABELS,
  timeAgo,
} from "@/lib/format"
import type { Incident, Project, TeamFix, WaitingFix } from "@/lib/types"

export function Incidents({ incidents, now }: { incidents: Incident[]; now: Date }) {
  const [opened, setOpened] = useState<string | null>(null)
  return (
    <section className="flex flex-col gap-4">
      <SectionTitle
        icon={Bug01Icon}
        tint="rose"
        title="Incidents"
        description="The latest runs of healer run that had something to fix, newest first. Steps opens a run from the failing tests to its fix, with each attempt and its change, redacted."
      />
      {incidents.length ? (
        <Card size="sm">
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Project</TableHead>
                  <TableHead>Error</TableHead>
                  <TableHead>Solved by</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead className="text-right">Calls to Claude</TableHead>
                  <TableHead className="text-right">Tokens spent</TableHead>
                  <TableHead className="text-right">Tokens saved</TableHead>
                  <TableHead className="text-right">Steps</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {incidents.map((incident, index) => (
                  <Fragment key={`${incident.recorded_at}-${index}`}>
                    <TableRow>
                      <TableCell title={formatMoment(incident.recorded_at)}>
                        {timeAgo(incident.recorded_at, now)}
                      </TableCell>
                      <TableCell title={incident.project}>
                        {projectName(incident.project)}
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{incident.error_type}</span>{" "}
                        <span className="text-xs text-muted-foreground">
                          {shortSignature(incident.signature)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="inline-flex items-center gap-2">
                          <span
                            className="size-2.5 rounded-[3px]"
                            style={{ backgroundColor: LEVEL_COLORS[incident.resolution] }}
                            aria-hidden
                          />
                          {RESOLUTION_LABELS[incident.resolution]}
                        </span>
                      </TableCell>
                      <TableCell>{STATUS_LABELS[incident.status]}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatFull(incident.model_attempts)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatFull(incident.tokens_used)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatFull(incident.tokens_saved)}
                      </TableCell>
                      <TableCell className="text-right">
                        {incident.run_id ? (
                          <StepsToggle
                            open={opened === incident.run_id}
                            onToggle={() =>
                              setOpened(opened === incident.run_id ? null : incident.run_id)
                            }
                          />
                        ) : (
                          <span className="text-xs text-muted-foreground" title="Not kept">
                            –
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                    {incident.run_id && opened === incident.run_id ? (
                      <TableRow className="hover:bg-transparent">
                        <TableCell colSpan={9} className="whitespace-normal">
                          <div className="py-2">
                            <RunSteps id={incident.run_id} />
                          </div>
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : (
        <EmptyState icon={Bug01Icon} tint="rose" title="No incidents yet">
          Each time healer run finds failing tests, it records here how the error was solved and
          what it cost.
        </EmptyState>
      )}
    </section>
  )
}

export function Projects({ projects, now }: { projects: Project[]; now: Date }) {
  return (
    <section className="flex flex-col gap-4">
      <SectionTitle
        icon={Folder01Icon}
        tint="sky"
        title="Projects"
        description="Each project with incidents, its code index, and the fixes this computer verified for it."
      />
      {projects.length ? (
        projects.map((project) => (
          <Card key={project.path}>
            <CardContent className="flex flex-col gap-4">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h3 className="font-heading text-lg font-bold">{projectName(project.path)}</h3>
                <code className="text-xs text-muted-foreground">{project.path}</code>
              </div>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Fact
                  label="Symbols indexed"
                  value={project.symbols === null ? "not indexed" : formatCount(project.symbols)}
                />
                <Fact label="Incidents" value={formatCount(project.incidents)} />
                <Fact label="Fixed" value={formatCount(project.fixed)} />
                <Fact
                  label="Last incident"
                  value={project.last_incident ? timeAgo(project.last_incident, now) : "never"}
                />
              </dl>
              {project.fixes.length ? (
                <div className="flex flex-col gap-3">
                  <p className="text-sm font-semibold">
                    {project.fixes.length} verified {project.fixes.length === 1 ? "fix" : "fixes"},
                    reused for free when the same error comes back
                  </p>
                  {project.fixes.map((fix) => (
                    <FixCard
                      key={fix.signature}
                      error={fix.error}
                      signature={fix.signature}
                      explanation={fix.explanation}
                      edits={fix.edits}
                      facts={[
                        `verified ${timeAgo(fix.verified_at, now)}`,
                        `by ${fix.model}`,
                        `cost ${formatFull(fix.tokens_spent)} tokens`,
                        `${fix.command}: ${exitCode(fix.exit_code_before)} before, passed after`,
                      ]}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No verified fixes kept yet.</p>
              )}
            </CardContent>
          </Card>
        ))
      ) : (
        <EmptyState icon={Folder01Icon} tint="sky" title="No projects yet">
          A project shows up here after its first incident, with its index and its fixes.
        </EmptyState>
      )}
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-muted/60 px-3 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-heading font-bold">{value}</dd>
    </div>
  )
}

export function Team({ team, sharing, now }: { team: TeamFix[]; sharing: boolean; now: Date }) {
  return (
    <section className="flex flex-col gap-4">
      <SectionTitle
        icon={UserGroupIcon}
        tint="lavender"
        title="From the team"
        description="Fixes other computers shared, several per error when they differ, tried first where they worked most. Each one is only a suggestion: healer run tests it, and nothing is applied on its own."
      />
      {team.length ? (
        team.map((fix) => (
          <FixCard
            key={fix.fix_id}
            error={fix.error}
            signature={fix.signature}
            explanation={fix.explanation}
            edits={fix.edits}
            facts={[
              `from ${fix.node}`,
              `received ${timeAgo(fix.received_at, now)}`,
              `found ${formatMoment(fix.published_at)}`,
              `${fix.command}: ${exitCode(fix.exit_code_before)} before, passed after`,
              ...(fix.worked || fix.failed ? [fixResults(fix.worked, fix.failed)] : []),
              ...(fix.fixes_for_error > 1
                ? [`tried ${ordinal(fix.rank)} of ${fix.fixes_for_error} for this error`]
                : []),
            ]}
          />
        ))
      ) : (
        <EmptyState icon={UserGroupIcon} tint="lavender" title="No suggestions yet">
          {sharing
            ? "The team listener keeps here the fixes other computers accept."
            : "The team listener keeps here the fixes other computers accept. Start it with docker compose --profile team up -d listener."}
        </EmptyState>
      )}
    </section>
  )
}

function StepsToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <Button variant="secondary" size="sm" onClick={onToggle} aria-expanded={open}>
      <HugeiconsIcon icon={ListViewIcon} strokeWidth={2} aria-hidden />
      {open ? "Hide" : "Show"}
    </Button>
  )
}

export function Outbox({ outbox, now }: { outbox: WaitingFix[]; now: Date }) {
  return (
    <section className="flex flex-col gap-4">
      <SectionTitle
        icon={InboxIcon}
        tint="peach"
        title="Outbox"
        description="Fixes accepted here that the broker did not take. The listener sends them when it is back, or run healer outbox --send."
      />
      {outbox.length ? (
        outbox.map((fix) => (
          <FixCard
            key={`${fix.topic}-${fix.signature}`}
            error={fix.error}
            signature={fix.signature}
            edits={fix.edits}
            facts={[
              `found ${formatMoment(fix.published_at)}`,
              `waiting since ${timeAgo(fix.queued_at, now)}`,
              `tried ${fix.attempts} ${fix.attempts === 1 ? "time" : "times"}`,
              `for ${fix.topic}`,
            ]}
            aside={
              <div className="flex flex-col gap-1 rounded-2xl bg-tint-peach/70 px-3 py-2 text-sm">
                <p className="flex items-center gap-2 font-semibold">
                  <HugeiconsIcon
                    icon={Alert02Icon}
                    className="size-4"
                    strokeWidth={2}
                    aria-hidden
                  />
                  Last error
                </p>
                <p className="break-words">{fix.last_error}</p>
                {fix.for_current_topic ? null : (
                  <p className="text-muted-foreground">
                    Kept for another topic: it is sent only when MQTT_TOPIC is that one again.
                  </p>
                )}
              </div>
            }
          />
        ))
      ) : (
        <EmptyState icon={InboxIcon} tint="peach" title="The outbox is empty">
          Every fix shared from here reached the broker.
        </EmptyState>
      )}
    </section>
  )
}
