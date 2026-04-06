import { useState, useCallback, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { GET } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { useMountEffect } from "@/hooks/use-mount-effect"

const SESSION_STATUS = {
  PENDING: "pending",
  RUNNING: "running",
  WAITING: "waiting",
  DONE: "done",
  FAILED: "failed",
  CANCELLED: "cancelled",
} as const

type SessionStatusValue = (typeof SESSION_STATUS)[keyof typeof SESSION_STATUS]

const ACTIVE_STATUSES: SessionStatusValue[] = [SESSION_STATUS.PENDING, SESSION_STATUS.RUNNING, SESSION_STATUS.WAITING]
const TERMINAL_STATUSES: SessionStatusValue[] = [SESSION_STATUS.DONE, SESSION_STATUS.FAILED, SESSION_STATUS.CANCELLED]

interface SessionInfo {
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

export function SessionsCard({
  onOpenTerminal,
}: {
  onOpenTerminal?: (terminalId: string) => void
}) {
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [loading, setLoading] = useState(false)
  const setViewingSessionId = useUIStore((s) => s.setViewingSessionId)

  const load = useCallback(async () => {
    if (!activeChannelId) return
    setLoading(true)
    try {
      const data = await GET<SessionInfo[]>(`/channels/${activeChannelId}/sessions`)
      setSessions(data ?? [])
    } catch { /* ignore */ }
    setLoading(false)
  }, [activeChannelId])

  // Load on mount and when channel changes
  const prevChannelRef = useRef(activeChannelId)
  if (prevChannelRef.current !== activeChannelId) {
    prevChannelRef.current = activeChannelId
    load()
  }
  useMountEffect(() => { load() })

  // List view — clicking a session opens the full-width viewer
  const active = sessions.filter((s) => (ACTIVE_STATUSES as string[]).includes(s.status))
  const completed = sessions.filter((s) => (TERMINAL_STATUSES as string[]).includes(s.status))

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Sessions</h3>
        <button onClick={load} className="text-[10px] text-gray-500 hover:text-gray-300">Refresh</button>
      </div>

      {loading && sessions.length === 0 && (
        <p className="text-xs text-gray-500 text-center py-4">Loading...</p>
      )}

      {!loading && sessions.length === 0 && (
        <p className="text-xs text-gray-500 text-center py-4">
          No sessions yet. Ask the orchestrator to build something.
        </p>
      )}

      {active.length > 0 && (
        <div className="space-y-1.5">
          <span className="text-[10px] text-gray-500 uppercase">Active</span>
          {active.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              onSelect={() => setViewingSessionId(s.id)}
              onAction={() => {
                if (s.terminal_ids.length > 0) {
                  const tid = s.terminal_ids[0]
                  if (onOpenTerminal) onOpenTerminal(tid)
                  else useTerminalStore.getState().setActiveTerminalId(tid)
                }
              }}
            />
          ))}
        </div>
      )}

      {completed.length > 0 && (
        <div className="space-y-1.5">
          <span className="text-[10px] text-gray-500 uppercase">Recent</span>
          {completed.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              onSelect={() => setViewingSessionId(s.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Session list row (compact) ──

function SessionRow({
  session: s,
  onSelect,
  onAction,
}: {
  session: SessionInfo
  onSelect: () => void
  onAction?: () => void
}) {
  const isActive = (ACTIVE_STATUSES as string[]).includes(s.status)
  const statusColor = s.status === SESSION_STATUS.RUNNING ? "bg-blue-500 animate-pulse"
    : s.status === SESSION_STATUS.WAITING ? "bg-yellow-500 animate-pulse"
    : s.status === SESSION_STATUS.DONE ? "bg-green-500"
    : s.status === SESSION_STATUS.FAILED ? "bg-red-500"
    : "bg-gray-500"

  return (
    <button
      onClick={onSelect}
      className="w-full bg-gray-800 hover:bg-gray-750 rounded-lg px-3 py-2 text-left"
    >
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
        <span className="text-xs text-gray-200 flex-1 truncate">{s.description || "Untitled"}</span>
        <span className="text-[10px] text-gray-500">{formatAge(s.created_at)}</span>
      </div>
      <div className="flex items-center gap-2 mt-1 ml-4">
        <span className="text-[10px] text-gray-500">{s.project}</span>
        <div className="flex-1" />
        {isActive && onAction && s.terminal_ids.length > 0 && (
          <span
            onClick={(e) => { e.stopPropagation(); onAction() }}
            className="text-[10px] text-blue-400 hover:text-blue-300"
          >
            View Terminal
          </span>
        )}
        <span className={`text-[10px] ${
          s.status === SESSION_STATUS.DONE ? "text-green-400" : s.status === SESSION_STATUS.FAILED ? "text-red-400" : "text-gray-400"
        }`}>
          {s.status}
        </span>
      </div>
    </button>
  )
}



// ── Helpers ──

function formatAge(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "now"
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

