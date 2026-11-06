import { useCallback, useRef, useState } from "react"
import type { TabId } from "@/types"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useAuthStore } from "@/stores/auth-store"
import { useUIStore } from "@/stores/ui-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { getClientId, getClientName } from "@/lib/client-id"
import { CommandPalette, AddProjectModal } from "@/components/project-bar"
import { EmbeddedTerminal } from "@/features/terminal/embedded-terminal"
import { RightTabs } from "@/features/right-tabs/right-tabs"
import { CommandBar } from "@/features/command-bar/command-bar"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { ProjectSettingsModal } from "@/features/modals/project-settings"
import { GlobalTextInput } from "@/components/global-text-input"
import { FloatingAgentPanel } from "@/features/browser/floating-agent-panel"
import { useChannelStore } from "@/stores/channel-store"
import { ChannelSidebar } from "@/features/channels/channel-sidebar"
import { ChannelSettings } from "@/features/channels/channel-settings"
import { FloatingChannelPanel } from "@/features/channels/floating-channel-panel"
import { SessionsNavButton, WorkstreamGoalsWorkspace } from "@/features/sessions/sessions-hub"
import { useMountEffect } from "@/hooks/use-mount-effect"

const LAYOUTS = [
  { id: "desktop", label: "Desktop", short: "D" },
  { id: "mobile", label: "Mobile", short: "M" },
  { id: "kiosk", label: "Kiosk", short: "K" },
]

export function DesktopLayout() {
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const loadCollections = useProjectStore((s) => s.loadCollections)
  const currentProject = useProjectStore((s) => s.currentProject)
  const selectProject = useProjectStore((s) => s.selectProject)
  const logout = useAuthStore((s) => s.logout)
  const connect = useStateStore((s) => s.connect)
  const disconnect = useStateStore((s) => s.disconnect)
  const connected = useStateStore((s) => s.connected)
  const toast = useUIStore((s) => s.toast)
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen)
  const layout = useUIStore((s) => s.layout)
  const setTab = useUIStore((s) => s.setTab)
  const setLayout = useUIStore((s) => s.setLayout)
  const workspaceMode = useUIStore((s) => s.workspaceMode)
  const setWorkspaceMode = useUIStore((s) => s.setWorkspaceMode)
  const spawnTerminal = useTerminalStore((s) => s.spawnTerminal)
  const cycleActiveProject = useProjectStore((s) => s.cycleActiveProject)
  const systemSettingsOpen = useUIStore((s) => s.systemSettingsOpen)
  const setSystemSettingsOpen = useUIStore((s) => s.setSystemSettingsOpen)
  const projectSettingsOpen = useUIStore((s) => s.projectSettingsOpen)
  const setProjectSettingsOpen = useUIStore((s) => s.setProjectSettingsOpen)
  const workstreamSettingsOpen = useUIStore((s) => s.workstreamSettingsOpen)
  const setWorkstreamSettingsOpen = useUIStore((s) => s.setWorkstreamSettingsOpen)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const [addProjectOpen, setAddProjectOpen] = useState(false)

  useMountEffect(() => {
    loadProjects()
    loadCollections()
    connect()
    return () => disconnect()
  })

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey

      // ⌘K — project search
      if (meta && e.key === "k") {
        e.preventDefault()
        toggleCommandPalette()
        return
      }

      // ⌘J — toggle Goals & Review vs Raw Terminal
      if (meta && e.key === "j") {
        e.preventDefault()
        const current = useUIStore.getState().workspaceMode
        setWorkspaceMode(current === "goals" ? "terminal" : "goals")
        return
      }

      // ⌘T — open raw shell terminal in current project
      if (meta && e.key === "t") {
        e.preventDefault()
        if (!currentProject) {
          toast("Select a project first", "warning")
        } else {
          setWorkspaceMode("terminal")
          void spawnTerminal(currentProject, "", useChannelStore.getState().activeChannelId || undefined)
        }
        return
      }

      // ⌘/ — toggle chat panel
      if (meta && e.key === "/") {
        e.preventDefault()
        useUIStore.getState().toggleChat()
        return
      }

      // Ctrl+] / Ctrl+[ — cycle active project
      if (meta && e.key === "]") {
        e.preventDefault()
        cycleActiveProject(1)
        return
      }
      if (meta && e.key === "[") {
        e.preventDefault()
        cycleActiveProject(-1)
        return
      }

      if (e.key === "Escape") {
        setCommandPaletteOpen(false)
      }
    },
    [currentProject, toggleCommandPalette, setCommandPaletteOpen, spawnTerminal, toast, cycleActiveProject, setWorkspaceMode]
  )

  const keyHandlerRef = useRef(handleKeyDown)
  keyHandlerRef.current = handleKeyDown

  useMountEffect(() => {
    const listener = (e: KeyboardEvent) => keyHandlerRef.current(e)
    window.addEventListener("keydown", listener)
    return () => window.removeEventListener("keydown", listener)
  })

  const terminals = useStateStore((s) => s.terminals)
  const attentionTerminals = terminals.filter((t) => t.waiting_for_input)
  const setActiveTerminalId = useTerminalStore((s) => s.setActiveTerminalId)

  return (
    <div className="h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden">
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-2 border-b border-gray-800/80 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${connected ? "bg-green-500" : "bg-red-500"}`} />
          <h1 className="text-base font-bold tracking-tight">REMOTE CTRL</h1>
          <span
            className="text-[10px] uppercase tracking-wider text-blue-400 bg-blue-500/10 border border-blue-500/30 px-1.5 py-0.5 rounded"
            title={connected ? "This machine is HomeBase" : "HomeBase disconnected"}
          >
            HomeBase
          </span>
        </div>
        <div className="flex items-center gap-3">
          <SessionsNavButton />
          <div className="flex rounded-md overflow-hidden border border-gray-700">
            {LAYOUTS.map((l) => (
              <button
                key={l.id}
                className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                  layout === l.id
                    ? "bg-blue-600 text-white"
                    : "bg-gray-800 text-gray-400 hover:text-gray-200"
                }`}
                onClick={() => setLayout(l.id)}
                title={`Switch to ${l.label} layout`}
              >
                {l.short}
              </button>
            ))}
          </div>
          <button
            className="text-xs text-gray-500 hover:text-gray-300 font-mono"
            onClick={toggleCommandPalette}
            title="Search projects (⌘K)"
          >
            ⌘K
          </button>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 font-mono" title={getClientId()}>
              {getClientName() || getClientId().slice(0, 12)}
            </span>
          </div>
          <button className="text-xs text-gray-400 hover:text-gray-200" onClick={logout}>
            Logout
          </button>
        </div>
      </div>

      {/* Attention: terminals waiting for input */}
      {attentionTerminals.length > 0 && (
        <div className="mx-4 mt-2 bg-orange-900/50 rounded-lg px-4 py-2 flex items-center gap-3 flex-shrink-0">
          <span className="text-xs font-semibold text-orange-300">
            Needs Input ({attentionTerminals.length})
          </span>
          <div className="flex gap-2 overflow-x-auto">
            {attentionTerminals.map((t) => (
              <button
                key={t.id}
                className="px-3 py-1 text-xs rounded bg-orange-700 hover:bg-orange-600 text-orange-100 whitespace-nowrap"
                onClick={() => {
                  setActiveTerminalId(t.id)
                  setWorkspaceMode("terminal")
                }}
              >
                {t.project || t.id.slice(0, 8)}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Main 3-Pane Workspace: Workstreams Sidebar + Goal-First Center Stage + Context Panel */}
      <div className="flex-1 flex gap-0 min-h-0 pb-[52px]">
        <ChannelSidebar onAddProject={() => setAddProjectOpen(true)} />

        <div className="flex-1 flex flex-col min-h-0 min-w-0 px-2 py-2">
          {workspaceMode === "goals" ? (
            <WorkstreamGoalsWorkspace />
          ) : (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="mb-1.5 flex items-center justify-between rounded-md border border-gray-800 bg-gray-900 px-3 py-1.5 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-gray-400 font-mono text-[11px]">
                    Live Terminal {currentProject ? `· ${currentProject}` : ""}
                  </span>
                </div>
                <div className="flex rounded-md border border-gray-700 bg-gray-800 p-0.5">
                  <button
                    type="button"
                    onClick={() => setWorkspaceMode("goals")}
                    className="rounded px-2.5 py-1 text-xs font-medium text-gray-400 hover:text-gray-200"
                  >
                    📋 Board (⌘J)
                  </button>
                  <button
                    type="button"
                    disabled
                    className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white"
                  >
                    💻 Terminal
                  </button>
                </div>
              </div>
              <EmbeddedTerminal />
            </div>
          )}
        </div>

        <div className="w-[340px] min-h-0 min-w-0 overflow-hidden px-2 py-2 flex-shrink-0">
          <RightTabs />
        </div>
      </div>

      {/* Chief-of-Staff Chat & Command Bar (hidden when full-screen settings modal is open) */}
      {!systemSettingsOpen && !projectSettingsOpen && (
        <FloatingChannelPanel
          onOpenTerminal={(project) => {
            if (project) selectProject(project)
            setWorkspaceMode("terminal")
          }}
          onCreateTask={() => setWorkspaceMode("goals")}
          onOpenActivity={() => setTab("activity" as TabId)}
          onEditProject={() => setSystemSettingsOpen(true)}
          onSystemSettings={() => setSystemSettingsOpen(true)}
        />
      )}
      <CommandBar />
      <FloatingAgentPanel channel="desktop" />
      <CommandPalette />
      {systemSettingsOpen && (
        <SystemSettingsModal onClose={() => setSystemSettingsOpen(false)} />
      )}
      {projectSettingsOpen && currentProject && (
        <ProjectSettingsModal projectName={currentProject} onClose={() => setProjectSettingsOpen(false)} fullPage />
      )}
      {workstreamSettingsOpen && activeChannelId && (
        <ChannelSettings channelId={activeChannelId} onClose={() => setWorkstreamSettingsOpen(false)} />
      )}
      {addProjectOpen && (
        <AddProjectModal
          onClose={() => setAddProjectOpen(false)}
          onCreated={() => {
            setAddProjectOpen(false)
            loadProjects()
            useChannelStore.getState().loadChannels()
          }}
          scaffoldProject={useProjectStore.getState().scaffoldProject}
        />
      )}
      <GlobalTextInput />
    </div>
  )
}
