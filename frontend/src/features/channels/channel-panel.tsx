import { useState, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelSend } from "@/hooks/use-channel-send"
import { ChannelSettings } from "./channel-settings"
import { MessageRenderer } from "./message-renderer"

/**
 * Toggleable channel panel — docks at the bottom of the workspace.
 * Replaces ChatFAB as the primary chat interface.
 * Routes messages through useOrchestrator for both local commands
 * and server-side LLM orchestration with UI action dispatch.
 */
export function ChannelPanel({
  onClose,
  onOpenTerminal,
  onCreateTask,
  onOpenBrowser,
  onOpenActivity,
  onEditProject,
  onSystemSettings,
}: {
  onClose: () => void
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
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

  // Use the same orchestrator hook that ChatFAB used — gets local commands + server dispatch
  const orchestrator = useOrchestrator({
    channel: "desktop",
    onOpenTerminal,
    onCreateTask,
    onOpenBrowser,
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

  const [input, setInput] = useState("")
  const [showSettings, setShowSettings] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSend = async () => {
    if (!input.trim()) return
    const msg = input.trim()
    setInput("")
    await sendToChannel(msg)
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  if (!activeChannelId || !channel) {
    return (
      <div className="border-t border-gray-800 bg-gray-900 px-4 py-3 text-center text-xs text-gray-600">
        Select a workstream to start
      </div>
    )
  }

  return (
    <div className="border-t border-gray-700 bg-gray-900 flex flex-col" style={{ height: "280px" }}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800 flex-shrink-0">
        <span className="text-xs font-medium text-gray-300">{channel.name}</span>
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
          onClick={onClose}
          className="text-gray-500 hover:text-gray-300 text-xs px-1"
          title="Close panel"
        >
          _
        </button>
      </div>

      {/* Settings modal */}
      {showSettings && (
        <ChannelSettings channelId={activeChannelId} onClose={() => setShowSettings(false)} />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
        {messages.length === 0 && (
          <div className="text-center text-gray-600 text-[10px] py-4">
            Try "show tasks", "open terminal", "start server", or ask anything.
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className="flex gap-2 text-xs">
            <span className={`font-medium flex-shrink-0 ${msg.role === "user" ? "text-blue-400" : msg.role === "system" ? "text-gray-600" : "text-green-400"}`}>
              {msg.role === "user" ? "you" : msg.role === "system" ? "sys" : msg.role}
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
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 px-3 py-1.5 border-t border-gray-800">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend() } }}
            placeholder={projectName ? `Message #${projectName}...` : "Type a message..."}
            className="flex-1 px-3 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-200 outline-none focus:border-blue-500"
            disabled={sending}
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim()}
            className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-white"
          >
            {sending ? "..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  )
}

