import { useState, useRef } from "react"
import { POST } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { ChatRenderer } from "./chat-renderer"
import type { ChatMessage } from "./chat-renderer"

export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toggleChat = useUIStore((s) => s.toggleChat)

  const sendMessage = async () => {
    const msg = input.trim()
    if (!msg || loading) return

    setInput("")
    setMessages((prev) => [...prev, { role: "user", content: msg }])
    setLoading(true)

    try {
      const body: Record<string, unknown> = {
        message: msg,
        channel: "desktop",
        client_id: getClientId(),
      }
      if (currentProject) body.project = currentProject
      const result = await POST<{ response: string; usage?: { model?: string; duration_ms?: number } }>("/orchestrator", body)
      if (result?.response) {
        setMessages((prev) => [...prev, {
          role: "assistant",
          content: result.response,
          model: result.usage?.model,
          duration_ms: result.usage?.duration_ms,
        }])
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Sorry, something went wrong." },
      ])
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="w-80 border-l border-border flex flex-col bg-card shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border flex-shrink-0">
        <span className="text-sm font-medium">Chat</span>
        <Button variant="ghost" size="sm" className="text-xs h-6 w-6 p-0" onClick={toggleChat}>
          &times;
        </Button>
      </div>

      {/* Messages */}
      <ChatRenderer
        messages={messages}
        loading={loading}
        emptyText="Ask me to manage processes, create tasks, or control your projects."
      />

      {/* Input */}
      <div className="p-3 border-t border-border flex-shrink-0">
        <Textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
          className="text-xs min-h-[60px] resize-none"
          disabled={loading}
        />
        <Button
          size="sm"
          className="w-full mt-2 text-xs"
          onClick={sendMessage}
          disabled={loading || !input.trim()}
        >
          Send
        </Button>
      </div>
    </div>
  )
}
