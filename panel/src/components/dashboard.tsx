"use client"

import { Alert02Icon, Leaf01Icon, RefreshIcon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import { cn } from "cn"
import { useSyncExternalStore } from "react"

import { Activity } from "@/components/activity"
import { Health } from "@/components/health"
import { Incidents, Outbox, Projects, Team } from "@/components/lists"
import { Savings } from "@/components/savings"
import { IconBubble } from "@/components/section"
import { StatusBadge, worstState } from "@/components/status"
import { ThemeToggle } from "@/components/theme-toggle"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useSnapshot } from "@/hooks/use-snapshot"
import { formatMoment, timeAgo } from "@/lib/format"
import type { Snapshot } from "@/lib/types"

const TABS = ["overview", "incidents", "projects", "team", "outbox"]

function subscribeToHash(onChange: () => void) {
  window.addEventListener("hashchange", onChange)
  return () => window.removeEventListener("hashchange", onChange)
}

/** The open tab lives in the address (#incidents), so it can be bookmarked and survives a reload. */
function useTab(): [string, (tab: string) => void] {
  const hash = useSyncExternalStore(
    subscribeToHash,
    () => window.location.hash.slice(1),
    () => "",
  )
  const tab = TABS.includes(hash) ? hash : "overview"
  const choose = (next: string) => {
    window.location.hash = next === "overview" ? "" : next
  }
  return [tab, choose]
}

const SECTION_NAMES: Record<string, string> = {
  savings: "Savings and incidents",
  listener: "Team listener",
  outbox: "Outbox",
  team: "Team suggestions",
  projects: "Projects",
}

export function Dashboard() {
  const { snapshot, error, refreshing, refresh } = useSnapshot()
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:py-10">
      <Header snapshot={snapshot} error={error} refreshing={refreshing} refresh={refresh} />
      {error ? (
        <Alert variant="destructive">
          <HugeiconsIcon icon={Alert02Icon} strokeWidth={2} aria-hidden />
          <AlertTitle>The panel server does not answer</AlertTitle>
          <AlertDescription>
            {snapshot
              ? `Showing what it said at ${formatMoment(snapshot.taken_at)}. ${error}.`
              : `${error}. Check that the panel container runs: docker compose ps panel.`}
          </AlertDescription>
        </Alert>
      ) : null}
      {snapshot ? (
        <div className={cn("transition-opacity", error && "opacity-70")}>
          <Content snapshot={snapshot} />
        </div>
      ) : error ? null : (
        <Loading />
      )}
    </div>
  )
}

function Header({
  snapshot,
  error,
  refreshing,
  refresh,
}: {
  snapshot: Snapshot | null
  error: string | null
  refreshing: boolean
  refresh: () => void
}) {
  const overall = snapshot ? worstState(snapshot.checks.map((check) => check.state)) : null
  return (
    <header className="flex flex-wrap items-center gap-3">
      <IconBubble icon={Leaf01Icon} tint="mint" className="size-11 rounded-3xl" />
      <div className="min-w-0 flex-1">
        <h1 className="font-heading text-2xl font-extrabold tracking-tight">Healer</h1>
        <p className="truncate text-sm text-muted-foreground">
          {snapshot ? (
            <>
              node <span className="font-semibold text-foreground">{snapshot.node}</span> on{" "}
              <span className="font-mono text-xs">{snapshot.topic}</span>
            </>
          ) : (
            "Local panel"
          )}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {overall ? <StatusBadge state={error ? "fail" : overall} /> : null}
        {snapshot ? (
          <span className="hidden text-xs text-muted-foreground sm:inline" aria-live="polite">
            updated {timeAgo(snapshot.taken_at)}
          </span>
        ) : null}
        <Button variant="ghost" size="icon" onClick={refresh} aria-label="Refresh now">
          <HugeiconsIcon
            icon={RefreshIcon}
            strokeWidth={2}
            className={cn(refreshing && "animate-spin")}
            aria-hidden
          />
        </Button>
        <ThemeToggle />
      </div>
    </header>
  )
}

function Content({ snapshot }: { snapshot: Snapshot }) {
  const now = new Date(snapshot.taken_at)
  const problems = Object.entries(snapshot.problems)
  const fixes = snapshot.projects.reduce((sum, project) => sum + project.fixes.length, 0)
  const [tab, setTab] = useTab()
  return (
    <div className="flex flex-col gap-6">
      {problems.length ? (
        <Alert>
          <HugeiconsIcon icon={Alert02Icon} strokeWidth={2} aria-hidden />
          <AlertTitle>Some parts could not be read</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {problems.map(([section, problem]) => (
                <li key={section}>
                  <span className="font-semibold">{SECTION_NAMES[section] ?? section}</span>:{" "}
                  {problem}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      <Tabs value={tab} onValueChange={setTab} className="gap-6">
        <TabsList className="h-auto max-w-full flex-wrap justify-start">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <Trigger value="incidents" label="Incidents" count={snapshot.incidents.length} />
          <Trigger value="projects" label="Projects" count={fixes} />
          <Trigger value="team" label="Team" count={snapshot.team.length} />
          <Trigger value="outbox" label="Outbox" count={snapshot.outbox.length} />
        </TabsList>
        <TabsContent value="overview" className="flex flex-col gap-8">
          <div className="grid gap-8 xl:grid-cols-[3fr_2fr]">
            <Health checks={snapshot.checks} listener={snapshot.listener} now={now} />
            <Activity items={snapshot.activity} now={now} />
          </div>
          <Savings savings={snapshot.savings} />
        </TabsContent>
        <TabsContent value="incidents">
          <Incidents incidents={snapshot.incidents} now={now} />
        </TabsContent>
        <TabsContent value="projects">
          <Projects projects={snapshot.projects} now={now} />
        </TabsContent>
        <TabsContent value="team">
          <Team team={snapshot.team} sharing={snapshot.sharing} now={now} />
        </TabsContent>
        <TabsContent value="outbox">
          <Outbox outbox={snapshot.outbox} now={now} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function Trigger({ value, label, count }: { value: string; label: string; count: number }) {
  return (
    <TabsTrigger value={value}>
      {label}
      {count ? (
        <Badge variant="secondary" className="tabular-nums">
          {count}
        </Badge>
      ) : null}
    </TabsTrigger>
  )
}

function Loading() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading the panel">
      <Skeleton className="h-9 w-80 rounded-full" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-32 rounded-2xl" />
        ))}
      </div>
      <Skeleton className="h-72 rounded-2xl" />
    </div>
  )
}
