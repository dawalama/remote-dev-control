import { useState, useRef, useEffect } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useTerminalStore } from "@/stores/terminal-store"
import { ChatRenderer } from "./chat-renderer"
import type { ChatMessage } from "./chat-renderer"

export function ChatFAB() {
  const open = useUIStore((s) => s.chatOpen)
  const toggleChat = useUIStore((s) => s.toggleChat)
  const spawnTerminal = useTerminalStore((s) => s.spawnTerminal)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50)
  }, [open])

  const orchestrator = useOrchestrator({
    channel: "desktop",
    onOpenTerminal: (project) => spawnTerminal(project),
  })

  const send = async () => {
    const msg = input.trim()
    if (!msg || loading) return

    setInput("")
    setMessages((prev) => [...prev, { role: "user", content: msg }])
    setLoading(true)

    try {
      const result = await orchestrator.send(msg)
      if (result?.response) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: result.response!,
            actions: result.actions as ChatMessage["actions"],
          },
        ])
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Sorry, something went wrong." },
      ])
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  if (!open) return null

  return (
    <div className="fixed bottom-[60px] right-4 w-96 h-[500px] bg-gray-800 rounded-lg shadow-xl flex flex-col z-45 border border-gray-700">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700 flex-shrink-0">
        <span className="text-sm font-medium text-gray-200">Chat</span>
        <div className="flex items-center gap-1">
          {messages.length > 0 && (
            <button
              className="text-[10px] text-gray-500 hover:text-gray-300 px-1"
              onClick={() => {
                setMessages([])
                orchestrator.clearHistory()
              }}
            >
              Clear
            </button>
          )}
          <button
            className="text-gray-400 hover:text-gray-200 text-lg leading-none"
            onClick={toggleChat}
          >
            &times;
          </button>
        </div>
      </div>

      {/* Messages */}
      <ChatRenderer
        messages={messages}
        loading={loading}
        emptyText="Ask me to manage processes, create tasks, or control your projects."
      />

      {/* Input */}
      <div className="p-3 border-t border-gray-700 flex gap-2 flex-shrink-0">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
          className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-blue-500"
          disabled={loading}
          autoFocus
        />
        <button
          className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={send}
          disabled={loading || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}
