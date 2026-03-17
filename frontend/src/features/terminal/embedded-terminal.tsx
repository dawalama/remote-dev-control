import { useState, useCallback, useEffect, useRef } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { useUIStore } from "@/stores/ui-store"
import { useLogsStore } from "@/stores/logs-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { TerminalView, TerminalToolbar } from "./terminal-view"

const VIRTUAL_KEYS = [
  { label: "↑", data: "\x1b[A" },
  { label: "↓", data: "\x1b[B" },
  { label: "←", data: "\x1b[D" },
  { label: "→", data: "\x1b[C" },
  { label: "Enter", data: "\r" },
  { label: "Tab", data: "\t" },
  { label: "Esc", data: "\x1b" },
  { label: "C-c", data: "\x03" },
  { label: "y", data: "y" },
  { label: "n", data: "n" },
]

export function EmbeddedTerminal({
  onTerminalSendReady,
}: {
  onTerminalSendReady?: (send: (data: string) => void) => void
} = {}) {
  const currentProject = useProjectStore((s) => s.currentProject)
  const terminals = useStateStore((s) => s.terminals)
  const terminalStore = useTerminalStore()
  const activeTerminalId = useTerminalStore((s) => s.activeTerminalId)
  const setActiveTerminalId = useTerminalStore((s) => s.setActiveTerminalId)
  const mode = useTerminalStore((s) => s.mode)
  const setMode = useTerminalStore((s) => s.setMode)
  const toast = useUIStore((s) => s.toast)
  const [connected, setConnected] = useState(false)
  const layout = useUIStore((s) => s.layout)
  const openTextInput = useUIStore((s) => s.openTextInput)
  const sendRef = useRef<((data: string) => void) | null>(null)
  const redrawRef = useRef<(() => void) | null>(null)

  const setTerminalFocused = useTerminalStore((s) => s.setTerminalFocused)

  const handleSendReady = useCallback((send: (data: string) => void) => {
    sendRef.current = send
    onTerminalSendReady?.(send)
  }, [onTerminalSendReady])

  const handleRedrawReady = useCallback((redraw: () => void) => {
    redrawRef.current = redraw
  }, [])

  const sendToTerminal = useCallback((data: string) => {
    sendRef.current?.(data)
  }, [])

  const activateTextInput = useCallback(() => {
    if (layout === "kiosk") {
      const name = activeTerminalId ? `Terminal (${activeTerminalId.slice(0, 8)})` : "Terminal"
      openTextInput((text) => sendRef.current?.(text.replace(/\n/g, "\r") + "\r"), name, "", true)
    }
  }, [layout, openTextInput, activeTerminalId])

  // Log panes from logsStore (shown as tabs alongside terminal tabs)
  const logPanes = useLogsStore((s) => s.panes)
  const activePaneId = useLogsStore((s) => s.activePaneId)
  const logsOpen = useLogsStore((s) => s.isOpen)
  const setActivePane = useLogsStore((s) => s.setActivePane)
  const closePane = useLogsStore((s) => s.closePane)
  const togglePause = useLogsStore((s) => s.togglePause)
  const refreshPane = useLogsStore((s) => s.refreshPane)
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  // All terminals — show every terminal for the project (or all projects)
  const projectTerminals =
    currentProject !== "all"
      ? terminals.filter((t) => t.project === currentProject)
      : terminals

  // Active tab: either a terminal id or a log pane id
  const [activeTab, setActiveTab] = useState<string | null>(null)

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  useEffect(() => {
    if (logsOpen && activePaneId) {
      setActiveTab(`log:${activePaneId}`)
    }
  }, [logsOpen, activePaneId])

  // Auto-select: if activeTerminalId is set and valid, use it.
  // Otherwise fall back to first terminal.
  const resolvedTerminalId =
    projectTerminals.find((t) => t.id === activeTerminalId)?.id ||
    projectTerminals[0]?.id ||
    null

  // Keep activeTerminalId in sync
  useEffect(() => {
    if (resolvedTerminalId && resolvedTerminalId !== activeTerminalId) {
      setActiveTerminalId(resolvedTerminalId)
    }
  }, [resolvedTerminalId, activeTerminalId, setActiveTerminalId])

  // Determine what's actually shown
  const isLogTab = activeTab?.startsWith("log:")
  const activeLogPaneId = isLogTab ? activeTab!.slice(4) : null
  const activeLogPane = activeLogPaneId
    ? logPanes.find((p) => p.id === activeLogPaneId)
    : null
  const showingTerminalId = isLogTab ? null : (activeTab || resolvedTerminalId)
  const showingTerminal = showingTerminalId
    ? projectTerminals.find((t) => t.id === showingTerminalId)
    : null

  const handleSpawn = useCallback(
    async (command?: string) => {
      if (currentProject === "all") {
        toast("Select a project first", "warning")
        return
      }
      const session = await terminalStore.spawnTerminal(currentProject, command)
      if (session) {
        toast("Terminal started", "success")
        setMode("embedded")
        setActiveTerminalId(session.id)
        setActiveTab(null)
      } else {
        toast("Failed to start terminal", "error")
      }
    },
    [currentProject, terminalStore, toast, setMode, setActiveTerminalId]
  )

  const handleRestart = useCallback(async () => {
    if (!showingTerminalId) return
    const session = await terminalStore.restartTerminal(showingTerminalId)
    if (session) toast("Terminal restarted", "success")
    else toast("Failed to restart", "error")
  }, [showingTerminalId, terminalStore, toast])

  const handleKill = useCallback(async () => {
    if (!showingTerminalId) return
    await terminalStore.killTerminal(showingTerminalId)
    setMode("embedded")
    // Switch to next terminal if available
    const remaining = projectTerminals.filter((t) => t.id !== showingTerminalId)
    if (remaining.length > 0) {
      setActiveTerminalId(remaining[0].id)
      setActiveTab(null)
    }
    toast("Terminal killed", "success")
  }, [showingTerminalId, terminalStore, toast, setMode, projectTerminals, setActiveTerminalId])

  // No project selected and no terminals anywhere
  if (currentProject === "all" && projectTerminals.length === 0 && logPanes.length === 0) {
    return (
      <div className="flex-[65] flex items-center justify-center bg-gray-900 rounded-lg min-h-0">
        <p className="text-gray-500 text-sm">Select a project to open a terminal</p>
      </div>
    )
  }

  // No terminals and no log panes — show new session prompt
  if (projectTerminals.length === 0 && logPanes.length === 0) {
    return (
      <div className="flex-[65] flex items-center justify-center bg-gray-900 rounded-lg min-h-0">
        <div className="text-center space-y-3">
          <p className="text-gray-400 text-sm">No active terminal for {currentProject}</p>
          <AgentLauncher presets={presets} onSelect={handleSpawn} />
        </div>
      </div>
    )
  }

  // Minimized
  if (mode === "minimized" && !isLogTab) {
    return (
      <div className="flex-[65] flex items-center justify-center bg-gray-900 rounded-lg min-h-0">
        <div className="text-center space-y-2">
          <p className="text-gray-500 text-sm">Terminal minimized</p>
          <button
            className="px-3 py-1.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-white"
            onClick={() => setMode("embedded")}
          >
            Restore
          </button>
        </div>
      </div>
    )
  }

  const isWaiting = showingTerminal?.waiting_for_input
  const isTerminalStopped = showingTerminal && (showingTerminal.status === "stopped" || showingTerminal.status === "error")

  const terminalContent = showingTerminalId ? (
    <>
      <TerminalToolbar
        project={showingTerminal?.project || currentProject}
        sessionId={showingTerminalId}
        connected={connected}
        onRestart={handleRestart}
        onDisconnect={() => setMode("minimized")}
        onKill={handleKill}
        onReset={() => redrawRef.current?.()}
        mode={mode}
        onModeChange={setMode}
      />
      {isWaiting && (
        <div className="px-3 py-1 bg-yellow-900/60 flex items-center gap-2 flex-shrink-0">
          <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
          <span className="text-xs text-yellow-300">Waiting for input</span>
        </div>
      )}
      <div
        className="flex-1 min-h-0 relative"
        onClick={activateTextInput}
        onPointerDown={() => setTerminalFocused(true)}
      >
        {/* Always render TerminalView to show buffered output */}
        <TerminalView
          key={showingTerminalId}
          sessionId={showingTerminalId}
          project={showingTerminal?.project || currentProject}
          fontSize={layout === "kiosk" ? 18 : 13}
          onDisconnect={() => setConnected(false)}
          onSendReady={handleSendReady}
          onRedrawReady={handleRedrawReady}
        />
        {/* Overlay for stopped terminals */}
        {isTerminalStopped && (
          <div className="absolute bottom-0 left-0 right-0 flex items-center justify-center gap-3 py-3 bg-gradient-to-t from-gray-900 via-gray-900/95 to-transparent">
            <span className="text-gray-400 text-sm">Session ended</span>
            <button
              className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={handleRestart}
            >
              Restart
            </button>
            <button
              className="px-3 py-1.5 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-300"
              onClick={handleKill}
            >
              Remove
            </button>
          </div>
        )}
      </div>
      {/* Virtual key bar for kiosk touchscreen */}
      {layout === "kiosk" && (
        <div className="flex items-center gap-2 px-3 py-2 bg-gray-800 border-t border-gray-700 overflow-x-auto flex-shrink-0">
          <button
            className="rounded select-none px-5 py-3 text-sm font-medium bg-gray-700 text-gray-300 active:bg-gray-600"
            onPointerDown={(e) => {
              e.preventDefault()
              activateTextInput()
            }}
          >
            Txt
          </button>
          {VIRTUAL_KEYS.map((k) => (
            <button
              key={k.label}
              className="rounded bg-gray-700 text-gray-300 active:bg-gray-600 whitespace-nowrap select-none px-5 py-3 text-sm font-medium"
              onPointerDown={(e) => {
                e.preventDefault()
                sendToTerminal(k.data)
              }}
            >
              {k.label}
            </button>
          ))}
          <button
            className="rounded bg-gray-700 text-gray-300 active:bg-gray-600 select-none px-5 py-3 text-sm font-medium"
            onPointerDown={async (e) => {
              e.preventDefault()
              try {
                const text = await navigator.clipboard.readText()
                if (text) { sendToTerminal(text); return }
              } catch { /* clipboard denied — fall through to text input */ }
              activateTextInput()
            }}
          >
            Paste
          </button>
        </div>
      )}
    </>
  ) : null

  const logContent = activeLogPane ? (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-700/50 flex-shrink-0">
        <span className="text-xs text-gray-400 flex-1 truncate">{activeLogPane.title}</span>
        <button
          className={`px-1.5 py-0.5 text-[10px] rounded ${
            activeLogPane.paused ? "bg-yellow-600 text-white" : "bg-gray-600 text-gray-300"
          }`}
          onClick={() => togglePause(activeLogPane.id)}
        >
          {activeLogPane.paused ? ">" : "||"}
        </button>
        <button
          className="px-1.5 py-0.5 text-[10px] rounded bg-gray-600 text-gray-300 hover:bg-gray-500"
          onClick={() => refreshPane(activeLogPane.id)}
        >
          refresh
        </button>
      </div>
      <LogContent pane={activeLogPane} />
    </div>
  ) : null

  const labelFor = (command: string | undefined) => {
    const exact = presets.find((p) => p.command === command)?.label
    if (exact) return exact
    const base = (command || "").trim().split(/\s+/)[0] || ""
    const base2 = base.split("/").pop() || base
    const byBase = presets.find((p) => (p.command || "").trim().split(/\s+/)[0].split("/").pop() === base2)?.label
    return byBase ?? (command || "Shell")
  }

  const getTabLabel = (terminal: typeof projectTerminals[0]) => {
    if (currentProject === "all") return terminal.project
    const baseLabel = labelFor(terminal.command)
    const pid = terminal.pid ? ` (${terminal.pid})` : ""
    const sameLabel = projectTerminals.filter(
      (t) => labelFor(t.command) === baseLabel
    )
    if (sameLabel.length > 1) {
      const num = sameLabel.indexOf(terminal) + 1
      return `${baseLabel} ${num}${pid}`
    }
    return `${baseLabel}${pid}`
  }

  // Tab bar showing terminal tabs + log tabs
  // The launcher button is outside the scroll area so its dropdown isn't clipped
  const tabBar = (projectTerminals.length > 0 || logPanes.length > 0) ? (
    <div className="flex items-center bg-gray-800 flex-shrink-0">
      <div className="flex items-center px-2 py-1 gap-0.5 overflow-x-auto flex-1 min-w-0">
        {projectTerminals.map((t) => {
          const isActive = !isLogTab && (showingTerminalId === t.id)
          const isStopped = t.status === "stopped" || t.status === "error"
          const dotColor = t.waiting_for_input
            ? "bg-yellow-400 animate-pulse"
            : isStopped
              ? "bg-gray-500"
              : "bg-green-400"
          const label = getTabLabel(t)
          return (
            <button
              key={t.id}
              className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded whitespace-nowrap ${
                isActive ? "bg-gray-700 text-white" : isStopped ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => {
                setActiveTerminalId(t.id)
                setActiveTab(null)
              }}
            >
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`} />
              {label}
              {isStopped && <span className="text-[10px] text-gray-500">(stopped)</span>}
            </button>
          )
        })}
        {logPanes.map((pane) => {
          const isActive = activeTab === `log:${pane.id}`
          return (
            <button
              key={pane.id}
              className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded whitespace-nowrap ${
                isActive ? "bg-gray-700 text-white" : "text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => {
                setActiveTab(`log:${pane.id}`)
                setActivePane(pane.id)
              }}
            >
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                pane.type === "process-log" ? "bg-green-400"
                  : pane.type === "system-log" ? "bg-yellow-400"
                  : "bg-blue-400"
              }`} />
              {pane.title}
              {pane.paused && <span className="text-yellow-400 text-[10px]">||</span>}
              <span
                className="ml-0.5 text-gray-500 hover:text-red-400"
                onClick={(e) => {
                  e.stopPropagation()
                  closePane(pane.id)
                  if (activeTab === `log:${pane.id}`) setActiveTab(null)
                }}
              >
                x
              </span>
            </button>
          )
        })}
      </div>
      <AgentLauncherButton presets={presets} onSelect={handleSpawn} />
    </div>
  ) : null

  // Fullscreen overlay
  if (mode === "fullscreen" && !isLogTab) {
    return (
      <>
        <div className="flex-[65] flex items-center justify-center bg-gray-900 rounded-lg min-h-0">
          <p className="text-gray-500 text-sm">Terminal in fullscreen mode</p>
        </div>
        <div className="fixed inset-0 z-[100] bg-gray-900 flex flex-col">
          {terminalContent}
        </div>
      </>
    )
  }

  // Embedded (default)
  return (
    <div className="flex-[65] flex flex-col min-h-0 rounded-lg overflow-hidden">
      {tabBar}
      {isLogTab ? logContent : terminalContent}
    </div>
  )
}

// --- Agent Launcher (empty state — larger buttons) ---

function AgentLauncher({
  presets,
  onSelect,
}: {
  presets: { id: string; label: string; command: string; icon: string; description: string }[]
  onSelect: (command?: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-2 justify-center">
      {presets.map((preset) => (
        <button
          key={preset.id}
          className="flex items-center gap-2 px-3 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-200 border border-gray-600"
          onClick={() => onSelect(preset.command)}
          title={preset.description}
        >
          <span className="w-5 h-5 rounded bg-gray-600 flex items-center justify-center text-xs font-mono font-bold">
            {preset.icon}
          </span>
          {preset.label}
        </button>
      ))}
    </div>
  )
}

// --- Agent Launcher Button (tab bar "+" dropdown) ---

function AgentLauncherButton({
  presets,
  onSelect,
}: {
  presets: { id: string; label: string; command: string; icon: string; description: string }[]
  onSelect: (command?: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  return (
    <div className="relative flex-shrink-0" ref={ref}>
      <button
        className="px-2.5 py-1 text-sm text-gray-400 hover:text-white hover:bg-gray-700 rounded mx-1"
        onClick={() => setOpen(!open)}
        title="New terminal"
      >
        +
      </button>
      {open && (
        <div className="absolute top-full right-0 mt-1 bg-gray-700 border border-gray-600 rounded shadow-lg z-50 min-w-[180px]">
          {presets.map((preset) => (
            <button
              key={preset.id}
              className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left text-gray-200 hover:bg-gray-600 first:rounded-t last:rounded-b"
              onClick={() => {
                onSelect(preset.command)
                setOpen(false)
              }}
            >
              <span className="w-5 h-5 rounded bg-gray-800 flex items-center justify-center text-[10px] font-mono font-bold flex-shrink-0">
                {preset.icon}
              </span>
              <span className="flex-1">{preset.label}</span>
              <span className="text-[10px] text-gray-500">{preset.command || "$SHELL"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// --- Log content viewer ---

// Strip ANSI escape codes (colors, cursor movement, etc.)
// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?(?:\x07|\x1b\\)|\x1b[()][0-9A-B]|\r/g

function stripAnsi(text: string): string {
  return text.replace(ANSI_RE, "")
}

function LogContent({ pane }: { pane: { id: string; content: string; paused: boolean } }) {
  const contentRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (contentRef.current && !pane.paused) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [pane.content, pane.paused])

  return (
    <pre
      ref={contentRef}
      className="flex-1 text-xs font-mono text-gray-300 whitespace-pre-wrap overflow-auto p-3"
    >
      {stripAnsi(pane.content) || "No output."}
    </pre>
  )
}



