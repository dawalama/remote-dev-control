import { useState } from "react"
import { POST } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"

interface BrowserSession {
  id: string
  viewer_url?: string
  project?: string
}

export function BrowserPreviewModal({
  session,
  onClose,
  onStop,
  visible = true,
}: {
  session: BrowserSession
  onClose: () => void
  onStop?: () => void
  visible?: boolean
}) {
  const toast = useUIStore((s) => s.toast)
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([])
  const [chatInput, setChatInput] = useState("")
  const [chatLoading, setChatLoading] = useState(false)

  const sendChat = async () => {
    const msg = chatInput.trim()
    if (!msg || chatLoading) return
    setChatInput("")
    setChatMessages((prev) => [...prev, { role: "user", content: msg }])
    setChatLoading(true)
    try {
      const result = await POST<{ response: string }>("/chat/message", {
        message: msg,
        mode: "preview",
        session_id: session.id,
        project: session.project,
      })
      if (result?.response) {
        setChatMessages((prev) => [...prev, { role: "assistant", content: result.response }])
      }
    } catch {
      setChatMessages((prev) => [...prev, { role: "assistant", content: "Error sending message" }])
    }
    setChatLoading(false)
  }

  const handleCapture = async () => {
    try {
      await POST("/context/capture", { session_id: session.id, project: session.project })
      toast("Context captured", "success")
    } catch {
      toast("Failed to capture", "error")
    }
  }

  const handleStop = async () => {
    try {
      await POST(`/browser/sessions/${session.id}/stop`)
      toast("Session stopped", "success")
      onStop?.()
    } catch {
      toast("Failed to stop", "error")
    }
  }

  if (!session.viewer_url) {
    return (
      <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50" onClick={onClose}>
        <div className="bg-gray-800 rounded-lg p-6" onClick={(e) => e.stopPropagation()}>
          <p className="text-sm text-gray-400">No viewer URL available</p>
          <button className="mt-3 px-3 py-1.5 text-xs rounded bg-gray-600 text-white" onClick={onClose}>Close</button>
        </div>
      </div>
    )
  }

  return (
    <div
      className="fixed inset-0 bg-black z-50 flex"
      style={visible ? undefined : { visibility: "hidden", pointerEvents: "none" }}
    >
      {/* iframe */}
      <div className="flex-1 min-w-0">
        <iframe
          src={session.viewer_url}
          className="w-full h-full border-0"
          allow="clipboard-write"
        />
      </div>

      {/* Side panel */}
      <div className="w-80 bg-gray-800 border-l border-gray-700 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
          <span className="text-sm font-medium text-gray-200">Preview</span>
          <div className="flex gap-1">
            <button
              className="px-2 py-0.5 text-xs rounded bg-blue-600 text-white"
              onClick={handleCapture}
            >
              Capture
            </button>
            <button
              className="px-2 py-0.5 text-xs rounded bg-red-600 text-white"
              onClick={handleStop}
            >
              Stop
            </button>
            <button
              className="px-2 py-0.5 text-xs rounded bg-gray-600 text-white"
              onClick={onClose}
            >
              Close
            </button>
          </div>
        </div>

        {/* Chat messages */}
        <div className="flex-1 overflow-auto p-3 space-y-2">
          {chatMessages.length === 0 && (
            <p className="text-xs text-gray-500 text-center mt-4">
              Chat about what you see in the preview
            </p>
          )}
          {chatMessages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[240px] rounded-lg px-3 py-2 text-xs ${
                  msg.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-gray-700 text-gray-200"
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}
          {chatLoading && (
            <div className="text-xs text-gray-500 animate-pulse">Thinking...</div>
          )}
        </div>

        {/* Chat input */}
        <div className="p-3 border-t border-gray-700 flex gap-2">
          <input
            type="text"
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") sendChat() }}
            placeholder="Ask about the preview..."
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
          />
          <button
            className="px-2 py-1.5 text-xs rounded bg-blue-600 text-white disabled:opacity-50"
            onClick={sendChat}
            disabled={chatLoading || !chatInput.trim()}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
