import { useState, useCallback, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { GET, POST } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { useProjectStore } from "@/stores/project-store"
import { useMountEffect } from "@/hooks/use-mount-effect"

export const SESSION_STATUS = {
  PROPOSED: "proposed",
  PENDING: "pending",
  RUNNING: "running",
  WAITING: "waiting",
  VERIFYING: "verifying",
  DONE: "done",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
} as const

export type SessionStatusValue = (typeof SESSION_STATUS)[keyof typeof SESSION_STATUS]

export type AttentionState = "needs_attention" | "running" | "staged" | "accepted" | "closed"

export function getSessionAttentionState(status: string): AttentionState {
  if (status === "waiting" || status === "verifying" || status === "failed") {
    return "needs_attention"
  }
  if (status === "pending" || status === "running") {
    return "running"
  }
  if (status === "proposed") {
    return "staged"
  }
  if (status === "completed") {
    return "accepted"
  }
  return "closed"
}

export interface SessionInfo {
  id: string
  channel_id: string
  project: string
  terminal_ids: string[]
  task_id?: string | null
  parent_session_id?: string | null
  description: string
  goal?: string
  status: SessionStatusValue
  attention_state?: AttentionState
  collection_id?: string
  collection_role?: "unsorted" | "own" | "client"
  collection_name?: string
  agent_provider: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  output_summary: string | null
  acceptance_criteria?: string[]
}

export interface GoalCard {
  id: string
  goal: string
  project: string
  channel_id: string
  collection_id?: string
  collection_role?: "unsorted" | "own" | "client"
  collection_name?: string
  acceptance_criteria?: string[]
  attention_state: AttentionState
  latestSession: SessionInfo
  sessions: SessionInfo[]
  runsCount: number
}

export function groupSessionsIntoGoalCards(sessions: SessionInfo[]): GoalCard[] {
  const sorted = [...sessions].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  )

  const groups = new Map<string, SessionInfo[]>()

  for (const session of sorted) {
    let groupKey = ""
    if (session.task_id) {
      groupKey = `task:${session.task_id}`
    } else if (session.parent_session_id) {
      for (const [k, sessList] of groups.entries()) {
        if (sessList.some((s) => s.id === session.parent_session_id)) {
          groupKey = k
          break
        }
      }
    }

    if (!groupKey) {
      const normGoal = (session.goal || session.description || "").trim().toLowerCase()
      groupKey = `goal:${session.channel_id}:::${session.project}:::${normGoal}`
    }

    const list = groups.get(groupKey) || []
    list.push(session)
    groups.set(groupKey, list)
  }

  const cards: GoalCard[] = []
  for (const [key, sessList] of groups.entries()) {
    sessList.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    const latest = sessList[sessList.length - 1]

    const hasNeedsAttention = sessList.some(
      (s) => getSessionAttentionState(s.status) === "needs_attention"
    )
    const hasRunning = sessList.some(
      (s) => getSessionAttentionState(s.status) === "running"
    )

    let cardAttention: AttentionState
    if (hasNeedsAttention) {
      cardAttention = "needs_attention"
    } else if (hasRunning) {
      cardAttention = "running"
    } else {
      cardAttention = getSessionAttentionState(latest.status)
    }

    cards.push({
      id: key,
      goal: latest.goal || latest.description,
      project: latest.project,
      channel_id: latest.channel_id,
      collection_id: latest.collection_id,
      collection_role: latest.collection_role,
      collection_name: latest.collection_name,
      acceptance_criteria: latest.acceptance_criteria,
      attention_state: cardAttention,
      latestSession: latest,
      sessions: sessList,
      runsCount: sessList.length,
    })
  }

  cards.sort(
    (a, b) =>
      new Date(b.latestSession.updated_at || b.latestSession.created_at).getTime() -
      new Date(a.latestSession.updated_at || a.latestSession.created_at).getTime()
  )

  return cards
}

export function SessionsCard({
  onOpenTerminal,
}: {
  onOpenTerminal?: (terminalId: string) => void
  scope?: "workstream" | "all"
}) {
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const selectChannel = useChannelStore((s) => s.selectChannel)
  const channels = useChannelStore((s) => s.channels)
  const collections = useProjectStore((s) => s.collections)
  const currentCollection = useProjectStore((s) => s.currentCollection)
  const projects = useProjectStore((s) => s.projects)
  const openSessionFromHub = useUIStore((s) => s.openSessionFromHub)
  const toast = useUIStore((s) => s.toast)
  const setNeedsAttentionCount = useUIStore((s) => s.setNeedsAttentionCount)

  // System vs Focused Workstream Mode
  const activeChannel = channels.find((channel) => channel.id === activeChannelId)
  const isSystemWorkstream = activeChannelId === "ch-system"

  // Client info for the active workstream
  const activeChannelClient = activeChannel
    ? collections.find((c) => c.id === activeChannel.collection_id)
    : null

  // Focus & Filter controls (for #system Overview Mode)
  const groupBy = useUIStore((s) => s.sessionsHubGroupBy)
  const setGroupBy = useUIStore((s) => s.setSessionsHubGroupBy)

  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [showOptions, setShowOptions] = useState(false)
  const [showClosed, setShowClosed] = useState(false)
  const [creating, setCreating] = useState(false)
  const [startingSessionId, setStartingSessionId] = useState<string | null>(null)

  const [draft, setDraft] = useState({ project: "", goal: "", criteria: "", provider: "orchestrated" })
  const [providerOptions, setProviderOptions] = useState<Array<{ id: string; label: string; ready?: boolean }>>([
    { id: "codex", label: "Codex CLI" },
    { id: "claude", label: "Claude Code" },
    { id: "cursor", label: "Cursor Agent" },
    { id: "diagnostic", label: "Diagnostic (no writes)" },
  ])

  // Multi-project support for workstream
  const workstreamProjects = activeChannel?.project_names ?? []
  const availableProjects = workstreamProjects.length > 0
    ? projects.filter((p) => workstreamProjects.includes(p.name))
    : projects
  const selectedProject = draft.project || availableProjects[0]?.name || (projects[0]?.name ?? "")

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await GET<SessionInfo[]>("/sessions")
      const list = data ?? []
      setSessions(list)
      const cards = groupSessionsIntoGoalCards(list)
      const attnCount = cards.filter((c) => c.attention_state === "needs_attention").length
      setNeedsAttentionCount(attnCount)
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [setNeedsAttentionCount])

  const loadRef = useRef(load)
  loadRef.current = load

  useMountEffect(() => {
    load()
    void useProjectStore.getState().loadCollections()
    void GET<{ providers: Array<{ id: string; label: string; ready: boolean }> }>("/settings/agent-providers")
      .then((res) => {
        if (res?.providers?.length) setProviderOptions(res.providers)
      })
      .catch(() => {})
  })

  useMountEffect(() => {
    const interval = window.setInterval(() => {
      void loadRef.current()
    }, 5000)
    return () => window.clearInterval(interval)
  })

  // Start a proposed (staged) session
  const startSession = async (sessionId: string) => {
    setStartingSessionId(sessionId)
    try {
      await POST(`/sessions/${sessionId}/start`)
      toast("Session launched into execution", "success")
      await load()
      openSessionFromHub(sessionId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err || "")
      const detail = msg.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] || msg.replace(/^API \d+:\s*/, "") || "Could not start session"
      toast(detail, "error")
    } finally {
      setStartingSessionId(null)
    }
  }

  // Create session (either staged or immediately started)
  const handleCreate = async (asStaged: boolean) => {
    const targetChannelId = activeChannelId || channels.find((c) => !c.archived_at)?.id
    if (!targetChannelId || !selectedProject || !draft.goal.trim()) return
    setCreating(true)
    try {
      const created = await POST<SessionInfo>("/sessions", {
        channel_id: targetChannelId,
        project: selectedProject,
        goal: draft.goal.trim(),
        acceptance_criteria: draft.criteria.split("\n").map((item) => item.trim()).filter(Boolean),
        provider: draft.provider,
        status: asStaged ? "proposed" : "pending",
      })
      setDraft((value) => ({ ...value, goal: "", criteria: "" }))
      setShowCreate(false)
      setShowOptions(false)

      if (!asStaged) {
        try {
          await POST(`/sessions/${created.id}/start`)
        } catch {
          /* start error handled in poll */
        }
      }
      toast(asStaged ? "Goal staged on board" : "Session started", "success")
      await load()
      if (!asStaged) openSessionFromHub(created.id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err || "")
      const detail = msg.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] || msg.replace(/^API \d+:\s*/, "") || "Could not create session"
      toast(detail, "error")
    } finally {
      setCreating(false)
    }
  }

  // Start a new follow-up run on an existing Goal Card
  const handleFollowupRun = async (card: GoalCard) => {
    setCreating(true)
    try {
      const taskId = card.id.startsWith("task:") ? card.id.replace(/^task:/, "") : undefined
      const created = await POST<SessionInfo>("/sessions", {
        channel_id: card.channel_id,
        project: card.project,
        goal: card.goal,
        status: "pending",
        task_id: taskId,
        parent_session_id: card.latestSession.id,
        provider: card.latestSession.agent_provider || undefined,
      })
      try {
        await POST(`/sessions/${created.id}/start`)
      } catch {
        /* ignore */
      }
      toast(`Follow-up Run #${card.runsCount + 1} launched`, "success")
      await load()
      openSessionFromHub(created.id)
    } catch {
      toast("Could not launch follow-up run", "error")
    } finally {
      setCreating(false)
    }
  }

  // 1. Group all sessions into Goal Cards
  const allGoalCards = groupSessionsIntoGoalCards(sessions)

  // 2. Filter Goal Cards based on operating mode:
  // - If in System Overview Mode (isSystemWorkstream, ch-system): roll up every collection, or currentCollection if selected
  // - If in Focused Workstream Mode (!isSystemWorkstream): strictly this channel's cards
  // - If channel is missing: empty board
  const filteredGoalCards = isSystemWorkstream
    ? (currentCollection
        ? allGoalCards.filter((card) => (card.collection_id || "general") === currentCollection)
        : allGoalCards)
    : (activeChannel
        ? allGoalCards.filter((card) => card.channel_id === activeChannel.id)
        : [])

  // Attention buckets for Goal Cards
  const needsAttention = filteredGoalCards.filter((c) => c.attention_state === "needs_attention")
  const running = filteredGoalCards.filter((c) => c.attention_state === "running")
  const staged = filteredGoalCards.filter((c) => c.attention_state === "staged")
  const accepted = filteredGoalCards.filter((c) => c.attention_state === "accepted")
  const closed = filteredGoalCards.filter((c) => c.attention_state === "closed")

  // Client groups (for #system Overview Mode)
  const clientGroups = collections
    .map((c) => {
      const clientCards = filteredGoalCards.filter((card) => (card.collection_id || "general") === c.id)
      return {
        collection: c,
        cards: clientCards,
        needsAttention: clientCards.filter((card) => card.attention_state === "needs_attention"),
        running: clientCards.filter((card) => card.attention_state === "running"),
        staged: clientCards.filter((card) => card.attention_state === "staged"),
        accepted: clientCards.filter((card) => card.attention_state === "accepted"),
      }
    })
    .filter((g) => g.cards.length > 0)

  // Client badge styling
  const clientBadgeColor =
    activeChannelClient?.role === "client"
      ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
      : activeChannelClient?.role === "own"
        ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
        : "bg-gray-800 text-gray-400 border-gray-700"

  return (
    <div className="space-y-4">
      {/* ── MODE A: Focused Workstream Header Toolbar ── */}
      {!isSystemWorkstream && activeChannel && (
        <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-lg border border-gray-800 bg-gray-900/90 p-2.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-gray-200">{activeChannel.name}</span>
            {activeChannelClient && (
              <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${clientBadgeColor}`}>
                {activeChannelClient.name}
              </span>
            )}
            {workstreamProjects.length > 0 && (
              <span className="text-[10px] text-gray-400 font-mono">
                [{workstreamProjects.join(", ")}]
              </span>
            )}
          </div>

          <button
            type="button"
            onClick={() => setShowCreate((v) => !v)}
            className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 transition-colors"
          >
            {showCreate ? "Close Form" : "+ New Goal"}
          </button>
        </div>
      )}

      {/* ── MODE B: #system Executive Overview Toolbar ── */}
      {isSystemWorkstream && (
        <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-lg border border-gray-800 bg-gray-900/90 p-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 pr-2 border-r border-gray-800">
              <span className="text-xs font-bold text-gray-200">HomeBase Overview</span>
              <span className="text-[10px] text-gray-500 font-mono">
                {currentCollection
                  ? `(${collections.find((c) => c.id === currentCollection)?.name || currentCollection})`
                  : "(All Workstreams)"}
              </span>
            </div>

            {/* Grouping Toggle */}
            <button
              type="button"
              onClick={() => setGroupBy(groupBy === "client_workstream" ? "none" : "client_workstream")}
              className={`rounded border px-2 py-1 text-xs transition-colors ${
                groupBy === "client_workstream"
                  ? "border-blue-500/50 bg-blue-500/10 text-blue-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:text-gray-200"
              }`}
              title="Toggle between Client grouping and Flat Attention Board"
            >
              {groupBy === "client_workstream" ? "Grouped" : "Flat"}
            </button>
          </div>
        </div>
      )}

      {/* ── New Goal Form ── */}
      {showCreate && !isSystemWorkstream && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void handleCreate(false)
          }}
          className="space-y-3 rounded-lg border border-blue-500/40 bg-gray-800/95 p-4 shadow-xl"
        >
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-gray-200">What outcome should be accomplished?</span>
            <textarea
              autoFocus
              required
              rows={3}
              value={draft.goal}
              onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
              placeholder="e.g. Implement resilient OAuth refresh token backoff and write regression test..."
              className="w-full resize-y rounded border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-blue-500 focus:outline-none"
            />
          </label>

          <button
            type="button"
            onClick={() => setShowOptions((v) => !v)}
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            {showOptions ? "Hide Options ▴" : "Options (Criteria, Provider, Project) ▾"}
          </button>

          {showOptions && (
            <div className="space-y-2.5 border-t border-gray-700 pt-3">
              {workstreamProjects.length !== 1 && (
                <div>
                  <span className="mb-1 block text-[11px] text-gray-400">Target Project</span>
                  <select
                    required
                    value={selectedProject}
                    onChange={(e) => setDraft({ ...draft, project: e.target.value })}
                    className="w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-200"
                  >
                    {availableProjects.map((p) => (
                      <option key={p.name} value={p.name}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <span className="mb-1 block text-[11px] text-gray-400">Acceptance Criteria (one per line)</span>
                <textarea
                  rows={2}
                  value={draft.criteria}
                  onChange={(e) => setDraft({ ...draft, criteria: e.target.value })}
                  placeholder="pytest tests/test_auth.py passes&#10;Zero unhandled token refresh exceptions"
                  className="w-full resize-y rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-200"
                />
              </div>

              <div>
                <span className="mb-1 block text-[11px] text-gray-400">Agent Provider</span>
                <select
                  value={draft.provider}
                  onChange={(e) => setDraft({ ...draft, provider: e.target.value })}
                  className="w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-200"
                >
                  <option value="orchestrated">Orchestrated (Server default)</option>
                  {providerOptions.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          <div className="flex items-center justify-between pt-1">
            <span className="text-[10px] text-gray-400">Target Project: {selectedProject || "None"}</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={creating || !draft.goal.trim() || !selectedProject}
                onClick={() => void handleCreate(true)}
                className="rounded border border-purple-500/50 bg-purple-950/40 px-3 py-1.5 text-xs font-medium text-purple-300 hover:bg-purple-900/50 disabled:opacity-50"
                title="Stage goal on board without immediately launching an agent"
              >
                ⚪ Stage Goal
              </button>
              <button
                type="submit"
                disabled={creating || !draft.goal.trim() || !selectedProject}
                className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {creating ? "Launching…" : "⚡ Start Session"}
              </button>
            </div>
          </div>
        </form>
      )}

      {/* ── Attention Board Rendering ── */}
      {loading && sessions.length === 0 && (
        <p className="py-8 text-center text-xs text-gray-500">Loading Attention Board…</p>
      )}

      {!loading && filteredGoalCards.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-800 p-8 text-center text-xs text-gray-500">
          {isSystemWorkstream
            ? "No goal cards found across HomeBase. Select a workstream or click \"+ New Goal\"."
            : `No goals found for ${activeChannel?.name || "this workstream"}. Click "+ New Goal" to stage or start a goal.`}
        </div>
      )}

      {/* When in #system Overview and Grouped by Client */}
      {isSystemWorkstream && groupBy === "client_workstream" && clientGroups.length > 0 && (
        <div className="space-y-6">
          {clientGroups.map((group) => {
            const roleLabel =
              group.collection.role === "own"
                ? "Company"
                : group.collection.role === "client"
                  ? "Client"
                  : group.collection.id === "general"
                    ? "Unassigned"
                    : "Unsorted"
            const roleBadgeColor =
              group.collection.role === "own"
                ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
                : group.collection.role === "client"
                  ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
                  : "bg-gray-800 text-gray-400 border-gray-700"

            return (
              <div key={group.collection.id} className="space-y-2.5">
                <div className="flex items-center gap-2 border-b border-gray-800 pb-1.5">
                  <h3 className="text-xs font-bold text-gray-200">{group.collection.name}</h3>
                  <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${roleBadgeColor}`}>
                    {roleLabel}
                  </span>
                  <span className="text-[10px] text-gray-500">({group.cards.length} goals)</span>
                </div>

                <AttentionColumnsGrid
                  needsAttention={group.needsAttention}
                  running={group.running}
                  staged={group.staged}
                  accepted={group.accepted}
                  onOpenSession={openSessionFromHub}
                  onStartSession={startSession}
                  onNewRun={handleFollowupRun}
                  onSelectWorkstream={selectChannel}
                  startingSessionId={startingSessionId}
                  onOpenTerminal={onOpenTerminal}
                />
              </div>
            )
          })}
        </div>
      )}

      {/* Flat Attention Board Grid (Used in Focused Workstream Mode OR Flat Overview Mode) */}
      {(!isSystemWorkstream || groupBy === "none") && filteredGoalCards.length > 0 && (
        <AttentionColumnsGrid
          needsAttention={needsAttention}
          running={running}
          staged={staged}
          accepted={accepted}
          onOpenSession={openSessionFromHub}
          onStartSession={startSession}
          onNewRun={handleFollowupRun}
          onSelectWorkstream={selectChannel}
          startingSessionId={startingSessionId}
          onOpenTerminal={onOpenTerminal}
        />
      )}

      {/* Collapsed Closed Goals */}
      {closed.length > 0 && (
        <div className="border-t border-gray-800 pt-3">
          <button
            type="button"
            onClick={() => setShowClosed((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <span>{showClosed ? "▾" : "▸"}</span>
            <span>Closed / Cancelled ({closed.length})</span>
          </button>
          {showClosed && (
            <div className="mt-2 space-y-1.5">
              {closed.map((card) => (
                <GoalCardItem
                  key={card.id}
                  card={card}
                  onOpenSession={openSessionFromHub}
                  onStartSession={startSession}
                  onNewRun={handleFollowupRun}
                  onSelectWorkstream={selectChannel}
                  startingSessionId={startingSessionId}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AttentionColumnsGrid({
  needsAttention,
  running,
  staged,
  accepted,
  onOpenSession,
  onStartSession,
  onNewRun,
  onSelectWorkstream,
  startingSessionId,
  onOpenTerminal,
}: {
  needsAttention: GoalCard[]
  running: GoalCard[]
  staged: GoalCard[]
  accepted: GoalCard[]
  onOpenSession: (id: string) => void
  onStartSession: (id: string) => void
  onNewRun: (card: GoalCard) => void
  onSelectWorkstream?: (channelId: string) => void
  startingSessionId: string | null
  onOpenTerminal?: (terminalId: string) => void
}) {
  return (
    <div className="grid gap-3 lg:grid-cols-4">
      {/* 🔴 Needs Attention Column */}
      <div className="flex flex-col rounded-lg border border-amber-500/30 bg-amber-950/10 p-2.5">
        <div className="mb-2 flex items-center justify-between border-b border-amber-500/20 pb-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-amber-300">
            <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
            Needs Attention
          </span>
          <span className="rounded-full bg-amber-500/20 px-1.5 py-0.2 text-[10px] font-bold text-amber-300">
            {needsAttention.length}
          </span>
        </div>
        <div className="flex-1 space-y-2 overflow-y-auto">
          {needsAttention.length === 0 ? (
            <p className="py-4 text-center text-[11px] text-gray-500">All clear</p>
          ) : (
            needsAttention.map((card) => (
              <GoalCardItem
                key={card.id}
                card={card}
                onOpenSession={onOpenSession}
                onStartSession={onStartSession}
                onNewRun={onNewRun}
                onSelectWorkstream={onSelectWorkstream}
                startingSessionId={startingSessionId}
                onOpenTerminal={onOpenTerminal}
              />
            ))
          )}
        </div>
      </div>

      {/* 🟡 Running Column */}
      <div className="flex flex-col rounded-lg border border-blue-500/20 bg-gray-900/50 p-2.5">
        <div className="mb-2 flex items-center justify-between border-b border-gray-800 pb-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-blue-300">
            <span className="h-2 w-2 rounded-full bg-blue-400" />
            Running
          </span>
          <span className="rounded-full bg-blue-500/20 px-1.5 py-0.2 text-[10px] font-bold text-blue-300">
            {running.length}
          </span>
        </div>
        <div className="flex-1 space-y-2 overflow-y-auto">
          {running.length === 0 ? (
            <p className="py-4 text-center text-[11px] text-gray-500">No active runs</p>
          ) : (
            running.map((card) => (
              <GoalCardItem
                key={card.id}
                card={card}
                onOpenSession={onOpenSession}
                onStartSession={onStartSession}
                onNewRun={onNewRun}
                onSelectWorkstream={onSelectWorkstream}
                startingSessionId={startingSessionId}
                onOpenTerminal={onOpenTerminal}
              />
            ))
          )}
        </div>
      </div>

      {/* ⚪ Staged Column */}
      <div className="flex flex-col rounded-lg border border-purple-500/20 bg-gray-900/50 p-2.5">
        <div className="mb-2 flex items-center justify-between border-b border-gray-800 pb-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-purple-300">
            <span className="h-2 w-2 rounded-full bg-purple-400" />
            Staged
          </span>
          <span className="rounded-full bg-purple-500/20 px-1.5 py-0.2 text-[10px] font-bold text-purple-300">
            {staged.length}
          </span>
        </div>
        <div className="flex-1 space-y-2 overflow-y-auto">
          {staged.length === 0 ? (
            <p className="py-4 text-center text-[11px] text-gray-500">None staged</p>
          ) : (
            staged.map((card) => (
              <GoalCardItem
                key={card.id}
                card={card}
                onOpenSession={onOpenSession}
                onStartSession={onStartSession}
                onNewRun={onNewRun}
                onSelectWorkstream={onSelectWorkstream}
                startingSessionId={startingSessionId}
                onOpenTerminal={onOpenTerminal}
              />
            ))
          )}
        </div>
      </div>

      {/* 🟢 Accepted Column */}
      <div className="flex flex-col rounded-lg border border-emerald-500/20 bg-gray-900/50 p-2.5">
        <div className="mb-2 flex items-center justify-between border-b border-gray-800 pb-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            Accepted
          </span>
          <span className="rounded-full bg-emerald-500/20 px-1.5 py-0.2 text-[10px] font-bold text-emerald-300">
            {accepted.length}
          </span>
        </div>
        <div className="flex-1 space-y-2 overflow-y-auto">
          {accepted.length === 0 ? (
            <p className="py-4 text-center text-[11px] text-gray-500">No accepted goals</p>
          ) : (
            accepted.map((card) => (
              <GoalCardItem
                key={card.id}
                card={card}
                onOpenSession={onOpenSession}
                onStartSession={onStartSession}
                onNewRun={onNewRun}
                onSelectWorkstream={onSelectWorkstream}
                startingSessionId={startingSessionId}
                onOpenTerminal={onOpenTerminal}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function GoalCardItem({
  card,
  onOpenSession,
  onStartSession,
  onNewRun,
  onSelectWorkstream,
  startingSessionId,
  onOpenTerminal,
}: {
  card: GoalCard
  onOpenSession: (id: string) => void
  onStartSession: (id: string) => void
  onNewRun?: (card: GoalCard) => void
  onSelectWorkstream?: (channelId: string) => void
  startingSessionId: string | null
  onOpenTerminal?: (terminalId: string) => void
}) {
  const latest = card.latestSession
  const goalText = card.goal || "Untitled Goal"
  const isVerifying = latest.status === "verifying"
  const isWaiting = latest.status === "waiting"
  const isFailed = latest.status === "failed"
  const isProposed = latest.status === "proposed"
  const isStarting = startingSessionId === latest.id

  const badgeStyle = isVerifying
    ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
    : isWaiting
      ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/40"
      : isFailed
        ? "bg-red-500/20 text-red-300 border-red-500/40"
        : isProposed
          ? "bg-purple-500/20 text-purple-300 border-purple-500/40"
          : latest.status === "completed"
            ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
            : "bg-blue-500/20 text-blue-300 border-blue-500/40"

  const statusText = isVerifying
    ? "Ready for Review"
    : isWaiting
      ? "Waiting for Input"
      : isFailed
        ? "Run Failed"
        : isProposed
          ? "Staged"
          : latest.status

  const channels = useChannelStore.getState().channels
  const ch = channels.find((c) => c.id === card.channel_id)
  const channelName = ch ? ch.name.replace(/^#+/, "") : null

  return (
    <div
      onClick={() => onOpenSession(latest.id)}
      className="group cursor-pointer rounded-md border border-gray-800 bg-gray-900/90 p-2.5 transition-all hover:border-gray-700 hover:bg-gray-850"
    >
      {/* Badges: Project, Channel, Status */}
      <div className="flex items-start justify-between gap-1.5">
        <div className="flex items-center gap-1.5 min-w-0 flex-1">
          <span className="text-[10px] font-semibold text-gray-400 truncate max-w-[100px]">{card.project}</span>
          {channelName && (
            <button
              type="button"
              onClick={(e) => {
                if (onSelectWorkstream) {
                  e.stopPropagation()
                  onSelectWorkstream(card.channel_id)
                }
              }}
              className="text-[9px] px-1.5 py-0.2 rounded bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 font-mono truncate max-w-[90px] transition-colors"
              title={`Switch to #${channelName}`}
            >
              #{channelName}
            </button>
          )}
        </div>
        <span className={`rounded border px-1.5 py-0.5 text-[9px] font-medium leading-none flex-shrink-0 ${badgeStyle}`}>
          {statusText}
        </span>
      </div>

      {/* Goal Title */}
      <p className="mt-1 line-clamp-2 text-xs font-medium text-gray-200 group-hover:text-white" title={goalText}>
        {goalText}
      </p>

      {/* Multiple Runs Badge and Chips */}
      {card.runsCount > 1 && (
        <div className="mt-2 flex items-center gap-1 border-t border-gray-800/60 pt-1.5">
          <span className="text-[9px] text-gray-500 font-medium">Runs ({card.runsCount}):</span>
          <div className="flex items-center gap-1 overflow-x-auto py-0.5">
            {card.sessions.map((s, idx) => {
              const runNum = idx + 1
              const isLatest = idx === card.sessions.length - 1
              const st = s.status
              const runColor =
                st === "completed"
                  ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/30"
                  : st === "failed"
                    ? "bg-red-500/20 text-red-300 border-red-500/30"
                    : st === "verifying"
                      ? "bg-amber-500/20 text-amber-300 border-amber-500/30"
                      : st === "running"
                        ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
                        : "bg-gray-800 text-gray-400 border-gray-700"
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpenSession(s.id)
                  }}
                  className={`rounded border px-1.5 py-0.5 text-[9px] font-mono transition-colors hover:brightness-125 ${runColor} ${
                    isLatest ? "font-bold ring-1 ring-blue-500/40" : ""
                  }`}
                  title={`Run #${runNum}: ${s.status}`}
                >
                  #{runNum} {st === "completed" ? "✓" : st === "failed" ? "✕" : st === "running" ? "⚡" : ""}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {latest.output_summary && latest.status === "completed" && (
        <p className="mt-1 line-clamp-1 text-[10px] text-gray-400 italic">
          {latest.output_summary.replace(/[*_#]/g, "")}
        </p>
      )}

      {/* Quick Action Bar */}
      <div className="mt-2.5 flex items-center justify-between border-t border-gray-800/80 pt-1.5">
        <span className="text-[9px] text-gray-500">
          {latest.agent_provider || "orchestrated"}
        </span>

        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          {isProposed && (
            <button
              type="button"
              disabled={isStarting}
              onClick={() => onStartSession(latest.id)}
              className="rounded bg-purple-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-purple-500 disabled:opacity-50"
            >
              {isStarting ? "Starting…" : "▶ Start"}
            </button>
          )}

          {isVerifying && (
            <button
              type="button"
              onClick={() => onOpenSession(latest.id)}
              className="rounded bg-amber-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-amber-500"
            >
              🔍 Review
            </button>
          )}

          {isWaiting && (
            <button
              type="button"
              onClick={() => {
                if (latest.terminal_ids?.[0] && onOpenTerminal) {
                  onOpenTerminal(latest.terminal_ids[0])
                } else {
                  onOpenSession(latest.id)
                }
              }}
              className="rounded bg-yellow-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-yellow-500"
            >
              💬 Input
            </button>
          )}

          {isFailed && (
            <>
              <button
                type="button"
                disabled={isStarting}
                onClick={() => onStartSession(latest.id)}
                className="rounded bg-red-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-red-500 disabled:opacity-50"
              >
                ↺ Retry
              </button>
              {onNewRun && (
                <button
                  type="button"
                  onClick={() => onNewRun(card)}
                  className="rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-300 hover:bg-gray-700"
                  title="Launch follow-up run on this goal"
                >
                  + Run
                </button>
              )}
            </>
          )}

          {latest.status === "completed" && onNewRun && (
            <button
              type="button"
              onClick={() => onNewRun(card)}
              className="rounded border border-emerald-500/40 bg-emerald-950/30 px-1.5 py-0.5 text-[10px] text-emerald-300 hover:bg-emerald-900/40"
              title="Launch follow-up run"
            >
              + Follow-up
            </button>
          )}

          {latest.status === "running" && latest.terminal_ids?.[0] && (
            <button
              type="button"
              onClick={() => {
                const tid = latest.terminal_ids[0]
                if (onOpenTerminal) onOpenTerminal(tid)
                else useTerminalStore.getState().setActiveTerminalId(tid)
              }}
              className="rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] font-mono text-gray-300 hover:bg-gray-700"
            >
              &gt;_ Term
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
