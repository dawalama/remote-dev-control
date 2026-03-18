import { useState, useEffect, useCallback } from "react"
import { useBrowserStore } from "@/stores/browser-store"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { GET, POST, DELETE } from "@/lib/api"
import { RecordingPlayer } from "./recording-player"

interface RecordingMeta {
  id: string
  session_id: string
  status: string
  started_at: string
  event_count: number
  chunk_count: number
}

/**
 * Sidebar tab view — compact launcher + status.
 * Shared mode: Docker/screencast session — human + agent see the same browser.
 */
export function BrowserPanel() {
  const activeSession = useBrowserStore((s) => s.activeSession)
  const sessions = useBrowserStore((s) => s.sessions)
  const loading = useBrowserStore((s) => s.loading)
  const loadSessions = useBrowserStore((s) => s.loadSessions)
  const startSession = useBrowserStore((s) => s.startSession)
  const startFromProcess = useBrowserStore((s) => s.startFromProcess)
  const setActiveSession = useBrowserStore((s) => s.setActiveSession)

  const processes = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  const [urlInput, setUrlInput] = useState("")
  const [fullscreen, setFullscreen] = useState(false)
  const [allRecordings, setAllRecordings] = useState<RecordingMeta[]>([])
  const [playingRecording, setPlayingRecording] = useState<string | null>(null)

  useEffect(() => {
    loadSessions()
    const interval = setInterval(loadSessions, 10000)
    return () => clearInterval(interval)
  }, [loadSessions])

  // Load all recordings
  useEffect(() => {
    const load = () =>
      GET<RecordingMeta[]>("/recordings")
        .then((data) => { if (Array.isArray(data)) setAllRecordings(data) })
        .catch(() => {})
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [])

  const runningSessions = sessions.filter((s) => s.status === "running")
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (currentProject === "all" || p.project === currentProject)
  )

  // Start Docker/screencast session for a process
  const handleShared = async (processId: string) => {
    const session = await startFromProcess(processId)
    if (session) {
      setFullscreen(true)
    } else {
      toast("Failed to start shared session", "error")
    }
  }

  // URL input → start shared Docker session
  const handleUrlSubmit = useCallback(async () => {
    const url = urlInput.trim()
    if (!url) return
    const fullUrl = url.startsWith("http") ? url : `http://${url}`
    const project = currentProject !== "all" ? currentProject : undefined
    const session = await startSession(fullUrl, project)
    if (session) {
      setFullscreen(true)
    } else {
      toast("Failed to start browser", "error")
    }
  }, [urlInput, startSession, currentProject, toast])

  const handleSessionOpen = (session: typeof runningSessions[0]) => {
    setActiveSession(session)
    setFullscreen(true)
  }

  const handleDeleteRecording = async (id: string) => {
    try {
      await DELETE(`/recordings/${id}`)
      setAllRecordings((prev) => prev.filter((r) => r.id !== id))
      toast("Recording deleted", "success")
    } catch {
      toast("Failed to delete", "error")
    }
  }

  // Playing a recording in fullscreen
  if (playingRecording) {
    return (
      <div className="fixed inset-0 bg-gray-900 z-50 flex flex-col">
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700 flex-shrink-0">
          <button className="text-sm text-gray-400 hover:text-gray-200" onClick={() => setPlayingRecording(null)}>
            Back
          </button>
          <span className="text-xs text-gray-500">Recording Playback</span>
          <div />
        </div>
        <div className="flex-1 min-h-0 p-4">
          <RecordingPlayer
            recordingId={playingRecording}
            onBack={() => setPlayingRecording(null)}
          />
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="space-y-3">
        {/* URL input — always visible */}
        <div className="flex gap-1">
          <input
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleUrlSubmit() }}
            placeholder="localhost:3000 or any URL..."
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
          />
          <button
            className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
            onClick={handleUrlSubmit}
            disabled={loading || !urlInput.trim()}
          >
            {loading ? "..." : "Go"}
          </button>
        </div>

        {/* URL suggestions from running processes */}
        {processesWithPorts.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {processesWithPorts.map((p) => {
              const url = p.preview_url || `http://localhost:${p.port}`
              return (
                <button
                  key={p.id}
                  className="px-2 py-0.5 text-[10px] rounded bg-gray-700 hover:bg-gray-600 text-gray-300 flex items-center gap-1"
                  onClick={() => setUrlInput(url)}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                  {p.name || p.id}
                  <span className="text-gray-500">:{p.port}</span>
                </button>
              )
            })}
          </div>
        )}

        {/* Active shared session — mini preview + expand */}
        {activeSession && activeSession.status === "running" && (
          <div className="rounded-lg overflow-hidden border border-purple-600/40">
            <div
              className="relative cursor-pointer group"
              onClick={() => setFullscreen(true)}
              style={{ height: 180 }}
            >
              {activeSession.viewer_url ? (
                <>
                  <iframe
                    src={activeSession.viewer_url}
                    className="w-full h-full border-0 pointer-events-none"
                    title="Shared Session"
                    tabIndex={-1}
                  />
                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center">
                    <span className="opacity-0 group-hover:opacity-100 transition-opacity text-white text-xs bg-black/60 px-3 py-1.5 rounded-full">
                      Expand
                    </span>
                  </div>
                </>
              ) : (
                <div className="flex items-center justify-center h-full bg-gray-800 text-gray-500 text-xs">
                  Connecting...
                </div>
              )}
            </div>
            <div className="flex items-center gap-1.5 px-2 py-1.5 bg-gray-800 border-t border-gray-700">
              <span className="text-[10px] text-purple-400 flex-shrink-0">Shared</span>
              <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse flex-shrink-0" />
              <span className="text-[10px] text-gray-400 flex-1 truncate min-w-0">
                {activeSession.target_url}
              </span>
              <button
                className="text-[10px] text-purple-400 hover:text-purple-300 flex-shrink-0"
                onClick={() => setFullscreen(true)}
              >
                Expand
              </button>
            </div>
          </div>
        )}

        {/* Process shortcuts — start shared session */}
        {processesWithPorts.length > 0 && (
          <div>
            <h4 className="text-[10px] uppercase text-gray-500 mb-1">Quick Launch</h4>
            <div className="space-y-1">
              {processesWithPorts.map((p) => (
                <div key={p.id} className="rounded bg-gray-700 overflow-hidden">
                  <button
                    className="w-full px-2 py-1.5 text-xs hover:bg-gray-600 text-gray-300 flex items-center gap-2"
                    onClick={() => handleShared(p.id)}
                    disabled={loading}
                  >
                    <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
                    <span className="flex-1 truncate text-left">{p.project}/{p.name || p.id}</span>
                    <span className="text-gray-500">:{p.port}</span>
                  </button>
                  {p.preview_url && (
                    <a
                      href={p.preview_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1.5 px-2 py-1 border-t border-gray-600 text-[10px] text-green-400 hover:text-green-300 truncate"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                      {p.preview_url.replace("https://", "")}
                    </a>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Active shared sessions list */}
        {runningSessions.length > 0 && (
          <div>
            <h4 className="text-[10px] uppercase text-gray-500 mb-1">Shared Sessions</h4>
            <div className="space-y-1">
              {runningSessions.map((s) => (
                <button
                  key={s.id}
                  className={`w-full text-left px-2 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300 flex items-center gap-2 ${
                    activeSession?.id === s.id ? "ring-1 ring-purple-500/50" : ""
                  }`}
                  onClick={() => handleSessionOpen(s)}
                >
                  <span className="w-2 h-2 rounded-full bg-purple-400 animate-pulse flex-shrink-0" />
                  <span className="flex-1 truncate">{s.target_url}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Recordings list */}
        {allRecordings.length > 0 && (
          <div>
            <h4 className="text-[10px] uppercase text-gray-500 mb-1">Recordings</h4>
            <div className="space-y-1">
              {allRecordings.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center gap-1.5 px-2 py-1.5 text-xs rounded bg-gray-700 text-gray-300"
                >
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    r.status === "recording" ? "bg-red-400 animate-pulse" : "bg-gray-400"
                  }`} />
                  <span className="flex-1 truncate min-w-0">
                    {new Date(r.started_at).toLocaleString([], {
                      month: "short", day: "numeric",
                      hour: "2-digit", minute: "2-digit",
                    })}
                  </span>
                  <span className="text-[10px] text-gray-500 flex-shrink-0">
                    {r.event_count} evt
                  </span>
                  {r.event_count > 0 && (
                    <button
                      className="text-[10px] text-blue-400 hover:text-blue-300 flex-shrink-0"
                      onClick={() => setPlayingRecording(r.id)}
                    >
                      Play
                    </button>
                  )}
                  <button
                    className="text-[10px] text-red-400 hover:text-red-300 flex-shrink-0"
                    onClick={() => handleDeleteRecording(r.id)}
                  >
                    Del
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {!activeSession && processesWithPorts.length === 0 && runningSessions.length === 0 && (
          <div className="text-center py-8">
            <p className="text-gray-500 text-xs">No browser sessions</p>
            <p className="text-gray-600 text-[10px] mt-1">Enter a URL or click a running process to start a shared session</p>
          </div>
        )}

      </div>

      {/* Fullscreen shared session overlay */}
      {fullscreen && (
        <BrowserFullscreen onClose={() => setFullscreen(false)} />
      )}
    </>
  )
}


/**
 * Fullscreen browser overlay — the actual browsing experience.
 * URL bar, iframe viewer, session tabs, recording controls.
 */
function BrowserFullscreen({ onClose }: { onClose: () => void }) {
  const activeSession = useBrowserStore((s) => s.activeSession)
  const sessions = useBrowserStore((s) => s.sessions)
  const stopSession = useBrowserStore((s) => s.stopSession)
  const navigate = useBrowserStore((s) => s.navigate)
  const startSession = useBrowserStore((s) => s.startSession)
  const setActiveSession = useBrowserStore((s) => s.setActiveSession)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  const [urlInput, setUrlInput] = useState(activeSession?.target_url || "")
  const [recording, setRecording] = useState(false)
  const [recordings, setRecordings] = useState<RecordingMeta[]>([])
  const [playingRecording, setPlayingRecording] = useState<string | null>(null)

  const runningSessions = sessions.filter((s) => s.status === "running")

  // Sync URL bar
  useEffect(() => {
    if (activeSession?.target_url) setUrlInput(activeSession.target_url)
  }, [activeSession?.target_url])

  // Load recordings for current session
  useEffect(() => {
    if (activeSession) {
      GET<RecordingMeta[]>(`/recordings?session_id=${activeSession.id}`)
        .then((data) => { if (Array.isArray(data)) setRecordings(data) })
        .catch(() => {})
    }
  }, [activeSession?.id, recording])

  // Close if no session
  useEffect(() => {
    if (!activeSession) onClose()
  }, [activeSession, onClose])

  const handleUrlSubmit = useCallback(async () => {
    const url = urlInput.trim()
    if (!url) return
    const fullUrl = url.startsWith("http") ? url : `http://${url}`

    if (activeSession && activeSession.status === "running") {
      await navigate(fullUrl)
    } else {
      const project = currentProject !== "all" ? currentProject : undefined
      await startSession(fullUrl, project)
    }
  }, [urlInput, activeSession, navigate, startSession, currentProject])

  const handleCapture = async () => {
    if (!activeSession) return
    try {
      const params = new URLSearchParams({ session_id: activeSession.id })
      if (currentProject && currentProject !== "all") params.set("project", currentProject)
      await POST(`/context/capture?${params}`)
      toast("Context captured", "success")
    } catch {
      toast("Capture failed", "error")
    }
  }

  const handleRecordToggle = async () => {
    if (!activeSession) return
    try {
      if (recording) {
        await POST(`/browser/sessions/${activeSession.id}/record/stop`)
        setRecording(false)
        toast("Recording stopped", "success")
      } else {
        await POST(`/browser/sessions/${activeSession.id}/record/start`)
        setRecording(true)
        toast("Recording started", "success")
      }
    } catch {
      toast("Recording failed", "error")
    }
  }

  const handleStop = async () => {
    if (!activeSession) return
    if (recording) {
      try { await POST(`/browser/sessions/${activeSession.id}/record/stop`) } catch {}
      setRecording(false)
    }
    await stopSession(activeSession.id)
  }

  if (!activeSession) return null

  // Playing a recording
  if (playingRecording) {
    return (
      <div className="fixed inset-0 bg-gray-900 z-50 flex flex-col">
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700 flex-shrink-0">
          <button className="text-sm text-gray-400 hover:text-gray-200" onClick={() => setPlayingRecording(null)}>
            Back to Live
          </button>
          <span className="text-xs text-gray-500">Recording Playback</span>
          <button className="text-sm text-gray-400 hover:text-gray-200" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="flex-1 min-h-0 p-4">
          <RecordingPlayer
            recordingId={playingRecording}
            onBack={() => setPlayingRecording(null)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-gray-900 z-50 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-700 flex-shrink-0">
        {/* Back button */}
        <button
          className="text-sm text-gray-400 hover:text-gray-200 flex-shrink-0"
          onClick={onClose}
        >
          Minimize
        </button>

        <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600 text-white flex-shrink-0">
          Shared
        </span>

        {/* URL bar */}
        <div className="flex-1 flex gap-1 min-w-0">
          <input
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleUrlSubmit() }}
            className="flex-1 bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-blue-500 min-w-0"
          />
          <button
            className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-white flex-shrink-0"
            onClick={handleUrlSubmit}
          >
            Go
          </button>
        </div>

        {/* Actions */}
        <div className="flex gap-1 flex-shrink-0">
          <button
            className="px-2.5 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
            onClick={handleCapture}
          >
            Capture
          </button>
          <button
            className={`px-2.5 py-1.5 text-xs rounded text-white ${
              recording
                ? "bg-red-600 hover:bg-red-700 animate-pulse"
                : "bg-gray-700 hover:bg-gray-600"
            }`}
            onClick={handleRecordToggle}
          >
            {recording ? "Stop Rec" : "Record"}
          </button>
          <button
            className="px-2.5 py-1.5 text-xs rounded bg-red-600 hover:bg-red-700 text-white"
            onClick={handleStop}
          >
            Stop
          </button>
        </div>
      </div>

      {/* Session tabs (if multiple) */}
      {runningSessions.length > 1 && (
        <div className="flex gap-1 px-3 py-1.5 border-b border-gray-800 flex-shrink-0 overflow-x-auto">
          {runningSessions.map((s) => (
            <button
              key={s.id}
              className={`px-3 py-1 text-xs rounded whitespace-nowrap ${
                s.id === activeSession.id
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => { setActiveSession(s); setPlayingRecording(null) }}
            >
              {s.target_url ? new URL(s.target_url).hostname : s.id}
            </button>
          ))}
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 min-h-0 flex">
        {/* Viewer iframe */}
        <div className="flex-1 min-w-0">
          {activeSession.viewer_url ? (
            <iframe
              src={activeSession.viewer_url}
              className="w-full h-full border-0"
              allow="clipboard-write"
              title="Browser Preview"
            />
          ) : (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              Connecting...
            </div>
          )}
        </div>

        {/* Recordings sidebar (only if recordings exist) */}
        {recordings.length > 0 && (
          <div className="w-48 border-l border-gray-700 bg-gray-800 flex flex-col flex-shrink-0">
            <div className="px-3 py-2 border-b border-gray-700">
              <h4 className="text-[10px] uppercase text-gray-500">Recordings</h4>
            </div>
            <div className="flex-1 overflow-auto p-2 space-y-1">
              {recordings.map((r) => (
                <button
                  key={r.id}
                  className="w-full text-left px-2 py-1.5 text-[10px] rounded bg-gray-700 hover:bg-gray-600 text-gray-300 flex items-center gap-1.5"
                  onClick={() => setPlayingRecording(r.id)}
                >
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    r.status === "recording" ? "bg-red-400 animate-pulse" : "bg-gray-400"
                  }`} />
                  <span className="flex-1 truncate">
                    {new Date(r.started_at).toLocaleTimeString()}
                  </span>
                  <span className="text-gray-500">{r.event_count}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
