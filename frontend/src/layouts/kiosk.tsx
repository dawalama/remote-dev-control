import { useState, useRef } from "react"
import { ChevronDown, Menu } from "lucide-react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { ProcessesCard } from "@/features/mobile/processes-card"
import { ContextsCard } from "@/features/mobile/contexts-card"
import { BrowserCard } from "@/features/mobile/browser-card"
import { TerminalOverlay } from "@/features/mobile/terminal-overlay"
import { HamburgerSheet } from "@/features/mobile/hamburger-sheet"
import { CreateTaskSheet } from "@/features/mobile/create-task-sheet"
import { ActivitySheet } from "@/features/mobile/activity-sheet"
import { UnifiedBrowserFullscreen } from "@/features/browser/unified-browser-panel"
import { AddProjectSheet } from "@/features/mobile/add-project-sheet"
import { PairedDevicesSheet } from "@/features/mobile/paired-devices-sheet"
import { ProjectSettingsModal } from "@/features/modals/project-settings"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { ChannelSettings } from "@/features/channels/channel-settings"
import { RecordingPlayer } from "@/features/browser/recording-player"
import { EmbeddedTerminal } from "@/features/terminal/embedded-terminal"
import { FloatingAgentPanel } from "@/features/browser/floating-agent-panel"
import { useChannelStore } from "@/stores/channel-store"
import { MobileChannelPanel } from "@/features/channels/mobile-channel-panel"
import { WorkspaceSelectorSheet } from "@/features/channels/mobile-workspace-selector"
import { ProjectCard } from "@/features/mobile/project-card"
import { useLogsStore } from "@/stores/logs-store"
import { Sheet } from "@/features/mobile/sheet"
import { POST } from "@/lib/api"
import { useTerminalStore } from "@/stores/terminal-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { getClientId, getClientName } from "@/lib/client-id"
import { GlobalTextInput } from "@/components/global-text-input"
import { useBrowserStore } from "@/stores/browser-store"
import { useWorkstreamVoice } from "@/hooks/use-workstream-voice"
import { useMountEffect } from "@/hooks/use-mount-effect"
import { WorkstreamGoalsWorkspace } from "@/features/sessions/sessions-hub"
import type { BrowserSession, Action } from "@/types"

export type KioskViewMode = "split" | "terminal" | "chat" | "preview" | "services" | "goals"
export type KioskSideTab = "chat" | "services" | "preview" | "goals" | "project" | "attachments"

export function KioskLayout() {
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
  const needsAttentionCount = useUIStore((s) => s.needsAttentionCount)
  const workstreamSettingsOpen = useUIStore((s) => s.workstreamSettingsOpen)
  const setWorkstreamSettingsOpen = useUIStore((s) => s.setWorkstreamSettingsOpen)
  const openProcessLog = useLogsStore((s) => s.openProcessLog)

  // Primary view mode: default to terminal (clean, focused, full-width canvas) or user's saved preference
  const [viewMode, setViewModeState] = useState<KioskViewMode>(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("rdc_kiosk_view") : null
    return (saved as KioskViewMode) || "terminal"
  })

  const setViewMode = (mode: KioskViewMode) => {
    localStorage.setItem("rdc_kiosk_view", mode)
    setViewModeState(mode)
  }

  // Side panel toggle state (collapsible companion panel in split view)
  const [sidePanelOpen, setSidePanelOpen] = useState<boolean>(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("rdc_kiosk_side_panel") : null
    return saved !== null ? saved === "true" : true
  })

  const toggleSidePanel = () => {
    setSidePanelOpen((prev) => {
      const next = !prev
      localStorage.setItem("rdc_kiosk_side_panel", String(next))
      return next
    })
  }

  const [activeSideTab, setActiveSideTab] = useState<KioskSideTab>("chat")

  const [projectSheetOpen, setProjectSheetOpen] = useState(false)
  const [hamburgerOpen, setHamburgerOpen] = useState(false)
  const [createTaskOpen, setCreateTaskOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(false)
  const [terminalOverlayId, setTerminalOverlayId] = useState<string | null>(null)
  const [browserFullscreen, setBrowserFullscreen] = useState(false)
  const [addProjectOpen, setAddProjectOpen] = useState(false)
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false)
  const [systemSettingsOpen, setSystemSettingsOpen] = useState(false)
  const [pairedDevicesOpen, setPairedDevicesOpen] = useState(false)
  const [browserUrlOpen, setBrowserUrlOpen] = useState(false)
  const [playingRecording, setPlayingRecording] = useState<string | null>(null)
  const [agentPickerOpen, setAgentPickerOpen] = useState(false)
  const browserActiveSession = useBrowserStore((s) => s.activeSession)

  // Terminal send function ref for voice dictation
  const terminalSendRef = useRef<((data: string) => void) | null>(null)
  const spawnTerminal = useTerminalStore((s) => s.spawnTerminal)

  // Workstream-aware voice
  const { voice, indicatorRef, cleanup: voiceCleanup } = useWorkstreamVoice({
    channel: "mobile",
    terminalSendRef,
    onOpenTerminal: async (project) => {
      const proj = project || currentProject
      if (!proj) { toast("Select a project first", "warning"); return }
      const session = await spawnTerminal(proj)
      if (session) { toast("Terminal started", "success") }
      else { toast("Failed to create terminal", "error") }
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

  // On-mount setup (strictly no useEffect)
  useMountEffect(() => {
    loadProjects()
    loadCollections()
    useChannelStore.getState().loadChannels()
    useTerminalPresetsStore.getState().load()
    connect()

    // Poll voice transcription indicator on animation frames
    let raf: number
    const poll = () => {
      if (indicatorRef.current !== voiceIndicator) {
        setVoiceIndicator(indicatorRef.current)
      }
      raf = requestAnimationFrame(poll)
    }
    raf = requestAnimationFrame(poll)

    return () => {
      cancelAnimationFrame(raf)
      disconnect()
      voiceCleanup()
    }
  })

  const attentionTerminals = terminals.filter((t) => t.waiting_for_input)
  const activeChannel = useChannelStore((s) => {
    const ch = s.channels.find((c) => c.id === s.activeChannelId)
    return ch ?? null
  })
  const workstreamProjects = activeChannel?.project_names && activeChannel.project_names.length > 0
    ? activeChannel.project_names
    : (currentProject ? [currentProject] : [])
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (workstreamProjects.length === 0 || workstreamProjects.includes(p.project))
  )
  const collectionName = collections.find((c) => c.id === currentCollection)?.name
  const presets = useTerminalPresetsStore((s) => s.presets)

  const handleSpawnTerminal = async (command?: string) => {
    if (!currentProject) {
      toast("Select a project first", "warning")
      return
    }
    const session = await spawnTerminal(currentProject, command)
    if (session) {
      toast("Terminal started", "success")
    } else {
      toast("Failed to create terminal", "error")
    }
  }

  if (playingRecording) {
    return (
      <div className="h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden max-w-6xl mx-auto font-sans">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 flex-shrink-0">
          <button
            className="text-sm font-semibold text-gray-300 hover:text-white flex items-center gap-1.5 min-h-[44px]"
            onClick={() => setPlayingRecording(null)}
          >
            ← Back
          </button>
          <span className="text-sm font-medium text-gray-400">Recording Playback</span>
          <div className="w-10" />
        </div>
        <div className="flex-1 min-h-0 p-4">
          <RecordingPlayer
            recordingId={playingRecording}
            onBack={() => setPlayingRecording(null)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden max-w-7xl mx-auto font-sans select-none">
      {/* ── Top Touch Header (Accessible, Large Tap Targets) ── */}
      <header className="flex items-center justify-between px-4 py-2 border-b border-gray-800 flex-shrink-0 bg-gray-900/98 z-30 min-h-[54px]">
        {/* Left: Workstream Selector */}
        <button
          className="flex items-center gap-2 py-1.5 px-3 rounded-xl bg-gray-800/80 border border-gray-700/60 hover:bg-gray-750 transition-colors min-w-0 max-w-[240px] min-h-[44px]"
          onClick={() => setProjectSheetOpen(true)}
          aria-label="Select Workstream"
        >
          {collectionName && (
            <span className="text-xs uppercase font-bold tracking-wider text-blue-400 truncate max-w-[70px]">
              {collectionName}
            </span>
          )}
          <span className="text-sm font-bold text-gray-100 truncate">
            {(() => {
              const ch = useChannelStore.getState().channels.find(
                (c: { id: string }) => c.id === useChannelStore.getState().activeChannelId
              )
              return ch ? (ch as { name: string }).name.replace(/^#/, "") : (!currentProject ? "All" : currentProject)
            })()}
          </span>
          <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
        </button>

        {/* Center: View Mode Segmented Controls */}
        <div
          role="tablist"
          aria-label="Kiosk View Mode Switcher"
          className="flex items-center rounded-xl bg-gray-800/90 border border-gray-700/70 p-1 gap-1"
        >
          <button
            role="tab"
            aria-selected={viewMode === "terminal"}
            onClick={() => setViewMode("terminal")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all min-h-[38px] flex items-center gap-1.5 ${
              viewMode === "terminal"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/50"
            }`}
          >
            <span>📟</span>
            <span className="hidden sm:inline">Terminal</span>
          </button>
          <button
            role="tab"
            aria-selected={viewMode === "chat"}
            onClick={() => setViewMode("chat")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all min-h-[38px] flex items-center gap-1.5 ${
              viewMode === "chat"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/50"
            }`}
          >
            <span>💬</span>
            <span className="hidden sm:inline">Chat</span>
          </button>
          <button
            role="tab"
            aria-selected={viewMode === "goals"}
            onClick={() => setViewMode("goals")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all min-h-[38px] flex items-center gap-1.5 ${
              viewMode === "goals"
                ? "bg-amber-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/50"
            }`}
          >
            <span>🎯</span>
            <span className="hidden sm:inline">Goals</span>
          </button>
          <button
            role="tab"
            aria-selected={viewMode === "preview"}
            onClick={() => setViewMode("preview")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all min-h-[38px] flex items-center gap-1.5 ${
              viewMode === "preview"
                ? "bg-purple-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/50"
            }`}
          >
            <span>🌐</span>
            <span className="hidden sm:inline">Preview</span>
          </button>
          <button
            role="tab"
            aria-selected={viewMode === "services"}
            onClick={() => setViewMode("services")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all min-h-[38px] flex items-center gap-1.5 ${
              viewMode === "services"
                ? "bg-emerald-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/50"
            }`}
          >
            <span>⚡</span>
            <span className="hidden sm:inline">Services</span>
          </button>
          <button
            role="tab"
            aria-selected={viewMode === "split"}
            onClick={() => setViewMode("split")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all min-h-[38px] flex items-center gap-1.5 ${
              viewMode === "split"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/50"
            }`}
          >
            <span>◫</span>
            <span className="hidden sm:inline">Split</span>
          </button>
        </div>

        {/* Right: Status, Voice & Menu */}
        <div className="flex items-center gap-2">
          <button
            onClick={voice.toggle}
            className={`w-10 h-10 rounded-full flex items-center justify-center text-base transition-all ${
              voice.listening
                ? "bg-red-600 text-white animate-pulse shadow-md"
                : "bg-gray-800 text-gray-300 hover:text-white hover:bg-gray-700"
            }`}
            title={voice.listening ? "Stop voice dictation" : "Start voice dictation"}
            aria-label="Voice Dictation"
          >
            🎤
          </button>

          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-gray-850 border border-gray-800"
            title={connected ? `Connected to HomeBase (${serverState})` : "Disconnected from HomeBase"}
          >
            <span
              className={`w-2.5 h-2.5 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500"}`}
            />
            <span className="text-xs text-gray-400 font-mono hidden md:inline">
              {getClientName() || getClientId().slice(0, 10)} ({serverState})
            </span>
          </div>

          <button
            className="w-10 h-10 rounded-xl flex items-center justify-center text-gray-300 hover:text-white hover:bg-gray-800 transition-colors"
            onClick={() => setHamburgerOpen(true)}
            title="Menu"
            aria-label="Menu"
          >
            <Menu className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* ── Needs Attention Banner ── */}
      {(needsAttentionCount > 0 || attentionTerminals.length > 0) && (
        <div className="mx-4 mt-2 bg-amber-950/40 border border-amber-500/50 rounded-xl px-4 py-2.5 flex items-center justify-between flex-shrink-0 shadow-sm">
          <div className="flex items-center gap-2.5">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse" />
            <span className="text-xs font-semibold text-amber-200">
              {needsAttentionCount > 0
                ? `${needsAttentionCount} goal session${needsAttentionCount === 1 ? "" : "s"} need your review`
                : `Terminal prompt waiting for user input (${attentionTerminals.length})`}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {needsAttentionCount > 0 && (
              <button
                onClick={() => setViewMode("goals")}
                className="px-3 py-1.5 text-xs rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold transition-colors min-h-[36px]"
              >
                Review Goals →
              </button>
            )}
            {attentionTerminals.map((t) => (
              <button
                key={t.id}
                className="px-3 py-1.5 text-xs rounded-lg bg-yellow-700 hover:bg-yellow-600 text-yellow-100 font-semibold whitespace-nowrap min-h-[36px]"
                onClick={() => setTerminalOverlayId(t.id)}
              >
                Answer: {t.project || t.id.slice(0, 8)}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Main Canvas Viewport ── */}
      <main className="flex-1 min-h-0 overflow-hidden p-3">
        {/* Split Mode: Resizable / Toggleable Terminal & Companion Touch Panel */}
        {viewMode === "split" && (
          <div className="h-full grid grid-cols-1 lg:grid-cols-12 gap-3 min-h-0">
            <div className={`${sidePanelOpen ? "lg:col-span-7" : "lg:col-span-12"} flex flex-col min-h-0 bg-gray-950 rounded-xl border border-gray-800 overflow-hidden shadow-inner transition-all`}>
              <div className="flex items-center justify-between px-3 py-1.5 bg-gray-900 border-b border-gray-800 flex-shrink-0">
                <span className="text-xs font-semibold text-gray-300 font-mono">📟 Active Terminal</span>
                <button
                  type="button"
                  onClick={toggleSidePanel}
                  className="px-2.5 py-1 text-xs rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center gap-1.5 transition-colors border border-gray-700 shadow-sm"
                  title={sidePanelOpen ? "Collapse Companion Panel" : "Expand Companion Panel"}
                >
                  <span>{sidePanelOpen ? "◧ Hide Side Panel" : "◫ Show Side Panel"}</span>
                </button>
              </div>
              <div className="flex-1 min-h-0">
                <EmbeddedTerminal onTerminalSendReady={(send) => { terminalSendRef.current = send }} />
              </div>
            </div>

            {sidePanelOpen && (
              <div className="lg:col-span-5 flex flex-col min-h-0 bg-gray-850 rounded-xl border border-gray-800 overflow-hidden shadow-sm">
                <KioskSideTouchPanel
                  activeTab={activeSideTab}
                  onSelectTab={setActiveSideTab}
                  onClose={() => setSidePanelOpen(false)}
                  currentProject={currentProject}
                  processesWithPorts={processesWithPorts}
                  onLogs={(id, name) => openProcessLog(id, name)}
                  onOpenSession={(session) => {
                    useBrowserStore.getState().setActiveSession({
                      id: session.session_id,
                      target_url: "",
                      status: "running",
                      viewer_url: session.viewer_url,
                    } as BrowserSession)
                    setBrowserFullscreen(true)
                  }}
                  onPlayRecording={(id) => setPlayingRecording(id)}
                  onEditProject={() => setProjectSettingsOpen(true)}
                  onCreateTask={() => setCreateTaskOpen(true)}
                  onSpawnTerminal={handleSpawnTerminal}
                  onOpenBrowserUrl={() => setBrowserUrlOpen(true)}
                  onOpenActivity={() => setActivityOpen(true)}
                  onOpenMenu={() => setHamburgerOpen(true)}
                />
              </div>
            )}
          </div>
        )}

        {/* Focused Terminal View (100% full width) */}
        {viewMode === "terminal" && (
          <div className="h-full flex flex-col min-h-0 bg-gray-950 rounded-xl border border-gray-800 overflow-hidden shadow-inner">
            <EmbeddedTerminal onTerminalSendReady={(send) => { terminalSendRef.current = send }} />
          </div>
        )}

        {/* Focused Chat View (100% full width) */}
        {viewMode === "chat" && (
          <div className="h-full flex flex-col min-h-0 bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
            <MobileChannelPanel
              inline
              hideHeader
              onOpenTerminal={async (project) => {
                const proj = project || currentProject
                if (!proj) return
                await spawnTerminal(proj)
                setViewMode("terminal")
              }}
              onCreateTask={() => setCreateTaskOpen(true)}
              onOpenBrowser={() => setBrowserUrlOpen(true)}
              onOpenActivity={() => setActivityOpen(true)}
              onOpenMenu={() => setHamburgerOpen(true)}
              onEditProject={() => setProjectSettingsOpen(true)}
              onSystemSettings={() => setSystemSettingsOpen(true)}
            />
          </div>
        )}

        {/* Focused Live Preview View (100% full width) */}
        {viewMode === "preview" && (
          <div className="h-full flex flex-col min-h-0 bg-gray-900 rounded-xl border border-gray-800 overflow-hidden p-4">
            <div className="flex items-center justify-between pb-3 border-b border-gray-800 flex-shrink-0">
              <span className="text-sm font-bold text-gray-200">Shared Browser &amp; Live App Previews</span>
              <button
                onClick={() => setBrowserUrlOpen(true)}
                className="px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold"
              >
                + Launch New URL
              </button>
            </div>
            <div className="flex-1 overflow-y-auto pt-3">
              <BrowserCard
                onOpenSession={(session) => {
                  useBrowserStore.getState().setActiveSession({
                    id: session.session_id,
                    target_url: "",
                    status: "running",
                    viewer_url: session.viewer_url,
                  } as BrowserSession)
                  setBrowserFullscreen(true)
                }}
                onPlayRecording={(id) => setPlayingRecording(id)}
              />
            </div>
          </div>
        )}

        {/* Focused Services View (100% full width) */}
        {viewMode === "services" && (
          <div className="h-full flex flex-col min-h-0 bg-gray-900 rounded-xl border border-gray-800 overflow-hidden p-4">
            <div className="flex-1 overflow-y-auto">
              <ProcessesCard onLogs={(id, name) => openProcessLog(id, name)} />
            </div>
          </div>
        )}

        {/* Focused Goals View (100% full width) */}
        {viewMode === "goals" && (
          <div className="h-full flex flex-col min-h-0 bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
            <WorkstreamGoalsWorkspace />
          </div>
        )}
      </main>

      {/* Floating resume-preview pill */}
      {browserActiveSession && !browserFullscreen && (
        <button
          className="fixed bottom-20 right-6 z-[100] flex items-center gap-2 px-4 py-2 rounded-full bg-purple-600 text-white text-sm shadow-xl font-medium"
          onClick={() => setBrowserFullscreen(true)}
        >
          <span className="w-2.5 h-2.5 rounded-full bg-green-400 animate-pulse" />
          Resume Live Preview
        </button>
      )}

      {/* Floating voice indicator */}
      {voiceIndicator && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-[100] max-w-lg px-5 py-2.5 rounded-full bg-gray-850/95 border border-blue-500/60 shadow-2xl backdrop-blur-md">
          <div className="flex items-center gap-2.5">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${voice.listening ? "bg-red-500 animate-pulse" : "bg-blue-500"}`} />
            <span className="text-sm font-medium text-gray-200 truncate">{voiceIndicator}</span>
          </div>
        </div>
      )}

      {/* ── Kiosk Touch Action Bar (Bottom) ── */}
      <KioskActionBar
        onCreateTask={() => setViewMode("goals")}
        onSpawnTerminal={() => {
          if (!currentProject) { toast("Select a project first", "warning"); return }
          void handleSpawnTerminal("")
        }}
        onOpenActivity={() => setActivityOpen(true)}
        onOpenMenu={() => setHamburgerOpen(true)}
        voice={voice}
      />

      {/* ── Modals & Overlays ── */}
      {terminalOverlayId && (
        <TerminalOverlay
          sessionId={terminalOverlayId}
          onClose={() => setTerminalOverlayId(null)}
          onSwitch={(id) => setTerminalOverlayId(id)}
        />
      )}

      {projectSheetOpen && (
        <WorkspaceSelectorSheet
          position="top"
          onClose={() => setProjectSheetOpen(false)}
          onSelect={(channelId) => {
            const { channels, selectChannel } = useChannelStore.getState()
            selectChannel(channelId)
            const ch = channels.find((c: { id: string; project_names?: string[] }) => c.id === channelId)
            if (ch && (ch as { project_names?: string[] }).project_names?.[0]) {
              useProjectStore.getState().selectProject((ch as { project_names: string[] }).project_names[0])
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
          onWorkstreamSettings={() => { setHamburgerOpen(false); setWorkstreamSettingsOpen(true) }}
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
                className="w-full flex items-center gap-3 px-4 py-3 rounded-lg bg-gray-800 hover:bg-gray-750 text-left border border-gray-700/60 min-h-[48px] transition-colors"
                onClick={() => {
                  setAgentPickerOpen(false)
                  handleSpawnTerminal(preset.command)
                }}
              >
                <span className="w-8 h-8 rounded-lg bg-gray-700 flex items-center justify-center text-sm font-mono font-bold text-gray-200">
                  {preset.icon || "💻"}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-gray-200">{preset.label}</div>
                  <div className="text-xs text-gray-400">{preset.description}</div>
                </div>
                <span className="text-xs text-gray-500 font-mono">{preset.command || "$SHELL"}</span>
              </button>
            ))}
          </div>
        </Sheet>
      )}

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

      {workstreamSettingsOpen && activeChannel && (
        <ChannelSettings channelId={activeChannel.id} onClose={() => setWorkstreamSettingsOpen(false)} />
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

      <FloatingAgentPanel channel="kiosk" />
      <GlobalTextInput />
    </div>
  )
}

/** ── Touch-First Kiosk Side Panel with Horizontal Tabs ── */
function KioskSideTouchPanel({
  activeTab,
  onSelectTab,
  currentProject,
  processesWithPorts,
  onLogs,
  onOpenSession,
  onPlayRecording,
  onEditProject,
  onCreateTask,
  onSpawnTerminal,
  onOpenBrowserUrl,
  onOpenActivity,
  onOpenMenu,
  onClose,
}: {
  activeTab: KioskSideTab
  onSelectTab: (tab: KioskSideTab) => void
  currentProject: string | null
  processesWithPorts: Action[]
  onLogs: (id: string, name: string) => void
  onOpenSession: (session: { viewer_url: string; session_id: string }) => void
  onPlayRecording: (id: string) => void
  onEditProject: () => void
  onCreateTask: () => void
  onSpawnTerminal: (command?: string) => Promise<void>
  onOpenBrowserUrl: () => void
  onOpenActivity: () => void
  onOpenMenu: () => void
  onClose?: () => void
}) {
  const tabs: { id: KioskSideTab; label: string; icon: string }[] = [
    { id: "chat", label: "Chat", icon: "💬" },
    { id: "services", label: "Services", icon: "⚡" },
    { id: "preview", label: "Preview", icon: "🌐" },
    { id: "goals", label: "Goals", icon: "🎯" },
    { id: "project", label: "Project", icon: "📁" },
    { id: "attachments", label: "Context", icon: "📎" },
  ]

  return (
    <div className="flex flex-col h-full min-h-0 bg-gray-850 overflow-hidden">
      {/* Horizontal, Touch-Friendly Tab Bar (Minimum 44px Height) */}
      <div
        role="tablist"
        aria-label="Kiosk Side Panel Navigation"
        className="flex items-center gap-1.5 px-3 py-2 bg-gray-900 border-b border-gray-800 overflow-x-auto flex-shrink-0 scrollbar-none min-h-[50px]"
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={activeTab === t.id}
            onClick={() => onSelectTab(t.id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all min-h-[38px] ${
              activeTab === t.id
                ? "bg-blue-600 text-white shadow-sm"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
            }`}
          >
            <span>{t.icon}</span>
            <span>{t.label}</span>
          </button>
        ))}

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="ml-auto px-2 py-1 text-xs rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors flex-shrink-0 flex items-center gap-1 border border-gray-700"
            title="Hide Companion Panel"
            aria-label="Hide Companion Panel"
          >
            <span>✕</span>
            <span className="hidden sm:inline text-[11px]">Hide</span>
          </button>
        )}
      </div>

      {/* Side Tab Body Viewport */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        {activeTab === "chat" && (
          <MobileChannelPanel
            inline
            hideHeader
            onOpenTerminal={async (project) => {
              const proj = project || currentProject
              if (!proj) return
              await onSpawnTerminal(proj)
            }}
            onCreateTask={onCreateTask}
            onOpenBrowser={onOpenBrowserUrl}
            onOpenActivity={onOpenActivity}
            onOpenMenu={onOpenMenu}
            onEditProject={onEditProject}
          />
        )}

        {activeTab === "services" && (
          <div className="space-y-3">
            <ProcessesCard onLogs={onLogs} />
          </div>
        )}

        {activeTab === "preview" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-gray-800">
              <span className="text-xs font-bold text-gray-300">Live Browser Sessions</span>
              <button
                onClick={onOpenBrowserUrl}
                className="px-2.5 py-1 rounded bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium"
              >
                + Open URL
              </button>
            </div>
            {processesWithPorts.length > 0 && (
              <div className="space-y-1.5 pb-2 border-b border-gray-800">
                <span className="text-[11px] uppercase font-semibold text-gray-400 tracking-wider">Running Ports</span>
                <div className="flex flex-wrap gap-1.5">
                  {processesWithPorts.map((p) => (
                    <button
                      key={p.id}
                      onClick={async () => {
                        try {
                          const params = new URLSearchParams({ target_url: `http://localhost:${p.port}` })
                          const session = await POST<BrowserSession>(`/browser/start?${params}`)
                          if (session) onOpenSession({ viewer_url: session.viewer_url || "", session_id: session.id })
                        } catch {
                          // fallback handled in store
                        }
                      }}
                      className="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-750 text-xs font-medium text-purple-300 border border-purple-500/30 flex items-center gap-1.5 min-h-[36px]"
                    >
                      <span className="w-2 h-2 rounded-full bg-emerald-400" />
                      <span>{p.name || p.id}</span>
                      <span className="text-purple-400 font-mono">:{p.port}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <BrowserCard
              onOpenSession={onOpenSession}
              onPlayRecording={onPlayRecording}
            />
          </div>
        )}

        {activeTab === "goals" && (
          <div className="h-full">
            <WorkstreamGoalsWorkspace />
          </div>
        )}

        {activeTab === "project" && (
          <ProjectCard onEdit={onEditProject} />
        )}

        {activeTab === "attachments" && (
          <ContextsCard defaultExpanded />
        )}
      </div>
    </div>
  )
}

function KioskActionBar({
  onCreateTask,
  onSpawnTerminal,
  onOpenActivity,
  onOpenMenu,
  voice,
}: {
  onCreateTask: () => void
  onSpawnTerminal: () => void
  onOpenActivity: () => void
  onOpenMenu: () => void
  voice: { listening: boolean; toggle: () => void }
}) {
  const phone = useStateStore((s) => s.phone)
  const toast = useUIStore((s) => s.toast)

  const handlePhone = async () => {
    if (phone?.active) {
      try { await POST("/voice/hangup"); toast("Call ended", "info") }
      catch { toast("Failed to hang up", "error") }
      return
    }
    if (phone?.configured) {
      try { await POST("/voice/call", { client_id: getClientId() }); toast("Calling...", "info") }
      catch { toast("Failed to call", "error") }
      return
    }
    toast("Phone not configured", "warning")
  }

  return (
    <div className="flex-shrink-0 border-t border-gray-800 bg-gray-900/98 z-30">
      <div className="max-w-7xl mx-auto px-4 py-2">
        <div className="flex gap-2.5">
          <button
            className="flex-1 py-2.5 text-sm rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-bold shadow-sm transition-colors min-h-[46px]"
            onClick={onCreateTask}
          >
            + New Goal
          </button>
          <button
            className="flex-1 py-2.5 text-sm rounded-xl bg-gray-800 hover:bg-gray-750 text-gray-200 font-mono font-medium border border-gray-700/60 transition-colors min-h-[46px]"
            onClick={onSpawnTerminal}
          >
            &gt;_ New Shell
          </button>
          <button
            className="flex-1 py-2.5 text-sm rounded-xl bg-gray-800 hover:bg-gray-750 text-gray-200 font-semibold border border-gray-700/60 transition-colors min-h-[46px]"
            onClick={onOpenActivity}
          >
            📜 Activity Timeline
          </button>

          {/* Voice Dictation Button */}
          <button
            className={`w-12 py-2 rounded-xl flex items-center justify-center text-lg flex-shrink-0 transition-all min-h-[46px] ${
              voice.listening
                ? "bg-red-600 text-white animate-pulse shadow-md"
                : "bg-gray-800 text-gray-300 hover:text-white hover:bg-gray-750 border border-gray-700/60"
            }`}
            onClick={voice.toggle}
            title={voice.listening ? "Stop voice dictation" : "Start voice dictation"}
            aria-label="Voice Dictation"
          >
            🎤
          </button>

          {/* Phone Button */}
          <button
            className={`w-12 py-2 rounded-xl flex items-center justify-center text-lg flex-shrink-0 transition-all min-h-[46px] ${
              phone?.active
                ? "bg-emerald-600 text-white animate-pulse shadow-md"
                : phone?.configured
                  ? "bg-gray-800 text-gray-300 hover:text-white hover:bg-gray-750 border border-gray-700/60"
                  : "bg-gray-800 text-gray-500 opacity-40 border border-gray-800"
            }`}
            onClick={handlePhone}
            title={phone?.active ? "Hang up" : phone?.configured ? "Call" : "Phone not configured"}
            aria-label="Phone Call"
          >
            📞
          </button>

          {/* Menu Button */}
          <button
            className="w-12 py-2 rounded-xl flex items-center justify-center text-gray-300 hover:text-white bg-gray-800 hover:bg-gray-750 border border-gray-700/60 flex-shrink-0 transition-colors min-h-[46px]"
            onClick={onOpenMenu}
            title="Menu"
            aria-label="Menu"
          >
            ☰
          </button>
        </div>
      </div>
    </div>
  )
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
      <div className="space-y-3 font-sans">
        <div className="flex gap-2">
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleShared() }}
            placeholder="localhost:3000 or any URL"
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-2.5 text-sm text-gray-200 outline-none focus:border-blue-500 min-h-[44px]"
            autoFocus
          />
        </div>
        <button
          className="w-full py-2.5 text-sm font-semibold rounded-lg bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-50 min-h-[44px] transition-colors"
          onClick={() => handleShared()}
          disabled={loading || !url.trim()}
        >
          {loading ? "Starting..." : "Open Shared Session"}
        </button>
        <p className="text-xs text-gray-400">
          Shared session — you and the agent see the same browser.
        </p>

        {processesWithPorts.length > 0 && (
          <div>
            <p className="text-xs text-gray-400 uppercase font-semibold mb-1.5">Running services</p>
            <div className="space-y-1.5">
              {processesWithPorts.map((p) => (
                <button
                  key={p.id}
                  className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg bg-gray-800 hover:bg-gray-750 text-left border border-gray-700/60 min-h-[44px] transition-colors"
                  onClick={() => handleShared(`http://localhost:${p.port}`)}
                >
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
                  <span className="flex-1 text-sm font-medium text-gray-200 truncate">
                    {p.name || p.id}
                  </span>
                  <span className="text-sm font-mono text-purple-400">:{p.port}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </Sheet>
  )
}
