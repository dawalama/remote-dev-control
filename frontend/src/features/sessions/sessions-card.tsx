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
  scope = "workstream",
}: {
  onOpenTerminal?: (terminalId: string) => void
  scope?: "workstream" | "all"
}) {
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const channels = useChannelStore((s) => s.channels)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [showOptions, setShowOptions] = useState(false)
  const [creating, setCreating] = useState(false)
  const projects = useProjectStore((s) => s.projects)
  const [draft, setDraft] = useState({ project: "", goal: "", criteria: "", provider: "orchestrated" })
  const openSessionFromHub = useUIStore((s) => s.openSessionFromHub)
  const toast = useUIStore((s) => s.toast)
  const activeChannel = channels.find((channel) => channel.id === activeChannelId)
  const workstreamProjects = activeChannel?.project_names ?? []
  const selectedProject = draft.project || workstreamProjects[0] || ""

  const openSession = (sessionId: string) => {
    openSessionFromHub(sessionId)
  }

  const load = useCallback(async () => {
    if (scope === "workstream" && !activeChannelId) return
    setLoading(true)
    try {
      const data = await GET<SessionInfo[]>(scope === "all" ? "/sessions" : `/channels/${activeChannelId}/sessions`)
      setSessions(data ?? [])
    } catch { /* ignore */ }
    setLoading(false)
  }, [activeChannelId, scope])
  const loadRef = useRef(load)
  loadRef.current = load

  // Load on mount and when channel changes
  const prevChannelRef = useRef(activeChannelId)
  if (prevChannelRef.current !== activeChannelId) {
    prevChannelRef.current = activeChannelId
    load()
  }
  useMountEffect(() => { load() })
  useMountEffect(() => {
    const interval = window.setInterval(() => { void loadRef.current() }, 5000)
    return () => window.clearInterval(interval)
  })

  const createSession = async () => {
    if (!activeChannelId || !selectedProject || !draft.goal.trim()) return
    setCreating(true)
    try {
      const created = await POST<SessionInfo>("/sessions", {
        channel_id: activeChannelId,
        project: selectedProject,
        goal: draft.goal.trim(),
        acceptance_criteria: draft.criteria.split("\n").map((item) => item.trim()).filter(Boolean),
        provider: draft.provider,
      })
      await POST(`/sessions/${created.id}/retry`)
      setDraft((value) => ({ ...value, goal: "", criteria: "" }))
      setShowCreate(false)
      setShowOptions(false)
      await load()
      openSession(created.id)
    } catch {
      toast("Could not start the session", "error")
    } finally { setCreating(false) }
  }

  // List view — clicking a session opens the full-width viewer
  const needsAttention = sessions.filter((s) => s.status === SESSION_STATUS.WAITING)
  const active = sessions.filter((s) => (ACTIVE_STATUSES as string[]).includes(s.status) && s.status !== SESSION_STATUS.WAITING)
  const completed = sessions.filter((s) => (TERMINAL_STATUSES as string[]).includes(s.status))

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{scope === "all" ? "All sessions" : "Workstream sessions"}</h3>
        {scope === "workstream" && <button onClick={() => setShowCreate((value) => !value)} className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white">New session</button>}
      </div>

      {showCreate && <form onSubmit={(event) => { event.preventDefault(); void createSession() }} className="space-y-3 rounded-lg border border-blue-500/30 bg-gray-800 p-4"><label className="block"><span className="mb-1 block text-xs font-medium text-gray-300">What should be accomplished?</span><textarea autoFocus required rows={4} value={draft.goal} onChange={(event) => setDraft({ ...draft, goal: event.target.value })} placeholder="Describe the outcome you want…" className="w-full resize-y rounded border border-gray-600 bg-gray-900 px-3 py-2 text-sm" /></label><button type="button" onClick={() => setShowOptions((value) => !value)} className="text-xs text-gray-400 hover:text-gray-200">{showOptions ? "Hide options" : "Options"}</button>{showOptions && <div className="space-y-2 border-t border-gray-700 pt-3">{(workstreamProjects.length !== 1 || !selectedProject) && <select required value={selectedProject} onChange={(event) => setDraft({ ...draft, project: event.target.value })} className="w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs"><option value="" disabled>Select project</option>{projects.map((project) => <option key={project.name} value={project.name}>{project.name}</option>)}</select>}<textarea rows={3} value={draft.criteria} onChange={(event) => setDraft({ ...draft, criteria: event.target.value })} placeholder="Optional acceptance criteria — one per line" className="w-full resize-y rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs" /><select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value })} className="w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs"><option value="orchestrated">Orchestrated — use configured preference</option><option value="codex">Codex CLI</option><option value="claude">Claude Code</option><option value="cursor">Cursor Agent</option><option value="gemini">Gemini CLI</option><option value="diagnostic">Diagnostic (no writes)</option></select><p className="text-[10px] text-gray-500">RDC will stop and guide setup if the selected provider is unavailable. It will not silently fall back.</p></div>}<div className="flex items-center justify-between gap-2"><span className="truncate text-[10px] text-gray-500">{selectedProject ? `Using ${selectedProject}` : "Select a project in Options"}</span><div className="flex gap-2"><button type="button" onClick={() => { setShowCreate(false); setShowOptions(false) }} className="px-3 py-1.5 text-xs text-gray-400">Cancel</button><button disabled={creating || !selectedProject || !draft.goal.trim()} className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">{creating ? "Starting…" : "Start session"}</button></div></div></form>}

      {loading && sessions.length === 0 && (
        <p className="text-xs text-gray-500 text-center py-4">Loading...</p>
      )}

      {!loading && sessions.length === 0 && (
        <p className="text-xs text-gray-500 text-center py-4">
          No sessions yet. Start with an outcome you want accomplished.
        </p>
      )}

      {needsAttention.length > 0 && (
        <div className="space-y-1.5 rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-2">
          <span className="text-[10px] font-medium uppercase text-yellow-400">Needs attention</span>
          {needsAttention.map((s) => <SessionRow key={s.id} session={s} context={scope === "all" ? channels.find((channel) => channel.id === s.channel_id)?.name : undefined} onSelect={() => openSession(s.id)} />)}
        </div>
      )}

      {active.length > 0 && (
        <div className="space-y-1.5">
          <span className="text-[10px] text-gray-500 uppercase">In progress</span>
          {active.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              context={scope === "all" ? channels.find((channel) => channel.id === s.channel_id)?.name : undefined}
              onSelect={() => openSession(s.id)}
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
              context={scope === "all" ? channels.find((channel) => channel.id === s.channel_id)?.name : undefined}
              onSelect={() => openSession(s.id)}
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
  context,
}: {
  session: SessionInfo
  onSelect: () => void
  onAction?: () => void
  context?: string
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
        <span className="text-[10px] text-gray-500">{context ? `${context} · ${s.project}` : s.project}</span>
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
          {statusLabel(s.status)}
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

function statusLabel(status: SessionStatusValue): string {
  return ({ pending: "Queued", running: "Working", waiting: "Needs you", verifying: "Ready for review", done: "Completed", completed: "Completed", failed: "Failed", cancelled: "Cancelled" })[status]
}
