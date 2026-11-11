/**
 * The JSON that `healer panel` serves at /api/snapshot (`PanelSnapshot` in
 * src/healer/domain/panel.py). Times are ISO 8601 in UTC; days are UTC dates.
 */

export type CheckState = "ok" | "warning" | "fail" | "off"

export type Check = {
  key: string
  name: string
  state: CheckState
  detail: string
  since: string | null
}

export type ListenerEvent = {
  at: string
  kind: "connection" | "received" | "ignored" | "rejected" | "result"
  text: string
}

export type ListenerStatus = {
  node: string
  topic: string
  connection: string
  connected: boolean
  started_at: string
  changed_at: string
  updated_at: string
  received: number
  ignored: number
  rejected: number
  stopped: boolean
  recent: ListenerEvent[]
}

export type LedgerSummary = {
  key: string
  incidents: number
  fixed: number
  from_cache: number
  from_team: number
  model_attempts: number
  tokens_used: number
  tokens_saved: number
}

export type DailyTokens = {
  day: string
  incidents: number
  spent: number
  saved_by_cache: number
  saved_by_team: number
}

export type Resolution = "cache" | "team" | "model" | "none"

export type Savings = {
  total: LedgerSummary
  resolutions: Record<Resolution, number>
  days: DailyTokens[]
}

export type RepairStatus =
  "fixed" | "passing" | "undiagnosed" | "out_of_attempts" | "out_of_budget" | "stopped"

export type Incident = {
  recorded_at: string
  project: string
  signature: string
  error_type: string
  status: RepairStatus
  resolution: Resolution
  model_attempts: number
  tokens_used: number
  tokens_saved: number
}

export type DiffLine = {
  kind: "context" | "added" | "removed" | "gap"
  text: string
}

export type Edit = {
  path: string
  created: boolean
  lines: DiffLine[]
}

export type Fix = {
  signature: string
  error: string
  explanation: string
  model: string
  tokens_spent: number
  command: string
  exit_code_before: number | null
  verified_at: string
  edits: Edit[]
}

export type Project = {
  path: string
  symbols: number | null
  incidents: number
  fixed: number
  last_incident: string | null
  fixes: Fix[]
}

export type TeamFix = {
  /** One per fix; an error may have several. */
  fix_id: string
  /** Suggestions kept for the same error. */
  fixes_for_error: number
  /** Its place among them, in the order healer run tries them. */
  rank: number
  /** Nodes where it made the tests pass. */
  worked: number
  /** Nodes where it did not. */
  failed: number
  signature: string
  node: string
  error: string
  explanation: string
  model: string
  tokens_spent: number
  published_at: string
  received_at: string
  command: string
  exit_code_before: number | null
  edits: Edit[]
}

export type WaitingFix = {
  signature: string
  error: string
  topic: string
  for_current_topic: boolean
  published_at: string
  queued_at: string
  attempts: number
  last_error: string
  edits: Edit[]
}

export type ActivityItem = {
  at: string
  kind: "incident" | "suggestion" | "waiting" | "connection" | "ignored" | "rejected" | "result"
  state: CheckState
  title: string
  detail: string
}

export type Snapshot = {
  taken_at: string
  node: string
  topic: string
  sharing: boolean
  checks: Check[]
  listener: ListenerStatus | null
  savings: Savings
  incidents: Incident[]
  projects: Project[]
  team: TeamFix[]
  outbox: WaitingFix[]
  activity: ActivityItem[]
  problems: Record<string, string>
}
