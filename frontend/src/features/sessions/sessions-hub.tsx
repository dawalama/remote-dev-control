import { useState } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useStateStore } from "@/stores/state-store"
import { briefWorkstreamFilter } from "@/stores/chat-scope-store"
import { SessionsCard } from "./sessions-card"
import { ProviderSetup } from "./provider-setup"
import { ProjectBootstrap } from "./project-bootstrap"
import { EvidenceSetup } from "./evidence-setup"
import { SessionViewer } from "./session-viewer"
import { GET } from "@/lib/api"

export function WorkstreamGoalsWorkspace({ onCloseModal }: { onCloseModal?: () => void } = {}) {
  const [showProjectBootstrap, setShowProjectBootstrap] = useState(false)
  const [showSetupCards, setShowSetupCards] = useState(false)
  const [briefing, setBriefing] = useState(false)
  const [briefText, setBriefText] = useState<string | null>(null)
  const toast = useUIStore((state) => state.toast)
  const layout = useUIStore((state) => state.layout)
  const setVoicePanelOpen = useUIStore((state) => state.setVoicePanelOpen)
  const viewingSessionId = useUIStore((state) => state.viewingSessionId)
  const setViewingSessionId = useUIStore((state) => state.setViewingSessionId)
  const workspaceMode = useUIStore((state) => state.workspaceMode)
  const setWorkspaceMode = useUIStore((state) => state.setWorkspaceMode)
  const channels = useChannelStore((state) => state.channels)
  const activeChannelId = useChannelStore((state) => state.activeChannelId)
  const activeChannel = channels.find((c) => c.id === activeChannelId)
  const allActions = useStateStore((state) => state.actions)
  const workstreamProjects = activeChannel?.project_names ?? []
  const isMobileOrKiosk = layout === "mobile" || layout === "kiosk"
  const runningActions = allActions.filter(
    (a) => a.status === "running" && workstreamProjects.includes(a.project)
  )

  const runBrief = async () => {
    setBriefing(true)
    setBriefText(null)
    try {
      const filter = briefWorkstreamFilter()
      const currentCollection = useProjectStore.getState().currentCollection
      const collectionId = !filter && activeChannelId === "ch-system" ? currentCollection : null
      const params = new URLSearchParams()
      if (filter) params.set("workstream_id", filter)
      else if (collectionId) params.set("collection_id", collectionId)
      const qs = params.toString() ? `?${params.toString()}` : ""
      const brief = await GET<{ spoken_summary?: string }>(`/homebase/brief${qs}`)
      const text = brief.spoken_summary || "No brief available."
      setBriefText(text)
      toast(text.slice(0, 140), "info")
    } catch {
      toast("Failed to load HomeBase brief", "error")
    } finally {
      setBriefing(false)
    }
  }

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
      <header className="flex items-center justify-between gap-1.5 border-b border-gray-800 px-3 py-2 bg-gray-900/90 overflow-x-auto">
        <div className="min-w-0 flex items-center gap-1.5 flex-shrink-0">
          <h2 className="text-sm font-semibold text-gray-100 flex items-center gap-1.5 whitespace-nowrap">
            <span>{activeChannel?.type === "system" ? "HomeBase" : "Goal Board"}</span>
            {activeChannel && (
              <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-300 font-mono font-medium truncate max-w-[120px]">
                #{activeChannel.name.replace(/^#+/, "")}
              </span>
            )}
          </h2>
          {runningActions.length > 0 && (
            <div className="hidden sm:flex items-center gap-1.5 pl-2 border-l border-gray-800">
              {runningActions.map((action) => {
                const url = action.preview_url || (action.port ? `http://localhost:${action.port}` : null)
                if (!url) return null
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

        <div className="flex items-center gap-1 flex-shrink-0">
          {!onCloseModal && layout === "desktop" && (
            <div className="flex rounded-md border border-gray-700 bg-gray-800 p-0.5 mr-1">
              <button
                type="button"
                onClick={() => setWorkspaceMode("goals")}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  workspaceMode === "goals" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                📋 Board
              </button>
              <button
                type="button"
                onClick={() => setWorkspaceMode("terminal")}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  workspaceMode === "terminal" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
                title="Switch to raw PTY terminal view (⌘J)"
              >
                💻 Terminal (⌘J)
              </button>
            </div>
          )}

          <button
            type="button"
            disabled={briefing}
            onClick={() => { void runBrief() }}
            className="rounded border border-blue-500/40 bg-blue-500/10 px-2 py-1 text-[11px] sm:text-xs font-medium text-blue-200 hover:bg-blue-500/20 disabled:opacity-50 whitespace-nowrap"
            title="Whole HomeBase status (all projects)"
          >
            {briefing ? "Briefing…" : isMobileOrKiosk ? "🎙️ Brief" : "Brief me"}
          </button>

          <button
            type="button"
            onClick={() => setShowSetupCards((v) => !v)}
            className={`rounded border px-2 py-1 text-[11px] sm:text-xs whitespace-nowrap transition-colors ${
              showSetupCards
                ? "border-gray-600 bg-gray-800 text-white"
                : "border-gray-700 text-gray-400 hover:text-gray-200"
            }`}
          >
            {isMobileOrKiosk ? "⚙️ Setup" : "Agent & Evidence Setup"}
          </button>

          <button
            onClick={() => setShowProjectBootstrap(true)}
            className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-[11px] sm:text-xs text-gray-200 hover:bg-gray-700 whitespace-nowrap"
          >
            + Project
          </button>

          {onCloseModal && (
            <>
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
            </>
          )}
        </div>
      </header>

      {briefText && (
        <div className="flex items-center justify-between border-b border-gray-800 bg-blue-950/30 px-4 py-2 text-xs text-gray-200">
          <div>
            <span className="mr-2 font-semibold text-blue-300">HomeBase Brief:</span>
            {briefText}
          </div>
          <button onClick={() => setBriefText(null)} className="ml-3 text-gray-500 hover:text-gray-300">✕</button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {showSetupCards && (
          <div className="grid gap-3 lg:grid-cols-2">
            <ProviderSetup />
            <EvidenceSetup />
          </div>
        )}
        <SessionsCard />
      </div>

      {showProjectBootstrap && <ProjectBootstrap onClose={() => setShowProjectBootstrap(false)} />}
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
    return (
      <div className="flex rounded-md border border-gray-700 bg-gray-800 p-0.5">
        <button
          onClick={() => {
            setWorkspaceMode("goals")
            setViewingSessionId(null)
          }}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            workspaceMode === "goals" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"
          }`}
        >
          Goals &amp; Review
        </button>
        <button
          onClick={() => setWorkspaceMode("terminal")}
          className={`rounded px-2.5 py-1 text-xs font-mono transition-colors ${
            workspaceMode === "terminal" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"
          }`}
        >
          &gt;_ Terminal
        </button>
      </div>
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
