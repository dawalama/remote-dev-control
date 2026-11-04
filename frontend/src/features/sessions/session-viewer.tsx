import { useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { api, BLOB, DELETE, GET, PATCH, POST, PUT } from "@/lib/api"
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
  verification_command: string | null
  followup_instructions?: Array<{ instruction: string; created_at?: string | null }>
  worktree_mode?: "isolated" | "direct" | "reuse"
  worktree_path?: string | null
  worktree_branch?: string | null
}

interface SessionDiffFile {
  path: string
  status: string
  patch: string
  truncated?: boolean
}

interface SessionEvidence { id: string; evidence_type: string; title: string; data: Record<string, unknown> }
interface SessionCriterion { criterion_index: number; criterion: string; status: "unevaluated" | "passed" | "failed" | "waived" }
interface SessionApproval { id: string; summary: string; risk_level: string; status: "pending" | "approved" | "rejected" }

interface SuggestedNextStep {
  id: string
  kind: "host_commit" | "host_verify" | "agent_prompt"
  label: string
  detail?: string
  prompt?: string
}

interface DecisionTriageResult {
  outcome_satisfied?: number
  ready_for_host_commit?: number
  blocker_type?: { type: string; value: string; confidence: number; probabilities?: Record<string, number> }
  recommended_action?: { type: string; value: string; confidence: number; probabilities?: Record<string, number> }
}

interface SessionGitStatus {
  has_git: boolean
  branch?: string
  head?: string
  dirty: boolean
  status?: string
  changed_files: string[]
  diff_stat?: string
  has_remote: boolean
  remote_name?: string
  remote_url?: string
  suggested_commit_message?: string
  detected_blocker?: string | null
  decision_triage?: DecisionTriageResult
  suggested_next_steps?: SuggestedNextStep[]
}

interface SessionEvent {
  id: string
  timestamp: string
  type: string
  data: Record<string, unknown>
}

/**
 * Full-width session viewer — Artifact-first layout with 1-click host Git/Verification actions
 * and contextual next-step suggestions.
 */
export function SessionViewer({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) {
  const [session, setSession] = useState<SessionData | null>(null)
  const [tab, setTab] = useState<"artifact" | "summary" | "timeline" | "log">("artifact")
  const [artifact, setArtifact] = useState<{ source: string; html: string } | null>(null)
  const [gitStatus, setGitStatus] = useState<SessionGitStatus | null>(null)
  const [showCommitBox, setShowCommitBox] = useState(false)
  const [commitMessage, setCommitMessage] = useState("")
  const [committingGit, setCommittingGit] = useState(false)
  const [showReviewTools, setShowReviewTools] = useState(false)
  const [showTerminal, setShowTerminal] = useState(false)
  const [events, setEvents] = useState<SessionEvent[] | null>(null)
  const [log, setLog] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [evidence, setEvidence] = useState<SessionEvidence[]>([])
  const [criteria, setCriteria] = useState<SessionCriterion[]>([])
  const [verificationCommand, setVerificationCommand] = useState("")
  const [verificationError, setVerificationError] = useState<string | null>(null)
  const [verifyingEvidence, setVerifyingEvidence] = useState(false)
  const [productEvidenceNote, setProductEvidenceNote] = useState("")
  const [uploadingEvidence, setUploadingEvidence] = useState(false)
  const [showAcceptanceOverride, setShowAcceptanceOverride] = useState(false)
  const [acceptanceOverrideNote, setAcceptanceOverrideNote] = useState("")
  const [acceptingOverride, setAcceptingOverride] = useState(false)
  const evidenceInputRef = useRef<HTMLInputElement | null>(null)
  const [approvals, setApprovals] = useState<SessionApproval[]>([])
  const [guidance, setGuidance] = useState("")
  const [showMoreActions, setShowMoreActions] = useState(false)
  const [terminalGeneration, setTerminalGeneration] = useState(0)
  const [retryProvider, setRetryProvider] = useState("orchestrated")
  const [providerOptions, setProviderOptions] = useState<Array<{ id: string; label: string; ready?: boolean }>>([
    { id: "codex", label: "Codex CLI" },
    { id: "claude", label: "Claude Code" },
    { id: "cursor", label: "Cursor Agent" },
  ])
  const [showContinue, setShowContinue] = useState(false)
  const [continuationGoal, setContinuationGoal] = useState("Implement the approved outcome from this analysis. Make the changes, run relevant verification, and summarize the delivered result.")
  const [continuing, setContinuing] = useState(false)
  const [continuationFiles, setContinuationFiles] = useState<File[]>([])
  const continuationFileInputRef = useRef<HTMLInputElement>(null)
  const [continuation, setContinuation] = useState<SessionData | null>(null)
  const [nextInstruction, setNextInstruction] = useState("")
  const [sendingInstruction, setSendingInstruction] = useState(false)
  const [pinnedRun, setPinnedRun] = useState<number | null>(null)
  const [runDiff, setRunDiff] = useState<SessionDiffFile[] | null>(null)
  const pinnedRunRef = useRef<number | null>(null)
  const diffRequest = useRef(0)
  pinnedRunRef.current = pinnedRun
  const redrawRef = useRef<() => void>(() => {})
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)

  useMountEffect(() => {
    void loadSession()
    void loadSupervision()
    void loadArtifact()
    void loadGitStatus()
    void loadRunDiff()
    void GET<{ providers: Array<{ id: string; label: string; ready: boolean }> }>("/settings/agent-providers")
      .then((res) => {
        if (res?.providers?.length) {
          setProviderOptions(res.providers.filter((p) => p.id !== "diagnostic"))
        }
      })
      .catch(() => {})
  })
  useMountEffect(() => {
    const interval = window.setInterval(() => {
      void loadSession()
      void loadSupervision()
      void loadArtifact()
      void loadGitStatus()
      void loadEvents()
      void loadLog()
      void loadRunDiff()
    }, 5000)
    return () => window.clearInterval(interval)
  })

  const loadRunDiff = async () => {
    const run = pinnedRunRef.current
    const request = ++diffRequest.current
    const query = run == null ? "" : `?run=${run}`
    try {
      const data = await GET<{ files: SessionDiffFile[] }>(`/sessions/${sessionId}/diff${query}`)
      if (request !== diffRequest.current) return
      setRunDiff(data?.files ?? [])
    } catch {
      if (request === diffRequest.current) setRunDiff([])
    }
  }

  const selectRun = (index: number | null) => {
    pinnedRunRef.current = index
    setPinnedRun(index)
    setRunDiff(null)
    void loadRunDiff()
  }

  const loadArtifact = async () => {
    try {
      const data = await GET<{ source: string; html: string }>(`/sessions/${sessionId}/artifact`)
      if (data?.html) setArtifact(data)
    } catch { /* ignore */ }
  }

  const loadGitStatus = async () => {
    try {
      const data = await GET<SessionGitStatus>(`/sessions/${sessionId}/git-status`)
      if (data) {
        setGitStatus(data)
        setCommitMessage((prev) => prev || data.suggested_commit_message || "")
      }
    } catch { /* ignore */ }
  }

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

  const handleHostCommit = async (push = false) => {
    setCommittingGit(true)
    try {
      const res = await POST<{ short_head: string; branch: string; pushed: boolean; push_detail?: string }>(
        `/sessions/${sessionId}/git-commit`,
        { message: commitMessage.trim() || gitStatus?.suggested_commit_message || undefined, push },
      )
      toast(
        res?.push_detail
          ? `Committed ${res.short_head} · ${res.push_detail}`
          : `Committed ${res?.short_head || "changes"} on ${res?.branch || "branch"}`,
        "success",
      )
      setShowCommitBox(false)
      await Promise.all([loadSession(), loadSupervision(), loadArtifact(), loadGitStatus()])
    } catch (error) {
      toast(apiErrorDetail(error, "Host commit failed"), "error")
    }
    setCommittingGit(false)
  }

  const markDone = async (overrideInput: unknown = false) => {
    const override = overrideInput === true
    if (override) setAcceptingOverride(true)
    try {
      await POST(`/sessions/${sessionId}/complete`, override ? { override: true, note: acceptanceOverrideNote.trim() } : undefined)
      toast(override ? "Session accepted with exception" : "Session completed", "success")
      setShowAcceptanceOverride(false)
      await Promise.all([loadSession(), loadSupervision(), loadArtifact()])
    } catch (error) {
      toast(apiErrorDetail(error, "Could not complete Session"), "error")
    }
    if (override) setAcceptingOverride(false)
  }

  const retry = async () => {
    try {
      await POST(`/sessions/${sessionId}/retry`, { provider: retryProvider })
      toast(session?.status === "pending" ? "Session started" : "Session restarted", "success")
      await Promise.all([loadSession(), loadSupervision(), loadArtifact(), loadGitStatus()])
    } catch (error) {
      toast(apiErrorDetail(error, "Retry failed"), "error")
      await Promise.all([loadSession(), loadArtifact()])
    }
  }

  const restoreReview = async () => {
    try { await POST(`/sessions/${sessionId}/restore-review`); toast("Review restored", "success"); await loadSession() }
    catch { toast("Could not restore review", "error") }
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
      await loadSession()
    } catch { toast("Cancellation failed", "error") }
  }

  const captureGit = async () => {
    try {
      await POST(`/sessions/${sessionId}/evidence/git`)
      await Promise.all([loadSupervision(), loadArtifact(), loadGitStatus()])
      toast("Git evidence captured", "success")
    } catch { toast("Git evidence failed", "error") }
  }

  const verify = async () => {
    setVerifyingEvidence(true)
    setVerificationError(null)
    try {
      await POST(`/sessions/${sessionId}/evidence/verification`, { command: verificationCommand.trim() || undefined })
      await Promise.all([loadSupervision(), loadArtifact()])
      toast("Project checks recorded", "success")
    } catch (error) {
      const detail = apiErrorDetail(error, "Project checks could not run")
      setVerificationError(detail)
      toast(detail, "error")
    }
    setVerifyingEvidence(false)
  }

  const attachProductEvidence = async (file: File | undefined) => {
    if (!file) return
    setUploadingEvidence(true)
    try {
      const form = new FormData()
      form.append("file", file)
      form.append("note", productEvidenceNote.trim())
      await api(`/sessions/${sessionId}/evidence/media`, { method: "POST", body: form })
      setProductEvidenceNote("")
      await Promise.all([loadSupervision(), loadArtifact()])
      toast("Product evidence attached", "success")
    } catch (error) { toast(error instanceof Error ? error.message : "Evidence upload failed", "error") }
    setUploadingEvidence(false)
    if (evidenceInputRef.current) evidenceInputRef.current.value = ""
  }

  const decideCriterion = async (criterion: SessionCriterion, status: "passed" | "failed" | "waived") => {
    try {
      await PUT(`/sessions/${sessionId}/criteria/${criterion.criterion_index}`, { status, evidence_ids: evidence.map((item) => item.id) })
      await Promise.all([loadSupervision(), loadArtifact()])
    } catch { toast("Criterion update failed", "error") }
  }

  const decideApproval = async (approval: SessionApproval, decision: "approved" | "rejected") => {
    try { await POST(`/sessions/${sessionId}/approvals/${approval.id}/decide`, { decision }); await loadSupervision(); await loadSession() }
    catch { toast("Approval decision failed", "error") }
  }

  const takeControl = async () => {
    try {
      await POST(`/sessions/${sessionId}/take-control`)
      setShowTerminal(true)
      await loadSession()
    } catch { toast("Could not take control", "error") }
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
        review_mode: "implementation",
        acceptance_criteria: ["The requested next outcome is completed", "Relevant verification passes"],
      })
      if (continuationFiles.length > 0) {
        for (const file of continuationFiles) {
          try {
            const form = new FormData()
            form.append("file", file)
            form.append("note", `Attached at continuation: ${file.name}`)
            await api(`/sessions/${child.id}/evidence/media`, { method: "POST", body: form })
          } catch {
            /* ignore individual evidence upload error */
          }
        }
      }
      setContinuationFiles([])
      try { await POST(`/sessions/${child.id}/retry`, { provider: "orchestrated" }) }
      catch { toast("Continuation created; agent setup is required before it can run", "warning") }
      setContinuation(child)
      useUIStore.getState().setViewingSessionId(child.id)
    } catch { toast("Could not create continuation Session", "error") }
    setContinuing(false)
  }

  const sendInstructionText = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed) return
    setSendingInstruction(true)
    try {
      await POST(`/sessions/${sessionId}/instructions`, { instruction: trimmed })
      setNextInstruction("")
      toast("Agent is continuing in this Session", "success")
      await Promise.all([loadSession(), loadSupervision(), loadArtifact()])
    } catch (error) {
      toast(apiErrorDetail(error, "Could not continue this Session"), "error")
    }
    setSendingInstruction(false)
  }

  const instructCurrentSession = async () => {
    await sendInstructionText(nextInstruction)
  }

  if (!session) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-gray-500 text-sm">Loading session...</p>
      </div>
    )
  }

  const isOpen = ["pending", "running", "waiting"].includes(session.status)
  const terminalId = session.terminal_ids[0] || null
  const shortDesc = session.description.split(/[.\n]/)[0].slice(0, 120) || "Untitled"

  const statusColor = session.status === "running" ? "bg-blue-500 animate-pulse"
    : session.status === "waiting" ? "bg-yellow-500 animate-pulse"
    : ["done", "completed"].includes(session.status) ? "bg-green-500"
    : session.status === "failed" ? "bg-red-500"
    : "bg-purple-400"

  const duration = session.completed_at
    ? formatDuration(new Date(session.created_at), new Date(session.completed_at))
    : `Updated ${formatAge(session.updated_at)}`
  const hasGitEvidence = evidence.some((item) => item.evidence_type === "git")
  const hasPassingVerification = evidence.some((item) => item.evidence_type === "verification" && item.data.exit_code === 0)
  const criteriaResolved = criteria.every((criterion) => ["passed", "waived"].includes(criterion.status))
  const needsImplementationEvidence = session.review_mode === "implementation"
  const canComplete = criteriaResolved && (!needsImplementationEvidence || (hasGitEvidence && hasPassingVerification))
  const followups = session.followup_instructions ?? []
  const runCount = 1 + followups.length
  const activeRun = pinnedRun == null ? runCount - 1 : Math.min(pinnedRun, runCount - 1)
  const activeFollowup = activeRun > 0 ? followups[activeRun - 1] : null
  const canRestoreReview = session.status === "cancelled" && Boolean(session.output_summary)
  const runnableVerificationCommand = verificationCommand.trim() || session.verification_command
  const activeBlocker = session.blocker || gitStatus?.detected_blocker || null
  const changedCount = gitStatus?.changed_files?.length ?? 0

  return (
    <div className="flex-1 flex flex-col min-h-0 rounded-lg overflow-hidden bg-gray-950">
      {/* 1. Compact Session Header Bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 border-b border-gray-700 flex-shrink-0">
        <button onClick={onClose} className="text-xs text-blue-400 hover:text-blue-300 flex-shrink-0">← Sessions</button>
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
        <span className="text-xs font-medium text-gray-100 truncate flex-1" title={session.description}>{shortDesc}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium flex-shrink-0 ${
          activeBlocker ? "bg-amber-500/20 text-amber-300 border border-amber-500/30" : "bg-gray-700 text-gray-300"
        }`}>
          {activeBlocker && session.status === "verifying" ? "Needs action" : statusLabel(session.status)}
        </span>
        <span className="text-[10px] text-gray-400 flex-shrink-0">{session.project}</span>
        {session.worktree_branch ? (
          <span
            className="rounded border border-indigo-800/50 bg-indigo-950/50 px-1.5 py-0.5 text-[10px] font-mono text-indigo-300 flex-shrink-0"
            title={`Worktree: ${session.worktree_path || session.worktree_branch}`}
          >
            ⎇ {session.worktree_branch}
          </span>
        ) : session.worktree_mode === "direct" ? (
          <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] font-mono text-gray-400 flex-shrink-0" title="Direct repository checkout">
            📁 direct
          </span>
        ) : null}
        <span className="text-[10px] text-gray-500 flex-shrink-0">{duration}</span>
        {terminalId && (
          <button
            onClick={() => setShowTerminal((v) => !v)}
            className={`rounded px-2 py-0.5 text-[10px] font-mono transition-colors ${
              showTerminal || session.status === "waiting" || session.control_owner === "human"
                ? "bg-blue-600 text-white"
                : "border border-gray-600 bg-gray-900 text-gray-300 hover:bg-gray-700"
            }`}
            title="Toggle Live PTY Terminal fallback"
          >
            &gt;_ {showTerminal ? "Hide Terminal" : "Live Terminal"}
          </button>
        )}
        {["running", "waiting"].includes(session.status) && session.control_owner !== "human" && (
          <button onClick={takeControl} className="rounded bg-yellow-600/20 px-2 py-0.5 text-[10px] text-yellow-300">Take control</button>
        )}
        {session.status === "pending" && (
          <button onClick={retry} className="text-[10px] px-2 py-0.5 rounded bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 flex-shrink-0">
            Start
          </button>
        )}
        {["failed", "cancelled"].includes(session.status) && (
          canRestoreReview ? (
            <button onClick={restoreReview} className="rounded bg-blue-600 px-2 py-1 text-[10px] text-white">Restore review</button>
          ) : (
            <>
              <select value={retryProvider} onChange={(event) => setRetryProvider(event.target.value)} className="rounded border border-gray-600 bg-gray-900 px-2 py-1 text-[10px] text-gray-200">
                <option value="orchestrated">Configured preference</option>
                {providerOptions.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}{p.ready === true ? " (Ready)" : p.ready === false ? " (Setup)" : ""}</option>
                ))}
              </select>
              <button onClick={retry} className="rounded bg-blue-600 px-2 py-1 text-[10px] text-white">Run again</button>
            </>
          )
        )}
        <div className="relative">
          <button onClick={() => setShowMoreActions((value) => !value)} className="rounded px-2 py-1 text-sm text-gray-500 hover:bg-gray-700 hover:text-gray-200" aria-label="More session actions">•••</button>
          {showMoreActions && (
            <div className="absolute right-0 top-full z-20 mt-1 min-w-36 rounded border border-gray-700 bg-gray-800 p-1 shadow-xl">
              {isOpen && (
                <button onClick={() => { setShowMoreActions(false); setConfirmCancel(true) }} className="w-full rounded px-2 py-1.5 text-left text-xs text-red-400 hover:bg-gray-700">
                  {session.status === "pending" ? "Cancel session" : "Stop session"}
                </button>
              )}
              {!isOpen && (
                <button onClick={async () => { try { await DELETE(`/sessions/${sessionId}`); toast("Session deleted", "success"); onClose() } catch { toast("Delete failed", "error") } }} className="w-full rounded px-2 py-1.5 text-left text-xs text-red-400 hover:bg-gray-700">
                  Delete session
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Cancel confirmation */}
      {confirmCancel && (
        <div className="flex items-center justify-end gap-2 border-b border-red-900 bg-red-950/40 px-3 py-2 text-xs">
          <span className="mr-auto text-red-200">{session.status === "pending" ? "Cancel this queued session?" : "Stop this session? It cannot currently be resumed."}</span>
          <button onClick={() => setConfirmCancel(false)} className="rounded border border-gray-600 px-2 py-1">Go back</button>
          <button onClick={cancel} className="rounded bg-red-700 px-2 py-1 text-white">{session.status === "pending" ? "Cancel session" : "Stop session"}</button>
        </div>
      )}

      {/* Human control strip */}
      {isOpen && session.control_owner === "human" && (
        <div className="flex items-center gap-2 border-b border-yellow-900 bg-yellow-950/30 px-3 py-2">
          <span className="text-[10px] text-yellow-200">You have control</span>
          <input value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="Optional guidance before returning control" className="min-w-52 flex-1 rounded border border-yellow-800 bg-gray-950 px-2 py-1 text-[10px]" />
          <button onClick={returnControl} className="rounded bg-green-700 px-2 py-1 text-[10px] text-white">Return control</button>
        </div>
      )}

      {/* Pending terminal approvals */}
      {approvals.some((approval) => approval.status === "pending") && (
        <div className="space-y-2 border-b border-yellow-900 bg-yellow-950/20 p-3">
          <div className="text-xs font-medium text-yellow-300">Needs your decision</div>
          {approvals.filter((approval) => approval.status === "pending").map((approval) => (
            <div key={approval.id} className="flex items-center gap-2 rounded border border-yellow-800 p-2 text-xs">
              <span className="mr-auto text-gray-200">{approval.summary}</span>
              <button onClick={() => decideApproval(approval, "approved")} className="rounded bg-green-700 px-2 py-1 text-[10px]">Approve</button>
              <button onClick={() => decideApproval(approval, "rejected")} className="rounded bg-red-700 px-2 py-1 text-[10px]">Reject</button>
            </div>
          ))}
        </div>
      )}

      {/* 2. Prominent Blocker / Failed Callout Banner */}
      {(session.status === "failed" || activeBlocker) && (
        <div className={`flex flex-wrap items-center gap-3 border-b px-3 py-2 text-xs ${
          session.status === "failed"
            ? "border-red-900/80 bg-red-950/30 text-red-200"
            : "border-amber-700/50 bg-amber-950/30 text-amber-200"
        }`}>
          <span className="font-semibold uppercase tracking-wide text-[10px]">
            {session.status === "failed" ? "✕ Session Failed" : "⚠ Blocker Detected"}
          </span>
          <span className="flex-1 text-gray-200">
            {activeBlocker || session.output_summary || "Open the raw log for details, then retry when ready."}
          </span>
          {gitStatus?.dirty && (
            <button
              onClick={() => setShowCommitBox(true)}
              className="rounded bg-amber-500 px-2.5 py-1 text-[11px] font-semibold text-gray-950 hover:bg-amber-400"
            >
              Commit {changedCount} File{changedCount === 1 ? "" : "s"} on Host
            </button>
          )}
        </div>
      )}

      {/* 3. Sleek 1-Click Host Action Bar (Git, Verification, Acceptance) */}
      {["verifying", "completed", "done"].includes(session.status) && (
        <div className="border-b border-gray-800 bg-gray-900/90 px-3 py-2 flex-shrink-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {/* Live Git Status Pill */}
            {gitStatus?.has_git && (
              <div className="flex items-center gap-1.5 rounded border border-gray-700 bg-gray-950 px-2 py-1 text-[10px]">
                <span className={`h-1.5 w-1.5 rounded-full ${gitStatus.dirty ? "bg-amber-400" : "bg-emerald-400"}`} />
                <span className="font-mono text-gray-300">{gitStatus.branch || "main"}</span>
                <span className="text-gray-600">·</span>
                <span className={gitStatus.dirty ? "text-amber-300 font-medium" : "text-emerald-300"}>
                  {gitStatus.dirty ? `${changedCount} uncommitted file${changedCount === 1 ? "" : "s"}` : `Committed (${gitStatus.head || "clean"})`}
                </span>
                <span className="text-gray-600">·</span>
                <span className="text-gray-500" title={gitStatus.remote_url || "No git remote configured"}>
                  {gitStatus.has_remote ? `Remote: ${gitStatus.remote_name}` : "Local repo only (no remote)"}
                </span>
              </div>
            )}

            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              {/* 1-Click Host Commit Button */}
              {gitStatus?.dirty && (
                <button
                  onClick={() => setShowCommitBox((v) => !v)}
                  className="rounded bg-blue-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-blue-500"
                >
                  ✓ Commit Changes ({changedCount})
                </button>
              )}

              {/* 1-Click Run Project Checks */}
              {needsImplementationEvidence && (
                <button
                  disabled={verifyingEvidence}
                  onClick={() => {
                    if (!runnableVerificationCommand) {
                      setShowReviewTools(true)
                      return
                    }
                    void verify()
                  }}
                  className={`rounded px-2.5 py-1 text-[11px] font-medium border transition-colors ${
                    hasPassingVerification
                      ? "border-emerald-700/60 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-900/40"
                      : "border-gray-600 bg-gray-800 text-gray-200 hover:bg-gray-700"
                  }`}
                >
                  {verifyingEvidence ? "Running Checks…" : hasPassingVerification ? "✓ Checks Passed" : "▶ Run Checks"}
                </button>
              )}

              {/* 1-Click Accept / Complete Outcome */}
              {session.status === "verifying" && (
                canComplete ? (
                  <button
                    onClick={() => { void markDone(false) }}
                    className="rounded bg-emerald-600 px-3 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500"
                  >
                    ✓ Accept &amp; Complete
                  </button>
                ) : (
                  <button
                    onClick={() => setShowAcceptanceOverride((v) => !v)}
                    className="rounded border border-emerald-600/60 bg-emerald-950/30 px-2.5 py-1 text-[11px] font-medium text-emerald-300 hover:bg-emerald-900/40"
                    title="Accept outcome now (or complete remaining checks)"
                  >
                    Accept Outcome…
                  </button>
                )
              )}

              {/* Toggle extra review tools (screenshots, custom check command, criteria) */}
              <button
                onClick={() => setShowReviewTools((v) => !v)}
                className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-[10px] text-gray-400 hover:text-gray-200"
              >
                {showReviewTools ? "Hide Options ▴" : "Options ▾"}
              </button>
            </div>
          </div>

          {/* Inline 1-Click Host Commit Bar */}
          {showCommitBox && gitStatus?.dirty && (
            <div className="flex flex-wrap items-center gap-2 rounded border border-blue-800/60 bg-blue-950/20 p-2 text-xs">
              <span className="text-[10px] font-medium uppercase tracking-wide text-blue-300">Commit message</span>
              <input
                autoFocus
                value={commitMessage}
                onChange={(e) => setCommitMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !committingGit) {
                    void handleHostCommit(false)
                  }
                }}
                placeholder="feat: describe the changes..."
                className="min-w-60 flex-1 rounded border border-gray-700 bg-gray-950 px-2.5 py-1 text-xs text-gray-100"
              />
              <button
                disabled={committingGit}
                onClick={() => { void handleHostCommit(false) }}
                className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {committingGit ? "Committing…" : "Commit on Host"}
              </button>
              {gitStatus.has_remote ? (
                <button
                  disabled={committingGit}
                  onClick={() => { void handleHostCommit(true) }}
                  className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  Commit &amp; Push
                </button>
              ) : (
                <span className="text-[10px] text-gray-500">No remote configured (commits locally)</span>
              )}
              <button onClick={() => setShowCommitBox(false)} className="px-1.5 text-[10px] text-gray-400 hover:text-gray-200">Cancel</button>
            </div>
          )}

          {/* Inline Acceptance Override Box */}
          {showAcceptanceOverride && session.status === "verifying" && (
            <div className="flex flex-wrap items-center gap-2 rounded border border-emerald-800/60 bg-emerald-950/20 p-2 text-xs">
              <span className="text-[10px] text-emerald-300">Accept note:</span>
              <input
                autoFocus
                value={acceptanceOverrideNote}
                onChange={(event) => setAcceptanceOverrideNote(event.target.value)}
                placeholder="e.g. Verified 141 passing tests in agent report"
                className="min-w-60 flex-1 rounded border border-gray-700 bg-gray-950 px-2.5 py-1 text-xs text-gray-200"
              />
              <button
                disabled={acceptingOverride}
                onClick={() => {
                  if (!acceptanceOverrideNote.trim() || acceptanceOverrideNote.trim().length < 5) {
                    setAcceptanceOverrideNote("Accepted after reviewing proof artifact")
                  }
                  void POST(`/sessions/${sessionId}/complete`, {
                    override: true,
                    note: acceptanceOverrideNote.trim().length >= 5 ? acceptanceOverrideNote.trim() : "Accepted after reviewing proof artifact",
                  }).then(async () => {
                    toast("Session completed", "success")
                    setShowAcceptanceOverride(false)
                    await Promise.all([loadSession(), loadSupervision(), loadArtifact()])
                  }).catch((err) => toast(apiErrorDetail(err, "Could not complete Session"), "error"))
                }}
                className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
              >
                {acceptingOverride ? "Completing…" : "Confirm Complete"}
              </button>
              <button onClick={() => setShowAcceptanceOverride(false)} className="px-1.5 text-[10px] text-gray-400">Cancel</button>
            </div>
          )}

          {/* Collapsible Options Drawer (Evidence upload, Custom check cmd, Criteria) */}
          {showReviewTools && (
            <div className="space-y-2 rounded border border-gray-800 bg-gray-950/80 p-2.5 text-[11px]">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={verificationCommand}
                  onChange={(event) => { setVerificationCommand(event.target.value); setVerificationError(null) }}
                  placeholder={session.verification_command ? `Default check: ${session.verification_command}` : "Verification command (e.g. npm run verify or pytest)"}
                  className="min-w-52 flex-1 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200 font-mono"
                />
                <button disabled={verifyingEvidence || !runnableVerificationCommand} onClick={() => { void verify() }} className="rounded bg-blue-600 px-2.5 py-1 text-xs text-white disabled:opacity-40">
                  {verifyingEvidence ? "Running…" : "Run Check"}
                </button>
                <button onClick={() => { void captureGit() }} className="rounded border border-gray-700 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800">
                  Snapshot Git Diff
                </button>
                <input
                  ref={evidenceInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/webp,video/mp4,video/webm,video/quicktime"
                  className="hidden"
                  onChange={(event) => { void attachProductEvidence(event.target.files?.[0]) }}
                />
                <button disabled={uploadingEvidence} onClick={() => evidenceInputRef.current?.click()} className="rounded border border-purple-600/60 px-2.5 py-1 text-xs text-purple-300 hover:bg-purple-950/30">
                  {uploadingEvidence ? "Uploading…" : "+ Attach Screenshot/Video"}
                </button>
              </div>
              {verificationError && <div className="rounded border border-red-900 bg-red-950/20 p-2 text-[10px] text-red-300">{verificationError}</div>}
              {criteria.length > 0 && (
                <div className="space-y-1 pt-1">
                  {criteria.map((criterion) => (
                    <div key={criterion.criterion_index} className="flex items-center gap-2 rounded border border-gray-800 px-2 py-1 text-[10px]">
                      <span className="mr-auto text-gray-300">{criterion.criterion}</span>
                      <span className="text-gray-500">{criterion.status}</span>
                      {criterion.status === "unevaluated" && (
                        <>
                          <button onClick={() => decideCriterion(criterion, "passed")} className="text-green-400">Pass</button>
                          <button onClick={() => decideCriterion(criterion, "failed")} className="text-red-400">Fail</button>
                          <button onClick={() => decideCriterion(criterion, "waived")} className="text-gray-400">Waive</button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 4. Main Content: Proof Artifact Centerpiece (or Live Terminal when toggled) */}
      {(showTerminal || session.status === "waiting" || session.control_owner === "human") && terminalId ? (
        <>
          <div className="flex-1 min-h-0 flex flex-col">
            <TerminalToolbar
              project={session.project}
              sessionId={terminalId}
              connected={connected}
              onRestart={() => { void restartTerminal() }}
              onDisconnect={() => setShowTerminal(false)}
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

          <div className="flex-shrink-0 border-t border-gray-700 bg-gray-800">
            <SessionTabs sessionId={sessionId} tab={tab} setTab={setTab} artifact={artifact} events={events} log={log} session={session} evidence={evidence} loadSupervision={loadSupervision}
              loadEvents={loadEvents} loadLog={loadLog} compact activeRun={activeRun} runCount={runCount} activeFollowup={activeFollowup} runDiff={runDiff} onSelectRun={selectRun} />
          </div>
        </>
      ) : (
        <SessionTabs sessionId={sessionId} tab={tab} setTab={setTab} artifact={artifact} events={events} log={log} session={session} evidence={evidence} loadSupervision={loadSupervision}
          loadEvents={loadEvents} loadLog={loadLog} activeRun={activeRun} runCount={runCount} activeFollowup={activeFollowup} runDiff={runDiff} onSelectRun={selectRun} />
      )}

      {/* 5. Bottom Contextual Next Steps Dock (1-Click Chips + Compact Follow-up Input) */}
      {["verifying", "completed", "done", "failed", "cancelled"].includes(session.status) && (
        <div className="border-t border-gray-800 bg-gray-900/95 px-3 py-2 flex-shrink-0 space-y-2">
          {/* 1-Click Suggested Next-Step Chips */}
          <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-500 flex-shrink-0 mr-1">Next steps:</span>
            {gitStatus?.decision_triage?.blocker_type?.value && gitStatus.decision_triage.blocker_type.value !== "none" && (
              <span
                className="flex-shrink-0 rounded-full border border-amber-500/30 bg-amber-950/40 px-2.5 py-0.5 text-[9.5px] font-medium text-amber-300"
                title={`System 1 Calibrated Blocker Triage (${Math.round((gitStatus.decision_triage.blocker_type.confidence || 0.8) * 100)}% confidence)`}
              >
                ⚠️ {gitStatus.decision_triage.blocker_type.value.replace(/_/g, " ")}
              </span>
            )}
            {gitStatus?.suggested_next_steps && gitStatus.suggested_next_steps.map((step) => (
                <button
                  key={step.id}
                  disabled={sendingInstruction || committingGit || verifyingEvidence}
                  onClick={() => {
                    if (step.kind === "host_commit") {
                      setShowCommitBox(true)
                    } else if (step.kind === "host_verify") {
                      void verify()
                    } else if (step.prompt) {
                      void sendInstructionText(step.prompt)
                    }
                  }}
                  title={step.detail || step.prompt}
                  className={`flex-shrink-0 rounded-full border px-2.5 py-0.5 text-[10px] font-medium transition-colors ${
                    step.kind === "host_commit"
                      ? "border-blue-500/40 bg-blue-950/40 text-blue-300 hover:bg-blue-900/50"
                      : step.kind === "host_verify"
                        ? "border-emerald-500/40 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-900/40"
                        : "border-gray-700 bg-gray-800/80 text-gray-300 hover:border-gray-500 hover:text-white"
                  }`}
                >
                  {step.kind === "host_commit" ? "⚡ " : step.kind === "host_verify" ? "▶ " : "↻ "}
                  {step.label}
                </button>
              ))}
              {["completed", "done"].includes(session.status) && (
                continuation ? (
                  <button
                    onClick={() => useUIStore.getState().setViewingSessionId(continuation.id)}
                    className="flex-shrink-0 rounded-full border border-purple-500/40 bg-purple-950/30 px-2.5 py-0.5 text-[10px] text-purple-300"
                  >
                    → View Next Session ({statusLabel(continuation.status)})
                  </button>
                ) : (
                  <button
                    onClick={() => setShowContinue((v) => !v)}
                    className="flex-shrink-0 rounded-full border border-purple-500/40 bg-purple-950/30 px-2.5 py-0.5 text-[10px] text-purple-300 hover:bg-purple-900/40"
                  >
                    + New Linked Goal
                  </button>
                )
              )}
            </div>

          {/* Compact Single-Line Follow-Up Bar */}
          <div className="flex items-center gap-2">
            <input
              value={nextInstruction}
              onChange={(event) => setNextInstruction(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && nextInstruction.trim() && !sendingInstruction) {
                  event.preventDefault()
                  void instructCurrentSession()
                }
              }}
              placeholder="Ask the agent to iterate, refine code, or fix another issue in this session…"
              className="min-w-0 flex-1 rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-xs text-gray-200 placeholder-gray-500 focus:border-blue-500 focus:outline-none"
            />
            <button
              disabled={sendingInstruction || !nextInstruction.trim()}
              onClick={() => { void instructCurrentSession() }}
              className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-40"
            >
              {sendingInstruction ? "Running…" : "Send Follow-Up"}
            </button>
          </div>

          {showContinue && !continuation && (
            <div className="rounded-lg border border-blue-900 bg-blue-950/10 p-2.5 space-y-2">
              <div className="text-[10px] font-medium uppercase tracking-wide text-blue-300">Start Linked Follow-Up Session</div>
              <div className="flex gap-2">
                <input
                  autoFocus
                  value={continuationGoal}
                  onChange={(event) => setContinuationGoal(event.target.value)}
                  className="flex-1 rounded border border-gray-700 bg-gray-950 px-2.5 py-1 text-xs text-gray-200"
                />
                <button
                  type="button"
                  onClick={() => continuationFileInputRef.current?.click()}
                  className="rounded border border-gray-700 px-2 py-1 text-[11px] text-gray-300 hover:text-white"
                  title="Attach files to follow-up session"
                >
                  📎 {continuationFiles.length > 0 ? `(${continuationFiles.length})` : "Attach"}
                </button>
                <input
                  ref={continuationFileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const files = Array.from(e.target.files || [])
                    if (files.length > 0) {
                      setContinuationFiles((prev) => [...prev, ...files])
                    }
                    e.target.value = ""
                  }}
                />
                <button disabled={continuing || !continuationGoal.trim()} onClick={continueWithImplementation} className="rounded bg-blue-600 px-3 py-1 text-xs text-white disabled:opacity-50">
                  {continuing ? "Starting…" : "Start"}
                </button>
                <button onClick={() => { setShowContinue(false); setContinuationFiles([]) }} className="px-2 text-[10px] text-gray-400">Cancel</button>
              </div>
              {continuationFiles.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {continuationFiles.map((file, idx) => (
                    <span
                      key={`${file.name}-${idx}`}
                      className="inline-flex items-center gap-1 rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 border border-gray-700"
                    >
                      <span className="truncate max-w-[140px]">{file.name}</span>
                      <button
                        type="button"
                        onClick={() => setContinuationFiles((prev) => prev.filter((_, i) => i !== idx))}
                        className="text-gray-400 hover:text-red-400 font-bold ml-0.5"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function DiffText({ patch }: { patch: string }) {
  return (
    <pre className="min-w-0 overflow-auto px-3 py-2 font-mono text-[11px] leading-5">
      {patch.split("\n").map((line, index) => {
        const color = line.startsWith("+") && !line.startsWith("+++")
          ? "text-emerald-300"
          : line.startsWith("-") && !line.startsWith("---")
            ? "text-red-300"
            : line.startsWith("@@") || line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file")
              ? "text-sky-300"
              : "text-gray-400"
        return <div key={index} className={color}>{line || " "}</div>
      })}
    </pre>
  )
}

function RunChanges({ instruction, files, compact }: { instruction: string; files: SessionDiffFile[] | null; compact?: boolean }) {
  const reviewFiles = files ?? []
  const firstCode = reviewFiles.find((file) => file.path.startsWith("src/") || file.path.startsWith("tests/"))?.path
    ?? reviewFiles.find((file) => !file.path.startsWith("."))?.path
    ?? reviewFiles[0]?.path
    ?? null
  const [picked, setPicked] = useState<string | null>(null)
  const selectedPath = picked && reviewFiles.some((file) => file.path === picked) ? picked : firstCode
  const selected = reviewFiles.find((file) => file.path === selectedPath) ?? null

  if (compact) {
    return (
      <div className="max-h-36 overflow-y-auto px-3 py-2">
        <div className="mb-1 line-clamp-2 text-[11px] text-gray-200">{instruction}</div>
        <div className="text-[10px] text-gray-500">
          {files == null ? "Loading files…" : files.length === 0 ? "No file changes in this run." : `${files.length} files. Hide the terminal to read them.`}
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-shrink-0 border-b border-gray-800 px-3 py-2 text-xs text-gray-200">{instruction}</div>
      {files == null ? (
        <div className="px-3 py-4 text-xs text-gray-500">Loading the files from this run…</div>
      ) : reviewFiles.length === 0 ? (
        <div className="px-3 py-4 text-xs text-gray-500">This run has no file changes to review.</div>
      ) : (
        <>
          <div className="flex max-h-28 flex-shrink-0 flex-wrap gap-1 overflow-y-auto border-b border-gray-800 px-3 py-2">
            {reviewFiles.map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => setPicked(file.path)}
                className={`rounded border px-2 py-0.5 text-left font-mono text-[10px] ${
                  file.path === selectedPath
                    ? "border-blue-500 bg-blue-950/60 text-blue-100"
                    : "border-gray-700 text-gray-300 hover:border-gray-500"
                }`}
              >
                {file.path}
                <span className="ml-1 text-[9px] uppercase text-gray-500">{file.status}</span>
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-auto bg-gray-950">
            {selected?.patch ? (
              <DiffText patch={selected.patch} />
            ) : (
              <div className="px-3 py-4 text-[11px] text-gray-500">No text diff for this file.</div>
            )}
            {selected?.truncated && (
              <div className="px-3 pb-3 text-[10px] text-amber-300">This file is truncated. The rest is in the repo.</div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ── Tabbed content (used both as compact strip and full view) ──

function SessionTabs({
  sessionId, tab, setTab, artifact, events, log, session, evidence, loadSupervision, loadEvents, loadLog, compact,
  activeRun = 0, runCount = 1, activeFollowup = null, runDiff = null, onSelectRun,
}: {
  sessionId: string
  tab: "artifact" | "summary" | "timeline" | "log"
  setTab: (t: "artifact" | "summary" | "timeline" | "log") => void
  artifact: { source: string; html: string } | null
  events: SessionEvent[] | null
  log: string | null
  session: SessionData
  evidence: SessionEvidence[]
  loadSupervision: () => Promise<void>
  loadEvents: () => void
  loadLog: () => void
  compact?: boolean
  activeRun?: number
  runCount?: number
  activeFollowup?: { instruction: string; created_at?: string | null } | null
  runDiff?: SessionDiffFile[] | null
  onSelectRun?: (index: number | null) => void
}) {
  const showingFollowup = activeRun > 0
  return (
    <div className={compact ? "" : "flex-1 flex flex-col min-h-0"}>
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-3 pt-1 flex-shrink-0 border-b border-gray-800">
        {(["artifact", "summary", "timeline", "log"] as const).map((t) => (
          <button
            key={t}
            onClick={() => {
              setTab(t)
              if (t === "timeline" && events === null) loadEvents()
              if (t === "log" && log === null) loadLog()
            }}
            className={`px-2.5 py-1.5 text-[11px] font-medium border-b-2 transition-colors ${
              tab === t ? "border-blue-500 text-white" : "border-transparent text-gray-500 hover:text-gray-300"
            }`}
          >
            {t === "artifact"
              ? (showingFollowup ? "This run" : "Review")
              : t === "summary"
                ? "Details & Evidence"
                : t === "log"
                  ? "Raw log"
                  : "Timeline"}
          </button>
        ))}
        {artifact?.source === "custom" && !showingFollowup && (
          <span className="ml-auto rounded bg-purple-500/20 px-2 py-0.5 text-[10px] text-purple-300">
            Custom Agent Artifact
          </span>
        )}
        {runCount > 1 && onSelectRun && (
          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              disabled={activeRun === 0}
              onClick={() => onSelectRun(activeRun - 1)}
              className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 disabled:opacity-30"
            >
              Previous run
            </button>
            <span className="text-[10px] text-gray-300">
              {showingFollowup
                ? `Run ${activeRun + 1} of ${runCount}${runDiff ? ` · ${runDiff.length} files` : ""}`
                : `Run 1 of ${runCount} · review`}
            </span>
            <button
              type="button"
              disabled={activeRun >= runCount - 1}
              onClick={() => onSelectRun(activeRun + 1 >= runCount - 1 ? null : activeRun + 1)}
              className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 disabled:opacity-30"
            >
              Next run
            </button>
          </div>
        )}
      </div>

      {/* Content */}
      <div className={
        tab === "artifact" && showingFollowup && !compact
          ? "flex min-h-0 flex-1 flex-col overflow-hidden"
          : `overflow-y-auto ${tab === "artifact" && !compact ? "flex min-h-0 flex-1 flex-col" : `px-3 py-2 ${compact ? "max-h-36" : "min-h-0 flex-1"}`}`
      }>
        {tab === "artifact" && (
          showingFollowup ? (
            <RunChanges
              instruction={activeFollowup?.instruction || "Follow-up run"}
              files={runDiff}
              compact={compact}
            />
          ) : artifact?.html ? (
            <iframe
              title={`Session ${sessionId} Proof Artifact`}
              sandbox="allow-scripts"
              srcDoc={artifact.html}
              className={`w-full border-0 bg-gray-950 ${compact ? "h-32" : "flex-1 min-h-[420px]"}`}
            />
          ) : (
            <div className="p-4 text-xs text-gray-500">Building proof artifact…</div>
          )
        )}

        {tab === "summary" && (
          <div className="mx-auto max-w-4xl space-y-5 py-2 text-xs">
            <section><div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-500">Requested outcome</div><div className="whitespace-pre-wrap text-gray-200">{session.goal || session.description}</div></section>
            <section className="rounded-lg border border-gray-700 bg-gray-900 p-4"><div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">Result</div><MarkdownText content={session.output_summary || (["running", "waiting", "pending"].includes(session.status) ? "Session in progress…" : "The agent did not record a concise outcome. Review the evidence below; use the raw log only for debugging.")} /></section>
            <EvidenceSummary sessionId={sessionId} evidence={evidence} reviewMode={session.review_mode} onChanged={loadSupervision} />
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

function EvidenceSummary({ sessionId, evidence, reviewMode, onChanged }: { sessionId: string; evidence: SessionEvidence[]; reviewMode: "advisory" | "implementation"; onChanged: () => Promise<void> }) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState("")
  const [editingDescription, setEditingDescription] = useState("")
  if (reviewMode === "advisory") return <section className="rounded-lg border border-blue-900/60 bg-blue-950/10 p-4 text-gray-400"><div className="font-medium text-blue-300">Analysis-only outcome</div><div className="mt-1 text-[10px]">No files changed by this Session, so Git and test evidence are not required.</div></section>
  if (evidence.length === 0) return <section className="rounded-lg border border-dashed border-gray-700 p-4 text-gray-500"><div className="font-medium text-gray-400">Independent evidence required</div><div className="mt-1 text-[10px]">Capture Git changes and run verification before completing this Session.</div></section>
  return <section><div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">Evidence</div><div className="grid gap-2 md:grid-cols-2">{evidence.map((item) => {
    const isVerification = item.evidence_type === "verification"
    const isMedia = item.evidence_type === "media"
    const passed = isVerification && Number(item.data.exit_code) === 0
    const detail = isVerification
      ? `${String(item.data.command || "Verification")} · ${passed ? "Passed" : `Failed (exit ${String(item.data.exit_code)})`}`
      : isMedia ? `${String(item.data.filename || "Product evidence")} · ${formatBytes(Number(item.data.size || 0))}`
      : String(item.data.diff_stat || item.data.status || "Git working tree captured")
    const openMedia = async () => { const blob = await BLOB(`/sessions/${sessionId}/evidence/${item.id}/file`); window.open(URL.createObjectURL(blob), "_blank", "noopener,noreferrer") }
    const saveEvidence = async () => { if (!editingTitle.trim()) return; await PATCH(`/sessions/${sessionId}/evidence/${item.id}`, { title: editingTitle.trim(), note: editingDescription.trim() }); setEditingId(null); await onChanged() }
    const remove = async () => { if (!window.confirm(`Delete evidence “${item.title}”?`)) return; await DELETE(`/sessions/${sessionId}/evidence/${item.id}`); await onChanged() }
    return <div key={item.id} className="rounded-lg border border-gray-700 bg-gray-900 p-3">
      {editingId === item.id ? (
        <div className="space-y-2">
          <label className="block text-[10px] text-gray-500">Display name<input autoFocus value={editingTitle} onChange={(event) => setEditingTitle(event.target.value)} className="mt-1 w-full rounded border border-gray-600 bg-gray-950 px-2 py-1 text-xs text-gray-200" /></label>
          <label className="block text-[10px] text-gray-500">Description<textarea value={editingDescription} onChange={(event) => setEditingDescription(event.target.value)} placeholder="What does this evidence demonstrate?" rows={3} className="mt-1 w-full resize-y rounded border border-gray-600 bg-gray-950 px-2 py-1 text-xs text-gray-200" /></label>
          <div className="flex justify-end gap-3"><button onClick={() => setEditingId(null)} className="text-[10px] text-gray-500">Cancel</button><button onClick={() => { void saveEvidence() }} className="text-[10px] text-green-400">Save evidence</button></div>
        </div>
      ) : (
        <div className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${isVerification ? passed ? "bg-green-500" : "bg-red-500" : isMedia ? "bg-purple-500" : "bg-blue-500"}`} /><span className="font-medium text-gray-200">{item.title}</span>{isMedia && <button onClick={() => { void openMedia() }} className="ml-auto text-[10px] text-blue-400">Open</button>}{isMedia && <button onClick={() => { setEditingId(item.id); setEditingTitle(item.title); setEditingDescription(String(item.data.note || "")) }} className="text-[10px] text-gray-400">Edit</button>}<button onClick={() => { void remove() }} className={`${isMedia ? "" : "ml-auto"} text-[10px] text-red-500`}>Delete</button></div>
      )}
      {isMedia && editingId !== item.id && Boolean(item.data.note) && <p className="mt-2 whitespace-pre-wrap text-xs text-gray-400">{String(item.data.note)}</p>}
      <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-gray-500">{detail}</pre>
    </div>
  })}</div></section>
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function apiErrorDetail(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : String(error || "")
  return message.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] || message.replace(/^API \d+:\s*/, "") || fallback
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
