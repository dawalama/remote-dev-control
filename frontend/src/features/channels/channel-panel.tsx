import { useState, useRef, useCallback } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { ChannelSettings } from "./channel-settings"
import type { ChannelMessage } from "@/stores/channel-store"

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

  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSend = useCallback(async () => {
    if (!input.trim() || sending || !activeChannelId) return
    const userMessage = input.trim()
    setSending(true)
    setInput("")

    // Save user message to channel history
    await postMessage(activeChannelId, userMessage, "user")

    // Route through orchestrator (local commands + server LLM + action dispatch)
    const result = await orchestrator.send(userMessage, projectName || undefined)

    // Save orchestrator response to channel history
    if (result?.response) {
      await postMessage(activeChannelId, result.response, "orchestrator")
    }

    setSending(false)
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [input, sending, activeChannelId, projectName, postMessage, orchestrator])

  if (!activeChannelId || !channel) {
    return (
      <div className="border-t border-gray-800 bg-gray-900 px-4 py-3 text-center text-xs text-gray-600">
        Select a workspace to start
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
          <MessageLine key={msg.id} message={msg} />
        ))}
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

function MessageLine({ message }: { message: ChannelMessage }) {
  const isUser = message.role === "user"
  const isSystem = message.role === "system"

  if (isSystem) {
    return (
      <div className="text-[10px] text-gray-600 italic">{message.content}</div>
    )
  }

  return (
    <div className="flex gap-2 text-xs">
      <span className={`font-medium flex-shrink-0 ${isUser ? "text-blue-400" : "text-green-400"}`}>
        {isUser ? "you" : message.role}
      </span>
      <span className="text-gray-300 whitespace-pre-wrap">{message.content}</span>
    </div>
  )
}
