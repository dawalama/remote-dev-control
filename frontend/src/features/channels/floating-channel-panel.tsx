import { useState, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelSend } from "@/hooks/use-channel-send"
import { useFileUpload } from "@/hooks/use-file-upload"
import { ChannelSettings, SettingsGear } from "./channel-settings"
import { MessageRenderer } from "./message-renderer"
import { isActivityLogMessage } from "./message-filter"
import { OrchestratorStatusBanner } from "./orchestrator-status-banner"
import { ChatScopeChip } from "@/components/chat-scope-chip"

type PanelSize = "half" | "expanded"

/**
 * Expandable Thread History Drawer — docks directly above the single bottom CommandBar composer.
 * Shows conversation history, A2UI interactive responses, and contextual quick actions
 * without duplicating the composer input.
 */
export function FloatingChannelPanel({
  onOpenTerminal,
  onCreateTask,
  onOpenActivity,
  onEditProject,
  onSystemSettings,
}: {
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenActivity?: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
}) {
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const messages = useChannelStore((s) => s.messages)
  const postMessage = useChannelStore((s) => s.postMessage)

  const channel = channels.find((c) => c.id === activeChannelId)
  const isHomebase = !activeChannelId || activeChannelId === "ch-system"
  const scopeLabel = isHomebase
    ? "HomeBase"
    : `#${channel?.name.replace(/^#/, "") || "workstream"}`
  const projectName =
    channel?.project_names?.[0] || channel?.name.replace(/^#/, "").split("/")[0] || ""

  const orchestrator = useOrchestrator({
    channel: "desktop",
    onOpenTerminal,
    onCreateTask,
    onOpenActivity,
    onEditProject,
    onSystemSettings,
  })

  const { handleSend: sendToChannel, handleRespond, sending, runStatus, retryLast, dismissStatus } = useChannelSend({
    activeChannelId,
    projectName,
    orchestrator,
    postMessage,
  })

  const { uploadMultiple } = useFileUpload()
  const chatOpen = useUIStore((s) => s.chatOpen)
  const toggleChat = useUIStore((s) => s.toggleChat)
  const [size, setSize] = useState<PanelSize>("half")
  const [showSettings, setShowSettings] = useState(false)
  const [showActivityLogs, setShowActivityLogs] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const chatMessages = messages.filter((m) => !isActivityLogMessage(m))
  const activityCount = messages.length - chatMessages.length
  const visibleMessages = showActivityLogs ? messages : chatMessages

  if (!chatOpen) return null

  if (!activeChannelId || !channel) {
    return (
      <div className="fixed bottom-[54px] left-1/2 -translate-x-1/2 z-[110] bg-gray-900 border border-gray-700 rounded-xl shadow-2xl px-4 py-3 w-[min(672px,calc(100vw-32px))] space-y-2">
        <div className="flex items-center justify-between gap-2">
          <ChatScopeChip />
          <button
            type="button"
            onClick={toggleChat}
            className="text-gray-500 hover:text-gray-300 text-xs px-1"
            title="Close (⌘/)"
          >
            ✕
          </button>
        </div>
        <p className="text-center text-xs text-gray-500">
          Select a workstream on the left, or type below / click{" "}
          <span className="text-gray-300">⚡ Brief me</span>.
        </p>
      </div>
    )
  }

  const height = size === "expanded" ? "76vh" : "42vh"

  return (
    <div
      className={`fixed bottom-[54px] left-1/2 -translate-x-1/2 z-[110] bg-gray-900/98 backdrop-blur border rounded-xl shadow-2xl flex flex-col overflow-hidden transition-[height] duration-200 w-[min(672px,calc(100vw-32px))] ${
        dragOver ? "border-blue-500 ring-2 ring-blue-500/30" : "border-gray-700"
      }`}
      style={{ height, maxHeight: "calc(100vh - 104px)" }}
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragOver(false)
        if (e.dataTransfer.files.length) void uploadMultiple(e.dataTransfer.files)
      }}
    >
      {/* Compact Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 bg-gray-950/70 flex-shrink-0">
        <ChatScopeChip compact />
        {channel.auto_mode && (
          <span className="text-[9px] bg-yellow-600/30 text-yellow-400 px-1.5 py-0.5 rounded">
            Auto
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {activityCount > 0 && (
            <button
              type="button"
              onClick={() => setShowActivityLogs((v) => !v)}
              className={`rounded-full border px-2 py-0.5 text-[10px] whitespace-nowrap transition-colors ${
                showActivityLogs
                  ? "border-blue-500/50 bg-blue-950/50 text-blue-200"
                  : "border-gray-700 bg-gray-800/80 text-gray-400 hover:text-gray-200"
              }`}
              title={showActivityLogs ? "Hide background goal/system activity logs" : "Show background goal/system activity logs"}
            >
              {showActivityLogs ? `Logs on (${activityCount})` : `Logs (${activityCount})`}
            </button>
          )}
          <button
            disabled={sending}
            onClick={() => {
              void sendToChannel("What needs me")
            }}
            className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 hover:border-amber-500/50 hover:text-amber-300 whitespace-nowrap"
          >
            🔔 What needs me
          </button>
          {onCreateTask && (
            <button
              onClick={() => {
                onCreateTask()
                useUIStore.getState().setViewingSessionId(null)
              }}
              className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 hover:border-emerald-500/50 hover:text-emerald-300 whitespace-nowrap"
            >
              🎯 Goals
            </button>
          )}
          <button
            onClick={() => setShowSettings(true)}
            className="text-gray-400 hover:text-gray-200 p-1 rounded hover:bg-gray-800"
            title="Workstream settings"
            aria-label="Workstream settings"
          >
            <SettingsGear />
          </button>
          <button
            onClick={() => setSize(size === "expanded" ? "half" : "expanded")}
            className="text-gray-500 hover:text-gray-300 text-xs px-1"
            title={size === "expanded" ? "Shrink" : "Expand"}
          >
            {size === "expanded" ? "⌄" : "⌃"}
          </button>
          <button
            onClick={toggleChat}
            className="text-gray-500 hover:text-gray-300 text-xs px-1"
            title="Close (⌘/)"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Settings modal */}
      {showSettings && (
        <ChannelSettings channelId={activeChannelId} onClose={() => setShowSettings(false)} />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2 min-h-0">
        {visibleMessages.length === 0 && (
          <div className="text-center text-gray-500 text-xs py-8 space-y-1">
            <div className="font-medium text-gray-300">{scopeLabel}</div>
            <div className="text-[11px] text-gray-500">
              Type in the bar below to ask about this project, get a brief, or coordinate goals.
            </div>
            {activityCount > 0 && !showActivityLogs && (
              <button
                type="button"
                onClick={() => setShowActivityLogs(true)}
                className="mt-1 text-[10px] text-gray-400 hover:text-gray-200 underline underline-offset-2"
              >
                Show {activityCount} background activity log{activityCount === 1 ? "" : "s"}
              </button>
            )}
          </div>
        )}
        {visibleMessages.map((msg) => (
          <div key={msg.id} className="flex gap-2 text-xs">
            <span
              className={`font-medium flex-shrink-0 ${
                msg.role === "user"
                  ? "text-blue-400"
                  : msg.role === "system"
                    ? "text-gray-600"
                    : "text-green-400"
              }`}
            >
              {msg.role === "user" ? "you" : msg.role === "system" ? "sys" : "ai"}
            </span>
            <div className="flex-1 min-w-0">
              <MessageRenderer message={msg} compact onRespond={handleRespond} />
            </div>
          </div>
        ))}
        {runStatus && (
          <OrchestratorStatusBanner
            key={runStatus.startedAt}
            runStatus={runStatus}
            onRetry={retryLast}
            onDismiss={dismissStatus}
          />
        )}
        <div
          ref={(el) => {
            ;(bottomRef as React.MutableRefObject<HTMLDivElement | null>).current = el
            el?.scrollIntoView()
          }}
        />
      </div>

      {/* Drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-10 bg-blue-600/10 flex items-center justify-center pointer-events-none">
          <span className="text-sm text-blue-400 font-medium">Drop files here</span>
        </div>
      )}
    </div>
  )
}
