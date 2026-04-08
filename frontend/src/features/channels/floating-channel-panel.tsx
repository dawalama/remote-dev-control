import { useState, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelSend } from "@/hooks/use-channel-send"
import { useFileUpload } from "@/hooks/use-file-upload"
import { ChannelSettings } from "./channel-settings"
import { MessageRenderer } from "./message-renderer"

type PanelSize = "half" | "expanded"

/**
 * Floating desktop channel panel — overlays the bottom-right of the workspace.
 * Three sizes: minimized (pill), half (~40vh), expanded (~80vh).
 * Replaces the old fixed bottom dock.
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
      <div className="fixed bottom-20 right-6 z-[110] bg-gray-900 border border-gray-700 rounded-xl shadow-2xl px-4 py-3 w-96">
        <div className="text-center text-xs text-gray-600">Select a workstream to start</div>
      </div>
    )
  }

  const height = size === "expanded" ? "80vh" : "40vh"

  return (
    <div
      className={`fixed bottom-20 right-6 z-[110] bg-gray-900 border rounded-xl shadow-2xl flex flex-col overflow-hidden transition-[height] duration-200 ${
        dragOver ? "border-blue-500 ring-2 ring-blue-500/30" : "border-gray-700"
      }`}
      style={{ height, width: "420px", maxHeight: "calc(100vh - 120px)" }}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) uploadMultiple(e.dataTransfer.files) }}
    >
      {/* Hidden file input for attach button */}
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) uploadMultiple(e.target.files); e.target.value = "" }} />
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 flex-shrink-0">
        <span className="text-xs font-medium text-gray-300 truncate">{channel.name.replace(/^#/, "")}</span>
        {projectName && (
          <span className="text-[10px] text-gray-600">{projectName}</span>
        )}
        {channel.auto_mode && (
          <span className="text-[9px] bg-yellow-600/30 text-yellow-400 px-1.5 py-0.5 rounded">Auto</span>
        )}
        <div className="flex-1" />
        <button
          onClick={() => setShowSettings(true)}
          className="text-[10px] text-gray-500 hover:text-gray-300 px-1.5 py-0.5 rounded hover:bg-gray-700"
        >
          Settings
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

      {/* Settings modal */}
      {showSettings && (
        <ChannelSettings channelId={activeChannelId} onClose={() => setShowSettings(false)} />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2 min-h-0">
        {messages.length === 0 && (
          <div className="text-center text-gray-600 text-[10px] py-8">
            Ask anything — review code, fix bugs, run commands, or plan your next feature.
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className="flex gap-2 text-xs">
            <span className={`font-medium flex-shrink-0 ${msg.role === "user" ? "text-blue-400" : msg.role === "system" ? "text-gray-600" : "text-green-400"}`}>
              {msg.role === "user" ? "you" : msg.role === "system" ? "sys" : "ai"}
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
      <div className="flex-shrink-0 px-3 py-2 border-t border-gray-800">
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
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend() } }}
            placeholder={projectName ? `Message ${projectName}...` : "Type a message..."}
            className="flex-1 px-3 py-1.5 text-xs bg-gray-800 border border-gray-700 rounded-lg text-gray-200 outline-none focus:border-blue-500"
            disabled={sending}
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim()}
            className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-white"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
