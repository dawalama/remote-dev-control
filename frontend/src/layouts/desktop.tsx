import { useEffect, useCallback, useState } from "react"
import type { TabId } from "@/types"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useAuthStore } from "@/stores/auth-store"
import { useUIStore } from "@/stores/ui-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { POST } from "@/lib/api"
import { getClientId, getClientName } from "@/lib/client-id"
import { CommandPalette } from "@/components/project-bar"
import { EmbeddedTerminal } from "@/features/terminal/embedded-terminal"
import { RightTabs } from "@/features/right-tabs/right-tabs"
// ChatFAB replaced by ChannelPanel in v2
import { CommandBar } from "@/features/command-bar/command-bar"
import { SystemSettingsModal } from "@/features/modals/system-settings"
import { GlobalTextInput } from "@/components/global-text-input"
import { FloatingAgentPanel } from "@/features/browser/floating-agent-panel"
import { useChannelStore } from "@/stores/channel-store"
import { ChannelSidebar } from "@/features/channels/channel-sidebar"
import { ChannelPanel } from "@/features/channels/channel-panel"

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
  const tasks = useStateStore((s) => s.tasks)
  const toast = useUIStore((s) => s.toast)
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen)
  const layout = useUIStore((s) => s.layout)
  const setTab = useUIStore((s) => s.setTab)
  const setLayout = useUIStore((s) => s.setLayout)
  const spawnTerminal = useTerminalStore((s) => s.spawnTerminal)
  const cycleActiveProject = useProjectStore((s) => s.cycleActiveProject)
  const [systemSettingsOpen, setSystemSettingsOpen] = useState(false)
  const [agentPickerOpen, setAgentPickerOpen] = useState(false)
  const [channelPanelOpen, setChannelPanelOpen] = useState(true)
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  useEffect(() => {
    loadProjects()
    loadCollections()
  }, [loadProjects, loadCollections])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  // Global keyboard shortcuts
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey

      // ⌘K — project search
      if (meta && e.key === "k") {
        e.preventDefault()
        toggleCommandPalette()
        return
      }

      // ⌘T — new terminal for current project (show preset picker)
      if (meta && e.key === "t") {
        e.preventDefault()
        if (currentProject === "all") {
          toast("Select a project first", "warning")
        } else {
          setAgentPickerOpen(true)
        }
        return
      }

      // ⌘/ — toggle channel panel
      if (meta && e.key === "/") {
        e.preventDefault()
        setChannelPanelOpen((o) => !o)
        return
      }

      // Ctrl+] — next active project
      if (meta && e.key === "]") {
        e.preventDefault()
        cycleActiveProject(1)
        return
      }

      // Ctrl+[ — previous active project
      if (meta && e.key === "[") {
        e.preventDefault()
        cycleActiveProject(-1)
        return
      }

      // Escape — close command palette / agent picker
      if (e.key === "Escape") {
        setCommandPaletteOpen(false)
        setAgentPickerOpen(false)
      }
    },
    [currentProject, toggleCommandPalette, setCommandPaletteOpen, spawnTerminal, toast, cycleActiveProject, setAgentPickerOpen]
  )

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [handleKeyDown])

  // Terminals waiting for input
  const terminals = useStateStore((s) => s.terminals)
  const attentionTerminals = terminals.filter((t) => t.waiting_for_input)
  const setActiveTerminalId = useTerminalStore((s) => s.setActiveTerminalId)

  // Pending reviews
  const reviewItems = tasks.filter(
    (t) => t.status === "needs_review" || t.status === "awaiting_review"
  )

  const handleApprove = async (taskId: string) => {
    try {
      await POST(`/tasks/${taskId}/review`, { action: "approve" })
      toast("Approved", "success")
    } catch { toast("Failed", "error") }
  }

  const handleReject = async (taskId: string) => {
    const reason = prompt("Reason for rejection:")
    if (reason === null) return
    try {
      await POST(`/tasks/${taskId}/review`, { action: "reject", reason })
      toast("Rejected", "success")
    } catch { toast("Failed", "error") }
  }

  return (
    <div className="h-app flex flex-col bg-gray-900 text-gray-100 overflow-hidden">
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-2 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${connected ? "bg-green-500" : "bg-red-500"}`} />
          <h1 className="text-lg font-bold tracking-tight">REMOTE CTRL</h1>
          <span className="text-[10px] text-gray-500 font-mono self-end mb-0.5">v0.1.0</span>
        </div>
        <div className="flex items-center gap-3">
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
          <a
            href="/kb"
            className="text-xs text-gray-400 hover:text-gray-200"
            title="Knowledge Base"
          >
            KB
          </a>
          <button
            className="text-xs text-gray-400 hover:text-gray-200"
            onClick={() => setSystemSettingsOpen(true)}
            title="System settings"
          >
            Settings
          </button>
          <button className="text-xs text-gray-400 hover:text-gray-200" onClick={logout}>
            Logout
          </button>
        </div>
      </div>

      {/* Attention: terminals waiting for input */}
      {attentionTerminals.length > 0 && (
        <div className="mx-4 mt-2 bg-orange-900/50 rounded-lg px-4 py-2 flex items-center gap-3 flex-shrink-0">
          <span className="text-xs font-semibold text-orange-300">
            Attention ({attentionTerminals.length})
          </span>
          <div className="flex gap-2 overflow-x-auto">
            {attentionTerminals.map((t) => (
              <button
                key={t.id}
                className="px-3 py-1 text-xs rounded bg-orange-700 hover:bg-orange-600 text-orange-100 whitespace-nowrap"
                onClick={() => setActiveTerminalId(t.id)}
              >
                {t.project || t.id.slice(0, 8)}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Pending reviews banner */}
      {reviewItems.length > 0 && (
        <div className="mx-4 mt-2 bg-yellow-900/50 rounded-lg p-3 flex-shrink-0">
          <h3 className="text-xs font-semibold text-yellow-300 mb-1.5">
            Pending Review ({reviewItems.length})
          </h3>
          <div className="space-y-1.5">
            {reviewItems.map((task) => (
              <div key={task.id} className="flex items-center justify-between">
                <span className="text-xs text-gray-200 truncate flex-1 mr-2">
                  {task.title || task.description?.slice(0, 60)}
                  {task.project && <span className="text-yellow-400 ml-1">({task.project})</span>}
                </span>
                <div className="flex gap-1 flex-shrink-0">
                  <button
                    className="px-2 py-0.5 text-xs rounded bg-green-600 hover:bg-green-700 text-white"
                    onClick={() => handleApprove(task.id)}
                  >
                    Approve
                  </button>
                  <button
                    className="px-2 py-0.5 text-xs rounded bg-red-600 hover:bg-red-700 text-white"
                    onClick={() => handleReject(task.id)}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Main content: channel sidebar + workspace + right tabs */}
      <div className="flex-1 flex gap-0 min-h-0 pb-[60px]">
        {/* Channel sidebar */}
        <ChannelSidebar onAddProject={() => {
          const path = prompt("Project path (e.g. ~/my-project):")
          if (path) {
            const name = prompt("Project name:", path.split("/").pop() || "")
            if (name) {
              POST("/projects", { name, path, description: "" }).then(() => {
                useProjectStore.getState().loadProjects()
                useChannelStore.getState().loadChannels()
              })
            }
          }
        }} />

        {/* Workspace: terminal (full width) + channel panel (bottom dock) */}
        <div className="flex-1 flex flex-col min-h-0 min-w-0">
          {/* Terminal — takes all available height */}
          <div className="flex-1 flex flex-col min-h-0 min-w-0 px-2 py-2">
            <EmbeddedTerminal />
          </div>

          {/* Channel panel — toggleable bottom dock */}
          {channelPanelOpen ? (
            <ChannelPanel
              onClose={() => setChannelPanelOpen(false)}
              onOpenTerminal={(project) => {
                if (project && project !== "all") selectProject(project)
                setAgentPickerOpen(true)
              }}
              onCreateTask={() => {
                /* TODO: wire create task */
              }}
              onOpenActivity={() => setTab("activity" as TabId)}
              onEditProject={() => setSystemSettingsOpen(true)}
              onSystemSettings={() => setSystemSettingsOpen(true)}
            />
          ) : (
            <button
              onClick={() => setChannelPanelOpen(true)}
              className="flex items-center gap-2 px-3 py-1 border-t border-gray-800 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800/50 flex-shrink-0"
            >
              <span>Chat</span>
              <span className="text-[9px] text-gray-600">Click to open channel messages</span>
            </button>
          )}
        </div>

        {/* Right tabs */}
        <div className="w-80 min-h-0 min-w-0 overflow-hidden px-2 py-2 flex-shrink-0">
          <RightTabs />
        </div>
      </div>

      {/* Floating elements */}
      <CommandBar />
      <FloatingAgentPanel channel="desktop" />
      <CommandPalette />
      {systemSettingsOpen && (
        <SystemSettingsModal onClose={() => setSystemSettingsOpen(false)} />
      )}
      {agentPickerOpen && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setAgentPickerOpen(false)}>
          <div
            className="bg-gray-800 rounded-lg p-4 w-full max-w-sm shadow-xl border border-gray-700"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold mb-3">New Terminal — {currentProject}</h3>
            <div className="space-y-1.5">
              {presets.map((preset) => (
                <button
                  key={preset.id}
                  className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg bg-gray-700 hover:bg-gray-600 text-left"
                  onClick={() => {
                    setAgentPickerOpen(false)
                    spawnTerminal(currentProject, preset.command, useChannelStore.getState().activeChannelId || undefined)
                    toast(`Terminal started: ${preset.label}`, "success")
                  }}
                >
                  <span className="w-7 h-7 rounded bg-gray-800 flex items-center justify-center text-xs font-mono font-bold text-gray-200 flex-shrink-0">
                    {preset.icon}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-gray-200">{preset.label}</div>
                    {preset.description && (
                      <div className="text-[10px] text-gray-500 truncate">{preset.description}</div>
                    )}
                  </div>
                  <span className="text-[10px] text-gray-500 font-mono flex-shrink-0">{preset.command || "$SHELL"}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
      <GlobalTextInput />
    </div>
  )
}

