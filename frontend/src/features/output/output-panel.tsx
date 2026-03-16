import { useState, useEffect, useRef } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useStateStore } from "@/stores/state-store"
import { GET } from "@/lib/api"
import { ManagedWebSocket } from "@/lib/ws"

export function OutputPanel() {
  const selectedTaskId = useUIStore((s) => s.selectedTaskId)
  const tasks = useStateStore((s) => s.tasks)
  const [live, setLive] = useState(false)
  const [output, setOutput] = useState<string>("")
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<ManagedWebSocket | null>(null)

  const selectedTask = tasks.find((t) => t.id === selectedTaskId)

  // Fetch task output when task is selected
  useEffect(() => {
    if (!selectedTaskId) {
      setOutput("")
      return
    }
    setLoading(true)
    GET<{ output?: string; text?: string } | string>(`/tasks/${selectedTaskId}/output`)
      .then((result) => {
        if (typeof result === "string") {
          setOutput(result)
        } else {
          setOutput(result?.output || result?.text || "No output available.")
        }
      })
      .catch(() => setOutput("Failed to load output."))
      .finally(() => setLoading(false))
  }, [selectedTaskId])

  // Live streaming via /ws event bus
  useEffect(() => {
    if (!live) {
      wsRef.current?.close()
      wsRef.current = null
      return
    }

    const ws = new ManagedWebSocket("/ws", {
      reconnect: true,
      reconnectInterval: 3000,
    })

    ws.onMessage((data) => {
      if (
        data &&
        typeof data === "object" &&
        "type" in data
      ) {
        const evt = data as { type: string; data?: { output?: string; text?: string; task_id?: string } }
        if (
          evt.type === "agent.output" ||
          evt.type === "task.output" ||
          evt.type === "agent_output"
        ) {
          const text = evt.data?.output || evt.data?.text || ""
          if (text) {
            setOutput((prev) => prev + text)
          }
        }
      }
    })

    ws.connect()
    wsRef.current = ws

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [live])

  // Auto-scroll when output changes
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [output])

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Output</h2>
          {selectedTask && (
            <span className="text-xs text-gray-400 truncate max-w-[200px]">
              — {selectedTask.title || selectedTask.description?.slice(0, 40)}
            </span>
          )}
        </div>
        <button
          className={`px-2 py-1 text-xs rounded ${
            live ? "bg-green-600 text-white" : "bg-gray-600 text-gray-300"
          }`}
          onClick={() => setLive(!live)}
        >
          {live ? "● Live" : "○ Live"}
        </button>
      </div>
      <div
        ref={scrollRef}
        className="max-h-64 overflow-y-auto bg-gray-900 p-3 rounded font-mono text-xs text-gray-300 whitespace-pre-wrap"
      >
        {loading && <p className="text-gray-500 animate-pulse">Loading output...</p>}
        {!loading && !output && !selectedTaskId && (
          <p className="text-gray-500">Select a task to view output...</p>
        )}
        {!loading && !output && selectedTaskId && (
          <p className="text-gray-500">No output available for this task.</p>
        )}
        {!loading && output && output}
      </div>
    </div>
  )
}
