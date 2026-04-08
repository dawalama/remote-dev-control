import { useState, useEffect, useRef, useCallback } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { useLogsStore } from "@/stores/logs-store"
import { useDictationStore } from "@/stores/dictation-store"
import { GET, POST, DELETE, api } from "@/lib/api"
import { ManagedWebSocket } from "@/lib/ws"
import { TaskOutputOverlay } from "@/features/mobile/task-output-overlay"
import { PortAssignmentsModal } from "@/features/modals/port-assignments"
import { ProjectSettingsModal } from "@/features/modals/project-settings"
import { UnifiedBrowserPanel } from "@/features/browser/unified-browser-panel"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { ContextViewerModal } from "@/features/modals/context-viewer"
import { useModels, ModelSelector, CreateTaskForm } from "@/features/tasks/create-task-form"
import { AddActionForm } from "@/features/processes/add-action-form"
import { SessionsCard } from "@/features/sessions/sessions-card"
import type { ActivityEvent, Task } from "@/types"

type RightTab = "activity" | "processes" | "tasks" | "browser" | "attachments" | "system" | "dictation"

export function RightTabs() {
  const globalTab = useUIStore((s) => s.currentTab)
  const setGlobalTab = useUIStore((s) => s.setTab)
  const phone = useStateStore((s) => s.phone)
  const dictationBlocks = useDictationStore((s) => s.blocks)

  // Map global TabId → RightTab (all RightTab values are valid TabIds now)
  const VALID_TABS = new Set<string>(["activity", "processes", "tasks", "browser", "pinchtab", "attachments", "system", "dictation"])
  // Map legacy "pinchtab" tab to unified "browser" tab
  const mappedTab = globalTab === "pinchtab" ? "browser" : globalTab
  const tab: RightTab = VALID_TABS.has(mappedTab) ? (mappedTab as RightTab) : "processes"
  const setTab = (t: RightTab) => setGlobalTab(t)
  const tasks = useStateStore((s) => s.tasks)
  const runningCount = tasks.filter(
    (t) => t.status === "in_progress" || t.status === "running"
  ).length

  const phoneActive = (phone as { active?: boolean })?.active === true

  const tabs: { id: RightTab; label: string; badge?: number }[] = [
    { id: "activity", label: "Activity" },
    { id: "processes", label: "Actions" },
    { id: "tasks", label: "Sessions", badge: runningCount > 0 ? runningCount : undefined },
    { id: "browser", label: "Browser" },
    { id: "attachments", label: "Attachments" },
    ...(phoneActive ? [{ id: "dictation" as RightTab, label: "Dictation", badge: dictationBlocks.length > 0 ? dictationBlocks.length : undefined }] : []),
    { id: "system", label: "System" },
  ]

  return (
    <div className="bg-gray-800 rounded-lg flex flex-col h-full min-h-0 min-w-0 overflow-hidden">
      <div className="flex gap-1 flex-shrink-0 px-3 pt-3 pb-1 overflow-x-auto scrollbar-none">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`px-3 py-1.5 text-xs rounded transition-colors whitespace-nowrap flex-shrink-0 ${
              tab === t.id
                ? "bg-gray-700 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.badge != null && (
              <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded-full bg-blue-600 text-white">
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="px-3 pb-3 pt-2 flex-1 min-h-0 overflow-auto">
        {tab === "activity" && <ActivityTab />}
        {tab === "processes" && <ProcessesTab />}
        {tab === "tasks" && <SessionsCard />}
        {tab === "browser" && <UnifiedBrowserPanel />}
        {tab === "attachments" && <ContextsTab />}
        {tab === "dictation" && <DictationTab />}
        {tab === "system" && <SystemTab />}
      </div>
    </div>
  )
}

// ─── Activity Tab ──────────────────────────────────────────────────────
function ActivityTab() {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [hasMore, setHasMore] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const currentProject = useProjectStore((s) => s.currentProject)
  const wsRef = useRef<ManagedWebSocket | null>(null)

  // Channel-first: use active channel's primary project for filtering
  const activeChannel = useChannelStore((s) => {
    const ch = s.channels.find((c) => c.id === s.activeChannelId)
    return ch ?? null
  })
  const filterProject = activeChannel?.project_names?.[0] || (currentProject !== "all" ? currentProject : null)

  const loadActivity = useCallback(
    (before?: string) => {
      const params = new URLSearchParams({ limit: "30" })
      if (filterProject) params.set("project", filterProject)
      if (before) params.set("before", before)
      return GET<ActivityEvent[]>(`/activity?${params}`)
    },
    [filterProject]
  )

  useEffect(() => {
    loadActivity().then((evts) => {
      setEvents(evts)
      setHasMore(evts.length >= 30)
    }).catch(() => {})
  }, [loadActivity])

  // WS for real-time
  useEffect(() => {
    const ws = new ManagedWebSocket("/ws", { reconnect: true, reconnectInterval: 3000 })
    ws.onMessage((data) => {
      if (data && typeof data === "object" && "type" in data) {
        const evt = data as { type: string; id?: string; message?: string; project?: string; timestamp?: string }
        if (evt.message) {
          const newEvent: ActivityEvent = {
            id: evt.id || `ws-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            type: evt.type,
            message: evt.message,
            project: evt.project,
            timestamp: evt.timestamp || new Date().toISOString(),
          }
          setEvents((prev) => {
            // Deduplicate consecutive
            if (prev.length > 0 && prev[0].message === newEvent.message) return prev
            return [newEvent, ...prev].slice(0, 100)
          })
        }
      }
    })
    ws.connect()
    wsRef.current = ws
    return () => { ws.close(); wsRef.current = null }
  }, [currentProject])

  const loadMore = async () => {
    if (loadingMore || !hasMore || events.length === 0) return
    setLoadingMore(true)
    try {
      const oldest = events[events.length - 1]
      const more = await loadActivity(oldest.timestamp)
      setEvents((prev) => [...prev, ...more])
      setHasMore(more.length >= 30)
    } catch { /* */ }
    setLoadingMore(false)
  }

  const filtered = currentProject === "all"
    ? events
    : events.filter((e) => !e.project || e.project === currentProject)

  const dotColor = (type: string) => {
    if (type.includes("error") || type.includes("fail")) return "bg-red-400"
    if (type.includes("warn")) return "bg-orange-400"
    if (type.includes("debug")) return "bg-gray-500"
    return "bg-indigo-400"
  }

  return (
    <div className="space-y-0.5">
      {filtered.length === 0 && <p className="text-gray-500 text-xs">No recent activity</p>}
      {filtered.map((event, i) => (
        <div key={`${event.id}-${i}`} className="flex items-start gap-1.5 text-xs py-1">
          <span className={`w-1.5 h-1.5 rounded-full mt-1 flex-shrink-0 ${dotColor(event.type)}`} />
          <span className="text-gray-500 flex-shrink-0">
            {new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
          <span className="text-gray-300 min-w-0">{event.message}</span>
        </div>
      ))}
      {hasMore && (
        <button
          className="text-xs text-blue-400 hover:text-blue-300 mt-2"
          onClick={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? "Loading..." : "Load older"}
        </button>
      )}
    </div>
  )
}

// ─── Actions Tab (was Processes) ───────────────────────────────────────
function ProcessesTab() {
  const actions = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const openProcessLog = useLogsStore((s) => s.openProcessLog)
  const [detecting, setDetecting] = useState(false)
  const [settingUp, setSettingUp] = useState(false)
  const [showPorts, setShowPorts] = useState(false)

  const filtered = (currentProject === "all" ? actions : actions.filter((p) => p.project === currentProject))
    .slice()
    .sort((a, b) => {
      // Services first, then commands
      if (a.kind !== b.kind) return a.kind === "service" ? -1 : 1
      // Running first
      return (a.status === "running" ? -1 : 1) - (b.status === "running" ? -1 : 1)
    })

  const services = filtered.filter((p) => (p.kind || "service") === "service")
  const commands = filtered.filter((p) => p.kind === "command")

  const statusColor = (s: string) => {
    if (s === "running") return "bg-green-400"
    if (s === "completed") return "bg-blue-400"
    if (s === "error" || s === "failed") return "bg-red-400"
    return "bg-gray-400"
  }

  const handleAction = async (action: string, id: string) => {
    try {
      await POST(`/actions/${encodeURIComponent(id)}/${action}`)
    } catch { /* */ }
  }

  const handleExecute = async (id: string, name: string) => {
    try {
      await POST(`/actions/${encodeURIComponent(id)}/start`)
      toast("Running...", "success")
      // Auto-open logs for commands so user sees output
      openProcessLog(id, name)
    } catch { toast("Execute failed", "error") }
  }

  const handleSync = async () => {
    if (currentProject === "all") { toast("Select a project first", "warning"); return }
    setDetecting(true)
    try {
      await POST(`/projects/${encodeURIComponent(currentProject)}/detect-actions?force_rediscover=true`)
      toast("Sync complete", "success")
    } catch { toast("Sync failed", "error") }
    setDetecting(false)
  }

  const handlePreview = async (processId: string) => {
    try {
      await POST(`/browser/start/${encodeURIComponent(processId)}`)
      toast("Preview started — switch to Browser tab", "success")
    } catch {
      toast("Failed to start preview", "error")
    }
  }

  const renderService = (p: typeof filtered[0]) => (
    <div key={p.id} className={`bg-gray-700 rounded-lg p-2.5 ${p.status === "error" ? "ring-1 ring-red-500/30" : ""}`}>
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor(p.status)} ${p.status === "running" ? "animate-pulse" : ""}`} />
        <span className="text-xs font-medium flex-1 min-w-0 truncate">{p.project}/{p.name || p.id}</span>
        {p.port && (
          <a href={`http://localhost:${p.port}`} target="_blank" rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 text-xs">:{p.port}</a>
        )}
      </div>
      {p.command && <div className="text-[10px] text-gray-500 truncate mt-0.5">{p.command}</div>}
      {p.preview_url && p.status === "running" && (
        <a href={p.preview_url} target="_blank" rel="noopener noreferrer"
          className="flex items-center gap-1 mt-0.5 text-[10px] text-green-400 hover:text-green-300 truncate">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
          {p.preview_url.replace("https://", "")}
        </a>
      )}
      <div className="flex gap-1 mt-1 flex-wrap">
        {p.status === "running" ? (
          <>
            <Btn color="red" onClick={() => handleAction("stop", p.id)}>Stop</Btn>
            <Btn color="yellow" onClick={() => handleAction("restart", p.id)}>Restart</Btn>
          </>
        ) : (
          <>
            <Btn color="green" onClick={() => handleAction("start", p.id)}>Start</Btn>
            <Btn color="blue" onClick={async () => {
              try {
                const url = p.port
                  ? `/actions/${encodeURIComponent(p.id)}/attach?port=${p.port}`
                  : `/actions/${encodeURIComponent(p.id)}/attach`
                await POST(url)
                toast("Attached to running process", "success")
              } catch { toast("Failed to attach", "error") }
            }}>Attach</Btn>
          </>
        )}
        <Btn color="gray" onClick={() => openProcessLog(p.id, `${p.project}/${p.name || p.id}`)}>Logs</Btn>
        {p.port && p.status === "running" && !p.preview_url && (
          <Btn color="purple" onClick={() => handlePreview(p.id)}>Preview</Btn>
        )}
        {p.status === "error" && (
          <Btn color="purple" onClick={() => POST(`/actions/${encodeURIComponent(p.id)}/create-fix-task`).then(() => toast("Fix task created", "success")).catch(() => toast("Failed", "error"))}>
            Fix with AI
          </Btn>
        )}
      </div>
    </div>
  )

  const renderCommandChip = (p: typeof filtered[0]) => {
    const label = p.name || p.id.split("-").pop() || p.id
    const logName = `${p.project}/${p.name || p.id}`

    const isRunning = p.status === "running"
    const isError = p.status === "error" || p.status === "failed"
    const isDone = p.status === "completed"

    const ringStyle = isRunning ? "ring-1 ring-blue-500/30" : isError ? "ring-1 ring-red-500/30" : ""

    return (
      <div key={p.id} className={`bg-gray-700 rounded-lg p-2 ${ringStyle}`}>
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
            isRunning ? "bg-blue-400 animate-pulse" :
            isError ? "bg-red-400" :
            isDone ? "bg-green-400" :
            "bg-gray-500"
          }`} />
          <span className="text-xs font-medium flex-1 min-w-0 truncate" title={label}>{label}</span>
        </div>
        {p.command && <div className="text-[10px] text-gray-500 truncate mt-0.5">{p.command}</div>}
        <div className="flex gap-1 mt-1.5">
          {isRunning ? (
            <Btn color="red" onClick={() => handleAction("stop", p.id)}>Stop</Btn>
          ) : (
            <Btn color="green" onClick={() => handleExecute(p.id, logName)}>Run</Btn>
          )}
          {(isRunning || isDone || isError) && (
            <Btn color="gray" onClick={() => openProcessLog(p.id, logName)}>Logs</Btn>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-1">
        <button
          className="px-2 py-1 text-xs rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
          onClick={() => setShowPorts(true)}
        >
          Ports
        </button>
        <button
          className="px-2 py-1 text-xs rounded bg-gray-600 hover:bg-gray-500 text-gray-200 disabled:opacity-50"
          onClick={handleSync}
          disabled={detecting || currentProject === "all"}
        >
          {detecting ? "Syncing..." : "Sync"}
        </button>
        <button
          className="px-2 py-1 text-xs rounded bg-gray-600 hover:bg-gray-500 text-gray-200 disabled:opacity-50"
          onClick={async () => {
            if (currentProject === "all") { toast("Select a project first", "warning"); return }
            setSettingUp(true)
            try {
              await POST(`/projects/${encodeURIComponent(currentProject)}/setup`)
              toast("Setup started — detecting stack & processes", "success")
            } catch { toast("Setup failed", "error") }
            setSettingUp(false)
          }}
          disabled={settingUp || currentProject === "all"}
        >
          {settingUp ? "Setting up..." : "Setup"}
        </button>
      </div>

      <AddActionForm />

      {services.length > 0 && (
        <div>
          <h4 className="text-[10px] uppercase text-gray-500 mb-1">Services</h4>
          <div className="space-y-1.5">{services.map(renderService)}</div>
        </div>
      )}

      {commands.length > 0 && (
        <div>
          <h4 className="text-[10px] uppercase text-gray-500 mb-1">Commands</h4>
          <div className="space-y-1.5">{commands.map(renderCommandChip)}</div>
        </div>
      )}

      {filtered.length === 0 && <p className="text-gray-500 text-xs">No actions</p>}
      {showPorts && <PortAssignmentsModal onClose={() => setShowPorts(false)} />}
    </div>
  )
}

// ─── System Tab ────────────────────────────────────────────────────────
function SystemTab() {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showSystemSettings, setShowSystemSettings] = useState(false)
  const serverState = useStateStore((s) => s.serverState)
  const timestamp = useStateStore((s) => s.timestamp)
  const terminals = useStateStore((s) => s.terminals)
  const processes = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const openSystemLog = useLogsStore((s) => s.openSystemLog)

  const fetchAll = useCallback(() => {
    GET<Record<string, unknown>>("/admin/status").then(setStatus).catch(() => {})
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const handleRestart = async () => {
    if (!confirm("Restart the server?")) return
    try {
      await POST("/admin/restart")
      toast("Server restarting...", "info")
    } catch { toast("Restart failed", "error") }
  }

  const uptime = status?.uptime_seconds as number | undefined
  const memMb = status?.memory_mb as number | undefined

  const fmtUptime = (s: number) => {
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return h > 0 ? `${h}h ${m}m` : `${m}m`
  }

  const activeTerminals = terminals.filter((t) => t.status === "running").length
  const waitingTerminals = terminals.filter((t) => t.waiting_for_input).length
  const activeProcesses = processes.filter((p) => p.status === "running").length

  return (
    <div className="space-y-3">
      {/* Live stats */}
      <div className="bg-gray-700 rounded-lg p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-gray-300">Live</span>
          <button className="text-xs text-blue-400" onClick={fetchAll}>Refresh</button>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-lg font-bold text-green-400">{activeTerminals}</div>
            <div className="text-[10px] text-gray-400">Terminals</div>
          </div>
          <div>
            <div className="text-lg font-bold text-yellow-400">{waitingTerminals}</div>
            <div className="text-[10px] text-gray-400">Waiting</div>
          </div>
          <div>
            <div className="text-lg font-bold text-blue-400">{activeProcesses}</div>
            <div className="text-[10px] text-gray-400">Processes</div>
          </div>
        </div>
      </div>

      {/* Server status */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-gray-700 rounded-lg p-2.5">
          <div className="text-[10px] text-gray-400">State</div>
          <div className="text-xs font-mono">{serverState}</div>
        </div>
        <div className="bg-gray-700 rounded-lg p-2.5">
          <div className="text-[10px] text-gray-400">Updated</div>
          <div className="text-xs font-mono">{timestamp ? new Date(timestamp).toLocaleTimeString() : "—"}</div>
        </div>
        {uptime != null && (
          <div className="bg-gray-700 rounded-lg p-2.5">
            <div className="text-[10px] text-gray-400">Uptime</div>
            <div className="text-xs font-mono">{fmtUptime(uptime)}</div>
          </div>
        )}
        {memMb != null && (
          <div className="bg-gray-700 rounded-lg p-2.5">
            <div className="text-[10px] text-gray-400">Memory</div>
            <div className="text-xs font-mono">{memMb.toFixed(1)} MB</div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-1 flex-wrap">
        <Btn color="blue" onClick={handleRestart}>Restart Server</Btn>
        <Btn color="gray" onClick={openSystemLog}>Server Logs</Btn>
        {currentProject !== "all" && (
          <Btn color="gray" onClick={() => setShowSettings(true)}>Project Settings</Btn>
        )}

        <Btn color="gray" onClick={() => setShowSystemSettings(true)}>System Settings</Btn>
      </div>

      {/* Keyboard shortcuts */}
      <div className="text-[10px] text-gray-500 space-y-0.5">
        <div>⌘K — Search projects</div>
        <div>⌘T — New terminal</div>
        <div>⌘/ — Toggle chat</div>
      </div>

      {showSettings && currentProject !== "all" && (
        <ProjectSettingsModal projectName={currentProject} onClose={() => setShowSettings(false)} fullPage />
      )}
      {showSystemSettings && (
        <SystemSettingsModal onClose={() => setShowSystemSettings(false)} />
      )}
    </div>
  )
}

// ─── Legacy Tasks Tab (kept exported to avoid unused-function build error) ──
export function TasksTab() {
  const tasks = useStateStore((s) => s.tasks)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const [viewOutputTask, setViewOutputTask] = useState<{ id: string; title: string } | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [retryModal, setRetryModal] = useState<Task | null>(null)
  const [continueModal, setContinueModal] = useState<Task | null>(null)

  const filtered = currentProject === "all"
    ? tasks
    : tasks.filter((t) => t.project === currentProject || t.project_id === currentProject)

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const todayTasks = filtered.filter((t) => t.created_at && new Date(t.created_at) >= today)
  const pastTasks = filtered.filter((t) => !t.created_at || new Date(t.created_at) < today)

  const handleRun = (id: string) => POST(`/tasks/${id}/run`).catch(() => toast("Failed", "error"))
  const handleCancel = (id: string) => POST(`/tasks/${id}/cancel`).catch(() => toast("Failed", "error"))
  const handleRetry = (id: string) => POST(`/tasks/${id}/retry`).then(() => toast("Retried", "success")).catch(() => toast("Failed", "error"))
  const handleStop = async (task: Task) => {
    try {
      if (task.project) await POST(`/agents/${encodeURIComponent(task.project)}/stop`)
      else await POST(`/tasks/${task.id}/cancel`)
    } catch { toast("Failed to stop", "error") }
  }
  const handleDismiss = async (id: string) => {
    try {
      await DELETE(`/tasks/${id}`)
    } catch { toast("Failed to delete", "error") }
  }


  const statusDotColor: Record<string, string> = {
    pending: "bg-yellow-400",
    running: "bg-blue-400 animate-pulse",
    in_progress: "bg-blue-400 animate-pulse",
    completed: "bg-green-400",
    failed: "bg-red-400",
    needs_review: "bg-orange-400",
    awaiting_review: "bg-orange-400",
    blocked: "bg-orange-400",
  }

  const statusPill: Record<string, { label: string; color: string }> = {
    running: { label: "running", color: "blue" },
    in_progress: { label: "running", color: "blue" },
    failed: { label: "failed", color: "red" },
    needs_review: { label: "review", color: "orange" },
    awaiting_review: { label: "review", color: "orange" },
    blocked: { label: "blocked", color: "orange" },
  }

  const showProject = currentProject === "all"

  const renderTask = (task: Task) => {
    const pill = statusPill[task.status]
    return (
    <div key={task.id} className="bg-gray-700/50 hover:bg-gray-700 rounded-lg p-2 space-y-1">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${statusDotColor[task.status] || "bg-gray-400"}`} />
        <div className="flex-1 min-w-0">
          {showProject && task.project && <span className="text-[10px] text-gray-500 mr-1">{task.project}</span>}
          <span className="text-xs text-gray-200 line-clamp-1">{task.title || task.description?.slice(0, 60)}</span>
        </div>
        {pill && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap bg-${pill.color}-400/10 text-${pill.color}-400`}>
            {pill.label}
          </span>
        )}
      </div>
      {task.status === "failed" && task.output && (
        <div className="text-[10px] text-red-400 line-clamp-1">{task.output}</div>
      )}
      <div className="flex gap-1 flex-wrap">
        {task.status === "pending" && (
          <>
            <Btn color="green" onClick={() => handleRun(task.id)}>Run</Btn>
            <Btn color="red" onClick={() => handleCancel(task.id)}>Cancel</Btn>
          </>
        )}
        {(task.status === "in_progress" || task.status === "running") && (
          <>
            <Btn color="red" onClick={() => handleStop(task)}>Stop</Btn>
            <Btn color="red" onClick={() => POST(`/agents/${encodeURIComponent(task.project || "")}/stop?force=true`).catch(() => {})}>Force</Btn>
            <Btn color="blue" onClick={() => setViewOutputTask({
              id: task.id,
              title: task.title || task.description?.slice(0, 30) || "Task"
            })}>View</Btn>
          </>
        )}
        {task.status === "failed" && (
          <>
            <Btn color="blue" onClick={() => setViewOutputTask({
              id: task.id,
              title: task.title || task.description?.slice(0, 30) || "Task"
            })}>View</Btn>
            <Btn color="yellow" onClick={() => handleRetry(task.id)}>Retry</Btn>
            <Btn color="gray" onClick={() => setRetryModal(task)}>Edit & Retry</Btn>
            <Btn color="gray" onClick={() => setContinueModal(task)}>Chain</Btn>
            <Btn color="red" className="ml-auto" onClick={() => handleDismiss(task.id)}>Delete</Btn>
          </>
        )}
        {task.status === "completed" && (
          <>
            <Btn color="blue" onClick={() => setViewOutputTask({
              id: task.id,
              title: task.title || task.description?.slice(0, 30) || "Task"
            })}>View</Btn>
            <Btn color="gray" onClick={() => setContinueModal(task)}>Continue</Btn>
            <Btn color="red" className="ml-auto" onClick={() => handleDismiss(task.id)}>Delete</Btn>
          </>
        )}
      </div>
    </div>
  )}

  return (
    <div className="space-y-3">
      <button
        className="w-full px-3 py-1.5 text-xs rounded bg-blue-500/10 hover:bg-blue-500/20 text-blue-400"
        onClick={() => setShowAddModal(true)}
      >
        + Add Task
      </button>

      {todayTasks.length > 0 && (
        <div>
          <h4 className="text-[10px] uppercase text-gray-500 mb-1">Today</h4>
          <div className="space-y-1">{todayTasks.map(renderTask)}</div>
        </div>
      )}
      {pastTasks.length > 0 && (
        <div>
          <h4 className="text-[10px] uppercase text-gray-500 mb-1">Past</h4>
          <div className="space-y-1">{pastTasks.map(renderTask)}</div>
        </div>
      )}
      {filtered.length === 0 && <p className="text-gray-500 text-xs">No tasks</p>}

      {showAddModal && <AddTaskModal onClose={() => setShowAddModal(false)} />}
      {retryModal && <RetryModal task={retryModal} onClose={() => setRetryModal(null)} />}
      {continueModal && <ContinueModal task={continueModal} onClose={() => setContinueModal(null)} />}
      {viewOutputTask && (
        <TaskOutputOverlay
          taskId={viewOutputTask.id}
          taskTitle={viewOutputTask.title}
          onClose={() => setViewOutputTask(null)}
        />
      )}
    </div>
  )
}

// ─── Modals ────────────────────────────────────────────────────────────

function AddTaskModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Add Task" onClose={onClose}>
      <CreateTaskForm onClose={onClose} />
    </Modal>
  )
}

function RetryModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const { models } = useModels()
  const [description, setDescription] = useState(task.description || "")
  const [model, setModel] = useState((task.metadata?.model as string) || "")
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    setSubmitting(true)
    try {
      await POST(`/tasks/${task.id}/retry`, { description: description.trim(), model: model || undefined })
      toast("Retried", "success")
      onClose()
    } catch { toast("Failed", "error") }
    setSubmitting(false)
  }

  return (
    <Modal title="Edit & Retry" onClose={onClose}>
      {task.output && (
        <div className="bg-red-900/30 rounded p-2 text-xs text-red-300 mb-2 max-h-24 overflow-auto font-mono">
          {task.output.slice(0, 500)}
        </div>
      )}
      <Label>Description</Label>
      <textarea className="input-cls min-h-[80px] resize-y" value={description} onChange={(e) => setDescription(e.target.value)} autoFocus />
      <ModelSelector models={models} value={model} onChange={setModel} />
      <div className="flex justify-end gap-2 mt-3">
        <Btn color="gray" onClick={onClose}>Cancel</Btn>
        <Btn color="yellow" onClick={submit} disabled={submitting}>Retry</Btn>
      </div>
    </Modal>
  )
}

function ContinueModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const [instructions, setInstructions] = useState("")
  const [includeOutput, setIncludeOutput] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [showOutput, setShowOutput] = useState(false)

  const submit = async () => {
    if (!instructions.trim()) return
    setSubmitting(true)
    let desc = instructions.trim()
    if (includeOutput && task.output) {
      desc += `\n\n--- Previous output ---\n${task.output}`
    }
    try {
      await POST("/tasks", {
        project: task.project || task.project_id,
        description: desc,
        parent_task_id: task.id,
      })
      toast("Follow-up created", "success")
      onClose()
    } catch { toast("Failed", "error") }
    setSubmitting(false)
  }

  return (
    <Modal title="Create Follow-up" onClose={onClose}>
      <div className="text-xs text-gray-400 mb-2">
        Previous: {task.description?.slice(0, 100)}
      </div>
      {task.output && (
        <div className="mb-2">
          <button className="text-xs text-blue-400" onClick={() => setShowOutput(!showOutput)}>
            {showOutput ? "Hide" : "Show"} previous output
          </button>
          {showOutput && (
            <pre className="bg-gray-900 rounded p-2 text-[10px] text-gray-300 max-h-24 overflow-auto mt-1 font-mono">
              {task.output.slice(0, 1000)}
            </pre>
          )}
        </div>
      )}
      <Label>Additional instructions</Label>
      <textarea className="input-cls min-h-[80px] resize-y" value={instructions} onChange={(e) => setInstructions(e.target.value)} autoFocus />
      <label className="flex items-center gap-2 text-xs text-gray-300 mt-2 cursor-pointer">
        <input type="checkbox" checked={includeOutput} onChange={(e) => setIncludeOutput(e.target.checked)} />
        Include previous output as context
      </label>
      <div className="flex justify-end gap-2 mt-3">
        <Btn color="gray" onClick={onClose}>Cancel</Btn>
        <Btn color="blue" onClick={submit} disabled={submitting || !instructions.trim()}>Create Follow-up</Btn>
      </div>
    </Modal>
  )
}

// ─── Contexts Tab ─────────────────────────────────────────────────────

interface ContextSnapshot {
  id: string
  title?: string
  url?: string
  timestamp: string
  screenshot_url?: string
  screenshot_path?: string
  description?: string
}

function ContextsTab() {
  const [contexts, setContexts] = useState<ContextSnapshot[]>([])
  const [viewId, setViewId] = useState<string | null>(null)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const load = () => {
    const params = new URLSearchParams({ limit: "50" })
    if (currentProject !== "all") params.set("project", currentProject)
    GET<ContextSnapshot[]>(`/context?${params}`)
      .then(setContexts)
      .catch(() => {})
  }

  useEffect(() => { load() }, [currentProject])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ""
    const form = new FormData()
    form.append("file", file)
    if (currentProject && currentProject !== "all") form.append("project", currentProject)
    try {
      const res = await api<{ id: string; path: string }>("/context/upload", {
        method: "POST",
        body: form,
      })
      await navigator.clipboard.writeText(res.path).catch(() => {})
      toast(`Uploaded — path copied`, "success")
      load()
    } catch {
      toast("Upload failed", "error")
    }
  }

  const handleDelete = async (id: string) => {
    await DELETE(`/context/${id}`).catch(() => {})
    setContexts((prev) => prev.filter((c) => c.id !== id))
  }

  const handleCopyPath = (ctx: ContextSnapshot) => {
    const path = ctx.screenshot_path || `/context/${ctx.id}/screenshot`
    navigator.clipboard.writeText(path).then(
      () => toast(`Copied: ${path}`, "success"),
      () => toast("Copy failed", "error")
    )
  }

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="*/*"
        className="hidden"
        onChange={handleUpload}
      />
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] text-gray-500">{contexts.length} attachments</span>
        <div className="flex gap-1.5">
          <button
            className="px-2 py-0.5 text-[10px] rounded bg-blue-600 hover:bg-blue-700 text-white"
            onClick={() => fileInputRef.current?.click()}
          >
            Upload
          </button>
          <button
            className="px-2 py-0.5 text-[10px] rounded bg-gray-600 hover:bg-gray-500 text-white"
            onClick={load}
          >
            Refresh
          </button>
        </div>
      </div>

      {contexts.length === 0 ? (
        <p className="text-gray-500 text-xs text-center py-4">No attachments yet — capture from the Browser tab or upload a file</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {contexts.map((ctx) => (
            <div
              key={ctx.id}
              className="bg-gray-700 rounded p-2 group relative cursor-pointer"
              onClick={() => setViewId(ctx.id)}
            >
              <img
                src={`/context/${ctx.id}/screenshot`}
                alt=""
                className="w-full h-24 object-cover rounded mb-1"
                loading="lazy"
                onError={(e) => (e.currentTarget.style.display = "none")}
              />
              <div className="text-xs text-gray-300 truncate">{ctx.title || ctx.url || ctx.description || ctx.id}</div>
              {ctx.url && <div className="text-[10px] text-gray-500 truncate">{ctx.url}</div>}
              <div className="text-[10px] text-gray-500">
                {new Date(ctx.timestamp).toLocaleTimeString()}
              </div>
              <div className="absolute top-1 right-1 hidden group-hover:flex gap-1">
                <button
                  className="px-1.5 py-0.5 text-[10px] rounded bg-blue-600/80 text-white"
                  onClick={(e) => { e.stopPropagation(); handleCopyPath(ctx) }}
                  title="Copy file path to clipboard"
                >
                  Copy Path
                </button>
                <button
                  className="px-1.5 py-0.5 text-[10px] rounded bg-red-600/80 text-white"
                  onClick={(e) => { e.stopPropagation(); handleDelete(ctx.id) }}
                  title="Delete"
                >
                  Del
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {viewId && (
        <ContextViewerModal
          contextId={viewId}
          onClose={() => setViewId(null)}
          onDeleted={load}
        />
      )}
    </>
  )
}

// ─── Dictation Tab ────────────────────────────────────────────────────

function DictationTab() {
  const { active, blocks, clearAll, removeBlock, editBlock } = useDictationStore()
  const terminals = useStateStore((s) => s.terminals)
  const toast = useUIStore((s) => s.toast)
  const [toggling, setToggling] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState("")
  const [targetTerminal, setTargetTerminal] = useState("")

  // Default to first terminal
  useEffect(() => {
    if (!targetTerminal && terminals.length > 0) setTargetTerminal(terminals[0].id)
  }, [terminals, targetTerminal])

  const toggleDictation = async () => {
    setToggling(true)
    try {
      await POST("/voice/type-mode", { enabled: !active, target: "terminal" })
    } catch {
      toast("Failed to toggle dictation", "error")
    }
    setToggling(false)
  }

  const insertText = async (text: string) => {
    if (!targetTerminal) { toast("Select a terminal", "warning"); return }
    try {
      await POST(`/terminals/${encodeURIComponent(targetTerminal)}/input`, { text })
      toast("Inserted", "success")
    } catch {
      toast("Insert failed", "error")
    }
  }

  const copyText = (text: string) => {
    navigator.clipboard.writeText(text).then(
      () => toast("Copied", "success"),
      () => toast("Copy failed", "error"),
    )
  }

  const insertAll = async () => {
    const combined = blocks.map((b) => b.text).join(" ")
    await insertText(combined)
  }

  const copyAll = () => {
    const combined = blocks.map((b) => b.text).join(" ")
    copyText(combined)
  }

  const startEdit = (id: string, text: string) => {
    setEditingId(id)
    setEditText(text)
  }

  const commitEdit = () => {
    if (editingId) {
      editBlock(editingId, editText)
      setEditingId(null)
    }
  }

  return (
    <div className="space-y-3">
      {/* Toggle + terminal picker */}
      <div className="flex gap-2">
        <button
          className={`flex-1 px-3 py-1.5 text-xs rounded text-white ${
            active ? "bg-red-600 hover:bg-red-700" : "bg-green-600 hover:bg-green-700"
          } disabled:opacity-50`}
          onClick={toggleDictation}
          disabled={toggling}
        >
          {active ? "Stop Dictating" : "Start Dictating"}
        </button>
      </div>

      {terminals.length > 0 && (
        <div>
          <label className="block text-[10px] text-gray-500 mb-1">Insert target</label>
          <select
            className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
            value={targetTerminal}
            onChange={(e) => setTargetTerminal(e.target.value)}
          >
            {terminals.map((t) => (
              <option key={t.id} value={t.id}>
                {t.project} — {t.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Blocks */}
      {blocks.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-4">
          {active ? "Listening... speak to capture text" : "Start dictating to capture voice as text"}
        </p>
      ) : (
        <div className="space-y-2">
          {blocks.map((b) => (
            <div key={b.id} className="bg-gray-700 rounded-lg p-2.5 space-y-1">
              {editingId === b.id ? (
                <textarea
                  className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none resize-y min-h-[40px]"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  onBlur={commitEdit}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitEdit() } }}
                  autoFocus
                />
              ) : (
                <div
                  className="text-xs text-gray-200 cursor-pointer hover:text-white"
                  onClick={() => startEdit(b.id, b.text)}
                >
                  {b.text}
                </div>
              )}
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-gray-500 flex-1">
                  {new Date(b.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
                <Btn color="blue" onClick={() => copyText(b.text)}>Copy</Btn>
                <Btn color="green" onClick={() => insertText(b.text)}>Insert</Btn>
                <Btn color="red" onClick={() => removeBlock(b.id)}>Del</Btn>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Footer actions */}
      {blocks.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          <Btn color="blue" onClick={copyAll}>Copy All</Btn>
          <Btn color="green" onClick={insertAll}>Insert All</Btn>
          <Btn color="gray" onClick={clearAll}>Clear</Btn>
        </div>
      )}
    </div>
  )
}

// ─── Shared small components ───────────────────────────────────────────

function Btn({
  children, color, onClick, disabled, className,
}: {
  children: React.ReactNode; color: string; onClick?: () => void; disabled?: boolean; className?: string
}) {
  const colors: Record<string, string> = {
    red: "bg-red-500/10 hover:bg-red-500/20 text-red-400",
    green: "bg-green-500/10 hover:bg-green-500/20 text-green-400",
    blue: "bg-blue-500/10 hover:bg-blue-500/20 text-blue-400",
    yellow: "bg-yellow-500/10 hover:bg-yellow-500/20 text-yellow-400",
    purple: "bg-purple-500/10 hover:bg-purple-500/20 text-purple-400",
    gray: "bg-gray-500/10 hover:bg-gray-500/20 text-gray-400",
  }
  return (
    <button
      className={`px-2 py-0.5 text-xs rounded disabled:opacity-50 ${colors[color] || colors.gray} ${className || ""}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  )
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 rounded-lg p-5 w-full max-w-md shadow-xl border border-gray-700" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold mb-3">{title}</h3>
        {children}
      </div>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs text-gray-400 mb-1 mt-2">{children}</label>
}
