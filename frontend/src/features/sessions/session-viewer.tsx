import { useState } from "react"
import { GET, POST } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { TerminalView, TerminalToolbar } from "@/features/terminal/terminal-view"
import { useMountEffect } from "@/hooks/use-mount-effect"

const SESSION_STATUS = {
  PENDING: "pending", RUNNING: "running", WAITING: "waiting",
  DONE: "done", FAILED: "failed", CANCELLED: "cancelled",
} as const

type SessionStatusValue = (typeof SESSION_STATUS)[keyof typeof SESSION_STATUS]

interface SessionData {
  id: string
  channel_id: string
  project: string
  terminal_ids: string[]
  description: string
  status: SessionStatusValue
  agent_provider: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  output_summary: string | null
}

interface SessionEvent {
  id: string
  timestamp: string
  type: string
  data: Record<string, unknown>
}

/**
 * Full-width session viewer — replaces the terminal area in the main workspace.
 * Shows terminal (if active) + session details (summary, timeline, log).
 */
export function SessionViewer({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) {
  const [session, setSession] = useState<SessionData | null>(null)
  const [tab, setTab] = useState<"summary" | "timeline" | "log">("summary")
  const [events, setEvents] = useState<SessionEvent[] | null>(null)
  const [log, setLog] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)

  useMountEffect(() => {
    loadSession()
  })

  const loadSession = async () => {
    try {
      const data = await GET<SessionData>(`/sessions/${sessionId}`)
      setSession(data ?? null)
    } catch { /* ignore */ }
  }

  const loadEvents = async () => {
    try {
      const data = await GET<SessionEvent[]>(`/sessions/${sessionId}/events?limit=100`)
      setEvents(data ?? [])
    } catch { setEvents([]) }
  }

  const loadLog = async () => {
    try {
      const data = await GET<{ log: string | null }>(`/sessions/${sessionId}/log?tail=500`)
      setLog(data?.log || "No log available.")
    } catch { setLog("Failed to load log.") }
  }

  const markDone = async () => {
    try {
      await POST(`/sessions/${sessionId}/complete`)
      toast("Session marked done", "success")
      loadSession()
    } catch { toast("Failed", "error") }
  }

  const retry = async () => {
    try {
      await POST(`/sessions/${sessionId}/retry`)
      toast("Session retried", "success")
      loadSession()
    } catch { toast("Retry failed", "error") }
  }

  if (!session) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-gray-500 text-sm">Loading session...</p>
      </div>
    )
  }

  const isActive = ["pending", "running", "waiting"].includes(session.status)
  const terminalId = session.terminal_ids[0] || null
  const shortDesc = session.description.split(/[.\n]/)[0].slice(0, 120) || "Untitled"

  const statusColor = session.status === "running" ? "bg-blue-500 animate-pulse"
    : session.status === "waiting" ? "bg-yellow-500 animate-pulse"
    : session.status === "done" ? "bg-green-500"
    : session.status === "failed" ? "bg-red-500"
    : "bg-gray-500"

  const duration = session.completed_at
    ? formatDuration(new Date(session.created_at), new Date(session.completed_at))
    : formatAge(session.created_at)

  return (
    <div className="flex-1 flex flex-col min-h-0 rounded-lg overflow-hidden">
      {/* Session header bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 border-b border-gray-700 flex-shrink-0">
        <button onClick={onClose} className="text-xs text-blue-400 hover:text-blue-300 flex-shrink-0">← Back</button>
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
        <span className="text-xs text-gray-200 truncate flex-1" title={session.description}>{shortDesc}</span>
        <span className="text-[10px] text-gray-500 flex-shrink-0">{session.project}</span>
        {session.agent_provider && <span className="text-[10px] text-gray-600 flex-shrink-0">{session.agent_provider}</span>}
        <span className="text-[10px] text-gray-600 flex-shrink-0">{duration}</span>
        {isActive && (
          <button onClick={markDone} className="text-[10px] px-2 py-0.5 rounded bg-green-600/20 text-green-400 hover:bg-green-600/30 flex-shrink-0">
            Mark Done
          </button>
        )}
        {(session.status === "failed" || session.status === "pending") && (
          <button onClick={retry} className="text-[10px] px-2 py-0.5 rounded bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 flex-shrink-0">
            Retry
          </button>
        )}
        {!isActive && (
          <button onClick={async () => {
            try {
              const { DELETE: DEL } = await import("@/lib/api")
              await DEL(`/sessions/${sessionId}`)
              toast("Session deleted", "success")
              onClose()
            } catch { toast("Delete failed", "error") }
          }} className="text-[10px] px-2 py-0.5 rounded bg-red-600/20 text-red-400 hover:bg-red-600/30 flex-shrink-0">
            Delete
          </button>
        )}
      </div>

      {/* Main content: terminal (if active) or detail view */}
      {isActive && terminalId ? (
        <>
          {/* Terminal takes most of the space */}
          <div className="flex-1 min-h-0 flex flex-col">
            <TerminalToolbar
              project={session.project}
              sessionId={terminalId}
              connected={connected}
              onRestart={() => {}}
              onDisconnect={() => {}}
              onKill={() => {}}
              onReset={() => {}}
              mode="embedded"
              onModeChange={() => {}}
            />
            <div className="flex-1 min-h-0">
              <TerminalView
                key={terminalId}
                sessionId={terminalId}
                project={session.project}
                fontSize={layout === "kiosk" ? 15 : 13}
                onDisconnect={() => setConnected(false)}
                onSendReady={() => setConnected(true)}
                onRedrawReady={() => {}}
              />
            </div>
          </div>

          {/* Compact detail strip below terminal */}
          <div className="flex-shrink-0 border-t border-gray-700 bg-gray-800">
            <SessionTabs tab={tab} setTab={setTab} events={events} log={log} session={session}
              loadEvents={loadEvents} loadLog={loadLog} compact />
          </div>
        </>
      ) : (
        /* Done/failed: full detail view */
        <SessionTabs tab={tab} setTab={setTab} events={events} log={log} session={session}
          loadEvents={loadEvents} loadLog={loadLog} />
      )}
    </div>
  )
}

// ── Tabbed content (used both as compact strip and full view) ──

function SessionTabs({
  tab, setTab, events, log, session, loadEvents, loadLog, compact,
}: {
  tab: "summary" | "timeline" | "log"
  setTab: (t: "summary" | "timeline" | "log") => void
  events: SessionEvent[] | null
  log: string | null
  session: SessionData
  loadEvents: () => void
  loadLog: () => void
  compact?: boolean
}) {
  return (
    <div className={compact ? "" : "flex-1 flex flex-col min-h-0"}>
      {/* Tab bar */}
      <div className="flex gap-0.5 px-3 pt-1 flex-shrink-0">
        {(["summary", "timeline", "log"] as const).map((t) => (
          <button
            key={t}
            onClick={() => {
              setTab(t)
              if (t === "timeline" && events === null) loadEvents()
              if (t === "log" && log === null) loadLog()
            }}
            className={`px-2.5 py-1 text-[10px] border-b-2 ${
              tab === t ? "border-blue-500 text-white" : "border-transparent text-gray-500 hover:text-gray-300"
            }`}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className={`overflow-y-auto px-3 py-2 ${compact ? "max-h-32" : "flex-1 min-h-0"}`}>
        {tab === "summary" && (
          <div className="text-xs text-gray-400 whitespace-pre-wrap">
            {session.output_summary || (["running", "waiting", "pending"].includes(session.status) ? "Session in progress..." : "No summary available.")}
          </div>
        )}

        {tab === "timeline" && (
          <div className="space-y-0.5">
            {events === null && <div className="text-[10px] text-gray-500">Loading...</div>}
            {events?.length === 0 && <div className="text-[10px] text-gray-600">No events recorded</div>}
            {events?.map((e) => (
              <div key={e.id} className="flex items-start gap-2 text-[10px]">
                <span className="text-gray-600 flex-shrink-0 font-mono">
                  {new Date(e.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
                <span className={`${
                  e.type.includes("error") || e.type.includes("failed") ? "text-red-400"
                  : e.type.includes("completed") || e.type.includes("done") ? "text-green-400"
                  : e.type.includes("waiting") ? "text-yellow-400"
                  : "text-gray-400"
                }`}>
                  {e.type.replace("session.", "").replace(/_/g, " ")}
                </span>
                {e.data.tool ? <span className="text-gray-500">{String(e.data.tool)}</span> : null}
                {e.data.terminal_id ? <span className="text-gray-600 font-mono">{String(e.data.terminal_id).slice(0, 12)}</span> : null}
              </div>
            ))}
          </div>
        )}

        {tab === "log" && (
          <pre className="text-[10px] text-gray-400 whitespace-pre-wrap font-mono bg-gray-950 rounded p-3">
            {log === null ? "Loading..." : log}
          </pre>
        )}
      </div>
    </div>
  )
}

// ── Helpers ──

function formatAge(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

function formatDuration(start: Date, end: Date): string {
  const diff = end.getTime() - start.getTime()
  const secs = Math.floor(diff / 1000)
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ${secs % 60}s`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}
