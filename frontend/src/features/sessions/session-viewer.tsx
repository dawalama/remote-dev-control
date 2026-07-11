import { useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
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
  review_mode: "advisory" | "implementation"
  parent_session_id: string | null
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
  const [showMoreActions, setShowMoreActions] = useState(false)
  const [terminalGeneration, setTerminalGeneration] = useState(0)
  const [retryProvider, setRetryProvider] = useState("orchestrated")
  const [showContinue, setShowContinue] = useState(false)
  const [continuationGoal, setContinuationGoal] = useState("Implement the approved outcome from this analysis. Make the changes, run relevant verification, and summarize the delivered result.")
  const [continuing, setContinuing] = useState(false)
  const [continuation, setContinuation] = useState<SessionData | null>(null)
  const redrawRef = useRef<() => void>(() => {})
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)

  useMountEffect(() => {
    loadSession()
    loadSupervision()
  })
  useMountEffect(() => {
    const interval = window.setInterval(() => {
      void loadSession()
      void loadSupervision()
      void loadEvents()
      void loadLog()
    }, 5000)
    return () => window.clearInterval(interval)
  })

  const loadSupervision = async () => {
    try {
      const [evidenceItems, criterionItems, approvalItems, continuationItem] = await Promise.all([
        GET<SessionEvidence[]>(`/sessions/${sessionId}/evidence`),
        GET<SessionCriterion[]>(`/sessions/${sessionId}/criteria`),
        GET<SessionApproval[]>(`/sessions/${sessionId}/approvals`),
        GET<SessionData | null>(`/sessions/${sessionId}/continuation`),
      ])
      setEvidence(evidenceItems ?? [])
      setCriteria(criterionItems ?? [])
      setApprovals(approvalItems ?? [])
      setContinuation(continuationItem ?? null)
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
      toast("Session completed", "success")
      loadSession()
    } catch { toast("Failed", "error") }
  }

  const retry = async () => {
    try {
      await POST(`/sessions/${sessionId}/retry`, { provider: retryProvider })
      toast(session?.status === "pending" ? "Session started" : "Session restarted", "success")
      loadSession()
    } catch { toast("Retry failed", "error") }
  }

  const restartTerminal = async () => {
    if (!terminalId) return
    try {
      setConnected(false)
      await POST(`/terminals/${terminalId}/restart`)
      setTerminalGeneration((value) => value + 1)
      toast("Terminal restarted", "success")
    } catch {
      toast("Terminal could not be restarted. Stop the session, then run it again.", "error")
    }
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
    try { await POST(`/sessions/${sessionId}/return-control`, { guidance: guidance.trim() }); setGuidance(""); await loadSession() }
    catch { toast("Could not return control", "error") }
  }

  const continueWithImplementation = async () => {
    if (!continuationGoal.trim()) return
    setContinuing(true)
    try {
      const child = await POST<SessionData>(`/sessions/${sessionId}/continue`, {
        goal: continuationGoal.trim(),
        provider: "orchestrated",
        acceptance_criteria: ["The approved outcome is implemented", "Relevant verification passes"],
      })
      try { await POST(`/sessions/${child.id}/retry`, { provider: "orchestrated" }) }
      catch { toast("Continuation created; agent setup is required before it can run", "warning") }
      setContinuation(child)
      useUIStore.getState().setViewingSessionId(child.id)
    } catch { toast("Could not create continuation Session", "error") }
    setContinuing(false)
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
    : ["done", "completed"].includes(session.status) ? "bg-green-500"
    : session.status === "failed" ? "bg-red-500"
    : "bg-gray-500"

  const duration = session.completed_at
    ? formatDuration(new Date(session.created_at), new Date(session.completed_at))
    : `Updated ${formatAge(session.updated_at)}`
  const hasGitEvidence = evidence.some((item) => item.evidence_type === "git")
  const hasPassingVerification = evidence.some((item) => item.evidence_type === "verification" && item.data.exit_code === 0)
  const criteriaResolved = criteria.every((criterion) => ["passed", "waived"].includes(criterion.status))
  const needsImplementationEvidence = session.review_mode === "implementation"
  const canComplete = criteriaResolved && (!needsImplementationEvidence || (hasGitEvidence && hasPassingVerification))

  return (
    <div className="flex-1 flex flex-col min-h-0 rounded-lg overflow-hidden">
      {/* Session header bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 border-b border-gray-700 flex-shrink-0">
        <button onClick={onClose} className="text-xs text-blue-400 hover:text-blue-300 flex-shrink-0">← Sessions</button>
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
        <span className="text-xs text-gray-200 truncate flex-1" title={session.description}>{shortDesc}</span>
        <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 flex-shrink-0">{statusLabel(session.status)}</span>
        <span className="text-[10px] text-gray-500 flex-shrink-0">{session.project}</span>
        <span className="text-[10px] text-gray-600 flex-shrink-0">{duration}</span>
        {["running", "waiting"].includes(session.status) && session.control_owner !== "human" && <button onClick={takeControl} className="rounded bg-yellow-600/20 px-2 py-0.5 text-[10px] text-yellow-300">Take control</button>}
        {session.status === "pending" && (
          <button onClick={retry} className="text-[10px] px-2 py-0.5 rounded bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 flex-shrink-0">
            Start
          </button>
        )}
        {["failed", "cancelled"].includes(session.status) && <><select value={retryProvider} onChange={(event) => setRetryProvider(event.target.value)} className="rounded border border-gray-600 bg-gray-900 px-2 py-1 text-[10px] text-gray-200"><option value="orchestrated">Configured preference</option><option value="codex">Codex CLI</option><option value="claude">Claude Code</option><option value="cursor">Cursor Agent</option><option value="gemini">Gemini CLI</option></select><button onClick={retry} className="rounded bg-blue-600 px-2 py-1 text-[10px] text-white">Run again</button></>}
        <div className="relative"><button onClick={() => setShowMoreActions((value) => !value)} className="rounded px-2 py-1 text-sm text-gray-500 hover:bg-gray-700 hover:text-gray-200" aria-label="More session actions">•••</button>{showMoreActions && <div className="absolute right-0 top-full z-20 mt-1 min-w-32 rounded border border-gray-700 bg-gray-800 p-1 shadow-xl">{isOpen && <button onClick={() => { setShowMoreActions(false); setConfirmCancel(true) }} className="w-full rounded px-2 py-1.5 text-left text-xs text-red-400 hover:bg-gray-700">{session.status === "pending" ? "Cancel session" : "Stop session"}</button>}{!isOpen && <button onClick={async () => { try { const { DELETE: DEL } = await import("@/lib/api"); await DEL(`/sessions/${sessionId}`); toast("Session deleted", "success"); onClose() } catch { toast("Delete failed", "error") } }} className="w-full rounded px-2 py-1.5 text-left text-xs text-red-400 hover:bg-gray-700">Delete session</button>}</div>}</div>
      </div>
      {confirmCancel && <div className="flex items-center justify-end gap-2 border-b border-red-900 bg-red-950/40 px-3 py-2 text-xs"><span className="mr-auto text-red-200">{session.status === "pending" ? "Cancel this queued session?" : "Stop this session? It cannot currently be resumed."}</span><button onClick={() => setConfirmCancel(false)} className="rounded border border-gray-600 px-2 py-1">Go back</button><button onClick={cancel} className="rounded bg-red-700 px-2 py-1 text-white">{session.status === "pending" ? "Cancel session" : "Stop session"}</button></div>}
      {session.status === "failed" && <div className="border-b border-red-900 bg-red-950/20 px-3 py-2 text-xs"><span className="font-medium text-red-300">Session failed.</span><span className="ml-2 text-gray-400">{session.blocker || session.output_summary || "Open the log for details, then retry when ready."}</span></div>}
      {isOpen && session.control_owner === "human" && <div className="flex items-center gap-2 border-b border-yellow-900 bg-yellow-950/30 px-3 py-2"><span className="text-[10px] text-yellow-200">You have control</span><input value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="Optional guidance before returning control" className="min-w-52 flex-1 rounded border border-yellow-800 bg-gray-950 px-2 py-1 text-[10px]" /><button onClick={returnControl} className="rounded bg-green-700 px-2 py-1 text-[10px] text-white">Return control</button></div>}
      {approvals.some((approval) => approval.status === "pending") && <div className="space-y-2 border-b border-yellow-900 bg-yellow-950/20 p-3"><div className="text-xs font-medium text-yellow-300">Needs your decision</div>{approvals.filter((approval) => approval.status === "pending").map((approval) => <div key={approval.id} className="flex items-center gap-2 rounded border border-yellow-800 p-2 text-xs"><span className="mr-auto text-gray-200">{approval.summary}</span><button onClick={() => decideApproval(approval, "approved")} className="rounded bg-green-700 px-2 py-1 text-[10px]">Approve</button><button onClick={() => decideApproval(approval, "rejected")} className="rounded bg-red-700 px-2 py-1 text-[10px]">Reject</button></div>)}</div>}
      {(criteria.length > 0 || session.status === "verifying" || (["completed", "done"].includes(session.status) && session.review_mode === "advisory")) && <div className="space-y-3 border-b border-gray-700 bg-gray-900 p-3"><div className="flex items-center gap-2"><span className="mr-auto text-xs font-medium text-gray-200">Review outcome</span><span className="rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-400">{needsImplementationEvidence ? "Implementation" : "Analysis only"}</span>{needsImplementationEvidence && <span className="text-[10px] text-gray-500">{evidence.length} evidence item{evidence.length === 1 ? "" : "s"}</span>}{!needsImplementationEvidence && ["completed", "done"].includes(session.status) && (continuation ? <button onClick={() => useUIStore.getState().setViewingSessionId(continuation.id)} className="rounded border border-blue-500 px-3 py-1 text-xs text-blue-300">View implementation · {statusLabel(continuation.status)}</button> : <button onClick={() => setShowContinue((value) => !value)} className="rounded border border-blue-500 px-3 py-1 text-xs text-blue-300">Continue with implementation</button>)}{session.status === "verifying" && <button disabled={!canComplete} title={canComplete ? "Complete session" : needsImplementationEvidence ? "Capture Git evidence, pass verification, and resolve criteria first" : "Resolve acceptance criteria first"} onClick={markDone} className="rounded bg-green-600 px-3 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40">{canComplete ? "Complete session" : criteriaResolved ? "Evidence required" : "Criteria required"}</button>}</div>{showContinue && !continuation && <div className="rounded-lg border border-blue-900 bg-blue-950/10 p-3"><label className="block text-[10px] font-medium uppercase tracking-wide text-gray-500">Next Session goal<textarea autoFocus rows={4} value={continuationGoal} onChange={(event) => setContinuationGoal(event.target.value)} className="mt-2 w-full resize-y rounded border border-gray-600 bg-gray-950 px-3 py-2 text-xs normal-case tracking-normal text-gray-200" /></label><div className="mt-2 flex items-center justify-between gap-3"><span className="text-[10px] text-gray-500">The prior goal, result, evidence references, project, and policy will be inherited.</span><button disabled={continuing || !continuationGoal.trim()} onClick={continueWithImplementation} className="rounded bg-blue-600 px-3 py-1.5 text-xs text-white disabled:opacity-50">{continuing ? "Starting…" : "Start linked Session"}</button></div></div>}{criteria.map((criterion) => <div key={criterion.criterion_index} className="flex flex-wrap items-center gap-2 rounded border border-gray-700 px-2 py-1.5 text-[10px]"><span className="mr-auto text-gray-300">{criterion.criterion}</span><span className="text-gray-500">{criterion.status === "unevaluated" ? "Not reviewed" : criterion.status}</span>{criterion.status === "unevaluated" && <><button onClick={() => decideCriterion(criterion, "passed")} className="text-green-400">Pass</button><button onClick={() => decideCriterion(criterion, "failed")} className="text-red-400">Fail</button><button onClick={() => decideCriterion(criterion, "waived")} className="text-gray-400">Waive</button></>}</div>)}{needsImplementationEvidence && <details className="rounded border border-gray-700 bg-gray-950/40 p-2"><summary className="cursor-pointer text-[10px] text-gray-400">Verification details</summary><div className="mt-2 flex flex-wrap items-center gap-2"><button onClick={captureGit} className="rounded border border-gray-600 px-2 py-1 text-[10px]">Capture Git evidence</button><input value={verificationCommand} onChange={(event) => setVerificationCommand(event.target.value)} placeholder="Verification command or project default" className="min-w-52 flex-1 rounded border border-gray-600 bg-gray-950 px-2 py-1 text-[10px]" /><button onClick={verify} className="rounded border border-gray-600 px-2 py-1 text-[10px]">Run verification</button></div></details>}</div>}

      {/* Main content: terminal (if active) or detail view */}
      {isExecuting && terminalId ? (
        <>
          {/* Terminal takes most of the space */}
          <div className="flex-1 min-h-0 flex flex-col">
            <TerminalToolbar
              project={session.project}
              sessionId={terminalId}
              connected={connected}
              onRestart={() => { void restartTerminal() }}
              onDisconnect={onClose}
              onKill={() => setConfirmCancel(true)}
              onReset={() => redrawRef.current()}
              mode="embedded"
              onModeChange={() => {}}
            />
            <div className="flex-1 min-h-0">
              <TerminalView
                key={`${terminalId}:${terminalGeneration}`}
                sessionId={terminalId}
                project={session.project}
                fontSize={layout === "kiosk" ? 15 : 13}
                onDisconnect={() => setConnected(false)}
                onSendReady={() => setConnected(true)}
                onRedrawReady={(redraw) => { redrawRef.current = redraw }}
              />
            </div>
          </div>

          {/* Compact detail strip below terminal */}
          <div className="flex-shrink-0 border-t border-gray-700 bg-gray-800">
            <SessionTabs tab={tab} setTab={setTab} events={events} log={log} session={session} evidence={evidence}
              loadEvents={loadEvents} loadLog={loadLog} compact />
          </div>
        </>
      ) : (
        /* Done/failed: full detail view */
        <SessionTabs tab={tab} setTab={setTab} events={events} log={log} session={session} evidence={evidence}
          loadEvents={loadEvents} loadLog={loadLog} />
      )}
    </div>
  )
}

// ── Tabbed content (used both as compact strip and full view) ──

function SessionTabs({
  tab, setTab, events, log, session, evidence, loadEvents, loadLog, compact,
}: {
  tab: "summary" | "timeline" | "log"
  setTab: (t: "summary" | "timeline" | "log") => void
  events: SessionEvent[] | null
  log: string | null
  session: SessionData
  evidence: SessionEvidence[]
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
            {t === "log" ? "Raw log" : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className={`overflow-y-auto px-3 py-2 ${compact ? "max-h-32" : "flex-1 min-h-0"}`}>
        {tab === "summary" && (
          <div className="mx-auto max-w-4xl space-y-5 py-2 text-xs">
            <section><div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-500">Requested outcome</div><div className="whitespace-pre-wrap text-gray-200">{session.goal || session.description}</div></section>
            <section className="rounded-lg border border-gray-700 bg-gray-900 p-4"><div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">Result</div><MarkdownText content={session.output_summary || (["running", "waiting", "pending"].includes(session.status) ? "Session in progress…" : "The agent did not record a concise outcome. Review the evidence below; use the raw log only for debugging.")} /></section>
            <EvidenceSummary evidence={evidence} reviewMode={session.review_mode} />
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
          <div className="space-y-2"><p className="text-[10px] text-gray-500">Raw agent transcript for debugging. The readable outcome is under Summary.</p><pre className="whitespace-pre-wrap rounded bg-gray-950 p-3 font-mono text-[10px] text-gray-400">{log === null ? "Loading..." : log}</pre></div>
        )}
      </div>
    </div>
  )
}

function MarkdownText({ content }: { content: string }) {
  return <div className="space-y-2 text-sm leading-6 text-gray-300"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    h1: ({ children }) => <h3 className="text-base font-semibold text-gray-100">{children}</h3>,
    h2: ({ children }) => <h3 className="text-sm font-semibold text-gray-100">{children}</h3>,
    h3: ({ children }) => <h4 className="font-medium text-gray-100">{children}</h4>,
    ul: ({ children }) => <ul className="ml-5 list-disc space-y-1">{children}</ul>,
    ol: ({ children }) => <ol className="ml-5 list-decimal space-y-1">{children}</ol>,
    pre: ({ children }) => <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-xs">{children}</pre>,
    code: ({ children }) => <code className="rounded bg-gray-950 px-1 py-0.5 font-mono text-xs text-gray-300">{children}</code>,
    p: ({ children }) => <p>{children}</p>,
  }}>{content}</ReactMarkdown></div>
}

function EvidenceSummary({ evidence, reviewMode }: { evidence: SessionEvidence[]; reviewMode: "advisory" | "implementation" }) {
  if (reviewMode === "advisory") return <section className="rounded-lg border border-blue-900/60 bg-blue-950/10 p-4 text-gray-400"><div className="font-medium text-blue-300">Analysis-only outcome</div><div className="mt-1 text-[10px]">No files changed by this Session, so Git and test evidence are not required.</div></section>
  if (evidence.length === 0) return <section className="rounded-lg border border-dashed border-gray-700 p-4 text-gray-500"><div className="font-medium text-gray-400">Independent evidence required</div><div className="mt-1 text-[10px]">Capture Git changes and run verification before completing this Session.</div></section>
  return <section><div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">Evidence</div><div className="grid gap-2 md:grid-cols-2">{evidence.map((item) => {
    const isVerification = item.evidence_type === "verification"
    const passed = isVerification && Number(item.data.exit_code) === 0
    const detail = isVerification
      ? `${String(item.data.command || "Verification")} · ${passed ? "Passed" : `Failed (exit ${String(item.data.exit_code)})`}`
      : String(item.data.diff_stat || item.data.status || "Git working tree captured")
    return <div key={item.id} className="rounded-lg border border-gray-700 bg-gray-900 p-3"><div className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${isVerification ? passed ? "bg-green-500" : "bg-red-500" : "bg-blue-500"}`} /><span className="font-medium text-gray-200">{item.title}</span></div><pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-gray-500">{detail}</pre></div>
  })}</div></section>
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

function statusLabel(status: SessionStatusValue): string {
  return ({ pending: "Queued", running: "Working", waiting: "Needs you", verifying: "Ready for review", done: "Completed", completed: "Completed", failed: "Failed", cancelled: "Cancelled" })[status]
}
