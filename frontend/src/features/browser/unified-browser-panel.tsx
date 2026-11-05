import { useState, useEffect, useCallback } from "react"
import { useBrowserStore } from "@/stores/browser-store"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useChannelStore } from "@/stores/channel-store"
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
 * Sidebar tab view for the browser.
 * Shows the launcher, the active session preview, and recordings.
 */
export function UnifiedBrowserPanel() {
  const activeSession = useBrowserStore((s) => s.activeSession)
  const sessions = useBrowserStore((s) => s.sessions)
  const loading = useBrowserStore((s) => s.loading)
  const loadSessions = useBrowserStore((s) => s.loadSessions)
  const startSession = useBrowserStore((s) => s.startSession)
  const startFromProcess = useBrowserStore((s) => s.startFromProcess)
  const setActiveSession = useBrowserStore((s) => s.setActiveSession)

  const processes = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const activeChannel = useChannelStore((s) => {
    const ch = s.channels.find((c) => c.id === s.activeChannelId)
    return ch ?? null
  })
  const workstreamProjects = activeChannel?.project_names && activeChannel.project_names.length > 0
    ? activeChannel.project_names
    : (currentProject ? [currentProject] : [])
  const toast = useUIStore((s) => s.toast)

  const [urlInput, setUrlInput] = useState("")
  const [fullscreen, setFullscreen] = useState(false)
  const [allRecordings, setAllRecordings] = useState<RecordingMeta[]>([])
  const [playingRecording, setPlayingRecording] = useState<string | null>(null)

  useEffect(() => {
    loadSessions()
    const interval = setInterval(loadSessions, 30000)
    return () => clearInterval(interval)
  }, [loadSessions])

  useEffect(() => {
    const load = () =>
      GET<RecordingMeta[]>("/recordings")
        .then((data) => { if (Array.isArray(data)) setAllRecordings(data) })
        .catch(() => {})
    load()
    const interval = setInterval(load, 60000)
    return () => clearInterval(interval)
  }, [])

  const runningSessions = sessions.filter((s) => s.status === "running")
  const processesWithPorts = processes.filter(
    (p) => p.port && p.status === "running" && (workstreamProjects.length === 0 || workstreamProjects.includes(p.project)),
  )

  const handleShared = async (processId: string) => {
    const session = await startFromProcess(processId)
    if (session) setFullscreen(true)
    else toast("Failed to start browser session", "error")
  }

  const handleUrlSubmit = useCallback(async () => {
    const url = urlInput.trim()
    if (!url) return
    const fullUrl = url.startsWith("http") ? url : `http://${url}`
    const project = workstreamProjects[0] ?? currentProject ?? undefined
    const session = await startSession(fullUrl, project)
    if (session) setFullscreen(true)
    else toast("Failed to start browser", "error")
  }, [urlInput, startSession, workstreamProjects, currentProject, toast])

  const handleSessionOpen = (session: typeof runningSessions[0]) => {
    setActiveSession(session)
    setFullscreen(true)
  }

  const handleDeleteRecording = async (id: string) => {
    try {
      await DELETE(`/recordings/${id}`)
      setAllRecordings((prev) => prev.filter((r) => r.id !== id))
      toast("Recording deleted", "success")
    } catch { toast("Failed to delete", "error") }
  }

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
          <RecordingPlayer recordingId={playingRecording} onBack={() => setPlayingRecording(null)} />
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="space-y-3">
        {/* URL input */}
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

        {/* URL suggestions */}
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

        {/* Active session mini preview */}
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
                    title="Browser Session"
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

        {/* Quick launch */}
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

        {/* Active sessions */}
        {runningSessions.length > 0 && (
          <div>
            <h4 className="text-[10px] uppercase text-gray-500 mb-1">Sessions</h4>
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

        {/* Recordings */}
        {allRecordings.length > 0 && (
          <div>
            <h4 className="text-[10px] uppercase text-gray-500 mb-1">Recordings</h4>
            <div className="space-y-1">
              {allRecordings.map((r) => (
                <div key={r.id} className="flex items-center gap-1.5 px-2 py-1.5 text-xs rounded bg-gray-700 text-gray-300">
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    r.status === "recording" ? "bg-red-400 animate-pulse" : "bg-gray-400"
                  }`} />
                  <span className="flex-1 truncate min-w-0">
                    {new Date(r.started_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </span>
                  <span className="text-[10px] text-gray-500 flex-shrink-0">{r.event_count} evt</span>
                  {r.event_count > 0 && (
                    <button className="text-[10px] text-blue-400 hover:text-blue-300 flex-shrink-0" onClick={() => setPlayingRecording(r.id)}>
                      Play
                    </button>
                  )}
                  <button className="text-[10px] text-red-400 hover:text-red-300 flex-shrink-0" onClick={() => handleDeleteRecording(r.id)}>
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
            <p className="text-gray-600 text-[10px] mt-1">Enter a URL or click a running process to start</p>
          </div>
        )}
      </div>

      {fullscreen && (
        <UnifiedBrowserFullscreen
          onClose={() => {
            setFullscreen(false)
            useUIStore.getState().setAgentPanelOpen(false)
          }}
          onPlayRecording={(id) => { setFullscreen(false); setPlayingRecording(id) }}
        />
      )}
    </>
  )
}

/**
 * Fullscreen unified browser — live iframe + agent instruction bar.
 * Always-copilot: human interacts via iframe, agent via instruction bar.
 */
export function UnifiedBrowserFullscreen({
  onClose,
  onPlayRecording,
}: {
  onClose: () => void
  onPlayRecording: (id: string) => void
}) {
  const activeSession = useBrowserStore((s) => s.activeSession)
  const sessions = useBrowserStore((s) => s.sessions)
  const loadSessions = useBrowserStore((s) => s.loadSessions)
  const stopSession = useBrowserStore((s) => s.stopSession)
  const browserReload = useBrowserStore((s) => s.reload)
  const setActiveSession = useBrowserStore((s) => s.setActiveSession)

  const processes = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const activeChannel = useChannelStore((s) => {
    const ch = s.channels.find((c) => c.id === s.activeChannelId)
    return ch ?? null
  })
  const workstreamProjects = activeChannel?.project_names && activeChannel.project_names.length > 0
    ? activeChannel.project_names
    : (currentProject ? [currentProject] : [])
  const toast = useUIStore((s) => s.toast)
  const toggleAgentPanel = useUIStore((s) => s.toggleAgentPanel)

  const [recording, setRecording] = useState(false)
  const [recordings, setRecordings] = useState<RecordingMeta[]>([])

  const runningSessions = sessions.filter((s) => s.status === "running")

  useEffect(() => {
    if (activeSession) {
      GET<RecordingMeta[]>(`/recordings?session_id=${activeSession.id}`)
        .then((data) => { if (Array.isArray(data)) setRecordings(data) })
        .catch(() => {})
    }
  }, [activeSession?.id, recording])

  useEffect(() => {
    if (!activeSession) onClose()
  }, [activeSession, onClose])

  // Poll sessions while fullscreen to keep viewer_url fresh
  useEffect(() => {
    const interval = setInterval(loadSessions, 30000)
    return () => clearInterval(interval)
  }, [loadSessions])

  // Find caddy preview URL for the session's process
  const caddyUrl = activeSession?.process_id
    ? processes.find((p) => p.id === activeSession.process_id)?.preview_url
    : undefined

  const handleCapture = async () => {
    if (!activeSession) return
    try {
      const params = new URLSearchParams({ session_id: activeSession.id })
      const targetProject = workstreamProjects[0] || currentProject
      if (targetProject) params.set("project", targetProject)
      await POST(`/context/capture?${params}`)
      toast("Context captured", "success")
    } catch { toast("Capture failed", "error") }
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
    } catch { toast("Recording failed", "error") }
  }

  const handleStop = async () => {
    if (!activeSession) return
    if (recording) {
      try { await POST(`/browser/sessions/${activeSession.id}/record/stop`) } catch { /* */ }
      setRecording(false)
    }
    await stopSession(activeSession.id)
  }

  if (!activeSession) return null

  return (
    <div className="fixed inset-0 h-app bg-gray-900 z-50 flex flex-col">
      {/* Top bar — two rows on mobile, single row on desktop */}
      <div className="border-b border-gray-700 flex-shrink-0">
        {/* Row 1: Close + Reload + URL display + caddy link */}
        <div className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 sm:py-2">
          <button className="text-sm text-gray-400 hover:text-gray-200 flex-shrink-0" onClick={onClose}>
            ←
          </button>
          <button
            className="w-7 h-7 sm:w-8 sm:h-8 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 flex items-center justify-center flex-shrink-0"
            onClick={browserReload}
            title="Reload"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
            </svg>
          </button>
          <span className="flex-1 truncate text-xs sm:text-sm text-gray-400 min-w-0 px-1">
            {activeSession?.target_url || "—"}
          </span>
          {caddyUrl && (
            <a
              href={caddyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] sm:text-xs text-green-400 hover:text-green-300 truncate max-w-[120px] sm:max-w-[200px] flex-shrink-0"
              title={`Open ${caddyUrl} in new tab`}
            >
              {caddyUrl.replace("https://", "")}
            </a>
          )}

          {/* Desktop-only: inline action buttons */}
          <div className="hidden sm:flex gap-1 flex-shrink-0 ml-1">
            <button className="px-2.5 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white" onClick={handleCapture}>
              Capture
            </button>
            <button
              className={`px-2.5 py-1.5 text-xs rounded text-white flex items-center gap-1 ${
                recording ? "bg-red-600 hover:bg-red-700" : "bg-gray-700 hover:bg-gray-600"
              }`}
              onClick={handleRecordToggle}
            >
              {recording && <span className="w-2 h-2 rounded-full bg-white animate-pulse" />}
              {recording ? "Stop Rec" : "Record"}
            </button>
            <button className="px-2.5 py-1.5 text-xs rounded bg-red-600 hover:bg-red-700 text-white" onClick={handleStop}>
              Stop
            </button>
          </div>
        </div>

        {/* Row 2: Mobile-only action bar */}
        <div className="flex sm:hidden items-center gap-1.5 px-2 py-1 border-t border-gray-700/50">
          <button className="px-2.5 py-1 text-[11px] rounded bg-blue-600 hover:bg-blue-700 text-white" onClick={handleCapture}>
            Capture
          </button>
          <button
            className={`px-2.5 py-1 text-[11px] rounded text-white flex items-center gap-1 ${
              recording ? "bg-red-600 hover:bg-red-700" : "bg-gray-700 hover:bg-gray-600"
            }`}
            onClick={handleRecordToggle}
          >
            {recording && <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />}
            {recording ? "Stop Rec" : "Record"}
          </button>
          <div className="flex-1" />
          <button className="px-2.5 py-1 text-[11px] rounded bg-red-600 hover:bg-red-700 text-white" onClick={handleStop}>
            Stop Session
          </button>
        </div>
      </div>

      {/* Session tabs */}
      {runningSessions.length > 1 && (
        <div className="flex gap-1 px-3 py-1.5 border-b border-gray-800 flex-shrink-0 overflow-x-auto">
          {runningSessions.map((s) => (
            <button
              key={s.id}
              className={`px-3 py-1 text-xs rounded whitespace-nowrap ${
                s.id === activeSession.id ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => setActiveSession(s)}
            >
              {s.target_url ? new URL(s.target_url).hostname : s.id}
            </button>
          ))}
        </div>
      )}

      {/* Main content: live iframe */}
      <div className="flex-1 min-h-0 flex flex-col">
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

          {/* Recordings sidebar */}
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
                    onClick={() => onPlayRecording(r.id)}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                      r.status === "recording" ? "bg-red-400 animate-pulse" : "bg-gray-400"
                    }`} />
                    <span className="flex-1 truncate">{new Date(r.started_at).toLocaleTimeString()}</span>
                    <span className="text-gray-500">{r.event_count}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

      </div>

      {/* Bottom bar: agent + inspect */}
      <div className="flex items-center gap-1.5 sm:gap-2 px-2 sm:px-3 py-1.5 sm:py-2 border-t border-gray-700 flex-shrink-0">
        <button
          className="px-2.5 py-1 sm:px-3 sm:py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-700 text-white flex-shrink-0"
          onClick={toggleAgentPanel}
        >
          Agent
        </button>
      </div>
    </div>
  )
}
