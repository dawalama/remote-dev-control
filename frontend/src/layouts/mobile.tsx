import { useEffect, useState, useMemo, useRef } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore, getActiveProjectNames } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { AttentionCard } from "@/features/mobile/attention-card"
import { TerminalsCard } from "@/features/mobile/terminals-card"
import { ProcessesCard } from "@/features/mobile/processes-card"
import { SessionsCard } from "@/features/sessions/sessions-card"
import { ContextsCard } from "@/features/mobile/contexts-card"
import { DictationCard } from "@/features/mobile/dictation-card"
import { BrowserCard } from "@/features/mobile/browser-card"
// ChatCard replaced by MobileChannelPanel in v2
// PinchTab card/overlay removed — unified into Browser
import { GlobalTextInput } from "@/components/global-text-input"
import { useBrowserStore } from "@/stores/browser-store"
import { TerminalOverlay } from "@/features/mobile/terminal-overlay"
import { ProjectSheet } from "@/features/mobile/project-sheet"
import { HamburgerSheet } from "@/features/mobile/hamburger-sheet"
import { CreateTaskSheet } from "@/features/mobile/create-task-sheet"
import { ActivitySheet } from "@/features/mobile/activity-sheet"
import { UnifiedBrowserFullscreen } from "@/features/browser/unified-browser-panel"
import { AddProjectSheet } from "@/features/mobile/add-project-sheet"
import { ProcessLogOverlay } from "@/features/mobile/process-log-overlay"
import { PairedDevicesSheet } from "@/features/mobile/paired-devices-sheet"
import { ProjectSettingsModal } from "@/features/modals/project-settings"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { RecordingPlayer } from "@/features/browser/recording-player"
import { FloatingAgentPanel } from "@/features/browser/floating-agent-panel"
import { SessionViewer } from "@/features/sessions/session-viewer"
import { useChannelStore } from "@/stores/channel-store"
import { MobileChannelPanel } from "@/features/channels/mobile-channel-panel"
import { WorkspaceSelectorSheet } from "@/features/channels/mobile-workspace-selector"
import { Sheet } from "@/features/mobile/sheet"
import { POST } from "@/lib/api"
import { getClientId, getClientName } from "@/lib/client-id"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { useWorkstreamVoice } from "@/hooks/use-workstream-voice"
import type { BrowserSession, Action } from "@/types"

export function MobileLayout() {
  const layout = useUIStore((s) => s.layout)
  const maxW = layout === "kiosk" ? "max-w-2xl" : "max-w-lg"
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const loadCollections = useProjectStore((s) => s.loadCollections)
  const currentProject = useProjectStore((s) => s.currentProject)
  const currentCollection = useProjectStore((s) => s.currentCollection)
  const collections = useProjectStore((s) => s.collections)
  const connect = useStateStore((s) => s.connect)
  const disconnect = useStateStore((s) => s.disconnect)
  const connected = useStateStore((s) => s.connected)
  const serverState = useStateStore((s) => s.serverState)
  const terminals = useStateStore((s) => s.terminals)
  const processes = useStateStore((s) => s.actions)
  const toast = useUIStore((s) => s.toast)
  const viewingSessionId = useUIStore((s) => s.viewingSessionId)
  const setViewingSessionId = useUIStore((s) => s.setViewingSessionId)

  const cycleActiveProject = useProjectStore((s) => s.cycleActiveProject)

  const activeNames = useMemo(() => {
    // Re-derive when terminals/processes change
    void terminals; void processes
    return getActiveProjectNames()
  }, [terminals, processes])

  const [projectSheetOpen, setProjectSheetOpen] = useState(false)
  const [workspaceSelectorOpen, setWorkspaceSelectorOpen] = useState(false)
  const [hamburgerOpen, setHamburgerOpen] = useState(false)
  const [createTaskOpen, setCreateTaskOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(false)
  const [terminalOverlayId, setTerminalOverlayId] = useState<string | null>(null)
  const [browserFullscreen, setBrowserFullscreen] = useState(false)
  const browserActiveSession = useBrowserStore((s) => s.activeSession)
  const [addProjectOpen, setAddProjectOpen] = useState(false)
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false)
  const [systemSettingsOpen, setSystemSettingsOpen] = useState(false)
  const [processLog, setProcessLog] = useState<{ id: string; name: string } | null>(null)
  const [pairedDevicesOpen, setPairedDevicesOpen] = useState(false)
  const [browserUrlOpen, setBrowserUrlOpen] = useState(false)
  const [playingRecording, setPlayingRecording] = useState<string | null>(null)
  const [agentPickerOpen, setAgentPickerOpen] = useState(false)

  // Voice integration
  const terminalSendRef = useRef<((data: string) => void) | null>(null)
  const { voice, indicatorRef, cleanup: voiceCleanup } = useWorkstreamVoice({
    channel: "mobile",
    terminalSendRef,
    onOpenTerminal: async (project) => {
      const proj = project || currentProject
      if (!proj) { toast("Select a project first", "warning"); return }
      try {
        const activeChId = useChannelStore.getState().activeChannelId
        let url = `/terminals?project=${encodeURIComponent(proj)}`
        if (activeChId) url += `&channel_id=${encodeURIComponent(activeChId)}`
        const session = await POST<{ id: string }>(url)
        if (session?.id) setTerminalOverlayId(session.id)
      } catch { toast("Failed to create terminal", "error") }
    },
    onCreateTask: () => setCreateTaskOpen(true),
    onOpenBrowser: () => setBrowserUrlOpen(true),
    onOpenActivity: () => setActivityOpen(true),
    onOpenMenu: () => setHamburgerOpen(true),
    onEditProject: () => setProjectSettingsOpen(true),
    onSystemSettings: () => setSystemSettingsOpen(true),
    toast,
  })
  const [voiceIndicator, setVoiceIndicator] = useState("")

  // Sync voice indicator from ref (poll on animation frame when listening)
  useEffect(() => {
    if (!voice.listening) { setVoiceIndicator(""); return }
    let raf: number
    const poll = () => {
      setVoiceIndicator(indicatorRef.current)
      raf = requestAnimationFrame(poll)
    }
    raf = requestAnimationFrame(poll)
    return () => { cancelAnimationFrame(raf); voiceCleanup() }
  }, [voice.listening, indicatorRef, voiceCleanup])

  useEffect(() => {
    loadProjects()
    loadCollections()
  }, [loadProjects, loadCollections])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  // Find attention-worthy terminals
  const attentionTerminals = terminals.filter((t) => t.waiting_for_input)

  // Processes with ports for the current project (for URL suggestions)
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (!currentProject || p.project === currentProject)
  )

  const collectionName = collections.find((c) => c.id === currentCollection)?.name

  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  // Sync channel store with project selection
  const loadChannels = useChannelStore((s) => s.loadChannels)
  const selectChannel = useChannelStore((s) => s.selectChannel)
  const channels = useChannelStore((s) => s.channels)

  useEffect(() => {
    loadChannels()
  }, [loadChannels])

  useEffect(() => {
    if (currentProject) {
      const ch = channels.find((c) =>
        c.name === `#${currentProject}` || c.project_names?.includes(currentProject)
      )
      if (ch) selectChannel(ch.id)
    }
  }, [currentProject, channels, selectChannel])

  const handleSpawnTerminal = async (command?: string) => {
    if (!currentProject) {
      toast("Select a project first", "warning")
      return
    }
    try {
      let url = `/terminals?project=${encodeURIComponent(currentProject)}`
      if (command !== undefined) {
        url += `&command=${encodeURIComponent(command)}`
      }
      const activeChannelId = useChannelStore.getState().activeChannelId
      if (activeChannelId) {
        url += `&channel_id=${encodeURIComponent(activeChannelId)}`
      }
      const session = await POST<{ id: string }>(url)
      if (session?.id) setTerminalOverlayId(session.id)
    } catch {
      toast("Failed to create terminal", "error")
    }
  }

  // Recording playback fullscreen overlay
  if (playingRecording) {
    return (
      <div className={`h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden ${maxW} mx-auto`}>
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 flex-shrink-0">
          <button className="text-sm text-gray-400" onClick={() => setPlayingRecording(null)}>
            ← Back
          </button>
          <span className="text-xs text-gray-400">Recording Playback</span>
          <div />
        </div>
        <div className="flex-1 min-h-0 p-3">
          <RecordingPlayer
            recordingId={playingRecording}
            onBack={() => setPlayingRecording(null)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className={`h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden ${maxW} mx-auto`}>
      {/* Status bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 flex-shrink-0">
        <div className="flex items-center gap-1 min-w-0">
          {activeNames.length >= 2 && (
            <button
              className="text-gray-400 hover:text-gray-200 text-sm px-1"
              onClick={(e) => { e.stopPropagation(); cycleActiveProject(-1) }}
            >
              ‹
            </button>
          )}
          <button
            className="flex items-center gap-1 min-w-0"
            onClick={() => setWorkspaceSelectorOpen(true)}
          >
            {collectionName && (
              <span className="text-[10px] uppercase tracking-wider text-gray-500 mr-1">
                {collectionName}
              </span>
            )}
            <span className="text-sm font-semibold text-gray-100 truncate">
              {(() => {
                const ch = channels.find((c) => c.id === useChannelStore.getState().activeChannelId)
                return ch ? ch.name.replace(/^#/, "") : (!currentProject ? "All" : currentProject)
              })()}
            </span>
            <span className="text-gray-500 text-xs ml-0.5">▼</span>
          </button>
          {activeNames.length >= 2 && (
            <button
              className="text-gray-400 hover:text-gray-200 text-sm px-1"
              onClick={(e) => { e.stopPropagation(); cycleActiveProject(1) }}
            >
              ›
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 font-mono" title={getClientId()}>
            {getClientName() || getClientId().slice(0, 8)}
          </span>
          <span className="text-[10px] text-gray-500">{serverState}</span>
          <span
            className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
          />
          <button
            className="text-gray-400 hover:text-gray-200 text-lg"
            onClick={() => setHamburgerOpen(true)}
          >
            ☰
          </button>
        </div>
      </div>

      {/* Scrollable card area */}
      <div className="flex-1 overflow-auto px-3 py-3 space-y-3">
        {/* Attention */}
        {attentionTerminals.length > 0 && (
          <AttentionCard
            terminals={attentionTerminals}
            onOpen={(id) => setTerminalOverlayId(id)}
          />
        )}

        {/* Quick actions moved to bottom panel (MobileChannelPanel) */}

        {/* Cards */}
        <TerminalsCard onOpenTerminal={(id) => setTerminalOverlayId(id)} />
        <ProcessesCard
          onLogs={(id, name) => setProcessLog({ id, name })}
        />
        <BrowserCard
          onOpenSession={(session) => {
            useBrowserStore.getState().setActiveSession({
              id: session.session_id,
              target_url: "",
              status: "running",
              viewer_url: session.viewer_url,
            } as import("@/types").BrowserSession)
            setBrowserFullscreen(true)
          }}
          onPlayRecording={(id) => setPlayingRecording(id)}
        />
        <SessionsCard onOpenTerminal={(tid) => setTerminalOverlayId(tid)} />
        <ContextsCard />
        <PhoneDictation />
      </div>

      {/* Fixed bottom workspace panel */}
      {/* Chat panel (no action buttons — action bar below handles them) */}
      <MobileChannelPanel
        hideActions
        onOpenTerminal={async (project) => {
          const proj = project || currentProject
          if (!proj) { toast("Select a project first", "warning"); return }
          try {
            const activeChId = useChannelStore.getState().activeChannelId
            let url = `/terminals?project=${encodeURIComponent(proj)}`
            if (activeChId) url += `&channel_id=${encodeURIComponent(activeChId)}`
            const session = await POST<{ id: string }>(url)
            if (session?.id) setTerminalOverlayId(session.id)
          } catch { toast("Failed to create terminal", "error") }
        }}
        onCreateTask={() => setCreateTaskOpen(true)}
        onOpenBrowser={() => setBrowserUrlOpen(true)}
        onOpenActivity={() => setActivityOpen(true)}
        onOpenMenu={() => setHamburgerOpen(true)}
        onEditProject={() => setProjectSettingsOpen(true)}
        onSystemSettings={() => setSystemSettingsOpen(true)}
      />

      {/* Fixed bottom action bar */}
      <div className="flex-shrink-0 border-t border-gray-800 bg-gray-900 px-3 py-2">
        <div className="flex gap-2">
          <button className="flex-1 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white" onClick={() => setCreateTaskOpen(true)}>
            + Task
          </button>
          <button
            className="flex-1 py-2 text-sm font-medium rounded-lg border border-gray-600 text-gray-300"
            onClick={() => {
              if (!currentProject) { toast("Select a project first", "warning"); return }
              setAgentPickerOpen(true)
            }}
          >
            + Terminal
          </button>
          <button className="flex-1 py-2 text-sm font-medium rounded-lg border border-gray-600 text-gray-300" onClick={() => setBrowserUrlOpen(true)}>
            Browser
          </button>
          <button className="flex-1 py-2 text-sm font-medium rounded-lg border border-gray-600 text-gray-300" onClick={() => setHamburgerOpen(true)}>
            ☰
          </button>
          <button
            className={`w-10 py-2 rounded-lg flex items-center justify-center text-sm flex-shrink-0 ${
              voice.listening
                ? "bg-red-600 text-white animate-pulse"
                : "bg-gray-700 text-gray-300"
            }`}
            onClick={voice.toggle}
            title={voice.listening ? "Stop voice" : "Start voice"}
          >
            🎤
          </button>
        </div>
      </div>

      {/* Floating voice indicator */}
      {voiceIndicator && (
        <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-[90] max-w-sm px-4 py-2 rounded-full bg-gray-800/95 border border-gray-600 shadow-lg backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${voice.listening ? "bg-red-500 animate-pulse" : "bg-blue-500"}`} />
            <span className="text-sm text-gray-200 truncate">{voiceIndicator}</span>
          </div>
        </div>
      )}

      {/* Floating resume-preview pill */}
      {browserActiveSession && !browserFullscreen && (
        <button
          className="fixed bottom-20 right-3 z-[100] flex items-center gap-1.5 px-3 py-2 rounded-full bg-purple-600 text-white text-xs shadow-lg animate-pulse"
          onClick={() => setBrowserFullscreen(true)}
        >
          <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
          Preview
        </button>
      )}

      {/* Overlays */}
      {terminalOverlayId && (
        <TerminalOverlay
          sessionId={terminalOverlayId}
          onClose={() => setTerminalOverlayId(null)}
          onSwitch={(id) => setTerminalOverlayId(id)}
        />
      )}

      {/* Bottom sheets */}
      {projectSheetOpen && <ProjectSheet onClose={() => setProjectSheetOpen(false)} />}
      {workspaceSelectorOpen && (
        <WorkspaceSelectorSheet
          onClose={() => setWorkspaceSelectorOpen(false)}
          onSelect={(channelId) => {
            selectChannel(channelId)
            const ch = channels.find((c) => c.id === channelId)
            if (ch?.project_names?.[0]) {
              useProjectStore.getState().selectProject(ch.project_names[0])
            }
          }}
        />
      )}
      {hamburgerOpen && (
        <HamburgerSheet
          onClose={() => setHamburgerOpen(false)}
          onAddProject={() => { setHamburgerOpen(false); setAddProjectOpen(true) }}
          onCreateTask={() => { setHamburgerOpen(false); setCreateTaskOpen(true) }}
          onActivity={() => { setHamburgerOpen(false); setActivityOpen(true) }}
          onProjectSettings={() => { setHamburgerOpen(false); setProjectSettingsOpen(true) }}
          onSystemSettings={() => { setHamburgerOpen(false); setSystemSettingsOpen(true) }}
          onPairedDevices={() => { setHamburgerOpen(false); setPairedDevicesOpen(true) }}
        />
      )}
      {createTaskOpen && <CreateTaskSheet onClose={() => setCreateTaskOpen(false)} />}
      {activityOpen && <ActivitySheet onClose={() => setActivityOpen(false)} />}
      {addProjectOpen && <AddProjectSheet onClose={() => setAddProjectOpen(false)} />}
      {pairedDevicesOpen && <PairedDevicesSheet onClose={() => setPairedDevicesOpen(false)} />}
      {browserUrlOpen && (
        <BrowserUrlSheet
          processesWithPorts={processesWithPorts}
          onClose={() => setBrowserUrlOpen(false)}
          onShared={(session) => {
            setBrowserUrlOpen(false)
            useBrowserStore.getState().setActiveSession(session)
            setBrowserFullscreen(true)
          }}
        />
      )}

      {agentPickerOpen && (
        <Sheet title="New Terminal" onClose={() => setAgentPickerOpen(false)}>
          <div className="space-y-2">
            {presets.map((preset) => (
              <button
                key={preset.id}
                className="w-full flex items-center gap-3 px-4 py-3 rounded-lg bg-gray-700 hover:bg-gray-600 text-left"
                onClick={() => {
                  setAgentPickerOpen(false)
                  handleSpawnTerminal(
                    preset.command
                  )
                }}
              >
                <span className="w-8 h-8 rounded-lg bg-gray-800 flex items-center justify-center text-sm font-mono font-bold text-gray-200">
                  {preset.icon}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-gray-200">{preset.label}</div>
                  <div className="text-xs text-gray-500">{preset.description}</div>
                </div>
                <span className="text-xs text-gray-500 font-mono">{preset.command || "$SHELL"}</span>
              </button>
            ))}
          </div>
        </Sheet>
      )}

      {/* Project settings (full page on mobile) */}
      {projectSettingsOpen && currentProject && (
        <ProjectSettingsModal
          projectName={currentProject}
          onClose={() => setProjectSettingsOpen(false)}
          fullPage
        />
      )}
      {systemSettingsOpen && (
        <SystemSettingsModal onClose={() => setSystemSettingsOpen(false)} />
      )}

      {/* Process log overlay */}
      {processLog && (
        <ProcessLogOverlay
          processId={processLog.id}
          processName={processLog.name}
          onClose={() => setProcessLog(null)}
        />
      )}

      {browserFullscreen && (
        <UnifiedBrowserFullscreen
          onClose={() => {
            setBrowserFullscreen(false)
            useUIStore.getState().setAgentPanelOpen(false)
          }}
          onPlayRecording={(id) => { setBrowserFullscreen(false); setPlayingRecording(id) }}
        />
      )}
      {viewingSessionId && (
        <div className="fixed inset-0 z-[140] bg-gray-900 flex flex-col h-app">
          <SessionViewer
            sessionId={viewingSessionId}
            onClose={() => setViewingSessionId(null)}
          />
        </div>
      )}

      <FloatingAgentPanel channel="mobile" />
      <GlobalTextInput />
    </div>
  )
}

function PhoneDictation() {
  const phone = useStateStore((s) => s.phone)
  const phoneActive = (phone as { active?: boolean })?.active === true
  if (!phoneActive) return null
  return <DictationCard />
}

function BrowserUrlSheet({
  processesWithPorts,
  onClose,
  onShared,
}: {
  processesWithPorts: Action[]
  onClose: () => void
  onShared: (session: BrowserSession) => void
}) {
  const [url, setUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const toast = useUIStore((s) => s.toast)

  const handleShared = async (targetUrl?: string) => {
    const u = (targetUrl || url).trim()
    if (!u) return
    const fullUrl = u.startsWith("http") ? u : `http://${u}`
    setLoading(true)
    try {
      const params = new URLSearchParams({ target_url: fullUrl })
      const session = await POST<BrowserSession>(`/browser/start?${params}`)
      if (session) onShared(session)
    } catch {
      toast("Failed to start shared browser", "error")
    }
    setLoading(false)
  }

  return (
    <Sheet title="Open Browser" onClose={onClose} position="top">
      <div className="space-y-3">
        {/* URL input */}
        <div className="flex gap-2">
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleShared() }}
            placeholder="localhost:3000 or any URL"
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
            autoFocus
          />
        </div>
        <button
          className="w-full py-2 text-sm rounded bg-purple-600 text-white disabled:opacity-50"
          onClick={() => handleShared()}
          disabled={loading || !url.trim()}
        >
          {loading ? "Starting..." : "Open Shared Session"}
        </button>
        <p className="text-[10px] text-gray-500">
          Shared session — you and the agent see the same browser.
        </p>

        {/* Quick-launch from running processes */}
        {processesWithPorts.length > 0 && (
          <div>
            <p className="text-[10px] text-gray-500 uppercase mb-1.5">Running services</p>
            <div className="space-y-1">
              {processesWithPorts.map((p) => (
                <button
                  key={p.id}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded bg-gray-700 hover:bg-gray-600 text-left"
                  onClick={() => handleShared(`http://localhost:${p.port}`)}
                >
                  <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
                  <span className="flex-1 text-sm text-gray-200 truncate">
                    {p.name || p.id}
                  </span>
                  <span className="text-sm text-purple-400">:{p.port}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </Sheet>
  )
}
