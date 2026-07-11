import { useState, useCallback, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { GET, POST } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { useProjectStore } from "@/stores/project-store"
import { useMountEffect } from "@/hooks/use-mount-effect"

const SESSION_STATUS = {
  PENDING: "pending",
  RUNNING: "running",
  WAITING: "waiting",
  VERIFYING: "verifying",
  DONE: "done",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
} as const

type SessionStatusValue = (typeof SESSION_STATUS)[keyof typeof SESSION_STATUS]

const ACTIVE_STATUSES: SessionStatusValue[] = [SESSION_STATUS.PENDING, SESSION_STATUS.RUNNING, SESSION_STATUS.WAITING, SESSION_STATUS.VERIFYING]
const TERMINAL_STATUSES: SessionStatusValue[] = [SESSION_STATUS.DONE, SESSION_STATUS.COMPLETED, SESSION_STATUS.FAILED, SESSION_STATUS.CANCELLED]

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
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const projects = useProjectStore((s) => s.projects)
  const currentProject = useProjectStore((s) => s.currentProject)
  const [draft, setDraft] = useState({ project: currentProject || "", goal: "", criteria: "", provider: "" })
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

  const createSession = async () => {
    if (!activeChannelId || !draft.project || !draft.goal.trim()) return
    setCreating(true)
    try {
      const created = await POST<SessionInfo>("/sessions", {
        channel_id: activeChannelId,
        project: draft.project,
        goal: draft.goal.trim(),
        acceptance_criteria: draft.criteria.split("\n").map((item) => item.trim()).filter(Boolean),
        provider: draft.provider || undefined,
      })
      await POST(`/sessions/${created.id}/retry`)
      setDraft((value) => ({ ...value, goal: "", criteria: "" }))
      setShowCreate(false)
      await load()
      setViewingSessionId(created.id)
    } finally { setCreating(false) }
  }

  // List view — clicking a session opens the full-width viewer
  const active = sessions.filter((s) => (ACTIVE_STATUSES as string[]).includes(s.status))
  const completed = sessions.filter((s) => (TERMINAL_STATUSES as string[]).includes(s.status))

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Sessions</h3>
        <div className="flex gap-2"><button onClick={() => setShowCreate((value) => !value)} className="rounded bg-blue-600 px-2 py-1 text-[10px] font-medium text-white">New session</button><button onClick={load} className="text-[10px] text-gray-500 hover:text-gray-300">Refresh</button></div>
      </div>

      {showCreate && <form onSubmit={(event) => { event.preventDefault(); void createSession() }} className="space-y-2 rounded-lg border border-gray-700 bg-gray-800 p-3"><select required value={draft.project} onChange={(event) => setDraft({ ...draft, project: event.target.value })} className="w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs"><option value="" disabled>Select registered project</option>{projects.map((project) => <option key={project.name} value={project.name}>{project.name} — {project.path}</option>)}</select><textarea required rows={3} value={draft.goal} onChange={(event) => setDraft({ ...draft, goal: event.target.value })} placeholder="What outcome should be true?" className="w-full resize-y rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs" /><textarea rows={3} value={draft.criteria} onChange={(event) => setDraft({ ...draft, criteria: event.target.value })} placeholder={'Acceptance criteria — one per line'} className="w-full resize-y rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs" /><select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value })} className="w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs"><option value="">Default provider</option><option value="claude">Claude</option><option value="cursor">Cursor</option><option value="gwd">Gemini</option><option value="diagnostic">Diagnostic (no writes)</option></select><div className="flex justify-end gap-2"><button type="button" onClick={() => setShowCreate(false)} className="px-2 py-1 text-[10px] text-gray-400">Cancel</button><button disabled={creating || !draft.project || !draft.goal.trim()} className="rounded bg-blue-600 px-2 py-1 text-[10px] text-white disabled:opacity-50">{creating ? "Starting…" : "Create and start"}</button></div></form>}

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
    : [SESSION_STATUS.DONE, SESSION_STATUS.COMPLETED].includes(s.status as "done" | "completed") ? "bg-green-500"
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
          [SESSION_STATUS.DONE, SESSION_STATUS.COMPLETED].includes(s.status as "done" | "completed") ? "text-green-400" : s.status === SESSION_STATUS.FAILED ? "text-red-400" : "text-gray-400"
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
