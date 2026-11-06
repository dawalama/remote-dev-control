import { useState, useMemo, useRef } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore, getActiveProjectNames } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { AttentionCard } from "@/features/mobile/attention-card"
import { TerminalsCard } from "@/features/mobile/terminals-card"
import { ProcessesCard } from "@/features/mobile/processes-card"
import { ContextsCard } from "@/features/mobile/contexts-card"
import { DictationCard } from "@/features/mobile/dictation-card"
import { BrowserCard } from "@/features/mobile/browser-card"
import { GlobalTextInput } from "@/components/global-text-input"
import { useBrowserStore } from "@/stores/browser-store"
import { TerminalOverlay } from "@/features/mobile/terminal-overlay"
import { HamburgerSheet } from "@/features/mobile/hamburger-sheet"
import { CreateTaskSheet } from "@/features/mobile/create-task-sheet"
import { ActivitySheet } from "@/features/mobile/activity-sheet"
import { UnifiedBrowserFullscreen } from "@/features/browser/unified-browser-panel"
import { AddProjectSheet } from "@/features/mobile/add-project-sheet"
import { ProcessLogOverlay } from "@/features/mobile/process-log-overlay"
import { PairedDevicesSheet } from "@/features/mobile/paired-devices-sheet"
import { ProjectSettingsModal } from "@/features/modals/project-settings"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { ChannelSettings } from "@/features/channels/channel-settings"
import { CollectionsManagerModal } from "@/features/modals/collections-manager"
import { RecordingPlayer } from "@/features/browser/recording-player"
import { FloatingAgentPanel } from "@/features/browser/floating-agent-panel"
import { useChannelStore } from "@/stores/channel-store"
import { MobileChannelPanel } from "@/features/channels/mobile-channel-panel"
import { WorkspaceSelectorSheet } from "@/features/channels/mobile-workspace-selector"
import { WorkstreamGoalsWorkspace } from "@/features/sessions/sessions-hub"
import { Sheet } from "@/features/mobile/sheet"
import { POST } from "@/lib/api"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { useWorkstreamVoice } from "@/hooks/use-workstream-voice"
import { useMountEffect } from "@/hooks/use-mount-effect"
import type { BrowserSession, Action } from "@/types"

export type MobileNavTab = "chat" | "terminals" | "services" | "goals"

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
  const needsAttentionCount = useUIStore((s) => s.needsAttentionCount)
  const workstreamSettingsOpen = useUIStore((s) => s.workstreamSettingsOpen)
  const setWorkstreamSettingsOpen = useUIStore((s) => s.setWorkstreamSettingsOpen)
  const cycleActiveProject = useProjectStore((s) => s.cycleActiveProject)

  const activeNames = useMemo(() => {
    void terminals; void processes
    return getActiveProjectNames()
  }, [terminals, processes])

  // Navigation tab state
  const [activeTab, setActiveTab] = useState<MobileNavTab>("chat")

  // Overlays and sheets
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
  const [collectionsOpen, setCollectionsOpen] = useState(false)
  const [processLog, setProcessLog] = useState<{ id: string; name: string } | null>(null)
  const [pairedDevicesOpen, setPairedDevicesOpen] = useState(false)
  const [browserUrlOpen, setBrowserUrlOpen] = useState(false)
  const [playingRecording, setPlayingRecording] = useState<string | null>(null)
  const [agentPickerOpen, setAgentPickerOpen] = useState(false)
  const [moreToolsOpen, setMoreToolsOpen] = useState(false)

  // Presets and Channels
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)
  const loadChannels = useChannelStore((s) => s.loadChannels)
  const selectChannel = useChannelStore((s) => s.selectChannel)
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const messages = useChannelStore((s) => s.messages)

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

  // On-mount setup (strictly no useEffect)
  useMountEffect(() => {
    loadProjects()
    loadCollections()
    loadPresets()
    loadChannels()
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

  // Attention & Status computation
  const attentionTerminals = terminals.filter((t) => t.waiting_for_input)
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (!currentProject || p.project === currentProject)
  )
  const collectionName = collections.find((c) => c.id === currentCollection)?.name
  const currentChannel = channels.find((c) => c.id === activeChannelId)
  const channelTitle = currentChannel
    ? currentChannel.name.replace(/^#/, "")
    : (!currentProject ? "All Workstreams" : currentProject)

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
      const activeChId = useChannelStore.getState().activeChannelId
      if (activeChId) {
        url += `&channel_id=${encodeURIComponent(activeChId)}`
      }
      const session = await POST<{ id: string }>(url)
      if (session?.id) setTerminalOverlayId(session.id)
    } catch {
      toast("Failed to create terminal", "error")
    }
  }

  // Fullscreen recording playback overlay
  if (playingRecording) {
    return (
      <div className={`h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden ${maxW} mx-auto`}>
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 flex-shrink-0">
          <button
            className="text-sm text-gray-300 hover:text-white flex items-center gap-1 font-medium min-h-[44px]"
            onClick={() => setPlayingRecording(null)}
          >
            ← Back
          </button>
          <span className="text-xs text-gray-400">Recording Playback</span>
          <div className="w-8" />
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
    <div className={`h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden ${maxW} mx-auto font-sans select-none`}>
      {/* ── Top Bar (Minimal, High-Contrast & Accessible) ── */}
      <header className="flex items-center justify-between px-3.5 py-2 border-b border-gray-800 flex-shrink-0 bg-gray-900/95 backdrop-blur z-30 min-h-[50px]">
        {/* Workstream Switcher */}
        <div className="flex items-center gap-1 min-w-0">
          {activeNames.length >= 2 && (
            <button
              className="text-gray-400 hover:text-gray-100 text-base px-1.5 py-1 min-h-[40px] flex items-center"
              onClick={(e) => { e.stopPropagation(); cycleActiveProject(-1) }}
              aria-label="Previous active project"
            >
              ‹
            </button>
          )}
          <button
            className="flex items-center gap-1.5 py-1 px-2.5 rounded-lg bg-gray-800/80 border border-gray-700/60 hover:bg-gray-750 transition-colors min-w-0 max-w-[210px] min-h-[38px]"
            onClick={() => setWorkspaceSelectorOpen(true)}
            aria-label={`Select Workstream. Currently ${channelTitle}`}
          >
            {collectionName && (
              <span className="text-[10px] uppercase font-bold tracking-wider text-blue-400 truncate max-w-[65px]">
                {collectionName}
              </span>
            )}
            <span className="text-sm font-semibold text-gray-100 truncate">
              {channelTitle}
            </span>
            <span className="text-gray-400 text-xs flex-shrink-0">▼</span>
          </button>
          {activeNames.length >= 2 && (
            <button
              className="text-gray-400 hover:text-gray-100 text-base px-1.5 py-1 min-h-[40px] flex items-center"
              onClick={(e) => { e.stopPropagation(); cycleActiveProject(1) }}
              aria-label="Next active project"
            >
              ›
            </button>
          )}
        </div>

        {/* Right Status & Controls */}
        <div className="flex items-center gap-1.5">
          {browserActiveSession && !browserFullscreen && (
            <button
              className="flex items-center gap-1 px-2.5 py-1 rounded-full bg-purple-600 text-white text-xs font-medium shadow-sm animate-pulse min-h-[34px]"
              onClick={() => setBrowserFullscreen(true)}
              aria-label="Resume Live Preview"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
              Preview
            </button>
          )}

          <button
            onClick={voice.toggle}
            className={`w-9 h-9 rounded-full flex items-center justify-center text-sm transition-all ${
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
            className="flex items-center gap-1 px-1 py-1"
            title={connected ? `Connected (${serverState})` : "Disconnected from HomeBase"}
          >
            <span
              className={`w-2.5 h-2.5 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500"}`}
            />
          </div>

          <button
            className="w-9 h-9 rounded-lg flex items-center justify-center text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors text-lg"
            onClick={() => setHamburgerOpen(true)}
            aria-label="Open Settings and Menu"
          >
            ☰
          </button>
        </div>
      </header>

      {/* Floating voice transcription indicator */}
      {voiceIndicator && (
        <div className="fixed top-14 left-1/2 -translate-x-1/2 z-[100] max-w-sm px-4 py-2 rounded-full bg-gray-850/95 border border-blue-500/50 shadow-xl backdrop-blur-md">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${voice.listening ? "bg-red-500 animate-pulse" : "bg-blue-500"}`} />
            <span className="text-xs text-gray-200 truncate">{voiceIndicator}</span>
          </div>
        </div>
      )}

      {/* ── Active Tab Viewport (100% Flex Height, Ergonomic) ── */}
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden bg-gray-900">
        {/* Tab 1: 💬 Workstream Chat (Inline, Native, No Modal) */}
        {activeTab === "chat" && (
          <MobileChannelPanel
            inline
            hideHeader
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
            onCreateTask={() => setActiveTab("goals")}
            onOpenBrowser={() => setBrowserUrlOpen(true)}
            onOpenActivity={() => setActivityOpen(true)}
            onOpenMenu={() => setHamburgerOpen(true)}
            onEditProject={() => setProjectSettingsOpen(true)}
            onSystemSettings={() => setSystemSettingsOpen(true)}
          />
        )}

        {/* Tab 2: 💻 Terminals & Commands */}
        {activeTab === "terminals" && (
          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
            {/* Attention banner if waiting for terminal input */}
            {attentionTerminals.length > 0 && (
              <AttentionCard
                terminals={attentionTerminals}
                onOpen={(id) => setTerminalOverlayId(id)}
              />
            )}

            <TerminalsCard onOpenTerminal={(id) => setTerminalOverlayId(id)} />

            {/* Quick Starter Presets Bar */}
            <div className="rounded-lg border border-gray-800 bg-gray-850/70 p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  Terminal Starters
                </span>
                <button
                  onClick={() => setAgentPickerOpen(true)}
                  className="text-xs text-blue-400 hover:text-blue-300 font-medium"
                >
                  View all presets →
                </button>
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                {presets.slice(0, 4).map((preset) => (
                  <button
                    key={preset.id}
                    onClick={() => handleSpawnTerminal(preset.command)}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-750 text-xs font-medium text-gray-200 border border-gray-700/60 transition-colors"
                  >
                    <span>{preset.icon || "💻"}</span>
                    <span>{preset.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* More Tools Accordion (Activity, Browser, Attachments) */}
            <div className="rounded-lg border border-gray-800 bg-gray-850/50 p-3 space-y-2">
              <button
                type="button"
                onClick={() => setMoreToolsOpen((v) => !v)}
                className="flex w-full items-center justify-between text-xs font-semibold text-gray-400 uppercase tracking-wider hover:text-gray-200 min-h-[36px]"
              >
                <span>Diagnostics &amp; Context</span>
                <span className="text-xs normal-case text-gray-400 font-medium">
                  {moreToolsOpen ? "▴ Hide" : "▾ Expand"}
                </span>
              </button>
              {moreToolsOpen && (
                <div className="mt-2 space-y-3 border-t border-gray-800/80 pt-2.5">
                  <button
                    type="button"
                    onClick={() => setActivityOpen(true)}
                    className="w-full rounded-lg bg-gray-800 px-3 py-2.5 text-xs font-medium text-gray-200 hover:bg-gray-750 text-left flex items-center justify-between transition-colors min-h-[44px]"
                  >
                    <span>📜 View Workstream Activity Timeline</span>
                    <span className="text-gray-400">→</span>
                  </button>
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
                  <ContextsCard />
                </div>
              )}
            </div>
            <PhoneDictation />
          </div>
        )}

        {/* Tab 3: ⚡ Services & Actions */}
        {activeTab === "services" && (
          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
            {processesWithPorts.length > 0 && (
              <div className="rounded-lg border border-purple-500/40 bg-purple-950/20 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-purple-300">
                    Running Web Services ({processesWithPorts.length})
                  </span>
                  <button
                    onClick={() => setBrowserUrlOpen(true)}
                    className="text-xs text-purple-300 hover:text-white font-medium underline"
                  >
                    Open Custom URL
                  </button>
                </div>
                <div className="space-y-1.5 pt-1">
                  {processesWithPorts.map((p) => (
                    <div
                      key={p.id}
                      className="flex items-center justify-between px-3 py-2 rounded-lg bg-gray-800/90 border border-gray-700/60"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
                        <span className="text-xs font-medium text-gray-200 truncate">{p.name || p.id}</span>
                        <span className="text-xs font-mono text-purple-400">:{p.port}</span>
                      </div>
                      <button
                        onClick={async () => {
                          try {
                            const params = new URLSearchParams({ target_url: `http://localhost:${p.port}` })
                            const session = await POST<BrowserSession>(`/browser/start?${params}`)
                            if (session) {
                              useBrowserStore.getState().setActiveSession(session)
                              setBrowserFullscreen(true)
                            }
                          } catch {
                            toast("Failed to launch preview", "error")
                          }
                        }}
                        className="px-2.5 py-1 rounded bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium transition-colors"
                      >
                        Preview →
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <ProcessesCard onLogs={(id, name) => setProcessLog({ id, name })} />
          </div>
        )}

        {/* Tab 4: 🎯 Goals & Supervised Sessions */}
        {activeTab === "goals" && (
          <div className="flex-1 flex flex-col min-h-0 bg-gray-900">
            <WorkstreamGoalsWorkspace />
          </div>
        )}
      </main>

      {/* ── Ergonomic Bottom Navigation Bar (WCAG 2.5.5 ≥ 48px touch targets) ── */}
      <nav
        role="tablist"
        aria-label="Mobile View Navigation"
        className="flex-shrink-0 border-t border-gray-800 bg-gray-900/98 backdrop-blur px-2 py-1.5 z-40 safe-area-bottom shadow-lg"
      >
        <div className="grid grid-cols-4 gap-1">
          {/* Chat Tab */}
          <button
            role="tab"
            aria-selected={activeTab === "chat"}
            aria-label="Agent Chat"
            onClick={() => setActiveTab("chat")}
            className={`flex flex-col items-center justify-center py-1 px-1 rounded-xl transition-all min-h-[50px] ${
              activeTab === "chat"
                ? "bg-blue-600/20 text-blue-400 font-bold"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50 font-medium"
            }`}
          >
            <span className="text-lg relative leading-none mb-1">
              💬
              {messages.length > 0 && activeTab !== "chat" && (
                <span className="absolute -top-1 -right-2 px-1.5 py-0.2 bg-blue-600 text-white text-[9px] font-bold rounded-full">
                  {messages.length}
                </span>
              )}
            </span>
            <span className="text-[11px] tracking-tight">Chat</span>
          </button>

          {/* Terminals Tab */}
          <button
            role="tab"
            aria-selected={activeTab === "terminals"}
            aria-label="Terminals and Commands"
            onClick={() => setActiveTab("terminals")}
            className={`flex flex-col items-center justify-center py-1 px-1 rounded-xl transition-all min-h-[50px] ${
              activeTab === "terminals"
                ? "bg-blue-600/20 text-blue-400 font-bold"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50 font-medium"
            }`}
          >
            <span className="text-lg relative leading-none mb-1">
              💻
              {attentionTerminals.length > 0 && (
                <span className="absolute -top-1 -right-2 w-2.5 h-2.5 bg-amber-400 rounded-full animate-ping" />
              )}
            </span>
            <span className="text-[11px] tracking-tight">Terminals</span>
          </button>

          {/* Services Tab */}
          <button
            role="tab"
            aria-selected={activeTab === "services"}
            aria-label="Services and Actions"
            onClick={() => setActiveTab("services")}
            className={`flex flex-col items-center justify-center py-1 px-1 rounded-xl transition-all min-h-[50px] ${
              activeTab === "services"
                ? "bg-blue-600/20 text-blue-400 font-bold"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50 font-medium"
            }`}
          >
            <span className="text-lg relative leading-none mb-1">
              ⚡
              {processesWithPorts.length > 0 && (
                <span className="absolute -top-1 -right-2 px-1 py-0.2 bg-emerald-600 text-white text-[9px] font-bold rounded-full">
                  {processesWithPorts.length}
                </span>
              )}
            </span>
            <span className="text-[11px] tracking-tight">Services</span>
          </button>

          {/* Goals Tab */}
          <button
            role="tab"
            aria-selected={activeTab === "goals"}
            aria-label="Goals and Supervision Hub"
            onClick={() => setActiveTab("goals")}
            className={`flex flex-col items-center justify-center py-1 px-1 rounded-xl transition-all min-h-[50px] ${
              activeTab === "goals"
                ? "bg-blue-600/20 text-blue-400 font-bold"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50 font-medium"
            }`}
          >
            <span className="text-lg relative leading-none mb-1">
              🎯
              {needsAttentionCount > 0 && (
                <span className="absolute -top-1 -right-2 px-1.5 py-0.2 bg-amber-500 text-black text-[9px] font-extrabold rounded-full animate-pulse">
                  {needsAttentionCount}
                </span>
              )}
            </span>
            <span className="text-[11px] tracking-tight">Goals</span>
          </button>
        </div>
      </nav>

      {/* ── Overlays, Sheets & Modals ── */}
      {terminalOverlayId && (
        <TerminalOverlay
          sessionId={terminalOverlayId}
          onClose={() => setTerminalOverlayId(null)}
          onSwitch={(id) => setTerminalOverlayId(id)}
        />
      )}

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
          onCreateTask={() => { setHamburgerOpen(false); setActiveTab("goals"); useUIStore.getState().setSessionsHubOpen(true) }}
          onActivity={() => { setHamburgerOpen(false); setActivityOpen(true) }}
          onProjectSettings={() => { setHamburgerOpen(false); setProjectSettingsOpen(true) }}
          onWorkstreamSettings={() => { setHamburgerOpen(false); setWorkstreamSettingsOpen(true) }}
          onCollections={() => { setHamburgerOpen(false); setCollectionsOpen(true) }}
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
                <span className="w-9 h-9 rounded-lg bg-gray-700 flex items-center justify-center text-sm font-mono font-bold text-gray-200">
                  {preset.icon || "💻"}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-gray-200">{preset.label}</div>
                  <div className="text-xs text-gray-400 truncate">{preset.description}</div>
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

      {workstreamSettingsOpen && activeChannelId && (
        <ChannelSettings channelId={activeChannelId} onClose={() => setWorkstreamSettingsOpen(false)} />
      )}

      {collectionsOpen && (
        <CollectionsManagerModal onClose={() => setCollectionsOpen(false)} />
      )}

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
        <div className="flex gap-2">
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleShared() }}
            placeholder="localhost:3000 or any URL"
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 min-h-[44px]"
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
        <p className="text-[11px] text-gray-400">
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
                  <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
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
