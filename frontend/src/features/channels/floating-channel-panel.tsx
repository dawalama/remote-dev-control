import { useState, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelSend } from "@/hooks/use-channel-send"
import { useFileUpload } from "@/hooks/use-file-upload"
import { ChannelSettings, SettingsGear } from "./channel-settings"
import { MessageRenderer } from "./message-renderer"
import { ChatScopeChip } from "@/components/chat-scope-chip"

type PanelSize = "half" | "expanded"

/**
 * Workstream Chief-of-Staff Drawer — overlays the bottom-right of the workspace.
 * Scoped to the active or pinned workstream with quick 1-click actions (Brief me, New Goal, Voice).
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
  const projectName = channel?.project_names?.[0] || channel?.name.replace(/^#/, "").split("/")[0] || ""

  const orchestrator = useOrchestrator({
    channel: "desktop",
    onOpenTerminal,
    onCreateTask,
    onOpenActivity,
    onEditProject,
    onSystemSettings,
  })

  const { handleSend: sendToChannel, handleRespond, sending } = useChannelSend({
    activeChannelId,
    projectName,
    orchestrator,
    postMessage,
  })

  const { uploadMultiple } = useFileUpload()
  const chatOpen = useUIStore((s) => s.chatOpen)
  const toggleChat = useUIStore((s) => s.toggleChat)
  const setVoicePanelOpen = useUIStore((s) => s.setVoicePanelOpen)
  const [size, setSize] = useState<PanelSize>("half")
  const [input, setInput] = useState("")
  const [showSettings, setShowSettings] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleSend = async () => {
    if (!input.trim()) return
    const msg = input.trim()
    setInput("")
    await sendToChannel(msg)
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  // Hidden — command bar button controls visibility
  if (!chatOpen) return null

  if (!activeChannelId || !channel) {
    return (
      <div className="fixed bottom-14 right-6 z-[110] bg-gray-900 border border-gray-700 rounded-xl shadow-2xl px-4 py-3 w-96 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <ChatScopeChip />
          <button type="button" onClick={toggleChat} className="text-gray-500 hover:text-gray-300 text-xs px-1" title="Close">✕</button>
        </div>
        <p className="text-center text-xs text-gray-500">
          Workstream Chief of Staff is ready. Select a workstream on the left, or click <span className="text-gray-300">⚡ Brief me</span> in the bottom bar.
        </p>
      </div>
    )
  }

  const height = size === "expanded" ? "80vh" : "44vh"

  return (
    <div
      className={`fixed bottom-14 right-6 z-[110] bg-gray-900 border rounded-xl shadow-2xl flex flex-col overflow-hidden transition-[height] duration-200 ${
        dragOver ? "border-blue-500 ring-2 ring-blue-500/30" : "border-gray-700"
      }`}
      style={{ height, width: "440px", maxHeight: "calc(100vh - 96px)" }}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) uploadMultiple(e.dataTransfer.files) }}
    >
      {/* Hidden file input for attach button */}
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) uploadMultiple(e.target.files); e.target.value = "" }} />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 bg-gray-950/60 flex-shrink-0">
        <ChatScopeChip compact />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold text-gray-200 truncate">{channel.name.replace(/^#/, "")}</span>
            <span className="rounded bg-blue-500/10 border border-blue-500/30 px-1.5 py-0.5 text-[9px] font-medium text-blue-300">Chief of Staff</span>
            {channel.auto_mode && (
              <span className="text-[9px] bg-yellow-600/30 text-yellow-400 px-1.5 py-0.5 rounded">Auto</span>
            )}
          </div>
        </div>
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
          title="Close"
        >
          ✕
        </button>
      </div>

      {/* Quick Chief-of-Staff Action Strip */}
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-gray-800/80 bg-gray-900/80 text-[10px] flex-shrink-0 overflow-x-auto">
        <button
          disabled={sending}
          onClick={() => { void sendToChannel("Brief me") }}
          className="rounded-full border border-gray-700 bg-gray-800 px-2.5 py-0.5 text-gray-300 hover:border-blue-500/50 hover:text-blue-300 whitespace-nowrap"
        >
          ⚡ Brief me
        </button>
        <button
          disabled={sending}
          onClick={() => { void sendToChannel("What needs me") }}
          className="rounded-full border border-gray-700 bg-gray-800 px-2.5 py-0.5 text-gray-300 hover:border-amber-500/50 hover:text-amber-300 whitespace-nowrap"
        >
          🔔 What needs me
        </button>
        {onCreateTask && (
          <button
            onClick={() => {
              onCreateTask()
              useUIStore.getState().setViewingSessionId(null)
            }}
            className="rounded-full border border-gray-700 bg-gray-800 px-2.5 py-0.5 text-gray-300 hover:border-emerald-500/50 hover:text-emerald-300 whitespace-nowrap"
          >
            🎯 Goals &amp; Review
          </button>
        )}
        <button
          onClick={() => setVoicePanelOpen(true)}
          className="ml-auto rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-400 hover:text-gray-200 whitespace-nowrap"
        >
          🎤 Voice
        </button>
      </div>

      {/* Settings modal */}
      {showSettings && (
        <ChannelSettings channelId={activeChannelId} onClose={() => setShowSettings(false)} />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2 min-h-0">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 text-xs py-8 space-y-1">
            <div className="font-medium text-gray-400">Workstream Chief of Staff</div>
            <div className="text-[11px] text-gray-600">
              Ask for a status brief, spawn an autonomous goal session, or coordinate across your projects.
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className="flex gap-2 text-xs">
            <span className={`font-medium flex-shrink-0 ${msg.role === "user" ? "text-blue-400" : msg.role === "system" ? "text-gray-600" : "text-green-400"}`}>
              {msg.role === "user" ? "you" : msg.role === "system" ? "sys" : "cos"}
            </span>
            <div className="flex-1 min-w-0">
              <MessageRenderer message={msg} compact onRespond={handleRespond} />
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex items-center gap-1.5 text-xs text-gray-500 py-1">
            <span className="flex gap-0.5">
              <span className="w-1 h-1 bg-gray-600 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-1 h-1 bg-gray-600 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-1 h-1 bg-gray-600 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </span>
            Thinking...
          </div>
        )}
        <div ref={(el) => {
          (bottomRef as React.MutableRefObject<HTMLDivElement | null>).current = el
          el?.scrollIntoView()
        }} />
      </div>

      {/* Drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-10 bg-blue-600/10 flex items-center justify-center pointer-events-none">
          <span className="text-sm text-blue-400 font-medium">Drop files here</span>
        </div>
      )}

      {/* Input */}
      <div className="flex-shrink-0 px-3 py-2 border-t border-gray-800 bg-gray-950/60">
        <div className="flex gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="text-gray-500 hover:text-gray-300 flex-shrink-0 px-1"
            title="Attach file"
          >
            📎
          </button>
          <input
            ref={inputRef}
            data-no-global-intercept
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void handleSend() } }}
            placeholder={projectName ? `Instruct Chief of Staff (${projectName})...` : "Ask Chief of Staff..."}
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
            disabled={sending}
          />
          <button
            onClick={() => { void handleSend() }}
            disabled={!input.trim() || sending}
            className="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-xs font-medium flex-shrink-0"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
