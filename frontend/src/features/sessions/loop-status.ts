export type SessionLoopStatus =
  | "running"
  | "retrying"
  | "passed"
  | "stalled"
  | "budget_exhausted"
  | "unverified"
  | "completed"
  | "failed"

export interface SessionLoopState {
  round?: number
  max_rounds?: number
  status?: SessionLoopStatus
  exit_reason?: "success" | "stall" | "budget" | "runtime" | "verification_error" | "retry_error" | "no_check" | "agent_error" | null
  verification_command?: string | null
  last_exit_code?: number | null
  last_summary?: string | null
}

const BLOCKED_EXITS = new Set(["stall", "budget", "runtime", "verification_error", "retry_error"])
const BLOCKED_STATUSES = new Set(["stalled", "budget_exhausted", "unverified"])

export function loopBlocksAcceptance(loop?: { status?: string; exit_reason?: string | null } | null): boolean {
  if (!loop) return false
  if (loop.exit_reason && BLOCKED_EXITS.has(loop.exit_reason)) return true
  return Boolean(loop.status && BLOCKED_STATUSES.has(loop.status))
}

export function loopStatusLabel(status?: string): string | null {
  if (status === "stalled") return "Stalled"
  if (status === "budget_exhausted") return "Checks exhausted"
  if (status === "unverified") return "Checks not run"
  return null
}
