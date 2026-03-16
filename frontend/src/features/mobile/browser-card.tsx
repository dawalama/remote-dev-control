import { useState, useEffect } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useBrowserStore } from "@/stores/browser-store"
import { GET, DELETE as DEL, POST } from "@/lib/api"
import type { BrowserSession } from "@/types"

interface RecordingMeta {
  id: string
  session_id: string
  status: string
  started_at: string
  event_count: number
  chunk_count: number
}

export function BrowserCard({
  onOpenSession,
  onPlayRecording,
}: {
  onOpenSession: (session: { viewer_url: string; session_id: string }) => void
  onPlayRecording: (recordingId: string) => void
}) {
  const toast = useUIStore((s) => s.toast)
  const processes = useStateStore((s) => s.processes)
  const currentProject = useProjectStore((s) => s.currentProject)
  const startFromProcess = useBrowserStore((s) => s.startFromProcess)
  const [sessions, setSessions] = useState<BrowserSession[]>([])
  const [recordings, setRecordings] = useState<RecordingMeta[]>([])
  const [expanded, setExpanded] = useState(true)
  const [launching, setLaunching] = useState<string | null>(null)

  const load = () => {
    GET<BrowserSession[]>("/browser/sessions")
      .then(setSessions)
      .catch(() => {})
    GET<RecordingMeta[]>("/recordings")
      .then(setRecordings)
      .catch(() => {})
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [])

  const runningSessions = sessions.filter((s) => s.status === "running")
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (currentProject === "all" || p.project === currentProject),
  )
  // Don't show processes that already have a running browser session
  const runningProcessIds = new Set(runningSessions.map((s) => s.process_id).filter(Boolean))
  const launchableProcesses = processesWithPorts.filter((p) => !runningProcessIds.has(p.id))

  const totalCount = runningSessions.length + recordings.length

  const handleStopSession = async (id: string) => {
    try {
      await POST(`/browser/sessions/${id}/stop`)
      toast("Stopped", "success")
      load()
    } catch {
      toast("Failed", "error")
    }
  }

  const handleDeleteRecording = async (id: string) => {
    try {
      await DEL(`/recordings/${id}`)
      setRecordings((prev) => prev.filter((r) => r.id !== id))
      toast("Deleted", "success")
    } catch {
      toast("Failed", "error")
    }
  }

  const handleLaunch = async (processId: string) => {
    setLaunching(processId)
    try {
      const session = await startFromProcess(processId)
      if (session?.viewer_url) {
        onOpenSession({ viewer_url: session.viewer_url, session_id: session.id })
      } else {
        toast("Failed to start browser", "error")
      }
      load()
    } catch {
      toast("Failed to start browser", "error")
    } finally {
      setLaunching(null)
    }
  }

  const hasContent = totalCount > 0 || launchableProcesses.length > 0

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <button
        className="flex items-center justify-between w-full mb-2"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Browser
          </h3>
          {totalCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-700 text-gray-400">
              {totalCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            className="text-[10px] text-blue-400"
            onClick={(e) => { e.stopPropagation(); load() }}
          >
            Refresh
          </button>
          <span className="text-gray-500 text-xs">{expanded ? "▲" : "▼"}</span>
        </div>
      </button>

      {expanded && (
        <div className="space-y-2">
          {/* Running sessions */}
          {runningSessions.length > 0 && (
            <div>
              <p className="text-[10px] text-gray-500 uppercase mb-1">Sessions</p>
              {runningSessions.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-gray-700"
                >
                  <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse flex-shrink-0" />
                  <button
                    className="flex-1 min-w-0 text-left"
                    onClick={() => {
                      if (s.viewer_url) {
                        onOpenSession({ viewer_url: s.viewer_url, session_id: s.id })
                      }
                    }}
                  >
                    <p className="text-xs text-gray-200 truncate">{s.target_url || s.id}</p>
                  </button>
                  <button
                    className="text-[10px] text-red-400 flex-shrink-0"
                    onClick={() => handleStopSession(s.id)}
                  >
                    Stop
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Quick launch from running processes */}
          {launchableProcesses.length > 0 && (
            <div>
              <p className="text-[10px] text-gray-500 uppercase mb-1">Open in Browser</p>
              {launchableProcesses.map((p) => (
                <button
                  key={p.id}
                  className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-gray-700 w-full text-left disabled:opacity-50"
                  onClick={() => handleLaunch(p.id)}
                  disabled={launching === p.id}
                >
                  <span className="w-2 h-2 rounded-full bg-blue-400 flex-shrink-0" />
                  <span className="flex-1 min-w-0 text-xs text-gray-200 truncate">
                    {p.name || p.id}
                  </span>
                  <span className="text-[10px] text-gray-500 flex-shrink-0">:{p.port}</span>
                  <span className="text-[10px] text-blue-400 flex-shrink-0">
                    {launching === p.id ? "..." : "Launch"}
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Recordings */}
          {recordings.length > 0 && (
            <div>
              <p className="text-[10px] text-gray-500 uppercase mb-1">Recordings</p>
              {recordings.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-gray-700"
                >
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    r.status === "recording" ? "bg-red-400 animate-pulse" : "bg-gray-400"
                  }`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-gray-300">
                      {new Date(r.started_at).toLocaleString([], {
                        month: "short", day: "numeric",
                        hour: "2-digit", minute: "2-digit",
                      })}
                    </p>
                    <p className="text-[10px] text-gray-500">{r.event_count} events</p>
                  </div>
                  {r.event_count > 0 && (
                    <button
                      className="text-[10px] text-blue-400 flex-shrink-0"
                      onClick={() => onPlayRecording(r.id)}
                    >
                      Play
                    </button>
                  )}
                  <button
                    className="text-[10px] text-red-400 flex-shrink-0"
                    onClick={() => handleDeleteRecording(r.id)}
                  >
                    Del
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Empty state */}
          {!hasContent && (
            <p className="text-[10px] text-gray-500 text-center py-2">
              No browser sessions. Start a process with a port to launch a browser.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
