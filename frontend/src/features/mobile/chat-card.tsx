import { useState, useRef, useCallback } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST, api } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useVoice } from "@/hooks/use-voice"
import { ChatRenderer } from "@/features/chat/chat-renderer"
import type { ChatMessage } from "@/features/chat/chat-renderer"

/**
 * Fixed bottom chat panel for mobile/kiosk — persistent message history with
 * orchestrator.  Action buttons (upload, voice, phone) sit inline next to the
 * input.  Messages expand upward when the header is tapped.
 */
export function ChatCard({
  onOpenTerminal,
  onCreateTask,
  onOpenBrowser,
  onOpenActivity,
  onOpenMenu,
  onEditProject,
  onSystemSettings,
}: {
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
  onOpenActivity?: () => void
  onOpenMenu?: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
}) {
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [expanded, setExpanded] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const autoSubmitRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const phone = useStateStore((s) => s.phone)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

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
    setExpanded(true)

    setMessages((prev) => [...prev, { role: "user", content: msg, timestamp: Date.now() }])

    try {
      const result = await orchestrator.send(msg)
      if (result?.response) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: result.response!,
            actions: result.actions as ChatMessage["actions"],
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
    setExpanded(false)
  }

  // Voice
  const handleVoiceFinal = useCallback((text: string) => {
    setInput(text)
    if (autoSubmitRef.current) clearTimeout(autoSubmitRef.current)
    autoSubmitRef.current = setTimeout(() => { send(text) }, 600)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleVoiceInterim = useCallback((text: string) => {
    setInput(text)
  }, [])

  const voice = useVoice({ onFinal: handleVoiceFinal, onInterim: handleVoiceInterim })

  // Phone
  const handlePhone = async () => {
    if (phone?.active) {
      try { await POST("/voice/hangup"); toast("Call ended", "info") }
      catch { toast("Failed to hang up", "error") }
    } else if (phone?.configured) {
      try { await POST("/voice/call", { client_id: getClientId() }); toast("Calling...", "info") }
      catch { toast("Failed to call", "error") }
    } else {
      toast("Phone not configured", "warning")
    }
  }

  // Upload
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ""
    const form = new FormData()
    form.append("file", file)
    if (currentProject && currentProject !== "all") form.append("project", currentProject)
    try {
      const res = await api<{ id: string; path: string }>("/context/upload", {
        method: "POST",
        body: form,
      })
      await navigator.clipboard.writeText(res.path).catch(() => {})
      toast(`Copied path: ${res.path}`, "success")
    } catch {
      toast("Upload failed", "error")
    }
  }

  return (
    <div className="border-t border-gray-700 bg-gray-800 flex-shrink-0">
      {/* Header — tap to expand/collapse messages */}
      <button
        className="flex items-center justify-between w-full px-3 py-1.5"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Chat {messages.length > 0 && `(${messages.length})`}
          <span className="ml-1 text-gray-600">{expanded ? "▼" : "▲"}</span>
        </span>
        {messages.length > 0 && (
          <span
            className="text-[10px] text-gray-500 hover:text-gray-300"
            onClick={(e) => { e.stopPropagation(); handleClear() }}
          >
            Clear
          </span>
        )}
      </button>

      {/* Messages area — expands upward */}
      {expanded && (messages.length > 0 || loading) && (
        <div className="overflow-auto px-3" style={{ maxHeight: "40vh" }}>
          <ChatRenderer
            messages={messages}
            loading={loading}
            emptyText=""
          />
        </div>
      )}

      {/* Input row with inline actions */}
      <input
        ref={fileInputRef}
        type="file"
        accept="*/*"
        className="hidden"
        onChange={handleUpload}
      />
      <div className="flex items-center gap-1.5 px-3 py-2">
        <input
          ref={inputRef}
          data-global-text-input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send() }}
          placeholder={voice.listening ? "Listening..." : "Message..."}
          className="flex-1 bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 min-w-0"
          disabled={loading}
        />
        <button
          className="w-8 h-8 rounded-lg flex items-center justify-center text-sm bg-gray-700 text-gray-300 flex-shrink-0"
          onClick={() => fileInputRef.current?.click()}
          title="Upload file"
        >
          📎
        </button>
        <button
          className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm flex-shrink-0 ${
            voice.listening
              ? "bg-red-600 text-white animate-pulse"
              : "bg-gray-700 text-gray-300"
          }`}
          onClick={voice.toggle}
          title={voice.listening ? "Stop" : "Voice"}
        >
          🎤
        </button>
        <button
          className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm flex-shrink-0 ${
            phone?.active
              ? "bg-green-600 text-white animate-pulse"
              : phone?.configured
                ? "bg-gray-700 text-gray-300"
                : "bg-gray-700 text-gray-500 opacity-50"
          }`}
          onClick={handlePhone}
          disabled={!phone?.configured}
          title={phone?.active ? "Hang up" : "Call"}
        >
          📞
        </button>
        <button
          className="px-3 h-8 rounded-lg bg-blue-600 text-white text-sm font-medium disabled:opacity-50 flex-shrink-0"
          onClick={() => send()}
          disabled={loading || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}
