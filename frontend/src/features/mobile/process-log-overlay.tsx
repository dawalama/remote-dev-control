import { useState, useEffect, useRef } from "react"
import { ManagedWebSocket } from "@/lib/ws"

export function ProcessLogOverlay({
  processId,
  processName,
  onClose,
}: {
  processId: string
  processName: string
  onClose: () => void
}) {
  const [content, setContent] = useState("")
  const [paused, setPaused] = useState(false)
  const contentRef = useRef<HTMLPreElement>(null)
  const pausedRef = useRef(false)

  // Keep ref in sync
  useEffect(() => { pausedRef.current = paused }, [paused])

  useEffect(() => {
    const ws = new ManagedWebSocket(
      `/ws/action-logs/${encodeURIComponent(processId)}`,
      { reconnect: true, reconnectInterval: 3000 }
    )

    ws.onMessage((data) => {
      if (!data || typeof data !== "object") return
      if (pausedRef.current) return
      const msg = data as { type?: string; lines?: string[]; line?: string }
      if (msg.type === "initial" && msg.lines) {
        setContent(msg.lines.join("\n"))
      } else if (msg.type === "line" && msg.line) {
        setContent((prev) => prev + "\n" + msg.line)
      }
    })

    ws.connect()
    return () => ws.close()
  }, [processId])

  // Auto-scroll
  useEffect(() => {
    if (contentRef.current && !paused) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [content, paused])

  return (
    <div className="fixed inset-0 z-[100] bg-gray-900 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700 flex-shrink-0">
        <button className="text-sm text-blue-400" onClick={onClose}>
          ← Back
        </button>
        <span className="text-sm text-gray-300 font-medium truncate mx-2">
          {processName}
        </span>
        <button
          className={`px-2 py-0.5 text-[10px] rounded ${
            paused ? "bg-yellow-600 text-white" : "bg-gray-600 text-gray-300"
          }`}
          onClick={() => setPaused(!paused)}
        >
          {paused ? "Resume" : "Pause"}
        </button>
      </div>

      {/* Log content */}
      <pre
        ref={contentRef}
        className="flex-1 overflow-auto p-3 font-mono text-xs text-gray-300 whitespace-pre-wrap min-h-0"
      >
        {content || "Waiting for logs..."}
      </pre>
    </div>
  )
}
