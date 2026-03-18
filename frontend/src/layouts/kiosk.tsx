import { useEffect, useRef, useState } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { ProcessesCard } from "@/features/mobile/processes-card"
import { TasksCard } from "@/features/mobile/tasks-card"
import { ContextsCard } from "@/features/mobile/contexts-card"
import { BrowserCard } from "@/features/mobile/browser-card"
import { TerminalOverlay } from "@/features/mobile/terminal-overlay"
import { ProjectSheet } from "@/features/mobile/project-sheet"
import { HamburgerSheet } from "@/features/mobile/hamburger-sheet"
import { CreateTaskSheet } from "@/features/mobile/create-task-sheet"
import { ActivitySheet } from "@/features/mobile/activity-sheet"
import { UnifiedBrowserFullscreen } from "@/features/browser/unified-browser-panel"
import { AddProjectSheet } from "@/features/mobile/add-project-sheet"
import { PairedDevicesSheet } from "@/features/mobile/paired-devices-sheet"
import { ProjectSettingsModal } from "@/features/modals/project-settings"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { RecordingPlayer } from "@/features/browser/recording-player"
import { EmbeddedTerminal } from "@/features/terminal/embedded-terminal"
import { ProjectCard } from "@/features/mobile/project-card"
import { useLogsStore } from "@/stores/logs-store"
import { Sheet } from "@/features/mobile/sheet"
import { POST } from "@/lib/api"
import { useTerminalStore } from "@/stores/terminal-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { getClientId, getClientName } from "@/lib/client-id"
import { GlobalTextInput } from "@/components/global-text-input"
import { useBrowserStore } from "@/stores/browser-store"
import { useVoice } from "@/hooks/use-voice"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import type { BrowserSession, Action, TabId } from "@/types"

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
  const openProcessLog = useLogsStore((s) => s.openProcessLog)

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
  const [sideTabsCollapsed, setSideTabsCollapsed] = useState(false)
  const [agentPickerOpen, setAgentPickerOpen] = useState(false)
  // PinchTab overlay removed — unified into Browser tab
  const [voiceIndicator, setVoiceIndicator] = useState("")
  const voiceSubmitRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const voiceIndicatorRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Terminal send function ref for voice dictation
  const terminalSendRef = useRef<((data: string) => void) | null>(null)
  // Direct orchestrator ref for voice commands (doesn't need chat panel mounted)
  const voiceOrchestratorRef = useRef<((text: string) => Promise<void>) | null>(null)

  const browserActiveSession = useBrowserStore((s) => s.activeSession)
  const openTextInput = useUIStore((s) => s.openTextInput)

  const voice = useVoice({
    onFinal: (text) => {
      const isTerminalMode = useTerminalStore.getState().terminalFocused

      if (isTerminalMode) {
        // Dictation mode: append to text input popup for review before sending
        setVoiceIndicator("")
        const uiState = useUIStore.getState()
        if (uiState.textInputOpen) {
          // Already open — append the new text
          uiState.appendTextInput(text)
        } else {
          // First utterance — open with initial text
          openTextInput(
            (confirmed) => terminalSendRef.current?.(confirmed.replace(/\n/g, "\r") + "\r"),
            "Voice → Terminal",
            text,
            true, // keepOpen for follow-up dictation
          )
        }
      } else {
        // Command mode: send through orchestrator directly
        setVoiceIndicator(text)
        if (voiceSubmitRef.current) clearTimeout(voiceSubmitRef.current)
        voiceSubmitRef.current = setTimeout(() => {
          voiceOrchestratorRef.current?.(text)
          // Clear indicator after a moment
          if (voiceIndicatorRef.current) clearTimeout(voiceIndicatorRef.current)
          voiceIndicatorRef.current = setTimeout(() => setVoiceIndicator(""), 2000)
        }, 600)
      }
    },
    onInterim: (text) => {
      setVoiceIndicator(text)
    },
  })

  useEffect(() => {
    if (voice.error) toast(voice.error, "warning")
  }, [voice.error, toast])

  useEffect(() => {
    return () => {
      if (voiceSubmitRef.current) clearTimeout(voiceSubmitRef.current)
      if (voiceIndicatorRef.current) clearTimeout(voiceIndicatorRef.current)
    }
  }, [])


  useEffect(() => {
    loadProjects()
    loadCollections()
  }, [loadProjects, loadCollections])

  const spawnTerminal = useTerminalStore((s) => s.spawnTerminal)

  // Voice orchestrator — lives at layout level so it works regardless of which tab is active
  const voiceOrchestrator = useOrchestrator({
    channel: "mobile",
    onOpenTerminal: async (project) => {
      const proj = project && project !== "all" ? project : currentProject
      if (proj === "all") { toast("Select a project first", "warning"); return }
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
  })

  // Wire voice orchestrator ref
  useEffect(() => {
    voiceOrchestratorRef.current = async (text: string) => {
      const result = await voiceOrchestrator.send(text)
      if (result?.response) {
        toast(result.response, "info")
      }
    }
  }, [voiceOrchestrator, toast])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  const attentionTerminals = terminals.filter((t) => t.waiting_for_input)
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (currentProject === "all" || p.project === currentProject)
  )
  const collectionName = collections.find((c) => c.id === currentCollection)?.name
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  const handleSpawnTerminal = async (command?: string) => {
    if (currentProject === "all") {
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
      <div className="h-screen flex flex-col bg-gray-900 text-gray-100 overflow-hidden max-w-6xl mx-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 flex-shrink-0">
          <button className="text-sm text-gray-400" onClick={() => setPlayingRecording(null)}>
            ← Back
          </button>
          <span className="text-sm text-gray-400">Recording Playback</span>
          <div />
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
    <div className="h-screen flex flex-col bg-gray-900 text-gray-100 overflow-hidden max-w-6xl mx-auto">
      <div className="flex items-center justify-between px-5 py-2 border-b border-gray-800 flex-shrink-0">
        <button
          className="flex items-center gap-2 min-w-0"
          onClick={() => setProjectSheetOpen(true)}
        >
          {collectionName && (
            <span className="text-xs uppercase tracking-wider text-gray-500">
              {collectionName}
            </span>
          )}
          <span className="text-base font-semibold text-gray-100 truncate">
            {currentProject === "all" ? "All Projects" : currentProject}
          </span>
          <span className="text-gray-500 text-xs">▼</span>
        </button>

        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 font-mono" title={getClientId()}>
            {getClientName() || getClientId().slice(0, 12)}
          </span>
          <span className="text-xs text-gray-500">{serverState}</span>
          <span
            className={`w-2.5 h-2.5 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
          />
          <button
            className="text-gray-400 hover:text-gray-200 text-xl px-2"
            onClick={() => setHamburgerOpen(true)}
          >
            ☰
          </button>
        </div>
      </div>

      {attentionTerminals.length > 0 && (
        <div className="mx-3 mt-2 bg-yellow-900/50 rounded-lg px-4 py-2 flex items-center gap-3 flex-shrink-0">
          <span className="text-xs font-semibold text-yellow-300">Attention ({attentionTerminals.length})</span>
          <div className="flex gap-2 overflow-x-auto">
            {attentionTerminals.map((t) => (
              <button
                key={t.id}
                className="px-3 py-1 text-xs rounded bg-yellow-700 hover:bg-yellow-600 text-yellow-100 whitespace-nowrap"
                onClick={() => setTerminalOverlayId(t.id)}
              >
                {t.project || t.id}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className={`flex-1 grid gap-3 p-3 min-h-0 overflow-hidden ${sideTabsCollapsed ? "grid-cols-[1fr_auto]" : "grid-cols-3"}`}>
        <div className={`${sideTabsCollapsed ? "" : "col-span-2"} flex flex-col min-h-0`}>
          <EmbeddedTerminal onTerminalSendReady={(send) => { terminalSendRef.current = send }} />
        </div>
        <div className="min-h-0">
          <KioskSideTabs
            collapsed={sideTabsCollapsed}
            onToggle={() => setSideTabsCollapsed((c) => !c)}
            onLogs={(id, name) => openProcessLog(id, name)}
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
            onEditProject={() => setProjectSettingsOpen(true)}
            onOpenTerminal={async (project) => {
              const proj = project && project !== "all" ? project : currentProject
              if (proj === "all") { toast("Select a project first", "warning"); return }
              const session = await spawnTerminal(proj)
              if (session) { toast("Terminal started", "success") }
              else { toast("Failed to create terminal", "error") }
            }}
            onCreateTask={() => setCreateTaskOpen(true)}
            onOpenBrowser={() => setBrowserUrlOpen(true)}
            onOpenActivity={() => setActivityOpen(true)}
            onOpenMenu={() => setHamburgerOpen(true)}
            onSystemSettings={() => setSystemSettingsOpen(true)}
            voiceListening={voice.listening}
          />
        </div>
      </div>

      {browserActiveSession && !browserFullscreen && (
        <button
          className="fixed bottom-4 right-4 z-[100] flex items-center gap-2 px-4 py-2 rounded-full bg-purple-600 text-white text-sm shadow-lg"
          onClick={() => setBrowserFullscreen(true)}
        >
          <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
          Resume Preview
        </button>
      )}

      {/* Floating voice indicator — shows transcription without switching tabs */}
      {voiceIndicator && (
        <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-[90] max-w-md px-4 py-2 rounded-full bg-gray-800/95 border border-gray-600 shadow-lg backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${voice.listening ? "bg-red-500 animate-pulse" : "bg-blue-500"}`} />
            <span className="text-sm text-gray-200 truncate">{voiceIndicator}</span>
          </div>
        </div>
      )}

      <KioskActionBar
        onCreateTask={() => setCreateTaskOpen(true)}
        onSpawnTerminal={() => {
          if (currentProject === "all") { toast("Select a project first", "warning"); return }
          setAgentPickerOpen(true)
        }}
        onOpenBrowser={() => setBrowserUrlOpen(true)}
        onOpenActivity={() => setActivityOpen(true)}
        onOpenChat={() => {
          useUIStore.getState().setTab("chat" as TabId)
          if (sideTabsCollapsed) setSideTabsCollapsed(false)
        }}
        onOpenMenu={() => setHamburgerOpen(true)}
        voice={voice}
      />

      {terminalOverlayId && (
        <TerminalOverlay
          sessionId={terminalOverlayId}
          onClose={() => setTerminalOverlayId(null)}
        />
      )}

      {projectSheetOpen && <ProjectSheet onClose={() => setProjectSheetOpen(false)} />}
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

      {projectSettingsOpen && currentProject !== "all" && (
        <ProjectSettingsModal
          projectName={currentProject}
          onClose={() => setProjectSettingsOpen(false)}
          fullPage
        />
      )}
      {systemSettingsOpen && (
        <SystemSettingsModal onClose={() => setSystemSettingsOpen(false)} />
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

      <GlobalTextInput />
    </div>
  )
}

type SideTab = "project" | "processes" | "tasks" | "browser" | "attachments" | "chat"

function KioskSideTabs({
  collapsed,
  onToggle,
  onLogs,
  onOpenSession,
  onPlayRecording,
  onEditProject,
  onOpenTerminal,
  onCreateTask,
  onOpenBrowser,
  onOpenActivity,
  onOpenMenu,
  onSystemSettings,
  voiceListening,
}: {
  collapsed: boolean
  onToggle: () => void
  onLogs: (id: string, name: string) => void
  onOpenSession: (session: { viewer_url: string; session_id: string }) => void
  onPlayRecording: (id: string) => void
  onEditProject: () => void
  onOpenTerminal?: (project: string) => void
  onCreateTask: () => void
  onOpenBrowser: () => void
  onOpenActivity: () => void
  onOpenMenu: () => void
  onSystemSettings?: () => void
  voiceListening?: boolean
}) {
  const setTerminalFocused = useTerminalStore((s) => s.setTerminalFocused)

  // Sync with global UI store so orchestrator show_tab actions work in kiosk mode
  const globalTab = useUIStore((s) => s.currentTab)
  const setGlobalTab = useUIStore((s) => s.setTab)

  // Map global TabId → SideTab (some global tabs don't exist in side tabs)
  const GLOBAL_TO_SIDE: Record<string, SideTab> = {
    project: "project",
    processes: "processes",
    tasks: "tasks",
    browser: "browser",
    pinchtab: "browser",  // legacy: map to unified browser tab
    attachments: "attachments",
    chat: "chat",
  }

  const sideTab = GLOBAL_TO_SIDE[globalTab] || "processes"
  const setTab = (t: SideTab) => setGlobalTab(t as TabId)

  const tabs: { id: SideTab; label: string; short: string }[] = [
    { id: "project", label: "Project", short: "Proj" },
    { id: "processes", label: "Actions", short: "Acts" },
    { id: "tasks", label: "Tasks", short: "Tasks" },
    { id: "browser", label: "Browser", short: "Brws" },
    { id: "attachments", label: "Attachments", short: "Atch" },
    { id: "chat", label: "Chat", short: "Chat" },
  ]

  if (collapsed) {
    return (
      <div className="bg-gray-800 rounded-lg flex flex-col h-full items-center py-2 px-1 gap-1" onPointerDown={() => setTerminalFocused(false)}>
        <button
          className="text-gray-400 hover:text-gray-200 text-xs px-1 py-1 mb-1"
          onClick={onToggle}
          title="Expand sidebar"
        >
          »
        </button>
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`text-[10px] px-1 py-2 rounded writing-vertical ${
              sideTab === t.id ? "text-white bg-gray-700" : "text-gray-500 hover:text-gray-300"
            }`}
            style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
            onClick={() => { setTab(t.id); onToggle() }}
          >
            {t.short}
          </button>
        ))}
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg flex flex-col h-full min-h-0 overflow-hidden" onPointerDown={() => setTerminalFocused(false)}>
      <div className="flex items-center gap-1 flex-shrink-0 px-2 pt-2 pb-1">
        <button
          className="text-gray-400 hover:text-gray-200 text-xs px-1 py-1 mr-1"
          onClick={onToggle}
          title="Collapse sidebar"
        >
          «
        </button>
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`px-2 py-1 text-xs rounded transition-colors whitespace-nowrap ${
              sideTab === t.id ? "bg-gray-700 text-white" : "text-gray-400 hover:text-gray-200"
            }`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="px-3 pb-3 pt-2 flex-1 min-h-0 overflow-auto">
        {sideTab === "project" && (
          <ProjectCard onEdit={onEditProject} />
        )}
        {sideTab === "processes" && (
          <ProcessesCard onLogs={(id, name) => onLogs(id, name)} />
        )}
        {sideTab === "tasks" && <TasksCard />}
        {sideTab === "browser" && (
          <BrowserCard
            onOpenSession={(session) => onOpenSession(session)}
            onPlayRecording={(id) => onPlayRecording(id)}
          />
        )}
        {/* PinchTab merged into Browser */}
        {sideTab === "attachments" && <ContextsCard defaultExpanded />}
        {sideTab === "chat" && (
          <KioskChatPanel
            onOpenTerminal={onOpenTerminal}
            onCreateTask={onCreateTask}
            onOpenBrowser={onOpenBrowser}
            onOpenActivity={onOpenActivity}
            onOpenMenu={onOpenMenu}
            onEditProject={onEditProject}
            onSystemSettings={onSystemSettings}
            listening={voiceListening}
          />
        )}
      </div>
    </div>
  )
}

function KioskActionBar({
  onCreateTask,
  onSpawnTerminal,
  onOpenBrowser,
  onOpenActivity,
  onOpenMenu,
  onOpenChat,
  voice,
}: {
  onCreateTask: () => void
  onSpawnTerminal: () => void
  onOpenBrowser: () => void
  onOpenActivity: () => void
  onOpenMenu: () => void
  onOpenChat: () => void
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
    <div className="flex-shrink-0 border-t border-gray-800 bg-gray-900">
      <div className="max-w-6xl mx-auto px-3 py-2">
        <div className="flex gap-2">
          <button className="flex-1 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium" onClick={onCreateTask}>
            New Task
          </button>
          <button className="flex-1 py-2 text-sm rounded-lg bg-gray-700 text-gray-200 font-medium" onClick={onSpawnTerminal}>
            Terminal
          </button>
          <button className="flex-1 py-2 text-sm rounded-lg bg-gray-700 text-gray-200 font-medium" onClick={onOpenBrowser}>
            Browser
          </button>
          <button className="flex-1 py-2 text-sm rounded-lg bg-gray-700 text-gray-200 font-medium" onClick={onOpenActivity}>
            Activity
          </button>
          <button className="flex-1 py-2 text-sm rounded-lg bg-gray-700 text-gray-200 font-medium" onClick={onOpenMenu}>
            Menu
          </button>
          <button
            className="w-10 py-2 rounded-lg flex items-center justify-center text-sm flex-shrink-0 bg-gray-700 text-gray-300 hover:bg-gray-600"
            onClick={onOpenChat}
            title="Chat"
          >
            💬
          </button>
          <button
            className={`w-10 py-2 rounded-lg flex items-center justify-center text-lg flex-shrink-0 ${
              voice.listening
                ? "bg-red-600 text-white animate-pulse"
                : "bg-gray-700 text-gray-300"
            }`}
            onClick={voice.toggle}
            title={voice.listening ? "Stop voice" : "Start voice"}
          >
            🎤
          </button>
          <button
            className={`w-10 py-2 rounded-lg flex items-center justify-center text-lg flex-shrink-0 ${
              phone?.active
                ? "bg-green-600 text-white animate-pulse"
                : phone?.configured
                  ? "bg-gray-700 text-gray-300"
                  : "bg-gray-700 text-gray-500 opacity-50"
            }`}
            onClick={handlePhone}
            title={phone?.active ? "Hang up" : phone?.configured ? "Call" : "Phone not configured"}
          >
            📞
          </button>
        </div>
      </div>
    </div>
  )
}

import { ChatRenderer } from "@/features/chat/chat-renderer"
import type { ChatMessage as ChatMsg } from "@/features/chat/chat-renderer"

function KioskChatPanel({
  onOpenTerminal,
  onCreateTask,
  onOpenBrowser,
  onOpenActivity,
  onOpenMenu,
  onEditProject,
  onSystemSettings,
  listening,
}: {
  onOpenTerminal?: (project: string) => void
  onCreateTask: () => void
  onOpenBrowser: () => void
  onOpenActivity: () => void
  onOpenMenu: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
  listening?: boolean
}) {
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  const orchestrator = useOrchestrator({
    channel: "mobile",
    onOpenTerminal,
    onCreateTask,
    onOpenBrowser,
    onOpenActivity,
    onOpenMenu,
    onEditProject,
    onSystemSettings,
  })

  const send = async (text?: string) => {
    const msg = (text || input).trim()
    if (!msg || loading) return
    setInput("")
    setLoading(true)

    setMessages((prev) => [...prev, { role: "user", content: msg, timestamp: Date.now() }])

    try {
      const result = await orchestrator.send(msg)
      if (result?.response) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: result.response!,
            actions: result.actions as ChatMsg["actions"],
            timestamp: Date.now(),
          },
        ])
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Something went wrong.", timestamp: Date.now() },
      ])
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleClear = () => {
    setMessages([])
    orchestrator.clearHistory()
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Chat</span>
        {messages.length > 0 && (
          <button
            className="text-[10px] text-gray-500 hover:text-gray-300"
            onClick={handleClear}
          >
            Clear
          </button>
        )}
      </div>

      {/* Messages */}
      <ChatRenderer
        messages={messages}
        loading={loading}
        emptyText="Send a message to the orchestrator agent"
      />

      {/* Input area */}
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <input
          ref={inputRef}
          data-global-text-input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send() }}
          placeholder={listening ? "Listening..." : "Message..."}
          className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 min-w-0"
          disabled={loading}
        />
        <button
          className="px-3 h-9 rounded bg-blue-600 text-white text-sm font-medium disabled:opacity-50 flex-shrink-0"
          onClick={() => send()}
          disabled={loading || !input.trim()}
        >
          Send
        </button>
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
      <div className="space-y-3">
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
