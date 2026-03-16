import { useState, useEffect, useCallback } from "react"
import { usePinchTabStore } from "@/stores/pinchtab-store"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useBrowserAgent } from "@/hooks/use-browser-agent"

export function PinchTabOverlay({
  onClose,
  visible = true,
}: {
  onClose: () => void
  visible?: boolean
}) {
  const {
    available, tabs, activeTabId, screenshotDataUrl, screenshotTime,
    loading, loadStatus, navigate, takeSnapshot, takeScreenshot,
    startPinchTab,
  } = usePinchTabStore()

  const processes = useStateStore((s) => s.processes)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  const { agentInput, setAgentInput, sendingToAgent, sendToAgent, conversationHistory, clearHistory } = useBrowserAgent("mobile")

  const [urlInput, setUrlInput] = useState("")
  const [fitMode, setFitMode] = useState<"contain" | "cover">("contain")

  const urlSuggestions = processes
    .filter((p) => p.port && p.status === "running" && (currentProject === "all" || p.project === currentProject))
    .map((p) => ({
      label: p.name || p.id,
      url: p.preview_url || `http://localhost:${p.port}`,
      port: p.port!,
    }))

  // Load status on mount, auto-refresh screenshot every 5s
  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  useEffect(() => {
    if (!visible || !available) return
    takeScreenshot()
    const interval = setInterval(takeScreenshot, 5000)
    return () => clearInterval(interval)
  }, [visible, available, takeScreenshot])

  // Sync URL bar when active tab changes
  useEffect(() => {
    const activeTab = tabs.find((t) => t.id === activeTabId)
    if (activeTab?.url) setUrlInput(activeTab.url)
  }, [activeTabId, tabs])

  const handleNavigate = useCallback(() => {
    const url = urlInput.trim()
    if (!url) return
    const fullUrl = url.startsWith("http") ? url : `https://${url}`
    navigate(fullUrl)
  }, [urlInput, navigate])

  return (
    <div
      className="fixed inset-0 z-[110] bg-gray-900 flex flex-col"
      style={visible ? undefined : { visibility: "hidden", pointerEvents: "none" }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700 flex-shrink-0">
        <button className="text-sm text-gray-400" onClick={onClose}>
          ← Back
        </button>
        <div className="flex items-center gap-2">
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-600 text-white">PinchTab</span>
          {tabs.length > 0 && (
            <span className="text-[10px] text-gray-500">{tabs.length} tab{tabs.length !== 1 ? "s" : ""}</span>
          )}
        </div>
        <span className="text-[10px] text-gray-500">{screenshotTime || ""}</span>
      </div>

      {!available ? (
        /* Not available — show start button */
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <p className="text-gray-500 text-sm mb-3">PinchTab not running</p>
            <button
              className="px-6 py-2 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
              onClick={startPinchTab}
              disabled={loading}
            >
              {loading ? "Starting..." : "Start PinchTab"}
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* URL bar */}
          <div className="flex gap-1 px-3 py-2 border-b border-gray-800 flex-shrink-0">
            <input
              type="text"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleNavigate() }}
              placeholder="URL to navigate..."
              className="flex-1 bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-blue-500 min-w-0"
            />
            <button
              className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 flex-shrink-0"
              onClick={handleNavigate}
              disabled={loading || !urlInput.trim()}
            >
              {loading ? "..." : "Go"}
            </button>
          </div>

          {/* URL suggestions */}
          {urlSuggestions.length > 0 && (
            <div className="flex gap-1 px-3 py-1.5 border-b border-gray-800 flex-shrink-0 overflow-x-auto scrollbar-none">
              {urlSuggestions.map((s) => (
                <button
                  key={s.url}
                  className="px-2.5 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center gap-1.5 whitespace-nowrap flex-shrink-0"
                  onClick={() => { setUrlInput(s.url); navigate(s.url) }}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                  {s.label}
                  <span className="text-gray-500">:{s.port}</span>
                </button>
              ))}
            </div>
          )}

          {/* Action buttons strip */}
          <div className="flex gap-1.5 px-3 py-1.5 border-b border-gray-800 flex-shrink-0">
            <button
              className="px-2.5 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={() => takeScreenshot()}
            >
              Refresh
            </button>
            <button
              className="px-2.5 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300"
              onClick={async () => {
                await takeSnapshot()
                const snap = usePinchTabStore.getState().snapshot
                const count = snap ? snap.length : 0
                toast(`Snapshot: ${count} elements`, "success")
              }}
            >
              Snapshot
            </button>
            <button
              className="px-2.5 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300"
              onClick={handleNavigate}
            >
              Navigate
            </button>
          </div>

          {/* Screenshot area */}
          <div
            className="flex-1 min-h-0 flex items-center justify-center p-2 overflow-auto"
            onClick={() => setFitMode((m) => m === "contain" ? "cover" : "contain")}
          >
            {screenshotDataUrl ? (
              <img
                src={screenshotDataUrl}
                alt="Page screenshot"
                className="max-w-full max-h-full rounded shadow-2xl cursor-pointer"
                style={{ objectFit: fitMode }}
              />
            ) : (
              <p className="text-gray-500 text-sm">No screenshot yet — tap Refresh</p>
            )}
          </div>

          {/* Agent response — show latest from conversation history */}
          {conversationHistory.length > 0 && (() => {
            const last = conversationHistory[conversationHistory.length - 1]
            return last.role === "assistant" ? (
              <div className="mx-3 mb-1 bg-purple-900/20 border border-purple-600/30 rounded p-2 relative max-h-24 overflow-auto">
                <button
                  className="absolute top-1 right-1 text-[10px] text-gray-500 hover:text-gray-300"
                  onClick={clearHistory}
                >
                  X
                </button>
                <pre className="text-[10px] text-purple-200 whitespace-pre-wrap break-words font-mono pr-4">
                  {last.content}
                </pre>
              </div>
            ) : null
          })()}

          {/* Agent instruction bar — bottom */}
          <div className="flex items-center gap-2 px-3 py-2 border-t border-gray-700 flex-shrink-0">
            <span className="text-[10px] text-purple-400 flex-shrink-0">Agent</span>
            <input
              type="text"
              value={agentInput}
              onChange={(e) => setAgentInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") sendToAgent(agentInput) }}
              placeholder="Tell the agent what to do..."
              className="flex-1 bg-gray-800 border border-purple-600/40 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-purple-500 min-w-0"
            />
            <button
              className="px-3 py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50 flex-shrink-0"
              onClick={() => sendToAgent(agentInput)}
              disabled={sendingToAgent || !agentInput.trim()}
            >
              {sendingToAgent ? "..." : "Send"}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
