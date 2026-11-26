import { useUIStore } from "@/stores/ui-store"
import { useChannelStore } from "@/stores/channel-store"
import { useStateStore } from "@/stores/state-store"
import { SessionsCard } from "./sessions-card"
import { AgentModeWorkspace } from "./agent-mode-workspace"
import { SessionViewer } from "./session-viewer"

export function WorkstreamGoalsWorkspace({ onCloseModal }: { onCloseModal?: () => void } = {}) {
  const layout = useUIStore((state) => state.layout)
  const operatingMode = useUIStore((state) => state.operatingMode)
  const setOperatingMode = useUIStore((state) => state.setOperatingMode)
  const setVoicePanelOpen = useUIStore((state) => state.setVoicePanelOpen)
  const viewingSessionId = useUIStore((state) => state.viewingSessionId)
  const setViewingSessionId = useUIStore((state) => state.setViewingSessionId)
  const channels = useChannelStore((state) => state.channels)
  const activeChannelId = useChannelStore((state) => state.activeChannelId)
  const activeChannel = channels.find((c) => c.id === activeChannelId)
  const allActions = useStateStore((state) => state.actions)
  const workstreamProjects = activeChannel?.project_names ?? []
  const previewActions = allActions.filter(
    (a) =>
      a.status === "running" &&
      workstreamProjects.includes(a.project) &&
      Boolean(a.preview_url || a.port)
  )
  const isCompactView = layout !== "desktop" || Boolean(onCloseModal)
  const showHeader = isCompactView || previewActions.length > 0

  if (viewingSessionId && !onCloseModal) {
    return (
      <div className="flex-1 flex flex-col min-h-0 bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <SessionViewer
          key={viewingSessionId}
          sessionId={viewingSessionId}
          onClose={() => setViewingSessionId(null)}
        />
      </div>
    )
  }

  return (
    <section className="flex-1 flex flex-col min-h-0 overflow-hidden rounded-lg border border-gray-800 bg-gray-900">
      {showHeader && (
        <header className="flex items-center justify-between gap-2 border-b border-gray-800 px-3 py-1.5 bg-gray-900/95 overflow-x-auto no-scrollbar">
          <div className="min-w-0 flex items-center gap-2 flex-shrink-0">
            {isCompactView && (
              activeChannel?.type === "system" ? (
                <span className="text-sm font-semibold text-gray-100 whitespace-nowrap">HomeBase</span>
              ) : (
                <div className="flex rounded-md border border-gray-700 bg-gray-950 p-0.5 flex-shrink-0">
                  <button
                    type="button"
                    onClick={() => setOperatingMode("sessions")}
                    className={`rounded px-2.5 py-0.5 text-xs font-medium whitespace-nowrap transition-colors ${
                      operatingMode === "sessions"
                        ? "bg-blue-600 text-white shadow-sm"
                        : "text-gray-400 hover:text-gray-200"
                    }`}
                    title="Goals: 1 bounded outcome per isolated worktree with auto-verify loop and proof artifact"
                  >
                    🎯 Goals
                  </button>
                  <button
                    type="button"
                    onClick={() => setOperatingMode("agents")}
                    className={`rounded px-2.5 py-0.5 text-xs font-medium whitespace-nowrap transition-colors ${
                      operatingMode === "agents"
                        ? "bg-blue-600 text-white shadow-sm"
                        : "text-gray-400 hover:text-gray-200"
                    }`}
                    title="Agent Mode: Chat with persistent resident agents that create and complete task cards on the board"
                  >
                    🤖 Agents
                  </button>
                </div>
              )
            )}

            {isCompactView && activeChannel && (
              <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-300 font-mono font-medium truncate max-w-[160px]">
                #{activeChannel.name.replace(/^#+/, "")}
              </span>
            )}

            {previewActions.length > 0 && (
              <div className="flex items-center gap-1.5">
                {previewActions.map((action) => {
                  const url = action.preview_url || `http://localhost:${action.port}`
                  return (
                    <a
                      key={action.id}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300 hover:bg-emerald-500/20 hover:text-emerald-200 transition-colors"
                      title={`Open ${action.name || action.id} (${url}) in new tab`}
                    >
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0" />
                      <span className="truncate max-w-[120px]">{action.name || action.id}</span>
                      <span className="text-[10px] text-emerald-400/70 font-mono flex-shrink-0">
                        {action.preview_url ? action.preview_url.replace(/^https?:\/\//, "") : `:${action.port}`}
                      </span>
                      <span className="text-[10px] flex-shrink-0">↗</span>
                    </a>
                  )
                })}
              </div>
            )}
          </div>

          {onCloseModal && (
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <button
                type="button"
                onClick={() => setVoicePanelOpen(true)}
                className="rounded border border-red-500/40 bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-200 hover:bg-red-500/20"
              >
                🎤 Voice
              </button>
              <button
                onClick={onCloseModal}
                className="rounded border border-gray-700 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800 hover:text-white"
              >
                Close
              </button>
            </div>
          )}
        </header>
      )}

      <div
        className={
          operatingMode === "agents" && activeChannel?.type !== "system"
            ? "flex-1 min-h-0 flex flex-col p-2.5 overflow-hidden gap-2.5"
            : "flex-1 overflow-y-auto p-4 space-y-4"
        }
      >
        {operatingMode === "agents" && activeChannel?.type !== "system" ? (
          <AgentModeWorkspace />
        ) : (
          <SessionsCard />
        )}
      </div>
    </section>
  )
}

export function SessionsHub() {
  const open = useUIStore((state) => state.sessionsHubOpen)
  const setOpen = useUIStore((state) => state.setSessionsHubOpen)
  const layout = useUIStore((state) => state.layout)

  if (!open || layout === "desktop") return null

  return (
    <div
      className="fixed inset-0 z-[180] flex bg-black/70 p-2 backdrop-blur-sm sm:p-6"
      onMouseDown={() => setOpen(false)}
    >
      <div
        className="m-auto flex h-full max-h-[900px] w-full max-w-5xl flex-col overflow-hidden rounded-xl shadow-2xl"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <WorkstreamGoalsWorkspace onCloseModal={() => setOpen(false)} />
      </div>
    </div>
  )
}

export function SessionsNavButton({ compact = false }: { compact?: boolean }) {
  const layout = useUIStore((state) => state.layout)
  const workspaceMode = useUIStore((state) => state.workspaceMode)
  const setWorkspaceMode = useUIStore((state) => state.setWorkspaceMode)
  const setViewingSessionId = useUIStore((state) => state.setViewingSessionId)
  const setOpen = useUIStore((state) => state.setSessionsHubOpen)
  const setScope = useUIStore((state) => state.setSessionsHubScope)

  if (layout === "desktop") {
    if (workspaceMode !== "terminal") {
      return null
    }
    return (
      <button
        onClick={() => {
          setWorkspaceMode("goals")
          setViewingSessionId(null)
        }}
        className="rounded-md border border-blue-500/40 bg-blue-500/10 px-2.5 py-1 text-xs font-medium text-blue-300 hover:bg-blue-500/20"
        title="Return to Workspace (⌘J)"
      >
        ← Back to Workspace
      </button>
    )
  }

  return (
    <button
      onClick={() => {
        setScope("workstream")
        setOpen(true)
      }}
      className="rounded-md border border-blue-500/40 bg-blue-500/10 px-3 py-1.5 text-xs font-medium text-blue-300 hover:bg-blue-500/20"
    >
      {compact ? "Goals" : "Goals & Sessions"}
    </button>
  )
}
