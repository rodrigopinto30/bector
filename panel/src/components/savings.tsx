"use client"

import { Coins01Icon, Table01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import { useState } from "react"
import { Bar, BarChart, CartesianGrid, Rectangle, XAxis, YAxis } from "recharts"
import type { BarShapeProps } from "recharts"

import { SectionTitle } from "@/components/section"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  axisFormat,
  axisTicks,
  busiestDay,
  dayRows,
  LEVEL_COLORS,
  resolutionParts,
  type DayRow,
} from "@/lib/charts"
import { formatCount, formatFull, percent } from "@/lib/format"
import type { Savings as SavingsData } from "@/lib/types"

const STACK = ["cache", "team", "model"] as const

const chartConfig = {
  cache: { label: "Saved by the local cache", color: LEVEL_COLORS.cache },
  team: { label: "Saved by the team", color: LEVEL_COLORS.team },
  model: { label: "Spent on Claude", color: LEVEL_COLORS.model },
} satisfies ChartConfig

export function Savings({ savings }: { savings: SavingsData }) {
  const { total } = savings
  const wouldHaveCost = total.tokens_used + total.tokens_saved
  return (
    <section aria-labelledby="savings" className="flex flex-col gap-4">
      <SectionTitle
        icon={Coins01Icon}
        tint="butter"
        title="Savings"
        description="Every incident is solved by the cheapest source first: the local cache, then the team, and Claude last."
      />
      <div className="grid gap-3 lg:grid-cols-[1.3fr_2fr]">
        <Card className="justify-between bg-linear-to-br from-tint-butter via-card to-tint-mint">
          <CardHeader>
            <CardDescription>Tokens saved by reusing fixes</CardDescription>
            <p className="font-heading text-5xl font-extrabold tracking-tight">
              {formatCount(total.tokens_saved)}
            </p>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {total.tokens_saved > 0
              ? `${percent(total.tokens_saved, wouldHaveCost)}% of the ${formatCount(wouldHaveCost)} tokens these incidents would have cost.`
              : "Nothing reused yet: a fix found once is free the next time the same error appears."}
          </CardContent>
        </Card>
        <div className="grid grid-cols-2 gap-3">
          <Tile label="Incidents" value={total.incidents} />
          <Tile label="Fixed" value={total.fixed} />
          <Tile label="Calls to Claude" value={total.model_attempts} />
          <Tile label="Tokens spent" value={total.tokens_used} />
        </div>
      </div>
      <div className="grid gap-3 xl:grid-cols-[2fr_1fr]">
        <TokensChart savings={savings} />
        <Resolutions savings={savings} />
      </div>
    </section>
  )
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <Card size="sm">
      <CardContent className="flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className="font-heading text-2xl font-bold" title={formatFull(value)}>
          {formatCount(value)}
        </p>
      </CardContent>
    </Card>
  )
}

function TokensChart({ savings }: { savings: SavingsData }) {
  const rows = dayRows(savings.days)
  const busiest = busiestDay(rows)
  const empty = busiest === null
  const ticks = axisTicks(busiest?.total ?? 0)
  const tickLabel = axisFormat(ticks)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Tokens per day</CardTitle>
        <CardDescription>
          What each day&apos;s incidents would have cost: the part Claude was paid for and the part
          reuse avoided. Last {rows.length} days, in UTC.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {empty ? (
          <p className="rounded-2xl bg-muted/60 p-6 text-center text-sm text-muted-foreground">
            No incidents in these days. healer run records each one.
          </p>
        ) : (
          <>
            <Legend items={STACK.map((key) => ({ key, label: String(chartConfig[key].label) }))} />
            <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
              <BarChart data={rows} margin={{ top: 22, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} strokeWidth={1} />
                <XAxis
                  dataKey="label"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                  minTickGap={24}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  width={44}
                  ticks={ticks}
                  domain={[0, ticks.at(-1) ?? 0]}
                  tickFormatter={(value: number) => tickLabel(value)}
                />
                <ChartTooltip
                  cursor={{ fill: "var(--muted)", opacity: 0.6 }}
                  content={<ChartTooltipContent indicator="line" />}
                />
                {STACK.map((key) => (
                  <Bar
                    key={key}
                    dataKey={key}
                    stackId="tokens"
                    fill={`var(--color-${key})`}
                    maxBarSize={24}
                    isAnimationActive={false}
                    shape={(props: BarShapeProps) => (
                      <Segment {...props} level={key} busiest={busiest} />
                    )}
                  />
                ))}
              </BarChart>
            </ChartContainer>
          </>
        )}
        <NumbersTable title="Show the numbers per day">
          <DaysTable rows={rows.filter((row) => row.incidents > 0)} />
        </NumbersTable>
      </CardContent>
    </Card>
  )
}

/**
 * One part of a day's column. Only the top visible part gets rounded corners, and a 2px
 * gap in the card color separates the parts. The top part of the busiest day carries the
 * day's total.
 */
function Segment({
  level,
  busiest,
  ...props
}: BarShapeProps & { level: (typeof STACK)[number]; busiest: DayRow | null }) {
  const row = props.payload as DayRow
  const above = STACK.slice(STACK.indexOf(level) + 1).some((key) => row[key] > 0)
  const isTop = level === "model"
  return (
    <g>
      {props.height > 0 ? (
        <Rectangle
          x={props.x}
          y={props.y}
          width={props.width}
          height={props.height}
          fill={props.fill}
          radius={above ? 0 : [4, 4, 0, 0]}
          stroke="var(--card)"
          strokeWidth={2}
        />
      ) : null}
      {isTop && busiest !== null && row.day === busiest.day ? (
        <text
          x={props.x + props.width / 2}
          y={props.y - 7}
          textAnchor="middle"
          className="fill-foreground text-[11px] font-semibold"
        >
          {formatCount(row.total)}
        </text>
      ) : null}
    </g>
  )
}

function Resolutions({ savings }: { savings: SavingsData }) {
  const parts = resolutionParts(savings.resolutions)
  return (
    <Card>
      <CardHeader>
        <CardTitle>How incidents were solved</CardTitle>
        <CardDescription>
          {savings.total.incidents > 0
            ? `${formatFull(savings.total.incidents)} incidents, by the source that fixed them.`
            : "Each incident will count here once healer run records it."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {parts.length ? (
          <>
            <div
              className="flex h-7 w-full gap-0.5"
              role="img"
              aria-label={parts.map((p) => `${p.label}: ${p.value} (${p.share}%)`).join(", ")}
            >
              {parts.map((part) => (
                <Tooltip key={part.key}>
                  <TooltipTrigger asChild>
                    <div
                      tabIndex={0}
                      className="flex h-full min-w-1 items-center rounded-[4px] px-2 text-xs font-bold text-chart-ink outline-none transition-[filter] hover:brightness-105 focus-visible:ring-2 focus-visible:ring-ring"
                      style={{ flexGrow: part.value, backgroundColor: LEVEL_COLORS[part.key] }}
                    >
                      {part.share >= 10 ? `${part.share}%` : null}
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span className="font-bold">{formatFull(part.value)}</span> {part.label} (
                    {part.share}%)
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
            <Legend items={parts.map((part) => ({ key: part.key, label: part.label }))} />
          </>
        ) : (
          <p className="rounded-2xl bg-muted/60 p-6 text-center text-sm text-muted-foreground">
            No incidents yet.
          </p>
        )}
        <NumbersTable title="Show the numbers">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Solved by</TableHead>
                <TableHead className="text-right">Incidents</TableHead>
                <TableHead className="text-right">Share</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {resolutionParts(savings.resolutions).map((part) => (
                <TableRow key={part.key}>
                  <TableCell>{part.label}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatFull(part.value)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{part.share}%</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </NumbersTable>
      </CardContent>
    </Card>
  )
}

/** Names only, in the order of the marks; the swatch is a small square, like the bars. */
function Legend({ items }: { items: { key: keyof typeof LEVEL_COLORS; label: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
      {items.map((item) => (
        <li key={item.key} className="flex items-center gap-2">
          <span
            className="size-2.5 rounded-[3px]"
            style={{ backgroundColor: LEVEL_COLORS[item.key] }}
            aria-hidden
          />
          {item.label}
        </li>
      ))}
    </ul>
  )
}

function NumbersTable({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
          <HugeiconsIcon icon={Table01Icon} strokeWidth={2} aria-hidden />
          {open ? "Hide the numbers" : title}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">{children}</CollapsibleContent>
    </Collapsible>
  )
}

function DaysTable({ rows }: { rows: DayRow[] }) {
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No incidents in these days.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Day (UTC)</TableHead>
          <TableHead className="text-right">Incidents</TableHead>
          <TableHead className="text-right">Saved by cache</TableHead>
          <TableHead className="text-right">Saved by team</TableHead>
          <TableHead className="text-right">Spent on Claude</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.day}>
            <TableCell>{row.label}</TableCell>
            <TableCell className="text-right tabular-nums">{formatFull(row.incidents)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatFull(row.cache)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatFull(row.team)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatFull(row.model)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
