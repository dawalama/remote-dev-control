import { useState } from "react"
import { GET, POST, PUT } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { TerminalView, TerminalToolbar } from "@/features/terminal/terminal-view"
import { useMountEffect } from "@/hooks/use-mount-effect"

type SessionStatusValue = "pending" | "running" | "waiting" | "verifying" | "done" | "completed" | "failed" | "cancelled"

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
  goal: string
  acceptance_criteria: string[]
  control_owner: "agent" | "human" | "none"
  blocker: string | null
}

interface SessionEvidence { id: string; evidence_type: string; title: string; data: Record<string, unknown> }
interface SessionCriterion { criterion_index: number; criterion: string; status: "unevaluated" | "passed" | "failed" | "waived" }
interface SessionApproval { id: string; summary: string; risk_level: string; status: "pending" | "approved" | "rejected" }

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
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [evidence, setEvidence] = useState<SessionEvidence[]>([])
  const [criteria, setCriteria] = useState<SessionCriterion[]>([])
  const [verificationCommand, setVerificationCommand] = useState("")
  const [approvals, setApprovals] = useState<SessionApproval[]>([])
  const [guidance, setGuidance] = useState("")
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)

  useMountEffect(() => {
    loadSession()
    loadSupervision()
  })

  const loadSupervision = async () => {
    try {
      const [evidenceItems, criterionItems, approvalItems] = await Promise.all([
        GET<SessionEvidence[]>(`/sessions/${sessionId}/evidence`),
        GET<SessionCriterion[]>(`/sessions/${sessionId}/criteria`),
        GET<SessionApproval[]>(`/sessions/${sessionId}/approvals`),
      ])
      setEvidence(evidenceItems ?? [])
      setCriteria(criterionItems ?? [])
      setApprovals(approvalItems ?? [])
    } catch { /* Legacy sessions may not have supervision records. */ }
  }

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

  const cancel = async () => {
    try {
      await POST(`/sessions/${sessionId}/cancel`)
      setConfirmCancel(false)
      toast("Session cancelled", "success")
      loadSession()
    } catch { toast("Cancellation failed", "error") }
  }

  const captureGit = async () => {
    try { await POST(`/sessions/${sessionId}/evidence/git`); await loadSupervision(); toast("Git evidence captured", "success") }
    catch { toast("Git evidence failed", "error") }
  }

  const verify = async () => {
    try { await POST(`/sessions/${sessionId}/evidence/verification`, { command: verificationCommand.trim() || undefined }); await loadSupervision(); toast("Verification recorded", "success") }
    catch { toast("Verification failed", "error") }
  }

  const decideCriterion = async (criterion: SessionCriterion, status: "passed" | "failed" | "waived") => {
    try {
      await PUT(`/sessions/${sessionId}/criteria/${criterion.criterion_index}`, { status, evidence_ids: evidence.map((item) => item.id) })
      await loadSupervision()
    } catch { toast("Criterion update failed", "error") }
  }

  const decideApproval = async (approval: SessionApproval, decision: "approved" | "rejected") => {
    try { await POST(`/sessions/${sessionId}/approvals/${approval.id}/decide`, { decision }); await loadSupervision(); await loadSession() }
    catch { toast("Approval decision failed", "error") }
  }

  const takeControl = async () => {
    try { await POST(`/sessions/${sessionId}/take-control`); await loadSession() }
    catch { toast("Could not take control", "error") }
  }

  const returnControl = async () => {
    if (!guidance.trim()) return
    try { await POST(`/sessions/${sessionId}/return-control`, { guidance: guidance.trim() }); setGuidance(""); await loadSession() }
    catch { toast("Could not return control", "error") }
  }

  if (!session) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-gray-500 text-sm">Loading session...</p>
      </div>
    )
  }

  const isExecuting = ["pending", "running", "waiting"].includes(session.status)
  const isOpen = [...["pending", "running", "waiting"], "verifying"].includes(session.status)
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
        {session.status === "verifying" && (
          <button onClick={markDone} className="text-[10px] px-2 py-0.5 rounded bg-green-600/20 text-green-400 hover:bg-green-600/30 flex-shrink-0">
            Mark complete
          </button>
        )}
        {isExecuting && session.status !== "pending" && (
          <button onClick={() => setConfirmCancel(true)} className="text-[10px] px-2 py-0.5 rounded bg-red-600/20 text-red-400 hover:bg-red-600/30 flex-shrink-0">Cancel session</button>
        )}
        {["running", "waiting"].includes(session.status) && session.control_owner !== "human" && <button onClick={takeControl} className="rounded bg-yellow-600/20 px-2 py-0.5 text-[10px] text-yellow-300">Take control</button>}
        {(session.status === "failed" || session.status === "pending") && (
          <button onClick={retry} className="text-[10px] px-2 py-0.5 rounded bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 flex-shrink-0">
            {session.status === "pending" ? "Start" : "Retry"}
          </button>
        )}
        {!isOpen && (
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
      {confirmCancel && <div className="flex items-center justify-end gap-2 border-b border-red-900 bg-red-950/40 px-3 py-2 text-xs"><span className="mr-auto text-red-200">Permanently stop this session? It cannot currently be resumed.</span><button onClick={() => setConfirmCancel(false)} className="rounded border border-gray-600 px-2 py-1">Go back</button><button onClick={cancel} className="rounded bg-red-700 px-2 py-1 text-white">Cancel session</button></div>}
      {session.control_owner === "human" && <div className="flex items-center gap-2 border-b border-yellow-900 bg-yellow-950/30 px-3 py-2"><span className="text-[10px] text-yellow-200">You have control</span><input value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="Guidance before returning control" className="min-w-52 flex-1 rounded border border-yellow-800 bg-gray-950 px-2 py-1 text-[10px]" /><button disabled={!guidance.trim()} onClick={returnControl} className="rounded bg-green-700 px-2 py-1 text-[10px] text-white disabled:opacity-50">Return control</button></div>}
      {approvals.some((approval) => approval.status === "pending") && <div className="space-y-2 border-b border-yellow-900 bg-yellow-950/20 p-3"><div className="text-xs font-medium text-yellow-300">Needs your decision</div>{approvals.filter((approval) => approval.status === "pending").map((approval) => <div key={approval.id} className="flex items-center gap-2 rounded border border-yellow-800 p-2 text-xs"><span className="mr-auto text-gray-200">{approval.summary}</span><button onClick={() => decideApproval(approval, "approved")} className="rounded bg-green-700 px-2 py-1 text-[10px]">Approve</button><button onClick={() => decideApproval(approval, "rejected")} className="rounded bg-red-700 px-2 py-1 text-[10px]">Reject</button></div>)}</div>}
      {(criteria.length > 0 || session.status === "verifying") && <div className="space-y-2 border-b border-gray-700 bg-gray-900 p-3"><div className="flex flex-wrap items-center gap-2"><span className="mr-auto text-xs font-medium text-gray-300">Completion evidence</span><button onClick={captureGit} className="rounded border border-gray-600 px-2 py-1 text-[10px]">Capture Git</button><input value={verificationCommand} onChange={(event) => setVerificationCommand(event.target.value)} placeholder="Verification command or project default" className="min-w-52 flex-1 rounded border border-gray-600 bg-gray-950 px-2 py-1 text-[10px]" /><button onClick={verify} className="rounded border border-gray-600 px-2 py-1 text-[10px]">Run verification</button></div>{criteria.map((criterion) => <div key={criterion.criterion_index} className="flex flex-wrap items-center gap-2 rounded border border-gray-700 px-2 py-1.5 text-[10px]"><span className="mr-auto text-gray-300">{criterion.criterion}</span><span className="text-gray-500">{criterion.status}</span>{criterion.status === "unevaluated" && <><button onClick={() => decideCriterion(criterion, "passed")} className="text-green-400">Pass with evidence</button><button onClick={() => decideCriterion(criterion, "failed")} className="text-red-400">Fail</button><button onClick={() => decideCriterion(criterion, "waived")} className="text-gray-400">Waive</button></>}</div>)}</div>}

      {/* Main content: terminal (if active) or detail view */}
      {isExecuting && terminalId ? (
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
