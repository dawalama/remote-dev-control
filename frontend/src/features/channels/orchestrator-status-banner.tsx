import { useState } from "react"
import { useMountEffect } from "@/hooks/use-mount-effect"
import type { OrchestratorRunStatus } from "@/stores/channel-store"

function formatElapsed(ms: number): string {
  const secs = Math.max(0, Math.floor(ms / 1000))
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  return `${mins}m ${secs % 60}s`
}

/**
 * Live orchestrator activity indicator inside chat threads.
 * Mount with `key={runStatus.startedAt}` so the elapsed timer resets cleanly per request.
 */
export function OrchestratorStatusBanner({
  runStatus,
  onRetry,
  onDismiss,
}: {
  runStatus: OrchestratorRunStatus
  onRetry?: () => void
  onDismiss?: () => void
}) {
  const [now, setNow] = useState(() => Date.now())

  useMountEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  })

  const elapsed = formatElapsed(now - runStatus.startedAt)
  const quietMs = now - runStatus.lastHeartbeatAt

  if (runStatus.status === "sending" || runStatus.status === "working") {
    const slowHint = quietMs > 12_000 ? " · waiting on model" : ""
    return (
      <div className="flex items-center gap-2 rounded-lg border border-blue-500/30 bg-blue-950/30 px-2.5 py-1.5 text-xs text-blue-200">
        <span className="relative flex h-2 w-2 flex-shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-400" />
        </span>
        <span className="font-medium truncate">{runStatus.stage || "Thinking…"}</span>
        <span className="ml-auto flex-shrink-0 font-mono text-[10px] text-blue-300/80">
          {elapsed}{slowHint}
        </span>
      </div>
    )
  }

  const isFailed = runStatus.status === "failed"
  return (
    <div
      className={`rounded-lg border px-2.5 py-2 text-xs space-y-1.5 ${
        isFailed
          ? "border-red-500/40 bg-red-950/30 text-red-200"
          : "border-amber-500/40 bg-amber-950/30 text-amber-200"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="font-semibold">
          {isFailed ? "⚠ Orchestrator request failed" : `⏱ ${runStatus.stage} (${elapsed})`}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {onRetry && runStatus.prompt && (
            <button
              type="button"
              onClick={onRetry}
              className={`rounded px-2 py-0.5 text-[10px] font-medium text-white transition-colors ${
                isFailed ? "bg-red-600 hover:bg-red-500" : "bg-amber-600 hover:bg-amber-500"
              }`}
            >
              ↻ Retry
            </button>
          )}
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="rounded px-1.5 py-0.5 text-[10px] text-gray-300 hover:text-white"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
      {runStatus.error && (
        <p className="text-[11px] opacity-85 leading-snug">{runStatus.error}</p>
      )}
    </div>
  )
}
