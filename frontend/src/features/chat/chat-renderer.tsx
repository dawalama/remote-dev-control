import { useRef, useEffect } from "react"
import type { Spec } from "@json-render/core"
import { ChatSpecRenderer } from "@/lib/chat-components"

/** A chat message — either plain text or a json-render spec (or both). */
export interface ChatMessage {
  role: "user" | "assistant"
  content: string
  /** json-render spec for rich rendering (ActionResult cards, QuestionForm, etc.) */
  spec?: Spec | null
  /** Actions that were executed (shown as subtle metadata) */
  actions?: { action: string; [key: string]: unknown }[]
  /** Model name (optional metadata) */
  model?: string
  /** Duration in ms (optional metadata) */
  duration_ms?: number
  /** Timestamp */
  timestamp?: number
}

interface ChatRendererProps {
  messages: ChatMessage[]
  loading?: boolean
  /** Placeholder shown when message list is empty */
  emptyText?: string
  /** Called when user interacts with a json-render action (select_choice, send_reply, click_link) */
  onAction?: (actionName: string, params?: Record<string, unknown>) => void
}

/**
 * Shared chat message renderer used by all chat surfaces.
 *
 * Renders a list of messages as bubbles with auto-scroll.
 * Supports both plain text messages and json-render specs for rich content.
 */
export function ChatRenderer({ messages, loading, emptyText, onAction }: ChatRendererProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, loading])

  return (
    <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto p-3 space-y-2">
      {messages.length === 0 && !loading && (
        <p className="text-xs text-gray-500 text-center mt-8">
          {emptyText || "Send a message to get started."}
        </p>
      )}

      {messages.map((msg, i) => (
        <ChatMessageBubble key={i} message={msg} onAction={onAction} />
      ))}

      {loading && (
        <div className="flex justify-start">
          <div className="bg-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-400 animate-pulse">
            Thinking...
          </div>
        </div>
      )}
    </div>
  )
}

function ChatMessageBubble({
  message,
  onAction,
}: {
  message: ChatMessage
  onAction?: (actionName: string, params?: Record<string, unknown>) => void
}) {
  const { role, content, spec, actions, model, duration_ms } = message

  // Rich spec rendering (from browser agent or structured responses)
  if (spec && role === "assistant") {
    return (
      <div className="space-y-2">
        {content && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-lg px-3 py-1.5 text-sm bg-gray-700 text-gray-200">
              {content}
            </div>
          </div>
        )}
        <ChatSpecRenderer spec={spec} onAction={onAction} />
      </div>
    )
  }

  // Plain text bubble
  return (
    <div className={`flex ${role === "user" ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${
          role === "user"
            ? "bg-blue-600/80 text-white"
            : "bg-gray-700 text-gray-200"
        }`}
      >
        {content}
        {actions && actions.length > 0 && (
          <div className="mt-1 text-[10px] text-gray-400 italic">
            {actions.map((a) => (a.action || "").replace(/_/g, " ")).join(", ")}
          </div>
        )}
      </div>
      {role === "assistant" && model && (
        <div className="self-end ml-1 text-[10px] text-gray-600">
          {model}{duration_ms ? ` ${(duration_ms / 1000).toFixed(1)}s` : ""}
        </div>
      )}
    </div>
  )
}
