import { useState, useEffect, useRef } from "react"
import { GET } from "@/lib/api"
import { ManagedWebSocket } from "@/lib/ws"
import { useStateStore } from "@/stores/state-store"

export function TaskOutputOverlay({
  taskId,
  taskTitle,
  onClose,
}: {
  taskId: string
  taskTitle: string
  onClose: () => void
}) {
  const [lines, setLines] = useState<string[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const contentRef = useRef<HTMLPreElement>(null)
  const task = useStateStore((s) => s.tasks.find((t) => t.id === taskId))
  const isRunning = task?.status === "running" || task?.status === "in_progress"
  const isFinished = task?.status === "completed" || task?.status === "failed"

  // For running tasks, stream via WebSocket
  useEffect(() => {
    if (isFinished) {
      // Fetch final output from REST
      GET<{ output?: string; text?: string; result?: string } | string>(
        `/tasks/${taskId}/output`
      ).then((result) => {
        let text: string
        if (typeof result === "string") {
          text = result || "No output."
        } else {
          text = result?.output || result?.text || result?.result || "No output."
        }
        setLines(text.split("\n"))
        setStatus("done")
      }).catch(() => {
        setLines(["Failed to load output."])
        setStatus("error")
      })
      return
    }

    // Live streaming via WebSocket
    setLines(["Connecting..."])
    const ws = new ManagedWebSocket(
      `/ws/task-logs/${encodeURIComponent(taskId)}`,
      { reconnect: true, reconnectInterval: 3000 }
    )

    ws.onMessage((data) => {
      if (!data || typeof data !== "object") return
      const msg = data as {
        type?: string
        lines?: string[]
        line?: string
        status?: string
        content?: string
      }

      if (msg.type === "initial" && msg.lines) {
        setLines(msg.lines.length > 0 ? msg.lines : ["Waiting for output..."])
      } else if (msg.type === "line" && msg.line) {
        setLines((prev) => {
          const updated = prev[0] === "Connecting..." || prev[0] === "Waiting for output..."
            ? [msg.line!]
            : [...prev, msg.line!]
          // Keep last 2000 lines
          return updated.length > 2000 ? updated.slice(-2000) : updated
        })
      } else if (msg.type === "completed") {
        setStatus(msg.status || "completed")
      }
    })

    ws.connect()
    return () => { ws.close() }
  }, [taskId, isFinished])

  // Auto-scroll on new lines
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [lines])

  return (
    <div className="fixed inset-0 z-[100] bg-gray-900 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700 flex-shrink-0">
        <button className="text-sm text-blue-400" onClick={onClose}>
          ← Back
        </button>
        <div className="flex items-center gap-2 mx-2 min-w-0">
          <span className="text-sm text-gray-300 font-medium truncate">
            {taskTitle}
          </span>
          {isRunning && !status && (
            <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse shrink-0" />
          )}
          {status && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
              status === "completed" || status === "done"
                ? "bg-green-400/10 text-green-400"
                : status === "failed"
                  ? "bg-red-400/10 text-red-400"
                  : "bg-gray-400/10 text-gray-400"
            }`}>
              {status}
            </span>
          )}
        </div>
        <div className="shrink-0" />
      </div>

      {/* Output content */}
      <pre
        ref={contentRef}
        className="flex-1 overflow-auto p-3 font-mono text-xs text-gray-300 whitespace-pre-wrap min-h-0"
      >
        {lines.join("\n")}
      </pre>
    </div>
  )
}
