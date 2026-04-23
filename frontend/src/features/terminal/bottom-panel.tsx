import { useState, useEffect, useRef } from "react"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useLogsStore } from "@/stores/logs-store"
import { GET, POST, DELETE, api } from "@/lib/api"
import { ContextViewerModal } from "@/features/modals/context-viewer"

interface ContextSnapshot {
  id: string
  title?: string
  url?: string
  timestamp: string
  screenshot_url?: string
  description?: string
}

export function BottomPanel() {
  const [activeTab, setActiveTab] = useState("contexts")
  const [collapsed, setCollapsed] = useState(true)

  // Logs store integration
  const logPanes = useLogsStore((s) => s.panes)
  const activePaneId = useLogsStore((s) => s.activePaneId)
  const logsOpen = useLogsStore((s) => s.isOpen)
  const setActivePane = useLogsStore((s) => s.setActivePane)
  const closePane = useLogsStore((s) => s.closePane)
  const togglePause = useLogsStore((s) => s.togglePause)
  const refreshPane = useLogsStore((s) => s.refreshPane)

  // When a log pane opens, auto-expand and switch to it
  useEffect(() => {
    if (logsOpen && activePaneId) {
      setActiveTab(activePaneId)
      setCollapsed(false)
    }
  }, [logsOpen, activePaneId])

  const isContextsTab = activeTab === "contexts"
  const activeLogPane = logPanes.find((p) => p.id === activeTab)

  const handleCloseLogTab = (id: string) => {
    closePane(id)
    // Switch back to contexts if we closed the active tab
    if (activeTab === id) {
      setActiveTab("contexts")
    }
  }

  return (
    <div className={`${collapsed ? "flex-shrink-0" : "flex-[35]"} flex flex-col min-h-0 bg-gray-800 rounded-lg overflow-hidden`}>
      {/* Tab bar + collapse */}
      <div className="flex items-center px-3 py-1.5 flex-shrink-0">
        <div className="flex-1 flex items-center gap-1 overflow-x-auto">
          {/* Contexts tab (always present) */}
          <button
            className={`flex items-center gap-1 px-2 py-1 text-xs whitespace-nowrap rounded ${
              isContextsTab
                ? "text-white bg-gray-700"
                : "text-gray-400 hover:text-gray-200"
            }`}
            onClick={() => {
              setActiveTab("contexts")
              setCollapsed(false)
            }}
          >
            Attachments
          </button>

          {/* Log pane tabs */}
          {logPanes.map((pane) => (
            <button
              key={pane.id}
              className={`flex items-center gap-1 px-2 py-1 text-xs whitespace-nowrap rounded ${
                activeTab === pane.id
                  ? "text-white bg-gray-700"
                  : "text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => {
                setActiveTab(pane.id)
                setActivePane(pane.id)
                setCollapsed(false)
              }}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                  pane.type === "process-log"
                    ? "bg-green-400"
                    : pane.type === "system-log"
                      ? "bg-yellow-400"
                      : "bg-blue-400"
                }`}
              />
              {pane.title}
              {pane.paused && <span className="text-yellow-400 text-[10px]">⏸</span>}
              <span
                className="ml-0.5 text-gray-500 hover:text-red-400"
                onClick={(e) => {
                  e.stopPropagation()
                  handleCloseLogTab(pane.id)
                }}
              >
                ×
              </span>
            </button>
          ))}
        </div>

        {/* Controls */}
        <div className="flex items-center gap-1 ml-1 flex-shrink-0">
          {activeLogPane && (
            <>
              <button
                className={`px-1 py-0.5 text-[10px] rounded ${
                  activeLogPane.paused
                    ? "bg-yellow-600 text-white"
                    : "bg-gray-600 text-gray-300"
                }`}
                onClick={() => togglePause(activeLogPane.id)}
                title={activeLogPane.paused ? "Resume" : "Pause"}
              >
                {activeLogPane.paused ? "▶" : "⏸"}
              </button>
              <button
                className="px-1 py-0.5 text-[10px] rounded bg-gray-600 text-gray-300 hover:bg-gray-500"
                onClick={() => refreshPane(activeLogPane.id)}
                title="Refresh"
              >
                ↻
              </button>
            </>
          )}
          <button
            className="px-2 py-1 text-xs text-gray-400 hover:text-gray-200"
            onClick={() => setCollapsed(!collapsed)}
          >
            {collapsed ? "▲" : "▼"}
          </button>
        </div>
      </div>

      {/* Content */}
      {!collapsed && (
        <div className="flex-1 min-h-0 overflow-auto p-3">
          {isContextsTab && <ContextsTab />}
          {activeLogPane && <LogContent pane={activeLogPane} />}
        </div>
      )}
    </div>
  )
}

// ─── Log content viewer ───────────────────────────────────────────────

function LogContent({ pane }: { pane: { id: string; content: string; paused: boolean } }) {
  const contentRef = useRef<HTMLPreElement>(null)

  // Auto-scroll to bottom when content updates (unless paused)
  useEffect(() => {
    if (contentRef.current && !pane.paused) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [pane.content, pane.paused])

  return (
    <pre
      ref={contentRef}
      className="text-xs font-mono text-gray-300 whitespace-pre-wrap h-full overflow-auto"
    >
      {pane.content || "No output."}
    </pre>
  )
}

// ─── Contexts tab ─────────────────────────────────────────────────────

function ContextsTab() {
  const [contexts, setContexts] = useState<ContextSnapshot[]>([])
  const [viewId, setViewId] = useState<string | null>(null)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const load = () => {
    const params = new URLSearchParams({ limit: "50" })
    if (currentProject) params.set("project", currentProject)
    GET<ContextSnapshot[]>(`/context?${params}`)
      .then(setContexts)
      .catch(() => {})
  }

  useEffect(() => { load() }, [currentProject])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ""
    const form = new FormData()
    form.append("file", file)
    if (currentProject) form.append("project", currentProject)
    try {
      const res = await api<{ id: string; path: string }>("/context/upload", {
        method: "POST",
        body: form,
      })
      await navigator.clipboard.writeText(res.path).catch(() => {})
      toast(`Uploaded — path copied`, "success")
      load()
    } catch {
      toast("Upload failed", "error")
    }
  }

  const handleDelete = async (id: string) => {
    await DELETE(`/context/${id}`).catch(() => {})
    setContexts((prev) => prev.filter((c) => c.id !== id))
  }

  const handleMCP = (id: string) => {
    const instruction = `Use the get_browser_context tool with context_id="${id}" to see the current browser state`
    POST("/chat/message", { message: instruction, mode: "orchestrator" }).catch(() => {})
    toast("Sent context via MCP", "success")
  }

  const handleRaw = async (ctx: ContextSnapshot) => {
    const detail = await GET<{ url?: string; timestamp?: string; screenshot_path?: string; a11y?: unknown[] }>(`/context/${ctx.id}`).catch(() => null)
    if (!detail) return
    const text = [
      `[Context: ${ctx.title || ctx.id}]`,
      detail.url && `URL: ${detail.url}`,
      detail.timestamp && `Captured: ${detail.timestamp}`,
      detail.screenshot_path && `Screenshot: ${detail.screenshot_path}`,
      detail.a11y && `A11y nodes: ${JSON.stringify(detail.a11y).slice(0, 500)}`,
    ].filter(Boolean).join("\n")
    POST("/chat/message", { message: text, mode: "orchestrator" }).catch(() => {})
    toast("Sent raw context", "success")
  }

  const handleCopyPath = (id: string) => {
    navigator.clipboard.writeText(`/context/${id}/screenshot`).then(
      () => toast("Path copied", "success"),
      () => toast("Copy failed", "error")
    )
  }

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="*/*"
        className="hidden"
        onChange={handleUpload}
      />
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] text-gray-500">{contexts.length} attachments</span>
        <div className="flex gap-1.5">
          <button
            className="px-2 py-0.5 text-[10px] rounded bg-blue-600 hover:bg-blue-700 text-white"
            onClick={() => fileInputRef.current?.click()}
          >
            Upload
          </button>
          <button
            className="px-2 py-0.5 text-[10px] rounded bg-gray-600 hover:bg-gray-500 text-white"
            onClick={load}
          >
            Refresh
          </button>
        </div>
      </div>

      {contexts.length === 0 ? (
        <p className="text-gray-500 text-xs text-center py-4">No attachments yet — capture from the Browser tab or upload a file</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {contexts.map((ctx) => (
            <div
              key={ctx.id}
              className="bg-gray-700 rounded p-2 group relative cursor-pointer"
              onClick={() => setViewId(ctx.id)}
            >
              <img
                src={`/context/${ctx.id}/screenshot`}
                alt=""
                className="w-full h-24 object-cover rounded mb-1"
                loading="lazy"
                onError={(e) => (e.currentTarget.style.display = "none")}
              />
              <div className="text-xs text-gray-300 truncate">{ctx.title || ctx.url || ctx.description || ctx.id}</div>
              {ctx.url && <div className="text-[10px] text-gray-500 truncate">{ctx.url}</div>}
              <div className="text-[10px] text-gray-500">
                {new Date(ctx.timestamp).toLocaleTimeString()}
              </div>
              <div className="absolute top-1 right-1 hidden group-hover:flex gap-1">
                <button
                  className="px-1.5 py-0.5 text-[10px] rounded bg-green-600/80 text-white"
                  onClick={(e) => { e.stopPropagation(); handleMCP(ctx.id) }}
                  title="Send via MCP"
                >
                  MCP
                </button>
                <button
                  className="px-1.5 py-0.5 text-[10px] rounded bg-indigo-600/80 text-white"
                  onClick={(e) => { e.stopPropagation(); handleRaw(ctx) }}
                  title="Send raw context"
                >
                  Raw
                </button>
                <button
                  className="px-1.5 py-0.5 text-[10px] rounded bg-gray-600/80 text-white"
                  onClick={(e) => { e.stopPropagation(); handleCopyPath(ctx.id) }}
                  title="Copy path"
                >
                  Copy
                </button>
                <button
                  className="px-1.5 py-0.5 text-[10px] rounded bg-red-600/80 text-white"
                  onClick={(e) => { e.stopPropagation(); handleDelete(ctx.id) }}
                  title="Delete"
                >
                  Del
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {viewId && (
        <ContextViewerModal
          contextId={viewId}
          onClose={() => setViewId(null)}
          onDeleted={load}
        />
      )}
    </>
  )
}
