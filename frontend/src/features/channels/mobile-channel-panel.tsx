import { useState, useRef } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelSend } from "@/hooks/use-channel-send"
import { useFileUpload } from "@/hooks/use-file-upload"
import { MessageRenderer } from "./message-renderer"
import type { ChannelMessage } from "@/stores/channel-store"

/**
 * Mobile channel panel — two modes:
 * 1. Compact: bottom bar with quick actions + input (tap to expand)
 * 2. Fullscreen: full chat experience for back-and-forth interaction
 */
export function MobileChannelPanel({
  onOpenTerminal,
  onCreateTask,
  onOpenBrowser,
  onOpenActivity,
  onOpenMenu,
  onEditProject,
  onSystemSettings,
  hideActions = false,
  startFullscreen = false,
  onClose,
}: {
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
  onOpenActivity?: () => void
  onOpenMenu?: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
  hideActions?: boolean
  startFullscreen?: boolean
  onClose?: () => void
}) {
  const [fullscreen, setFullscreen] = useState(startFullscreen)
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const messages = useChannelStore((s) => s.messages)
  const postMessage = useChannelStore((s) => s.postMessage)

  const channel = channels.find((c) => c.id === activeChannelId)
  const projectName = channel?.project_names?.[0] || channel?.name.replace(/^#/, "").split("/")[0] || ""

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

  const { handleSend: sendToChannel, handleRespond, sending } = useChannelSend({
    activeChannelId,
    projectName,
    orchestrator,
    postMessage,
  })

  const { uploadMultiple } = useFileUpload()
  const [input, setInput] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  const fullscreenInputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const handleSend = async () => {
    if (!input.trim()) return
    const msg = input.trim()
    setInput("")
    await sendToChannel(msg)
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    requestAnimationFrame(() => {
      if (fullscreen) fullscreenInputRef.current?.focus()
      else inputRef.current?.focus()
    })
  }

  // ── Fullscreen chat view ──
  if (fullscreen) {
    return (
      <div className="fixed inset-0 z-[150] bg-gray-900 flex flex-col h-app">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-800 flex-shrink-0">
          <button
            onClick={() => { setFullscreen(false); onClose?.() }}
            className="text-blue-400 text-sm"
          >
            ← Back
          </button>
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium text-gray-200 truncate">
              {channel?.name.replace(/^#/, "") || "Chat"}
            </span>
            {projectName && (
              <span className="text-[10px] text-gray-500 ml-2">{projectName}</span>
            )}
          </div>
          {channel?.auto_mode && (
            <span className="text-[9px] bg-yellow-600/30 text-yellow-400 px-1.5 py-0.5 rounded">Auto</span>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.length === 0 && (
            <div className="text-center py-12">
              <p className="text-gray-500 text-sm">Start a conversation</p>
              <p className="text-gray-600 text-xs mt-1">
                Try "show tasks", "open terminal", or describe what you want to build
              </p>
            </div>
          )}
          {messages.map((msg) => (
            <FullscreenMessage key={msg.id} message={msg} onRespond={handleRespond} />
          ))}
          {sending && (
            <div className="flex justify-start">
              <div className="bg-gray-800 text-gray-400 px-4 py-2 rounded-2xl text-sm flex items-center gap-2">
                <span className="flex gap-1">
                  <span className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </span>
                <span className="text-xs text-gray-500">Thinking...</span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Quick actions (hidden in kiosk where KioskActionBar handles this) */}
        {!hideActions && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-gray-800 overflow-x-auto flex-shrink-0">
            <ActionButton label="+ Task" onClick={onCreateTask} primary />
            <ActionButton label="+ Terminal" onClick={() => onOpenTerminal?.(projectName || "")} />
            <ActionButton label="Browser" onClick={onOpenBrowser} />
            <ActionButton label="Activity" onClick={onOpenActivity} />
            <ActionButton label="Menu" onClick={onOpenMenu} />
          </div>
        )}

        {/* Input */}
        <div className="flex items-center gap-2 px-3 py-2 border-t border-gray-800 flex-shrink-0">
          <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) uploadMultiple(e.target.files); e.target.value = "" }} />
          <button onClick={() => fileInputRef.current?.click()} className="text-gray-500 hover:text-gray-300 flex-shrink-0" title="Attach file">📎</button>
          <input
            ref={fullscreenInputRef}
            autoFocus
            data-no-global-intercept
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleSend() } }}
            placeholder={projectName ? `Message ${projectName}...` : "Type a message..."}
            className="flex-1 px-4 py-2 text-sm bg-gray-800 border border-gray-700 rounded-full text-gray-200 outline-none focus:border-blue-500"
            disabled={sending}
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim()}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-full text-white flex-shrink-0"
          >
            {sending ? "..." : "Send"}
          </button>
        </div>
      </div>
    )
  }

  // ── Compact bottom bar ──
  return (
    <div className="border-t border-gray-800 bg-gray-900 flex-shrink-0">
      {/* Quick actions */}
      {!hideActions && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 overflow-x-auto">
          <ActionButton label="+ Task" onClick={onCreateTask} primary />
          <ActionButton label="+ Terminal" onClick={() => onOpenTerminal?.(projectName || "")} />
          <ActionButton label="Browser" onClick={onOpenBrowser} />
          <ActionButton label="Activity" onClick={onOpenActivity} />
          <ActionButton label="Menu" onClick={onOpenMenu} />
        </div>
      )}

      {/* Input bar — tap to go fullscreen */}
      <div className="flex items-center gap-2 px-3 py-2">
        {messages.length > 0 && (
          <button
            onClick={() => setFullscreen(true)}
            className="text-[10px] text-gray-500 flex-shrink-0 bg-gray-800 px-1.5 py-0.5 rounded"
          >
            {messages.length}
          </button>
        )}
        <input
          ref={inputRef}
          data-no-global-intercept
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => setFullscreen(true)}
          placeholder={projectName ? `${projectName}...` : "Type a message..."}
          className="flex-1 px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded-full text-gray-200 outline-none"
          readOnly
        />
      </div>
    </div>
  )
}

// ── Components ──

function ActionButton({ label, onClick, primary }: { label: string; onClick?: () => void; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 text-[11px] font-medium rounded-md whitespace-nowrap flex-shrink-0 ${
        primary
          ? "bg-blue-600 text-white"
          : "border border-gray-600 text-gray-300"
      }`}
    >
      {label}
    </button>
  )
}

function FullscreenMessage({ message, onRespond }: { message: ChannelMessage; onRespond?: (text: string) => void }) {
  const isUser = message.role === "user"
  const isSystem = message.role === "system"

  if (isSystem) {
    return (
      <div className="text-center text-[10px] text-gray-600 py-1">{message.content}</div>
    )
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] px-3 py-2 rounded-2xl ${
        isUser
          ? "bg-blue-600 text-white"
          : "bg-gray-800 text-gray-200"
      }`}>
        {!isUser && (
          <div className="text-[10px] text-gray-500 mb-0.5 font-medium">{message.role}</div>
        )}
        <MessageRenderer message={message} variant={isUser ? "user" : "default"} onRespond={onRespond} />
        <div className={`text-[9px] mt-1 ${isUser ? "text-blue-300" : "text-gray-600"}`}>
          {new Date(message.created_at).toLocaleTimeString()}
        </div>
      </div>
    </div>
  )
}
