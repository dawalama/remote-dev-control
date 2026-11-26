import { useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

export interface SemanticSessionEvent {
  id: string
  timestamp: string
  type: string
  data: Record<string, unknown>
}

interface PairedToolRow {
  kind: "tool"
  id: string
  timestamp: string
  tool: string
  summary: string
  provider?: string
  result?: {
    isError: boolean
    exitCode?: number | null
    output: string
  }
}

interface MessageRow {
  kind: "message"
  id: string
  timestamp: string
  text: string
  provider?: string
}

interface ThoughtRow {
  kind: "thought"
  id: string
  timestamp: string
  text: string
}

interface FileChangeRow {
  kind: "files"
  id: string
  timestamp: string
  changes: Array<{ path: string; kind: string }>
}

interface MetricsRow {
  kind: "metrics"
  id: string
  timestamp: string
  costUsd?: number
  durationMs?: number
  numTurns?: number
  inputTokens?: number
  outputTokens?: number
  isError?: boolean
}

interface LifecycleRow {
  kind: "lifecycle"
  id: string
  timestamp: string
  type: string
  detail: string
}

interface SteerRow {
  kind: "steer"
  id: string
  timestamp: string
  applied: boolean
  round?: number
  texts: string[]
}

interface LoopMilestoneRow {
  kind: "loop"
  id: string
  timestamp: string
  variant: "retry" | "verified" | "stall" | "budget" | "unverified" | "escalated"
  label: string
  detail?: string
}

type TimelineRow =
  | PairedToolRow
  | MessageRow
  | ThoughtRow
  | FileChangeRow
  | MetricsRow
  | SteerRow
  | LoopMilestoneRow
  | LifecycleRow

function buildTimelineRows(events: SemanticSessionEvent[]): TimelineRow[] {
  // Index tool_result events by tool_use_id so we can pair them into one card.
  const resultsById = new Map<string, SemanticSessionEvent>()
  for (const ev of events) {
    if (ev.type === "session.tool_result") {
      const tid = String(ev.data.tool_use_id || "")
      if (tid) resultsById.set(tid, ev)
    }
  }

  const consumedResultIds = new Set<string>()
  const rows: TimelineRow[] = []

  for (const ev of events) {
    if (ev.type === "session.agent_message") {
      const text = String(ev.data.text || "").trim()
      if (!text) continue
      rows.push({
        kind: "message",
        id: ev.id,
        timestamp: ev.timestamp,
        text,
        provider: ev.data.provider ? String(ev.data.provider) : undefined,
      })
      continue
    }

    if (ev.type === "session.agent_thought") {
      const text = String(ev.data.text || "").trim()
      if (!text) continue
      rows.push({
        kind: "thought",
        id: ev.id,
        timestamp: ev.timestamp,
        text,
      })
      continue
    }

    if (ev.type === "session.tool_use") {
      const tid = String(ev.data.tool_use_id || "")
      const matched = tid ? resultsById.get(tid) : undefined
      if (matched) consumedResultIds.add(matched.id)
      rows.push({
        kind: "tool",
        id: ev.id,
        timestamp: ev.timestamp,
        tool: String(ev.data.tool || "Tool"),
        summary: String(ev.data.summary || ""),
        provider: ev.data.provider ? String(ev.data.provider) : undefined,
        result: matched
          ? {
              isError: Boolean(matched.data.is_error),
              exitCode:
                typeof matched.data.exit_code === "number"
                  ? matched.data.exit_code
                  : null,
              output: String(matched.data.output || ""),
            }
          : undefined,
      })
      continue
    }

    if (ev.type === "session.tool_result") {
      if (consumedResultIds.has(ev.id)) continue
      rows.push({
        kind: "tool",
        id: ev.id,
        timestamp: ev.timestamp,
        tool: String(ev.data.tool || "Tool"),
        summary: String(ev.data.summary || ""),
        provider: ev.data.provider ? String(ev.data.provider) : undefined,
        result: {
          isError: Boolean(ev.data.is_error),
          exitCode:
            typeof ev.data.exit_code === "number" ? ev.data.exit_code : null,
          output: String(ev.data.output || ""),
        },
      })
      continue
    }

    if (ev.type === "session.file_change") {
      const raw = Array.isArray(ev.data.changes) ? ev.data.changes : []
      const changes = raw
        .filter((c): c is Record<string, unknown> => Boolean(c && typeof c === "object"))
        .map((c) => ({
          path: String(c.path || ""),
          kind: String(c.kind || "update"),
        }))
        .filter((c) => c.path)
      if (changes.length > 0) {
        rows.push({
          kind: "files",
          id: ev.id,
          timestamp: ev.timestamp,
          changes,
        })
      }
      continue
    }

    if (ev.type === "session.turn_metrics") {
      const usage =
        ev.data.usage && typeof ev.data.usage === "object"
          ? (ev.data.usage as Record<string, unknown>)
          : null
      rows.push({
        kind: "metrics",
        id: ev.id,
        timestamp: ev.timestamp,
        costUsd: typeof ev.data.cost_usd === "number" ? ev.data.cost_usd : undefined,
        durationMs: typeof ev.data.duration_ms === "number" ? ev.data.duration_ms : undefined,
        numTurns: typeof ev.data.num_turns === "number" ? ev.data.num_turns : undefined,
        inputTokens: typeof usage?.input_tokens === "number" ? usage.input_tokens : undefined,
        outputTokens: typeof usage?.output_tokens === "number" ? usage.output_tokens : undefined,
        isError: Boolean(ev.data.is_error),
      })
      continue
    }

    if (ev.type === "session.steer_queued") {
      const text = String(ev.data.text || "").trim()
      if (text) {
        rows.push({
          kind: "steer",
          id: ev.id,
          timestamp: ev.timestamp,
          applied: false,
          texts: [text],
        })
      }
      continue
    }

    if (ev.type === "session.steer_applied") {
      const rawSteers = Array.isArray(ev.data.steers)
        ? ev.data.steers.map((s) => String(s || "").trim()).filter(Boolean)
        : []
      rows.push({
        kind: "steer",
        id: ev.id,
        timestamp: ev.timestamp,
        applied: true,
        round: typeof ev.data.round === "number" ? ev.data.round : undefined,
        texts: rawSteers,
      })
      continue
    }

    if (ev.type === "session.loop_escalated") {
      rows.push({
        kind: "loop",
        id: ev.id,
        timestamp: ev.timestamp,
        variant: "escalated",
        label: `Escalated to Reasoning Tier · Round ${String(ev.data.round ?? "?")}`,
        detail: `${String(ev.data.from_provider || "coding")} → ${String(ev.data.to_provider || "reasoning")}`,
      })
      continue
    }

    if (ev.type === "session.loop_retry") {
      const fromR = ev.data.from_round
      const toR = ev.data.to_round
      const maxR = ev.data.max_rounds
      const exitCode = ev.data.exit_code
      rows.push({
        kind: "loop",
        id: ev.id,
        timestamp: ev.timestamp,
        variant: "retry",
        label: `Verify Loop · Round ${String(fromR ?? "?")} → ${String(toR ?? "?")}/${String(maxR ?? "?")}`,
        detail: exitCode !== undefined ? `exit ${String(exitCode)}` : undefined,
      })
      continue
    }

    if (ev.type === "session.loop_verified") {
      rows.push({
        kind: "loop",
        id: ev.id,
        timestamp: ev.timestamp,
        variant: "verified",
        label: `Verified on round ${String(ev.data.round ?? "?")}/${String(ev.data.max_rounds ?? "?")}`,
        detail: ev.data.command ? String(ev.data.command) : undefined,
      })
      continue
    }

    if (ev.type === "session.loop_stall" || ev.type === "session.loop_budget" || ev.type === "session.loop_unverified") {
      const variant =
        ev.type === "session.loop_stall"
          ? "stall"
          : ev.type === "session.loop_budget"
            ? "budget"
            : "unverified"
      const label =
        variant === "stall"
          ? `Loop stalled on round ${String(ev.data.round ?? "?")}/${String(ev.data.max_rounds ?? "?")}`
          : variant === "budget"
            ? `Loop budget reached (${String(ev.data.round ?? "?")}/${String(ev.data.max_rounds ?? "?")})`
            : "Verification skipped"
      rows.push({
        kind: "loop",
        id: ev.id,
        timestamp: ev.timestamp,
        variant,
        label,
        detail: ev.data.exit_code !== undefined ? `exit ${String(ev.data.exit_code)}` : undefined,
      })
      continue
    }

    const detailParts: string[] = []
    if (ev.data.tool) detailParts.push(String(ev.data.tool))
    if (ev.data.exit_code !== undefined) detailParts.push(`exit ${String(ev.data.exit_code)}`)
    if (ev.data.provider) detailParts.push(String(ev.data.provider))
    if (ev.data.terminal_id) detailParts.push(String(ev.data.terminal_id).slice(0, 12))
    rows.push({
      kind: "lifecycle",
      id: ev.id,
      timestamp: ev.timestamp,
      type: ev.type,
      detail: detailParts.join(" · "),
    })
  }

  return rows
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
  } catch {
    return ""
  }
}

export function SemanticTimeline({
  events,
  isRunning,
  currentStep,
  compact = false,
}: {
  events: SemanticSessionEvent[] | null
  isRunning: boolean
  currentStep?: string | null
  compact?: boolean
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const toggle = (id: string) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))

  if (events === null) {
    return <div className="text-xs text-gray-500">Loading timeline…</div>
  }

  const rows = buildTimelineRows(events)

  return (
    <div className={compact ? "space-y-2" : "space-y-2.5"}>
      {isRunning && currentStep && (
        <div className="flex items-center gap-2 rounded-lg border border-blue-500/30 bg-blue-950/30 px-3 py-2 text-xs text-blue-200">
          <span className="h-2 w-2 flex-shrink-0 rounded-full bg-blue-400 animate-pulse" />
          <span className="truncate font-medium">{currentStep}</span>
        </div>
      )}

      {rows.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-800 px-3 py-4 text-center text-xs text-gray-500">
          {isRunning
            ? "Waiting for agent events…"
            : "No events recorded for this session."}
        </div>
      )}

      {rows.map((row) => {
        if (row.kind === "message") {
          return (
            <div
              key={row.id}
              className="rounded-lg border border-gray-700/80 bg-gray-900/90 p-3 text-gray-200"
            >
              <div className="mb-1.5 flex items-center justify-between gap-2 text-[10px] text-gray-500">
                <span className="font-medium uppercase tracking-wide text-blue-300">
                  {row.provider || "Agent"}
                </span>
                <span className="font-mono">{formatTime(row.timestamp)}</span>
              </div>
              <div className="space-y-1.5 text-xs leading-relaxed text-gray-200 break-words">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    pre: ({ children }) => (
                      <pre className="overflow-x-auto rounded bg-gray-950 p-2.5 text-[11px]">
                        {children}
                      </pre>
                    ),
                    code: ({ children }) => (
                      <code className="rounded bg-gray-950 px-1 py-0.5 font-mono text-[11px] text-gray-300">
                        {children}
                      </code>
                    ),
                    ul: ({ children }) => (
                      <ul className="ml-4 list-disc space-y-0.5">{children}</ul>
                    ),
                    ol: ({ children }) => (
                      <ol className="ml-4 list-decimal space-y-0.5">{children}</ol>
                    ),
                  }}
                >
                  {row.text}
                </ReactMarkdown>
              </div>
            </div>
          )
        }

        if (row.kind === "thought") {
          const isOpen = Boolean(expanded[row.id])
          return (
            <div
              key={row.id}
              className="rounded-md border border-gray-800 bg-gray-950/60"
            >
              <button
                type="button"
                onClick={() => toggle(row.id)}
                className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-[11px] text-gray-400 hover:text-gray-200"
              >
                <span className="flex items-center gap-1.5 truncate">
                  <span className="text-gray-600">{isOpen ? "▾" : "▸"}</span>
                  <span className="italic">Reasoning</span>
                  {!isOpen && (
                    <span className="truncate text-gray-500">
                      — {row.text.slice(0, 70)}
                    </span>
                  )}
                </span>
                <span className="flex-shrink-0 font-mono text-[10px] text-gray-600">
                  {formatTime(row.timestamp)}
                </span>
              </button>
              {isOpen && (
                <div className="border-t border-gray-800/80 px-3 py-2 text-[11px] leading-relaxed text-gray-400 whitespace-pre-wrap break-words">
                  {row.text}
                </div>
              )}
            </div>
          )
        }

        if (row.kind === "tool") {
          const isOpen = Boolean(expanded[row.id])
          const hasOutput = Boolean(row.result?.output)
          const pending = !row.result && isRunning
          const isError = Boolean(row.result?.isError)
          return (
            <div
              key={row.id}
              className={`rounded-lg border ${
                isError
                  ? "border-red-900/70 bg-red-950/20"
                  : "border-gray-800 bg-gray-900/60"
              }`}
            >
              <button
                type="button"
                onClick={() => hasOutput && toggle(row.id)}
                className={`flex w-full items-center gap-2 px-2.5 py-2 text-left text-xs ${
                  hasOutput ? "cursor-pointer hover:bg-gray-800/40" : "cursor-default"
                }`}
              >
                <span
                  className={`h-2 w-2 flex-shrink-0 rounded-full ${
                    pending
                      ? "bg-blue-400 animate-pulse"
                      : isError
                        ? "bg-red-400"
                        : "bg-emerald-400"
                  }`}
                />
                <span className="flex-shrink-0 rounded bg-gray-800 px-1.5 py-0.5 font-mono text-[10px] font-medium text-gray-200">
                  {row.tool}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-300">
                  {row.summary || "Executed"}
                </span>
                {row.result?.exitCode !== null && row.result?.exitCode !== undefined && (
                  <span
                    className={`flex-shrink-0 font-mono text-[10px] ${
                      row.result.exitCode === 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    exit {row.result.exitCode}
                  </span>
                )}
                {hasOutput && (
                  <span className="flex-shrink-0 text-[10px] text-gray-500">
                    {isOpen ? "▾" : "▸"}
                  </span>
                )}
              </button>
              {isOpen && hasOutput && (
                <pre className="max-h-56 overflow-auto border-t border-gray-800 bg-gray-950/80 p-2.5 font-mono text-[10px] leading-relaxed text-gray-300 whitespace-pre-wrap break-words">
                  {row.result?.output}
                </pre>
              )}
            </div>
          )
        }

        if (row.kind === "files") {
          return (
            <div
              key={row.id}
              className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2"
            >
              <div className="mb-1 flex items-center justify-between text-[10px] text-gray-500">
                <span className="font-medium uppercase tracking-wide text-gray-400">
                  Files changed ({row.changes.length})
                </span>
                <span className="font-mono">{formatTime(row.timestamp)}</span>
              </div>
              <div className="space-y-1">
                {row.changes.map((c, idx) => (
                  <div
                    key={`${c.path}-${idx}`}
                    className="flex items-center gap-2 font-mono text-[11px]"
                  >
                    <span
                      className={`rounded px-1 text-[9px] uppercase ${
                        c.kind === "add"
                          ? "bg-emerald-950 text-emerald-300"
                          : c.kind === "delete"
                            ? "bg-red-950 text-red-300"
                            : "bg-blue-950 text-blue-300"
                      }`}
                    >
                      {c.kind}
                    </span>
                    <span className="truncate text-gray-300">{c.path}</span>
                  </div>
                ))}
              </div>
            </div>
          )
        }

        if (row.kind === "metrics") {
          const parts: string[] = []
          if (row.costUsd !== undefined) parts.push(`$${row.costUsd.toFixed(4)}`)
          if (row.durationMs !== undefined) parts.push(`${(row.durationMs / 1000).toFixed(1)}s`)
          if (row.numTurns !== undefined) parts.push(`${row.numTurns} turns`)
          if (row.inputTokens !== undefined || row.outputTokens !== undefined) {
            parts.push(
              `${row.inputTokens ?? 0} in / ${row.outputTokens ?? 0} out tok`,
            )
          }
          return (
            <div
              key={row.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-gray-900/50 px-2.5 py-1 text-[10px] text-gray-400"
            >
              <span className={row.isError ? "text-red-400" : "text-emerald-400"}>
                {row.isError ? "Turn ended with error" : "Turn complete"}
              </span>
              {parts.length > 0 && (
                <span className="font-mono text-gray-500">{parts.join(" · ")}</span>
              )}
            </div>
          )
        }

        if (row.kind === "steer") {
          return (
            <div
              key={row.id}
              className={`rounded-lg border px-3 py-2 text-xs ${
                row.applied
                  ? "border-indigo-700/60 bg-indigo-950/30 text-indigo-100"
                  : "border-purple-700/60 bg-purple-950/30 text-purple-100"
              }`}
            >
              <div className="mb-1 flex items-center justify-between gap-2 text-[10px]">
                <span className="font-semibold uppercase tracking-wide text-indigo-300">
                  {row.applied
                    ? `Operator Steer Applied${row.round ? ` · Round ${row.round}` : ""}`
                    : "Operator Steer Queued"}
                </span>
                <span className="font-mono text-gray-500">{formatTime(row.timestamp)}</span>
              </div>
              {row.texts.map((t, i) => (
                <p key={i} className="whitespace-pre-wrap break-words text-xs text-gray-100">
                  {t}
                </p>
              ))}
            </div>
          )
        }

        if (row.kind === "loop") {
          const tone =
            row.variant === "verified"
              ? "border-emerald-800/70 bg-emerald-950/30 text-emerald-200"
              : row.variant === "escalated"
                ? "border-purple-700/70 bg-purple-950/35 text-purple-200"
                : row.variant === "retry"
                  ? "border-blue-800/70 bg-blue-950/30 text-blue-200"
                  : "border-amber-800/70 bg-amber-950/30 text-amber-200"
          return (
            <div
              key={row.id}
              className={`flex flex-wrap items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-[11px] ${tone}`}
            >
              <span className="font-medium">{row.label}</span>
              <span className="flex items-center gap-2 font-mono text-[10px] opacity-80">
                {row.detail && <span>{row.detail}</span>}
                <span>{formatTime(row.timestamp)}</span>
              </span>
            </div>
          )
        }

        return (
          <div
            key={row.id}
            className="flex items-center gap-2 px-1 py-0.5 text-[10px]"
          >
            <span className="flex-shrink-0 font-mono text-gray-600">
              {formatTime(row.timestamp)}
            </span>
            <span
              className={
                row.type.includes("error") || row.type.includes("failed")
                  ? "text-red-400"
                  : row.type.includes("completed") || row.type.includes("done")
                    ? "text-green-400"
                    : row.type.includes("waiting")
                      ? "text-yellow-400"
                      : "text-gray-400"
              }
            >
              {row.type.replace("session.", "").replace(/_/g, " ")}
            </span>
            {row.detail && (
              <span className="truncate font-mono text-gray-600">{row.detail}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
