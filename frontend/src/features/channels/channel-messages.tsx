import { useState, useRef, useCallback } from "react"
import { useChannelStore } from "@/stores/channel-store"
import type { ChannelMessage } from "@/stores/channel-store"

export function ChannelMessages({ channelId }: { channelId: string }) {
  const messages = useChannelStore((s) => s.messages)
  const postMessage = useChannelStore((s) => s.postMessage)
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const handleSend = useCallback(async () => {
    if (!input.trim() || sending) return
    setSending(true)
    await postMessage(channelId, input.trim())
    setInput("")
    setSending(false)
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [input, sending, channelId, postMessage])

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {messages.length === 0 && (
          <div className="text-center text-gray-600 text-xs py-8">
            No messages yet. Start a conversation.
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 border-t border-gray-800 px-3 py-2">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend() } }}
            placeholder="Type a message..."
            className="flex-1 px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 outline-none focus:border-blue-500"
            disabled={sending}
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim()}
            className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-white font-medium"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: ChannelMessage }) {
  const isUser = message.role === "user"
  const isSystem = message.role === "system"

  if (isSystem) {
    return (
      <div className="text-center text-[10px] text-gray-600 py-1">
        {message.content}
      </div>
    )
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] px-3 py-1.5 rounded-lg text-sm ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-gray-800 text-gray-200"
        }`}
      >
        {!isUser && (
          <div className="text-[10px] text-gray-500 mb-0.5 font-medium">
            {message.role}
          </div>
        )}
        <div className="whitespace-pre-wrap">{message.content}</div>
        <div className={`text-[9px] mt-0.5 ${isUser ? "text-blue-300" : "text-gray-600"}`}>
          {new Date(message.created_at).toLocaleTimeString()}
        </div>
      </div>
    </div>
  )
}
