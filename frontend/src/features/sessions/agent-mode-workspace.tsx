import { useState, useCallback, useRef } from "react"
import { DELETE, GET, PATCH, POST } from "@/lib/api"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useMountEffect } from "@/hooks/use-mount-effect"
import { TerminalView } from "@/features/terminal/terminal-view"

export interface AgentWorkerItem {
  id: string
  name: string
  channel_id: string
  project: string
  provider: string
  status: "idle" | "working" | "waiting" | "stopped"
  current_session_id?: string | null
  last_session_id?: string | null
  terminal_id?: string | null
  terminal_state?: "streaming" | "idle" | "exited" | "none"
  worktree_path?: string | null
  worktree_branch?: string | null
  context_mode: "warm" | "fresh"
  auto_drain: boolean
  sessions_completed: number
  turns_completed: number
}

export interface AgentTaskItem {
  id: string
  channel_id: string
  project: string
  agent_id?: string | null
  title: string
  status: "todo" | "in_progress" | "done"
  result_note?: string | null
  created_by: "agent" | "user"
  created_at: string
  updated_at: string
}

type AgentViewMode = "terminal" | "split" | "board"

function statusMeta(worker: AgentWorkerItem, hasActiveTask: boolean) {
  const tState = worker.terminal_state || (worker.terminal_id ? "idle" : "none")
  if (tState === "exited") {
    return { dot: "bg-red-400", label: "Exited", textCls: "text-red-300" }
  }
  if (tState === "streaming" || worker.status === "working") {
    return { dot: "bg-blue-400 animate-pulse", label: "Streaming", textCls: "text-blue-300" }
  }
  if (hasActiveTask) {
    return { dot: "bg-amber-400 animate-pulse", label: "In Task", textCls: "text-amber-300" }
  }
  if (tState === "idle") {
    return { dot: "bg-emerald-400", label: "Ready", textCls: "text-emerald-300" }
  }
  return { dot: "bg-gray-500", label: "No PTY", textCls: "text-gray-400" }
}

export function AgentModeWorkspace() {
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const channels = useChannelStore((s) => s.channels)
  const projects = useProjectStore((s) => s.projects)
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)

  const activeChannel = channels.find((c) => c.id === activeChannelId)
  const workstreamProjects = activeChannel?.project_names ?? []
  const availableProjects =
    workstreamProjects.length > 0
      ? projects.filter((p) => workstreamProjects.includes(p.name))
      : projects

  const [selectedProject, setSelectedProject] = useState<string>("")
  const effectiveProject =
    selectedProject || availableProjects[0]?.name || projects[0]?.name || ""

  const [workers, setWorkers] = useState<AgentWorkerItem[]>([])
  const [tasks, setTasks] = useState<AgentTaskItem[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [secondaryAgentId, setSecondaryAgentId] = useState<string | null>(null)
  const [splitPanes, setSplitPanes] = useState(false)
  const [viewMode, setViewMode] = useState<AgentViewMode>("terminal")
  const [spawnProvider, setSpawnProvider] = useState<string>("codex")
  const [providerOptions, setProviderOptions] = useState<
    Array<{ id: string; label: string; ready?: boolean }>
  >([
    { id: "codex", label: "Codex CLI" },
    { id: "claude", label: "Claude Code" },
    { id: "agy", label: "Antigravity (agy)" },
    { id: "cursor", label: "Cursor Agent" },
  ])

  const [newTaskTitle, setNewTaskTitle] = useState("")
  const [addingTask, setAddingTask] = useState(false)
  const [showQuickInject, setShowQuickInject] = useState(() => layout === "mobile" || layout === "kiosk")
  const [quickSendText, setQuickSendText] = useState("")
  const [sendingText, setSendingText] = useState(false)
  const [pullingTask, setPullingTask] = useState(false)
  const [startingTerminalFor, setStartingTerminalFor] = useState<string | null>(null)

  const primaryRedrawRef = useRef<(() => void) | null>(null)
  const secondaryRedrawRef = useRef<(() => void) | null>(null)

  const targetChannelId =
    activeChannelId && activeChannelId !== "ch-system"
      ? activeChannelId
      : channels.find((c) => !c.archived_at && c.id !== "ch-system")?.id || "ch-system"

  const ensureWorkerTerminal = useCallback(async (workerId: string) => {
    setStartingTerminalFor(workerId)
    try {
      await POST(`/herd/agents/${workerId}/terminal`)
    } catch {
      /* ignore */
    } finally {
      setStartingTerminalFor(null)
    }
  }, [])

  const load = useCallback(async () => {
    try {
      const qs = new URLSearchParams()
      if (targetChannelId && targetChannelId !== "ch-system") {
        qs.set("channel_id", targetChannelId)
      }
      if (effectiveProject) {
        qs.set("project", effectiveProject)
      }
      const query = qs.toString() ? `?${qs.toString()}` : ""
      const [wRes, tRes] = await Promise.all([
        GET<AgentWorkerItem[]>(`/herd/agents${query}`).catch(() => [] as AgentWorkerItem[]),
        GET<AgentTaskItem[]>(`/herd/tasks${query}`).catch(() => [] as AgentTaskItem[]),
      ])
      const wList = Array.isArray(wRes) ? wRes : []
      const tList = Array.isArray(tRes) ? tRes : []
      setWorkers(wList)
      setTasks(tList)
    } catch {
      /* ignore */
    }
  }, [targetChannelId, effectiveProject])

  const loadRef = useRef(load)
  loadRef.current = load

  useMountEffect(() => {
    void loadRef.current()
    void GET<{ providers: Array<{ id: string; label: string; ready: boolean }> }>(
      "/settings/agent-providers"
    )
      .then((res) => {
        if (res?.providers?.length) setProviderOptions(res.providers)
      })
      .catch(() => {})
  })

  useMountEffect(() => {
    const timer = window.setInterval(() => {
      void loadRef.current()
    }, 2000)
    return () => window.clearInterval(timer)
  })

  const activeWorker =
    workers.find((w) => w.id === selectedAgentId) || workers[0] || null

  const secondaryWorker =
    workers.find((w) => w.id === secondaryAgentId && w.id !== activeWorker?.id) ||
    workers.find((w) => w.id !== activeWorker?.id) ||
    null

  const handleSelectAgent = async (worker: AgentWorkerItem) => {
    setSelectedAgentId(worker.id)
    if (!worker.terminal_id || worker.terminal_state === "exited") {
      await ensureWorkerTerminal(worker.id)
      await load()
    }
  }

  const handleToggleSplitPanes = async () => {
    if (workers.length < 2) return
    const next = !splitPanes
    setSplitPanes(next)
    if (next && viewMode === "board") {
      setViewMode("terminal")
    }
    if (next && secondaryWorker && !secondaryWorker.terminal_id) {
      await ensureWorkerTerminal(secondaryWorker.id)
      await load()
    }
  }

  const handleSpawnAgent = async () => {
    if (!effectiveProject) {
      toast("Select or add a project first", "error")
      return
    }
    try {
      const created = await POST<AgentWorkerItem>("/herd/agents", {
        channel_id: targetChannelId,
        project: effectiveProject,
        provider: spawnProvider,
        context_mode: "warm",
        start_terminal: true,
      })
      if (created && typeof created === "object" && "id" in created) {
        setSelectedAgentId(created.id)
        if (!created.terminal_id) {
          await ensureWorkerTerminal(created.id)
        }
        toast(`Spawned ${created.name} terminal`, "success")
        await load()
      }
    } catch {
      toast("Could not start agent", "error")
    }
  }

  const handleEnsureWorkerTerminal = async (workerId: string) => {
    await ensureWorkerTerminal(workerId)
    await load()
  }

  const handleSendToAgentTerminal = async (textToSend?: string, taskId?: string) => {
    const text = (textToSend ?? quickSendText).trim()
    if (!text || !activeWorker) return
    setSendingText(true)
    if (!textToSend) setQuickSendText("")
    try {
      await POST(`/herd/agents/${activeWorker.id}/send`, {
        text,
        task_id: taskId || null,
      })
      if (taskId) {
        toast(`Sent task ${taskId} to ${activeWorker.name}`, "success")
      }
      await load()
    } catch {
      toast("Could not send to agent terminal", "error")
    } finally {
      setSendingText(false)
    }
  }

  const handlePullNextTask = async () => {
    if (!activeWorker) {
      toast("Start an agent first", "error")
      return
    }
    setPullingTask(true)
    try {
      const res = await POST<{
        pulled: boolean
        message?: string
        task?: AgentTaskItem
      }>(`/herd/agents/${activeWorker.id}/pull-task`)
      if (res?.pulled && res.task) {
        toast(`${activeWorker.name} pulled ${res.task.id}: ${res.task.title}`, "success")
      } else {
        toast(res?.message || "No Todo tasks in queue", "info")
      }
      await load()
    } catch {
      toast("Could not pull next task", "error")
    } finally {
      setPullingTask(false)
    }
  }

  const handleStopAgent = async (worker: AgentWorkerItem) => {
    try {
      await POST(`/herd/agents/${worker.id}/stop`)
      toast(`Killed ${worker.name}`, "info")
      if (workers.length <= 2) {
        setSplitPanes(false)
      }
      await load()
    } catch {
      toast("Could not stop agent", "error")
    }
  }

  const handleAddTask = async () => {
    const title = newTaskTitle.trim()
    if (!title || !effectiveProject) return
    setAddingTask(true)
    try {
      await POST("/herd/tasks", {
        channel_id: targetChannelId,
        project: effectiveProject,
        agent_id: null,
        title,
        status: "todo",
      })
      setNewTaskTitle("")
      await load()
    } catch {
      toast("Could not add task card", "error")
    } finally {
      setAddingTask(false)
    }
  }

  const handleUpdateTaskStatus = async (
    task: AgentTaskItem,
    nextStatus: "todo" | "in_progress" | "done"
  ) => {
    try {
      await PATCH(`/herd/tasks/${task.id}`, {
        status: nextStatus,
        agent_id: activeWorker?.id || task.agent_id || undefined,
      })
      await load()
    } catch {
      toast("Could not update task", "error")
    }
  }

  const handleDeleteTask = async (taskId: string) => {
    try {
      await DELETE(`/herd/tasks/${taskId}`)
      await load()
    } catch {
      toast("Could not delete task", "error")
    }
  }

  const handleSendTaskCardToAgent = async (task: AgentTaskItem) => {
    if (!activeWorker) {
      toast("Start an agent first to send this task", "error")
      return
    }
    const promptText = `Task ${task.id}: ${task.title} (When finished, run: rdc-task done ${task.id} "summary")`
    await handleSendToAgentTerminal(promptText, task.id)
    if (viewMode === "board") {
      setViewMode("split")
    }
  }

  const handlePromoteToGoalSession = async (goalText: string, taskId?: string) => {
    const cleaned = goalText.trim()
    if (!cleaned || !effectiveProject) {
      toast("Select a project and enter a goal to promote", "error")
      return
    }
    try {
      const created = await POST<{ id: string }>("/sessions", {
        channel_id: targetChannelId,
        project: effectiveProject,
        goal: cleaned,
        task_id: taskId,
        recipe_id: "auto",
        routing_profile: "balanced",
        worktree_mode: "isolated",
        max_rounds: 3,
      })
      if (created?.id) {
        await POST(`/sessions/${created.id}/start`).catch(() => {})
        toast("Promoted to bounded Goal", "success")
        useUIStore.getState().setOperatingMode("sessions")
        useUIStore.getState().setViewingSessionId(created.id)
      }
    } catch {
      toast("Could not promote to Goal Session", "error")
    }
  }

  const todoTasks = tasks.filter((t) => t.status === "todo")
  const inProgressTasks = tasks.filter((t) => t.status === "in_progress")
  const doneTasks = tasks.filter((t) => t.status === "done")

  const renderTaskColumn = (
    title: string,
    items: AgentTaskItem[],
    variant: "todo" | "in_progress" | "done",
    compact = false
  ) => {
    const borderCls =
      variant === "in_progress"
        ? "border-blue-500/25 bg-blue-950/10"
        : variant === "done"
          ? "border-emerald-500/25 bg-emerald-950/10"
          : "border-gray-800 bg-gray-900/60"

    return (
      <div className={`flex flex-col rounded-lg border p-2.5 ${borderCls} ${compact ? "" : "min-h-0 flex-1"}`}>
        <div className="mb-2 flex items-center justify-between border-b border-gray-800/80 pb-1.5 flex-shrink-0">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-gray-200">
            {variant === "in_progress" && (
              <span className="h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
            )}
            {variant === "done" && <span className="h-2 w-2 rounded-full bg-emerald-400" />}
            {title}
          </span>
          <span className="rounded-full bg-gray-800 px-1.5 py-0.5 text-[10px] font-bold text-gray-300">
            {items.length}
          </span>
        </div>

        <div className={`space-y-2 overflow-y-auto ${compact ? "max-h-56" : "flex-1 min-h-0"}`}>
          {items.length === 0 ? (
            <p className="py-4 text-center text-[11px] text-gray-600">Empty</p>
          ) : (
            items.map((t) => {
              const assignedAgent = workers.find((w) => w.id === t.agent_id)
              return (
                <div
                  key={t.id}
                  className="rounded border border-gray-800 bg-gray-950/90 p-2 space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-mono text-[9px] text-gray-500">{t.id}</span>
                    <div className="flex items-center gap-1">
                      {assignedAgent && (
                        <span className="rounded bg-blue-950/60 border border-blue-500/30 px-1 py-0.2 text-[9px] font-mono text-blue-300">
                          {assignedAgent.name}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => void handleDeleteTask(t.id)}
                        className="px-1 text-[10px] text-gray-500 hover:text-red-400"
                        title="Delete task"
                      >
                        ×
                      </button>
                    </div>
                  </div>

                  <p
                    className={`text-xs font-medium leading-snug ${
                      variant === "done"
                        ? "text-gray-400 line-through decoration-gray-600"
                        : "text-gray-100"
                    }`}
                  >
                    {t.title}
                  </p>

                  {t.result_note && (
                    <p className="text-[10px] text-emerald-300/90 bg-emerald-950/30 rounded px-1.5 py-0.5">
                      {t.result_note}
                    </p>
                  )}

                  <div className="flex items-center justify-end gap-1 pt-1 border-t border-gray-900">
                    {variant === "todo" && (
                      <>
                        <button
                          type="button"
                          onClick={() => void handlePromoteToGoalSession(t.title, t.id)}
                          className="rounded border border-indigo-500/40 bg-indigo-950/40 px-1.5 py-0.5 text-[10px] font-medium text-indigo-200 hover:bg-indigo-900/50"
                          title="Promote this task into an isolated Goal with Auto recipe + verify loop"
                        >
                          🎯 Goal
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleSendTaskCardToAgent(t)}
                          className="rounded bg-blue-600/90 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-blue-500"
                          title={
                            activeWorker
                              ? `Inject this task into ${activeWorker.name}'s terminal`
                              : "Start an agent first"
                          }
                        >
                          ▶ Send to {activeWorker?.name || "Agent"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleUpdateTaskStatus(t, "done")}
                          className="rounded border border-emerald-500/30 bg-emerald-950/30 px-1.5 py-0.5 text-[10px] text-emerald-300 hover:bg-emerald-900/40"
                          title="Mark Done"
                        >
                          ✓
                        </button>
                      </>
                    )}

                    {variant === "in_progress" && (
                      <>
                        <button
                          type="button"
                          onClick={() => void handleUpdateTaskStatus(t, "done")}
                          className="rounded bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-emerald-500"
                        >
                          ✓ Done
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleUpdateTaskStatus(t, "todo")}
                          className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-400 hover:text-gray-200"
                          title="Return to To Do"
                        >
                          ↩
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    )
  }

  const renderTerminalPane = (
    worker: AgentWorkerItem,
    redrawRef: React.MutableRefObject<(() => void) | null>,
    isSecondary = false
  ) => {
    const activeTask = inProgressTasks.find((t) => t.agent_id === worker.id)
    const meta = statusMeta(worker, Boolean(activeTask))

    if (!worker.terminal_id || worker.terminal_state === "exited") {
      return (
        <div className="flex-1 min-h-0 flex flex-col items-center justify-center p-6 text-center space-y-3 bg-gray-950">
          <div className="text-sm font-semibold text-gray-200 font-mono">
            {worker.name} ({worker.provider})
          </div>
          <p className="text-xs text-gray-400">
            {worker.terminal_state === "exited"
              ? "This agent's PTY session exited."
              : "This agent does not have an active PTY attached yet."}
          </p>
          <button
            type="button"
            disabled={startingTerminalFor === worker.id}
            onClick={() => void handleEnsureWorkerTerminal(worker.id)}
            className="rounded-md bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {startingTerminalFor === worker.id
              ? "Starting Terminal…"
              : `>_ ${worker.terminal_state === "exited" ? "Restart" : "Open"} ${worker.name} Terminal`}
          </button>
        </div>
      )
    }

    return (
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-gray-950">
        {/* Compact Terminal Pane Header */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-1 bg-gray-900/95 border-b border-gray-800 text-xs flex-shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`h-2 w-2 rounded-full flex-shrink-0 ${meta.dot}`} />
            {isSecondary && workers.length > 1 ? (
              <select
                value={worker.id}
                onChange={(e) => setSecondaryAgentId(e.target.value)}
                className="rounded border border-gray-700 bg-gray-950 px-1.5 py-0.5 text-xs font-mono font-semibold text-gray-100"
              >
                {workers
                  .filter((w) => w.id !== activeWorker?.id)
                  .map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
              </select>
            ) : (
              <span className="font-mono font-semibold text-gray-100 truncate">
                {worker.name}
              </span>
            )}
            <span className={`text-[10px] font-mono ${meta.textCls}`}>{meta.label}</span>
            <span className="text-[11px] text-gray-500 font-mono hidden sm:inline">
              · {worker.project}
            </span>
            {activeTask && (
              <span className="rounded bg-blue-500/15 border border-blue-500/30 px-1.5 py-0.5 text-[10px] font-mono text-blue-300 truncate max-w-[200px]">
                {activeTask.id}: {activeTask.title}
              </span>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            {!isSecondary && activeTask && (
              <button
                type="button"
                onClick={() => void handlePromoteToGoalSession(activeTask.title, activeTask.id)}
                className="rounded border border-indigo-500/50 bg-indigo-950/50 px-2 py-0.5 text-[11px] font-medium text-indigo-200 hover:bg-indigo-900/50"
                title="Promote the active task into an isolated Goal with verify loop"
              >
                🎯 Promote to Goal
              </button>
            )}
            {!isSecondary && (
              <button
                type="button"
                onClick={() => setShowQuickInject((v) => !v)}
                className={`rounded border px-2 py-0.5 text-[11px] font-mono transition-colors ${
                  showQuickInject
                    ? "border-blue-500/60 bg-blue-950/60 text-blue-200"
                    : "border-gray-700 bg-gray-800 text-gray-300 hover:text-white"
                }`}
                title="Toggle bottom prompt-injection bar (useful for multi-line paste or mobile)"
              >
                ⌘ Inject
              </button>
            )}
            <button
              type="button"
              onClick={() => redrawRef.current?.()}
              className="rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300 hover:text-white"
              title="Redraw terminal display"
            >
              Redraw
            </button>
            <button
              type="button"
              onClick={() => void handleEnsureWorkerTerminal(worker.id)}
              className="rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300 hover:text-white"
              title="Reconnect or restart PTY if exited"
            >
              Reconnect
            </button>
          </div>
        </div>

        {/* Full-Height Live Xterm Canvas */}
        <div className="flex-1 min-h-0 w-full">
          <TerminalView
            key={worker.terminal_id}
            sessionId={worker.terminal_id}
            project={worker.project}
            fontSize={13}
            onRedrawReady={(fn) => {
              redrawRef.current = fn
            }}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-2">
      {/* ── 1. Herdr-Style Single-Row Agent Strip ── */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-800 bg-gray-950/90 px-2.5 py-1.5 flex-shrink-0">
        {/* Left: Project Selector + Agent Terminal Tabs + Spawn */}
        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
          {workstreamProjects.length > 1 && (
            <select
              value={effectiveProject}
              onChange={(e) => {
                setSelectedProject(e.target.value)
                void load()
              }}
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200 font-mono"
            >
              {availableProjects.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          )}

          {workers.map((w) => {
            const isSelected = activeWorker?.id === w.id
            const isSecondary = splitPanes && secondaryWorker?.id === w.id
            const activeTask = inProgressTasks.find((t) => t.agent_id === w.id)
            const meta = statusMeta(w, Boolean(activeTask))
            return (
              <div
                key={w.id}
                className={`inline-flex items-center rounded-md border text-xs font-mono transition-colors ${
                  isSelected
                    ? "border-blue-500/70 bg-blue-950/60 text-blue-100 shadow-sm"
                    : isSecondary
                      ? "border-indigo-500/50 bg-indigo-950/40 text-indigo-200"
                      : "border-gray-800 bg-gray-900 text-gray-400 hover:border-gray-700 hover:text-gray-200"
                }`}
              >
                <button
                  type="button"
                  onClick={() => void handleSelectAgent(w)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1"
                  title={`${w.name} · ${meta.label} (${w.provider})`}
                >
                  <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
                  <span className="font-semibold">&gt;_ {w.name}</span>
                  {activeTask && (
                    <span className="rounded bg-blue-500/20 px-1 text-[10px] text-blue-300 max-w-[110px] truncate">
                      {activeTask.id}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => void handleStopAgent(w)}
                  className="pr-2 pl-0.5 text-gray-500 hover:text-red-400"
                  title={`Kill ${w.name}`}
                >
                  ×
                </button>
              </div>
            )
          })}

          {/* Spawn new agent terminal */}
          <div className="flex items-center gap-1 pl-1">
            <select
              value={spawnProvider}
              onChange={(e) => setSpawnProvider(e.target.value)}
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300"
              title="Choose CLI agent to launch in a new terminal"
            >
              {providerOptions.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              <option value="shell">Shell (zsh)</option>
            </select>
            <button
              type="button"
              onClick={() => void handleSpawnAgent()}
              className="rounded bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-500 whitespace-nowrap transition-colors"
            >
              + New Agent
            </button>
          </div>
        </div>

        {/* Right: Pull Next Task + 2x Panes + View Switcher */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {activeWorker && (
            <button
              type="button"
              disabled={pullingTask || todoTasks.length === 0}
              onClick={() => void handlePullNextTask()}
              className="rounded border border-emerald-500/40 bg-emerald-950/40 px-2.5 py-1 text-xs font-medium text-emerald-200 hover:bg-emerald-900/50 disabled:opacity-40 whitespace-nowrap transition-colors"
              title="Claim the oldest To Do task card and type it into the active agent's terminal (or run `rdc-task pull` inside the shell)"
            >
              ⚡ Pull Task ({todoTasks.length})
            </button>
          )}

          {workers.length >= 2 && (
            <button
              type="button"
              onClick={() => void handleToggleSplitPanes()}
              className={`rounded border px-2 py-1 text-xs font-mono transition-colors ${
                splitPanes && viewMode !== "board"
                  ? "border-indigo-500/60 bg-indigo-950/60 text-indigo-200"
                  : "border-gray-700 bg-gray-900 text-gray-400 hover:text-gray-200"
              }`}
              title="Show 2 agent terminals side-by-side"
            >
              2× Panes
            </button>
          )}

          <div className="inline-flex items-center rounded-md border border-gray-800 bg-gray-900 p-0.5 text-xs">
            <button
              type="button"
              onClick={() => setViewMode("terminal")}
              className={`rounded px-2.5 py-1 font-medium transition-colors ${
                viewMode === "terminal"
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
              title="Full-height live terminal for the selected agent"
            >
              &gt;_ Terminal
            </button>
            <button
              type="button"
              onClick={() => setViewMode("split")}
              className={`rounded px-2.5 py-1 font-medium transition-colors ${
                viewMode === "split"
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
              title="Side-by-side: Live Agent Terminal + Task Queue"
            >
              ◫ Split
            </button>
            <button
              type="button"
              onClick={() => setViewMode("board")}
              className={`rounded px-2.5 py-1 font-medium transition-colors ${
                viewMode === "board"
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
              title="Alternative View: Full 3-Column Task Board"
            >
              📋 Tasks ({tasks.length})
            </button>
          </div>
        </div>
      </div>

      {/* ── 2. Full-Height Workspace Body ── */}
      {viewMode === "board" && (
        <div className="flex-1 min-h-0 flex flex-col gap-2.5 overflow-hidden">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void handleAddTask()
            }}
            className="flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900/90 p-2.5 flex-shrink-0"
          >
            <input
              type="text"
              value={newTaskTitle}
              onChange={(e) => setNewTaskTitle(e.target.value)}
              placeholder="Add a task card to the queue (agents can pull cards via `rdc-task pull` or ⚡ Pull Task)…"
              className="flex-1 rounded border border-gray-800 bg-gray-950 px-3 py-1.5 text-xs text-gray-100 placeholder:text-gray-500 focus:border-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={addingTask || !newTaskTitle.trim()}
              className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40 whitespace-nowrap"
            >
              + Add Task Card
            </button>
          </form>

          <div className="flex-1 min-h-0 grid gap-3 md:grid-cols-3 overflow-y-auto md:overflow-hidden">
            {renderTaskColumn("To Do (Queue)", todoTasks, "todo")}
            {renderTaskColumn("In Progress (Pulled)", inProgressTasks, "in_progress")}
            {renderTaskColumn("Done", doneTasks, "done")}
          </div>
        </div>
      )}

      <div
        className={`flex-1 min-h-0 grid gap-2.5 lg:grid-cols-12 overflow-y-auto lg:overflow-hidden ${
          viewMode === "board" ? "hidden" : ""
        }`}
      >
        {/* Terminal Area (1 pane or 2x side-by-side panes) */}
        <div
          className={`${
            viewMode === "split" ? "lg:col-span-8" : "lg:col-span-12"
          } flex-1 min-h-0 flex flex-col rounded-lg border border-gray-800 bg-gray-950 overflow-hidden shadow-lg`}
        >
          {!activeWorker ? (
            <div className="flex-1 min-h-0 flex flex-col items-center justify-center p-6 text-center space-y-3">
              <div className="text-sm font-semibold text-gray-200">
                No Resident Agent Running on{" "}
                <span className="font-mono text-blue-400">{effectiveProject}</span>
              </div>
              <p className="max-w-md text-xs text-gray-400">
                Each agent runs in its own full-height interactive terminal (like Herdr). Spawn an agent to open its live CLI session, or queue tasks on the Task Board and let agents pull them with <code className="text-gray-200">rdc-task pull</code>.
              </p>
              <div className="flex items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => void handleSpawnAgent()}
                  className="rounded-md bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500"
                >
                  + Launch {spawnProvider} Agent Terminal
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("board")}
                  className="rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-xs text-gray-300 hover:bg-gray-800"
                >
                  📋 Open Task Board ({tasks.length})
                </button>
              </div>
            </div>
          ) : (
            <>
              {splitPanes && secondaryWorker ? (
                <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-gray-800 overflow-hidden">
                  {renderTerminalPane(activeWorker, primaryRedrawRef, false)}
                  {renderTerminalPane(secondaryWorker, secondaryRedrawRef, true)}
                </div>
              ) : (
                <div className="relative flex-1 min-h-0 flex flex-col overflow-hidden">
                  {workers.map((w) => {
                    const isActive = w.id === activeWorker.id
                    return (
                      <div
                        key={w.id}
                        className={
                          isActive
                            ? "relative flex-1 min-h-0 flex flex-col z-10"
                            : "absolute inset-0 opacity-0 pointer-events-none invisible flex flex-col"
                        }
                      >
                        {renderTerminalPane(w, primaryRedrawRef, false)}
                      </div>
                    )
                  })}
                </div>
              )}

                {/* Collapsible Prompt-Inject Bar (closed by default on desktop so typing goes straight into xterm) */}
                {showQuickInject && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault()
                      void handleSendToAgentTerminal()
                    }}
                    className="flex items-center gap-1.5 border-t border-gray-800 bg-gray-900/95 px-2.5 py-1.5 flex-shrink-0"
                  >
                    <span className="text-[11px] font-mono text-gray-400">
                      send → {activeWorker.name}:
                    </span>
                    <input
                      type="text"
                      value={quickSendText}
                      onChange={(e) => setQuickSendText(e.target.value)}
                      placeholder="Paste prompt or command to inject + Enter…"
                      className="flex-1 rounded border border-gray-800 bg-gray-950 px-2.5 py-1 text-xs text-gray-100 placeholder:text-gray-500 focus:border-blue-500 focus:outline-none font-mono"
                    />
                    <button
                      type="submit"
                      disabled={sendingText || !quickSendText.trim()}
                      className="rounded bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40 whitespace-nowrap"
                    >
                      Send ↵
                    </button>
                  </form>
                )}

                {/* Slim Status Footer */}
                <div className="flex items-center justify-between px-3 py-1 bg-gray-950 border-t border-gray-900 text-[10px] text-gray-500 font-mono flex-shrink-0">
                  <span>
                    Type directly in terminal · CLI: <code className="text-gray-400">rdc-task pull</code> /{" "}
                    <code className="text-gray-400">rdc-task done "note"</code>
                  </span>
                  <span>
                    {workers.length} agent{workers.length === 1 ? "" : "s"} · {todoTasks.length} todo
                  </span>
                </div>
              </>
            )}
          </div>

          {/* Right-Side Task Queue Drawer when in Split Mode */}
          {viewMode === "split" && (
            <div className="lg:col-span-4 flex flex-col gap-2 min-h-0 overflow-hidden">
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  void handleAddTask()
                }}
                className="flex items-center gap-1.5 rounded-lg border border-gray-800 bg-gray-900/90 p-2 flex-shrink-0"
              >
                <input
                  type="text"
                  value={newTaskTitle}
                  onChange={(e) => setNewTaskTitle(e.target.value)}
                  placeholder="Add task card to queue…"
                  className="flex-1 rounded border border-gray-800 bg-gray-950 px-2.5 py-1 text-xs text-gray-100 placeholder:text-gray-500 focus:border-blue-500 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={addingTask || !newTaskTitle.trim()}
                  className="rounded bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40 whitespace-nowrap"
                >
                  + Add
                </button>
              </form>

              <div className="flex-1 min-h-0 space-y-2 overflow-y-auto pr-0.5">
                {renderTaskColumn("To Do (Queue)", todoTasks, "todo", true)}
                {renderTaskColumn("In Progress", inProgressTasks, "in_progress", true)}
                {doneTasks.length > 0 && renderTaskColumn("Done", doneTasks, "done", true)}
              </div>
            </div>
          )}
        </div>
    </div>
  )
}
