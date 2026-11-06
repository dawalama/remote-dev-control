import { useState, useCallback, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { api, GET, PATCH, POST } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { useProjectStore } from "@/stores/project-store"
import { useStateStore } from "@/stores/state-store"
import { useMountEffect } from "@/hooks/use-mount-effect"
import type { Action } from "@/types"
import { isCollectionVisible } from "@/components/collection-picker"

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
  worktree_mode?: "isolated" | "direct" | "reuse"
  worktree_path?: string | null
  worktree_branch?: string | null
  discovered_from?: string | null
  handoff_brief?: string | null
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

function ActionLinkChip({ action }: { action: Action }) {
  const url = action.preview_url || (action.port ? `http://localhost:${action.port}` : null)
  if (!url) return null
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300 hover:bg-emerald-500/20 hover:text-emerald-200 transition-colors"
      title={`Open ${action.name || action.id} (${url}) in new tab`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0" />
      <span className="truncate max-w-[140px]">{action.name || action.id}</span>
      <span className="text-[10px] text-emerald-400/70 font-mono flex-shrink-0">
        {action.preview_url ? action.preview_url.replace(/^https?:\/\//, "") : `:${action.port}`}
      </span>
      <span className="text-[10px] flex-shrink-0">↗</span>
    </a>
  )
}

interface ProjectGitInfo {
  is_git: boolean
  current_branch: string
  branches: string[]
  worktrees?: Array<{ path: string; branch?: string }>
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
  const allActions = useStateStore((s) => s.actions)
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
  const [chosenFiles, setChosenFiles] = useState<File[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [projectGitMap, setProjectGitMap] = useState<Record<string, ProjectGitInfo>>({})
  const [draft, setDraft] = useState({
    project: "",
    goal: "",
    criteria: "",
    provider: "orchestrated",
    worktreeMode: "isolated" as "isolated" | "direct",
    branchOption: "auto" as "auto" | "existing" | "new",
    selectedBranch: "",
    newBranchName: "",
  })
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

  const activeRunningActions = allActions.filter(
    (a) => a.status === "running" && workstreamProjects.includes(a.project)
  )

  const fetchGitBranches = useCallback(async (projectName: string) => {
    if (!projectName) return
    try {
      const res = await GET<ProjectGitInfo>(`/projects/${encodeURIComponent(projectName)}/branches`)
      if (res) {
        setProjectGitMap((prev) => ({ ...prev, [projectName]: res }))
        if (res.current_branch) {
          setDraft((prev) => ({
            ...prev,
            selectedBranch: prev.selectedBranch || res.current_branch,
          }))
        }
      }
    } catch {
      /* ignore */
    }
  }, [])

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
    if (selectedProject) {
      void fetchGitBranches(selectedProject)
    }
  })

  useMountEffect(() => {
    const interval = window.setInterval(() => {
      void loadRef.current()
    }, 5000)
    return () => window.clearInterval(interval)
  })

  const stagedProviderRef = useRef<Record<string, string>>({})
  const startingSessionRef = useRef<string | null>(null)

  const assignStagedAgent = async (sessionId: string, provider: string) => {
    stagedProviderRef.current[sessionId] = provider
    setSessions((prev) => prev.map((session) => (
      session.id === sessionId ? { ...session, agent_provider: provider } : session
    )))
    try {
      await PATCH(`/sessions/${sessionId}/agent`, { provider })
    } catch (err) {
      if (startingSessionRef.current === sessionId) return
      delete stagedProviderRef.current[sessionId]
      const msg = err instanceof Error ? err.message : String(err || "")
      const detail = msg.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] || msg.replace(/^API \d+:\s*/, "") || "Could not change agent"
      toast(detail, "error")
      await load()
    }
  }

  // Start a proposed (staged) session
  const startSession = async (sessionId: string) => {
    setStartingSessionId(sessionId)
    startingSessionRef.current = sessionId
    try {
      const provider = stagedProviderRef.current[sessionId]
      await POST(`/sessions/${sessionId}/start`, provider ? { provider } : undefined)
      toast("Session launched into execution", "success")
      await load()
      openSessionFromHub(sessionId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err || "")
      const detail = msg.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] || msg.replace(/^API \d+:\s*/, "") || "Could not start session"
      toast(detail, "error")
    } finally {
      if (startingSessionRef.current === sessionId) startingSessionRef.current = null
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
        worktree_mode: draft.worktreeMode,
        worktree_branch:
          draft.worktreeMode === "isolated"
            ? (draft.branchOption === "new"
                ? draft.newBranchName.trim() || undefined
                : draft.branchOption === "existing"
                ? draft.selectedBranch || undefined
                : undefined)
            : undefined,
      })

      // Step 8: Upload each chosen file as evidence on the session before starting
      if (chosenFiles.length > 0) {
        for (const file of chosenFiles) {
          try {
            const form = new FormData()
            form.append("file", file)
            form.append("note", `Attached at goal creation: ${file.name}`)
            await api(`/sessions/${created.id}/evidence/media`, { method: "POST", body: form })
          } catch {
            /* ignore individual upload error so session creation proceeds */
          }
        }
      }

      setChosenFiles([])
      setDraft((value) => ({ ...value, goal: "", criteria: "", branchOption: "auto", newBranchName: "" }))
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
  const handleFollowupRun = async (card: GoalCard, files: File[] = []) => {
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
        worktree_mode: card.latestSession.worktree_mode === "isolated" || card.latestSession.worktree_path ? "reuse" : "direct",
        worktree_branch: card.latestSession.worktree_branch || undefined,
      })
      if (files.length > 0) {
        for (const file of files) {
          try {
            const form = new FormData()
            form.append("file", file)
            form.append("note", `Attached at follow-up run: ${file.name}`)
            await api(`/sessions/${created.id}/evidence/media`, { method: "POST", body: form })
          } catch { /* ignore */ }
        }
      }
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
        : allGoalCards.filter((card) => isCollectionVisible(card.collection_id, collections)))
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
      const collChannels = channels.filter(
        (ch) => (ch.collection_id || "general") === c.id && ch.id !== "ch-system"
      )
      const wsGroups = collChannels
        .map((ch) => {
          const wsCards = clientCards.filter((card) => card.channel_id === ch.id)
          const wsRunningActions = allActions.filter(
            (a) => a.status === "running" && (ch.project_names || []).includes(a.project)
          )
          return {
            channel: ch,
            cards: wsCards,
            runningActions: wsRunningActions,
            needsAttention: wsCards.filter((card) => card.attention_state === "needs_attention"),
            running: wsCards.filter((card) => card.attention_state === "running"),
            staged: wsCards.filter((card) => card.attention_state === "staged"),
            accepted: wsCards.filter((card) => card.attention_state === "accepted"),
          }
        })
        .filter((g) => g.cards.length > 0 || g.runningActions.length > 0)

      const orphanCards = clientCards.filter((card) => !collChannels.some((ch) => ch.id === card.channel_id))
      return {
        collection: c,
        cards: clientCards,
        workstreamGroups: wsGroups,
        orphanCards,
        needsAttention: clientCards.filter((card) => card.attention_state === "needs_attention"),
        running: clientCards.filter((card) => card.attention_state === "running"),
        staged: clientCards.filter((card) => card.attention_state === "staged"),
        accepted: clientCards.filter((card) => card.attention_state === "accepted"),
      }
    })
    .filter((g) => g.cards.length > 0 || g.workstreamGroups.length > 0)

  // Client badge styling
  const clientBadgeColor =
    activeChannelClient?.role === "client"
      ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
      : activeChannelClient?.role === "own"
        ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
        : "bg-gray-800 text-gray-400 border-gray-700"

  const gitInfo = projectGitMap[selectedProject]

  return (
    <div className="space-y-4">
      {/* ── MODE A: Focused Workstream Header Toolbar ── */}
      {!isSystemWorkstream && activeChannel && (
        <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-lg border border-gray-800 bg-gray-900/90 p-2.5">
          <div className="flex flex-wrap items-center gap-2">
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
            {activeRunningActions.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 pl-2 border-l border-gray-800">
                {activeRunningActions.map((action) => (
                  <ActionLinkChip key={action.id} action={action} />
                ))}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => {
              setShowCreate((v) => {
                const next = !v
                if (next && selectedProject) void fetchGitBranches(selectedProject)
                return next
              })
            }}
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
            {showOptions ? "Hide Options ▴" : "Options (Workspace, Provider, Criteria) ▾"}
          </button>

          {showOptions && (
            <div className="space-y-2.5 border-t border-gray-700 pt-3">
              {workstreamProjects.length !== 1 && (
                <div>
                  <span className="mb-1 block text-[11px] text-gray-400">Target Project</span>
                  <select
                    required
                    value={selectedProject}
                    onChange={(e) => {
                      const nextProj = e.target.value
                      setDraft({ ...draft, project: nextProj })
                      void fetchGitBranches(nextProj)
                    }}
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

              {/* Workspace Execution Target & Branch Selection */}
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[11px] font-medium text-gray-300">Workspace Execution Target</span>
                  <span className="text-[10px] text-gray-400">
                    {draft.worktreeMode === "isolated" ? "Clean-room isolation" : "Direct working copy"}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => setDraft({ ...draft, worktreeMode: "isolated" })}
                    className={`rounded border p-2 text-left transition ${
                      draft.worktreeMode === "isolated"
                        ? "border-blue-500 bg-blue-950/40 text-blue-200"
                        : "border-gray-700 bg-gray-900/60 text-gray-400 hover:border-gray-600"
                    }`}
                  >
                    <div className="font-semibold text-gray-200">Isolated Worktree (Default)</div>
                    <div className="text-[10px] text-gray-400">Runs in isolated folder outside active checkout</div>
                  </button>

                  <button
                    type="button"
                    onClick={() => setDraft({ ...draft, worktreeMode: "direct" })}
                    className={`rounded border p-2 text-left transition ${
                      draft.worktreeMode === "direct"
                        ? "border-blue-500 bg-blue-950/40 text-blue-200"
                        : "border-gray-700 bg-gray-900/60 text-gray-400 hover:border-gray-600"
                    }`}
                  >
                    <div className="font-semibold text-gray-200">Direct Checkout</div>
                    <div className="text-[10px] text-gray-400">Runs directly in repository on active branch</div>
                  </button>
                </div>

                {draft.worktreeMode === "isolated" && gitInfo?.is_git && (
                  <div className="mt-2 space-y-1.5 rounded border border-gray-700/60 bg-gray-900/60 p-2">
                    <span className="block text-[11px] text-gray-400">Worktree Branch Target</span>
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        value={draft.branchOption}
                        onChange={(e) => {
                          const opt = e.target.value as "auto" | "existing" | "new"
                          setDraft({
                            ...draft,
                            branchOption: opt,
                            selectedBranch:
                              opt === "existing" && gitInfo.branches.length > 0
                                ? draft.selectedBranch || gitInfo.current_branch || gitInfo.branches[0]
                                : draft.selectedBranch,
                          })
                        }}
                        className="rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-200"
                      >
                        <option value="auto">Auto-generated branch (rdc/sess-...)</option>
                        {gitInfo.branches && gitInfo.branches.length > 0 && (
                          <option value="existing">Existing branch...</option>
                        )}
                        <option value="new">+ Create custom branch name...</option>
                      </select>

                      {draft.branchOption === "existing" && gitInfo.branches && (
                        <select
                          value={draft.selectedBranch || gitInfo.current_branch}
                          onChange={(e) => setDraft({ ...draft, selectedBranch: e.target.value })}
                          className="flex-1 min-w-[140px] rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-200"
                        >
                          {gitInfo.branches.map((b) => (
                            <option key={b} value={b}>
                              {b} {b === gitInfo.current_branch ? "(current HEAD)" : ""}
                            </option>
                          ))}
                        </select>
                      )}

                      {draft.branchOption === "new" && (
                        <input
                          type="text"
                          placeholder="e.g. fix/auth-token-backoff"
                          value={draft.newBranchName}
                          onChange={(e) => setDraft({ ...draft, newBranchName: e.target.value })}
                          className="flex-1 min-w-[140px] rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-200 placeholder:text-gray-500"
                        />
                      )}
                    </div>
                  </div>
                )}

                {draft.worktreeMode === "direct" && gitInfo?.is_git && (
                  <div className="mt-1 text-[11px] text-amber-300/80">
                    Agent will execute directly in <span className="font-mono text-white">{selectedProject}</span> on branch <span className="font-mono text-white">{gitInfo.current_branch || "HEAD"}</span>.
                  </div>
                )}
              </div>

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

          {/* File attachments for the goal (Step 8) */}
          <div className="space-y-1.5 rounded border border-gray-700/60 bg-gray-900/60 p-2.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-gray-300">Goal Evidence &amp; Media</span>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="text-xs text-blue-400 hover:text-blue-300"
              >
                + Attach Files
              </button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                const files = Array.from(e.target.files || [])
                if (files.length > 0) {
                  setChosenFiles((prev) => [...prev, ...files])
                }
                e.target.value = ""
              }}
            />
            {chosenFiles.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {chosenFiles.map((file, idx) => (
                  <span
                    key={`${file.name}-${idx}`}
                    className="inline-flex items-center gap-1.5 rounded bg-gray-800 px-2 py-0.5 text-[11px] text-gray-200 border border-gray-700"
                  >
                    <span className="truncate max-w-[180px]">{file.name}</span>
                    <button
                      type="button"
                      onClick={() => setChosenFiles((prev) => prev.filter((_, i) => i !== idx))}
                      className="text-gray-400 hover:text-red-400 font-bold ml-0.5"
                      title="Remove file"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-[10px] text-gray-500">Attach screenshots, logs, or specs to attach directly as session evidence.</p>
            )}
          </div>

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
                ? "Own"
                : group.collection.role === "client"
                  ? "Client"
                  : group.collection.role === "personal"
                    ? "Personal"
                    : group.collection.id === "general"
                      ? "Unclassified"
                      : "Unsorted"
            const roleBadgeColor =
              group.collection.role === "own"
                ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
                : group.collection.role === "client"
                  ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
                  : group.collection.role === "personal"
                    ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/30"
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

                {group.workstreamGroups.length > 0 ? (
                  <div className="space-y-4">
                    {group.workstreamGroups.map((wsGroup) => (
                      <div key={wsGroup.channel.id} className="rounded-lg border border-gray-800/80 bg-gray-900/60 p-2.5 space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800/60 pb-1.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={() => selectChannel(wsGroup.channel.id)}
                              className="text-xs font-bold text-gray-200 hover:text-blue-400 font-mono transition-colors"
                              title={`Open #${wsGroup.channel.name} workstream`}
                            >
                              #{wsGroup.channel.name}
                            </button>
                            {wsGroup.channel.project_names && wsGroup.channel.project_names.length > 0 && (
                              <span className="text-[10px] text-gray-500 font-mono">
                                [{wsGroup.channel.project_names.join(", ")}]
                              </span>
                            )}
                            {wsGroup.runningActions.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5 pl-2 border-l border-gray-800">
                                {wsGroup.runningActions.map((action) => (
                                  <ActionLinkChip key={action.id} action={action} />
                                ))}
                              </div>
                            )}
                          </div>
                          <span className="text-[10px] text-gray-500">
                            {wsGroup.cards.length} goal{wsGroup.cards.length === 1 ? "" : "s"}
                          </span>
                        </div>

                        {wsGroup.cards.length > 0 ? (
                          <AttentionColumnsGrid
                            needsAttention={wsGroup.needsAttention}
                            running={wsGroup.running}
                            staged={wsGroup.staged}
                            accepted={wsGroup.accepted}
                            onOpenSession={openSessionFromHub}
                            onStartSession={startSession}
                            providerOptions={providerOptions}
                            onAssignProvider={assignStagedAgent}
                            onNewRun={handleFollowupRun}
                            onSelectWorkstream={selectChannel}
                            startingSessionId={startingSessionId}
                            onOpenTerminal={onOpenTerminal}
                          />
                        ) : (
                          <p className="py-2 text-center text-[10px] text-gray-600">No active goals on this workstream</p>
                        )}
                      </div>
                    ))}
                    {group.orphanCards.length > 0 && (
                      <div className="rounded-lg border border-gray-800/80 bg-gray-900/40 p-2.5 space-y-2">
                        <div className="border-b border-gray-800/60 pb-1.5">
                          <span className="text-xs font-medium text-gray-400">Other Goals</span>
                        </div>
                        <AttentionColumnsGrid
                          needsAttention={group.orphanCards.filter((card) => card.attention_state === "needs_attention")}
                          running={group.orphanCards.filter((card) => card.attention_state === "running")}
                          staged={group.orphanCards.filter((card) => card.attention_state === "staged")}
                          accepted={group.orphanCards.filter((card) => card.attention_state === "accepted")}
                          onOpenSession={openSessionFromHub}
                          onStartSession={startSession}
                          providerOptions={providerOptions}
                          onAssignProvider={assignStagedAgent}
                          onNewRun={handleFollowupRun}
                          onSelectWorkstream={selectChannel}
                          startingSessionId={startingSessionId}
                          onOpenTerminal={onOpenTerminal}
                        />
                      </div>
                    )}
                  </div>
                ) : (
                  <AttentionColumnsGrid
                    needsAttention={group.needsAttention}
                    running={group.running}
                    staged={group.staged}
                    accepted={group.accepted}
                    onOpenSession={openSessionFromHub}
                    onStartSession={startSession}
                    providerOptions={providerOptions}
                    onAssignProvider={assignStagedAgent}
                    onNewRun={handleFollowupRun}
                    onSelectWorkstream={selectChannel}
                    startingSessionId={startingSessionId}
                    onOpenTerminal={onOpenTerminal}
                  />
                )}
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
          providerOptions={providerOptions}
          onAssignProvider={assignStagedAgent}
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
                  providerOptions={providerOptions}
                  onAssignProvider={assignStagedAgent}
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
  providerOptions = [],
  onAssignProvider,
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
  providerOptions?: Array<{ id: string; label: string; ready?: boolean }>
  onAssignProvider?: (sessionId: string, provider: string) => void
}) {
  const layout = useUIStore((state) => state.layout)
  const isMobile = layout === "mobile"
  const [viewMode, setViewMode] = useState<"stream" | "board">(isMobile ? "stream" : "board")
  const [laneFilter, setLaneFilter] = useState<"all" | "needs_attention" | "running" | "staged" | "accepted">("all")

  const totalCards = needsAttention.length + running.length + staged.length + accepted.length

  const emptyLanes: string[] = []
  if (needsAttention.length === 0) emptyLanes.push("Needs Attention")
  if (running.length === 0) emptyLanes.push("Running")
  if (staged.length === 0) emptyLanes.push("Staged")
  if (accepted.length === 0) emptyLanes.push("Accepted")
  const allClear = emptyLanes.length === 4
  const emptyLanesText =
    emptyLanes.length > 0 && !allClear
      ? `${emptyLanes.join(", ")} all clear`
      : null

  return (
    <div className="space-y-3">
      {/* ── Responsive Lane Filter & View Switcher ── */}
      <div className="flex items-center justify-between gap-2 border-b border-gray-800 pb-2">
        <div className="flex items-center gap-1.5 overflow-x-auto py-0.5 no-scrollbar flex-1 min-w-0">
          <button
            type="button"
            onClick={() => setLaneFilter("all")}
            className={`rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
              laneFilter === "all"
                ? "bg-blue-600 text-white shadow-sm"
                : "border border-gray-700 bg-gray-800 text-gray-300 hover:text-white"
            }`}
          >
            All ({totalCards})
          </button>
          <button
            type="button"
            onClick={() => setLaneFilter("needs_attention")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
              laneFilter === "needs_attention"
                ? "bg-amber-600 text-white shadow-sm"
                : "border border-amber-500/30 bg-amber-950/20 text-amber-300 hover:bg-amber-950/40"
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
            Attention ({needsAttention.length})
          </button>
          <button
            type="button"
            onClick={() => setLaneFilter("running")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
              laneFilter === "running"
                ? "bg-blue-600 text-white shadow-sm"
                : "border border-blue-500/30 bg-blue-950/20 text-blue-300 hover:bg-blue-950/40"
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
            Running ({running.length})
          </button>
          <button
            type="button"
            onClick={() => setLaneFilter("staged")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
              laneFilter === "staged"
                ? "bg-purple-600 text-white shadow-sm"
                : "border border-purple-500/30 bg-purple-950/20 text-purple-300 hover:bg-purple-950/40"
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-purple-400" />
            Staged ({staged.length})
          </button>
          <button
            type="button"
            onClick={() => setLaneFilter("accepted")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
              laneFilter === "accepted"
                ? "bg-emerald-600 text-white shadow-sm"
                : "border border-emerald-500/30 bg-emerald-950/20 text-emerald-300 hover:bg-emerald-950/40"
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Accepted ({accepted.length})
          </button>
        </div>

        {/* View Switcher: Stream vs Kanban Board */}
        <div className="flex rounded-md border border-gray-700 bg-gray-800 p-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={() => setViewMode("stream")}
            className={`rounded px-2.5 py-0.5 text-xs font-medium transition-colors ${
              viewMode === "stream" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"
            }`}
            title="Stream View (Full-width touch cards)"
          >
            ≡ Stream
          </button>
          <button
            type="button"
            onClick={() => setViewMode("board")}
            className={`rounded px-2.5 py-0.5 text-xs font-medium transition-colors ${
              viewMode === "board" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"
            }`}
            title="Kanban Board View (Side-by-side columns)"
          >
            ⊞ Board
          </button>
        </div>
      </div>

      {viewMode === "stream" ? (
        /* ── Stream Mode (Touch-first Full-Width Cards) ── */
        <div className="space-y-4">
          {totalCards === 0 && (
            <div className="rounded-lg border border-dashed border-gray-800 p-8 text-center text-xs text-gray-500 space-y-1">
              <p className="font-semibold text-gray-300 text-sm">No active goals on this board</p>
              <p>Stage or start a goal using "+ New Goal" above.</p>
            </div>
          )}

          {/* Lane: Needs Attention */}
          {(laneFilter === "all" || laneFilter === "needs_attention") && (
            needsAttention.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between border-b border-amber-500/30 pb-1 pt-1">
                  <span className="flex items-center gap-1.5 text-xs font-bold text-amber-300">
                    <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
                    Needs Attention ({needsAttention.length})
                  </span>
                  <span className="text-[10px] text-amber-400/80">Requires verification or user input</span>
                </div>
                <div className="space-y-2.5">
                  {needsAttention.map((card) => (
                    <GoalCardItem
                      key={card.id}
                      card={card}
                      onOpenSession={onOpenSession}
                      onStartSession={onStartSession}
                      onNewRun={onNewRun}
                      onSelectWorkstream={onSelectWorkstream}
                      startingSessionId={startingSessionId}
                      onOpenTerminal={onOpenTerminal}
                      providerOptions={providerOptions}
                      onAssignProvider={onAssignProvider}
                      isStream
                    />
                  ))}
                </div>
              </div>
            ) : laneFilter === "needs_attention" ? (
              <div className="rounded-lg border border-amber-500/20 bg-amber-950/10 p-6 text-center text-xs text-amber-200/90 space-y-1">
                <div className="text-sm font-semibold">🎉 All Clear!</div>
                <p className="text-amber-300/70">No goals currently need attention or human review.</p>
              </div>
            ) : null
          )}

          {/* Lane: Running */}
          {(laneFilter === "all" || laneFilter === "running") && (
            running.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between border-b border-blue-500/30 pb-1 pt-1">
                  <span className="flex items-center gap-1.5 text-xs font-bold text-blue-300">
                    <span className="h-2 w-2 rounded-full bg-blue-400" />
                    Running ({running.length})
                  </span>
                  <span className="text-[10px] text-blue-400/80">Active agent runs executing now</span>
                </div>
                <div className="space-y-2.5">
                  {running.map((card) => (
                    <GoalCardItem
                      key={card.id}
                      card={card}
                      onOpenSession={onOpenSession}
                      onStartSession={onStartSession}
                      onNewRun={onNewRun}
                      onSelectWorkstream={onSelectWorkstream}
                      startingSessionId={startingSessionId}
                      onOpenTerminal={onOpenTerminal}
                      providerOptions={providerOptions}
                      onAssignProvider={onAssignProvider}
                      isStream
                    />
                  ))}
                </div>
              </div>
            ) : laneFilter === "running" ? (
              <div className="rounded-lg border border-blue-500/20 bg-blue-950/10 p-6 text-center text-xs text-blue-200/90 space-y-1">
                <div className="text-sm font-semibold">No Active Runs</div>
                <p className="text-blue-300/70">No agents are actively executing in this workstream.</p>
              </div>
            ) : null
          )}

          {/* Lane: Staged */}
          {(laneFilter === "all" || laneFilter === "staged") && (
            staged.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between border-b border-purple-500/30 pb-1 pt-1">
                  <span className="flex items-center gap-1.5 text-xs font-bold text-purple-300">
                    <span className="h-2 w-2 rounded-full bg-purple-400" />
                    Staged ({staged.length})
                  </span>
                  <span className="text-[10px] text-purple-400/80">Planned goals ready to launch</span>
                </div>
                <div className="space-y-2.5">
                  {staged.map((card) => (
                    <GoalCardItem
                      key={card.id}
                      card={card}
                      onOpenSession={onOpenSession}
                      onStartSession={onStartSession}
                      onNewRun={onNewRun}
                      onSelectWorkstream={onSelectWorkstream}
                      startingSessionId={startingSessionId}
                      onOpenTerminal={onOpenTerminal}
                      providerOptions={providerOptions}
                      onAssignProvider={onAssignProvider}
                      isStream
                    />
                  ))}
                </div>
              </div>
            ) : laneFilter === "staged" ? (
              <div className="rounded-lg border border-purple-500/20 bg-purple-950/10 p-6 text-center text-xs text-purple-200/90 space-y-1">
                <div className="text-sm font-semibold">None Staged</div>
                <p className="text-purple-300/70">No proposed goals waiting to be started.</p>
              </div>
            ) : null
          )}

          {/* Lane: Accepted */}
          {(laneFilter === "all" || laneFilter === "accepted") && (
            accepted.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between border-b border-emerald-500/30 pb-1 pt-1">
                  <span className="flex items-center gap-1.5 text-xs font-bold text-emerald-300">
                    <span className="h-2 w-2 rounded-full bg-emerald-400" />
                    Accepted ({accepted.length})
                  </span>
                  <span className="text-[10px] text-emerald-400/80">Completed and verified outcomes</span>
                </div>
                <div className="space-y-2.5">
                  {accepted.map((card) => (
                    <GoalCardItem
                      key={card.id}
                      card={card}
                      onOpenSession={onOpenSession}
                      onStartSession={onStartSession}
                      onNewRun={onNewRun}
                      onSelectWorkstream={onSelectWorkstream}
                      startingSessionId={startingSessionId}
                      onOpenTerminal={onOpenTerminal}
                      providerOptions={providerOptions}
                      onAssignProvider={onAssignProvider}
                      isStream
                    />
                  ))}
                </div>
              </div>
            ) : laneFilter === "accepted" ? (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-950/10 p-6 text-center text-xs text-emerald-200/90 space-y-1">
                <div className="text-sm font-semibold">No Accepted Goals</div>
                <p className="text-emerald-300/70">Completed goals will appear here once accepted.</p>
              </div>
            ) : null
          )}

          {/* Subtle footer summary when in All view and some lanes are empty */}
          {laneFilter === "all" && emptyLanesText && totalCards > 0 && (
            <div className="rounded-lg border border-gray-800/80 bg-gray-900/40 px-3 py-2 text-center text-[11px] text-gray-500">
              ✓ {emptyLanesText}
            </div>
          )}
        </div>
      ) : (
        /* ── Board Mode (Responsive Kanban Grid) ── */
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
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
                    providerOptions={providerOptions}
                    onAssignProvider={onAssignProvider}
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
                    providerOptions={providerOptions}
                    onAssignProvider={onAssignProvider}
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
                    providerOptions={providerOptions}
                    onAssignProvider={onAssignProvider}
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
                    providerOptions={providerOptions}
                    onAssignProvider={onAssignProvider}
                  />
                ))
              )}
            </div>
          </div>
        </div>
      )}
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
  providerOptions = [],
  onAssignProvider,
  isStream = false,
}: {
  card: GoalCard
  onOpenSession: (id: string) => void
  onStartSession: (id: string) => void
  onNewRun?: (card: GoalCard) => void
  onSelectWorkstream?: (channelId: string) => void
  startingSessionId: string | null
  onOpenTerminal?: (terminalId: string) => void
  providerOptions?: Array<{ id: string; label: string; ready?: boolean }>
  onAssignProvider?: (sessionId: string, provider: string) => void
  isStream?: boolean
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
  const currentAgent = latest.agent_provider || "orchestrated"
  const agentChoices = [
    { id: "orchestrated", label: "Orchestrated" },
    ...providerOptions.filter((option) => option.id !== "orchestrated" && option.id !== "shell"),
  ]
  if (!agentChoices.some((option) => option.id === currentAgent)) {
    agentChoices.push({ id: currentAgent, label: currentAgent })
  }

  return (
    <div
      onClick={() => onOpenSession(latest.id)}
      className={`group cursor-pointer rounded-lg border border-gray-800 bg-gray-900/90 transition-all hover:border-gray-700 hover:bg-gray-850 shadow-sm ${
        isStream ? "p-3.5 space-y-2" : "p-2.5"
      }`}
    >
      {/* Badges: Project, Channel, Status */}
      <div className="flex items-start justify-between gap-1.5">
        <div className="flex flex-wrap items-center gap-1.5 min-w-0 flex-1">
          <span className="text-[10px] font-semibold text-gray-400 truncate max-w-[120px]">{card.project}</span>
          {channelName && (
            <button
              type="button"
              onClick={(e) => {
                if (onSelectWorkstream) {
                  e.stopPropagation()
                  onSelectWorkstream(card.channel_id)
                }
              }}
              className="text-[9px] px-1.5 py-0.2 rounded bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 font-mono truncate max-w-[100px] transition-colors"
              title={`Switch to #${channelName}`}
            >
              #{channelName}
            </button>
          )}
          {latest.worktree_branch ? (
            <span
              className="text-[9px] px-1.5 py-0.2 rounded bg-indigo-950/60 border border-indigo-800/40 text-indigo-300 font-mono truncate max-w-[140px]"
              title={`Worktree branch: ${latest.worktree_branch}`}
            >
              ⎇ {latest.worktree_branch}
            </span>
          ) : latest.worktree_mode === "direct" ? (
            <span className="text-[9px] px-1.5 py-0.2 rounded bg-gray-800 text-gray-400 font-mono" title="Direct repository checkout">
              📁 direct
            </span>
          ) : null}
        </div>
        <span className={`rounded border px-1.5 py-0.5 text-[9px] font-medium leading-none flex-shrink-0 ${badgeStyle}`}>
          {statusText}
        </span>
      </div>

      {/* Goal Title */}
      <p
        className={`font-medium text-gray-100 group-hover:text-blue-300 transition-colors ${
          isStream ? "text-sm line-clamp-3 leading-snug" : "mt-1 line-clamp-2 text-xs group-hover:text-white"
        }`}
        title={goalText}
      >
        {goalText}
      </p>

      {/* Acceptance Criteria count in stream mode */}
      {isStream && card.acceptance_criteria && card.acceptance_criteria.length > 0 && (
        <div className="text-[11px] text-gray-400 flex items-center gap-1.5">
          <span className="text-emerald-400 font-bold">✓</span>
          <span>{card.acceptance_criteria.length} acceptance criteria</span>
        </div>
      )}

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
        <p className={`text-gray-400 italic bg-gray-950/40 rounded border border-gray-800/40 ${
          isStream ? "mt-1.5 px-2.5 py-1.5 text-xs line-clamp-2" : "mt-1 px-1.5 py-0.5 text-[10px] line-clamp-1"
        }`}>
          {latest.output_summary.replace(/[*_#]/g, "")}
        </p>
      )}

      {/* Quick Action Bar */}
      <div className="mt-2.5 flex items-center justify-between border-t border-gray-800/80 pt-1.5">
        {isProposed && onAssignProvider ? (
          <select
            aria-label="Agent"
            title="Change the agent. The goal stays staged."
            value={currentAgent}
            onClick={(event) => event.stopPropagation()}
            onMouseDown={(event) => event.stopPropagation()}
            onChange={(event) => {
              event.stopPropagation()
              onAssignProvider(latest.id, event.target.value)
            }}
            className={`truncate rounded border border-gray-700 bg-gray-950 text-gray-200 ${
              isStream ? "max-w-[12rem] px-1.5 py-1 text-xs" : "max-w-[9.5rem] px-1 py-0.5 text-[10px]"
            }`}
          >
            {agentChoices.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        ) : (
          <span className="text-[10px] text-gray-400 font-mono">
            {latest.agent_provider ? `🤖 ${latest.agent_provider}` : "🤖 orchestrated"}
          </span>
        )}

        <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          {isProposed && (
            <button
              type="button"
              disabled={isStarting}
              onClick={() => onStartSession(latest.id)}
              className={`rounded bg-purple-600 font-semibold text-white hover:bg-purple-500 disabled:opacity-50 transition-colors ${
                isStream ? "px-3 py-1 text-xs min-h-[30px]" : "px-2 py-0.5 text-[10px]"
              }`}
            >
              {isStarting ? "Starting…" : "▶ Start"}
            </button>
          )}

          {isVerifying && (
            <button
              type="button"
              onClick={() => onOpenSession(latest.id)}
              className={`rounded bg-amber-600 font-semibold text-white hover:bg-amber-500 transition-colors ${
                isStream ? "px-3 py-1 text-xs min-h-[30px]" : "px-2 py-0.5 text-[10px]"
              }`}
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
              className={`rounded bg-yellow-600 font-semibold text-white hover:bg-yellow-500 transition-colors ${
                isStream ? "px-3 py-1 text-xs min-h-[30px]" : "px-2 py-0.5 text-[10px]"
              }`}
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
                className={`rounded bg-red-600 font-semibold text-white hover:bg-red-500 disabled:opacity-50 transition-colors ${
                  isStream ? "px-3 py-1 text-xs min-h-[30px]" : "px-2 py-0.5 text-[10px]"
                }`}
              >
                ↺ Retry
              </button>
              {onNewRun && (
                <button
                  type="button"
                  onClick={() => onNewRun(card)}
                  className={`rounded border border-gray-700 bg-gray-800 text-gray-300 hover:bg-gray-700 transition-colors ${
                    isStream ? "px-2.5 py-1 text-xs min-h-[30px]" : "px-1.5 py-0.5 text-[10px]"
                  }`}
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
              className={`rounded border border-emerald-500/40 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-900/40 transition-colors ${
                isStream ? "px-3 py-1 text-xs font-semibold min-h-[30px]" : "px-1.5 py-0.5 text-[10px]"
              }`}
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
              className={`rounded border border-gray-700 bg-gray-800 font-mono text-gray-300 hover:bg-gray-700 transition-colors ${
                isStream ? "px-2.5 py-1 text-xs min-h-[30px]" : "px-1.5 py-0.5 text-[10px]"
              }`}
            >
              &gt;_ Term
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
