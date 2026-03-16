import { useState, useEffect, useCallback } from "react"
import { usePinchTabStore } from "@/stores/pinchtab-store"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useBrowserAgent } from "@/hooks/use-browser-agent"

type ContentView = "screenshot" | "elements" | "text"

export function PinchTabPanel() {
  const {
    available, tabs, activeTabId, snapshot, screenshotDataUrl, screenshotTime,
    pageText, loading, loadStatus, navigate, takeSnapshot, takeScreenshot,
    extractText, performAction, setActiveTab, startPinchTab,
  } = usePinchTabStore()

  const processes = useStateStore((s) => s.processes)
  const currentProject = useProjectStore((s) => s.currentProject)

  const { agentInput, setAgentInput, sendingToAgent, sendToAgent, conversationHistory, clearHistory } = useBrowserAgent("desktop")

  const [urlInput, setUrlInput] = useState("")
  const [view, setView] = useState<ContentView>("screenshot")
  const [fillRef, setFillRef] = useState<string | null>(null)
  const [fillValue, setFillValue] = useState("")
  const [fullscreen, setFullscreen] = useState(false)

  const urlSuggestions = processes
    .filter((p) => p.port && p.status === "running" && (currentProject === "all" || p.project === currentProject))
    .map((p) => ({
      label: p.name || p.id,
      url: p.preview_url || `http://localhost:${p.port}`,
      port: p.port!,
    }))

  // Load status on mount, then poll every 30s while panel is visible
  useEffect(() => {
    loadStatus()
    const interval = setInterval(loadStatus, 30000)
    return () => clearInterval(interval)
  }, [loadStatus])

  const handleNavigate = useCallback(() => {
    const url = urlInput.trim()
    if (!url) return
    const fullUrl = url.startsWith("http") ? url : `https://${url}`
    navigate(fullUrl)
  }, [urlInput, navigate])

  const handleFillSubmit = (ref: string) => {
    if (!fillValue.trim()) return
    performAction("fill", ref, fillValue)
    setFillRef(null)
    setFillValue("")
  }

  // Not available state
  if (!available) {
    return (
      <div className="text-center py-8">
        <p className="text-gray-500 text-xs mb-3">PinchTab not running</p>
        <button
          className="px-4 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={startPinchTab}
          disabled={loading}
        >
          {loading ? "Starting..." : "Start"}
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* Tab strip */}
      {tabs.length > 1 && (
        <div className="flex gap-1 overflow-x-auto scrollbar-none">
          {tabs.map((t) => (
            <button
              key={t.id}
              className={`px-2 py-1 text-[10px] rounded whitespace-nowrap flex-shrink-0 ${
                t.id === activeTabId
                  ? "bg-green-600 text-white"
                  : "bg-gray-700 text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => setActiveTab(t.id)}
              title={t.url}
            >
              {t.title || t.url?.replace(/^https?:\/\//, "").slice(0, 20) || t.id.slice(0, 8)}
            </button>
          ))}
        </div>
      )}

      {/* URL bar */}
      <div className="flex gap-1">
        <input
          type="text"
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleNavigate() }}
          placeholder="URL to navigate..."
          className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500 min-w-0"
        />
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 flex-shrink-0"
          onClick={handleNavigate}
          disabled={loading || !urlInput.trim()}
        >
          {loading ? "..." : "Go"}
        </button>
      </div>

      {/* URL suggestions from running processes */}
      {urlSuggestions.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {urlSuggestions.map((s) => (
            <button
              key={s.url}
              className="px-2 py-0.5 text-[10px] rounded bg-gray-700 hover:bg-gray-600 text-gray-300 flex items-center gap-1"
              onClick={() => { setUrlInput(s.url); navigate(s.url) }}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
              {s.label}
              <span className="text-gray-500">:{s.port}</span>
            </button>
          ))}
        </div>
      )}

      {/* Agent instruction bar */}
      <div className="flex gap-1">
        <input
          type="text"
          value={agentInput}
          onChange={(e) => setAgentInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") sendToAgent(agentInput) }}
          placeholder="Tell the agent what to do..."
          className="flex-1 bg-gray-900 border border-purple-600/40 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-purple-500 min-w-0"
        />
        <button
          className="px-3 py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50 flex-shrink-0"
          onClick={() => sendToAgent(agentInput)}
          disabled={sendingToAgent || !agentInput.trim()}
        >
          {sendingToAgent ? "..." : "Send"}
        </button>
      </div>

      {/* Agent response — show latest from conversation history */}
      {conversationHistory.length > 0 && (() => {
        const last = conversationHistory[conversationHistory.length - 1]
        return last.role === "assistant" ? (
          <div className="bg-purple-900/20 border border-purple-600/30 rounded p-2 relative">
            <button
              className="absolute top-1 right-1 text-[10px] text-gray-500 hover:text-gray-300"
              onClick={clearHistory}
            >
              X
            </button>
            <pre className="text-[10px] text-purple-200 whitespace-pre-wrap break-words font-mono max-h-32 overflow-auto">
              {last.content}
            </pre>
          </div>
        ) : null
      })()}

      {/* View toggle buttons */}
      <div className="flex gap-1">
        {(["screenshot", "elements", "text"] as ContentView[]).map((v) => (
          <button
            key={v}
            className={`flex-1 px-2 py-1 text-[10px] rounded capitalize ${
              view === v
                ? "bg-gray-600 text-white"
                : "bg-gray-700 text-gray-400 hover:text-gray-200"
            }`}
            onClick={() => {
              setView(v)
              if (v === "screenshot") takeScreenshot()
              else if (v === "elements") takeSnapshot()
              else if (v === "text") extractText()
            }}
          >
            {v === "screenshot" ? "Screen" : v === "elements" ? "Elements" : "Text"}
          </button>
        ))}
      </div>

      {/* Content area */}
      {view === "screenshot" && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500">
              {screenshotTime || "No screenshot yet"}
            </span>
            <div className="flex gap-1">
              <button
                className="px-2 py-0.5 text-[10px] rounded bg-blue-600 hover:bg-blue-700 text-white"
                onClick={takeScreenshot}
              >
                Screenshot
              </button>
              {screenshotDataUrl && (
                <button
                  className="px-2 py-0.5 text-[10px] rounded bg-gray-600 hover:bg-gray-500 text-white"
                  onClick={() => setFullscreen(true)}
                >
                  Expand
                </button>
              )}
            </div>
          </div>
          {screenshotDataUrl ? (
            <img
              src={screenshotDataUrl}
              alt="Page screenshot"
              className="w-full rounded border border-gray-700 cursor-pointer"
              onClick={() => setFullscreen(true)}
            />
          ) : (
            <div className="text-center py-6 text-gray-500 text-xs">
              Click Screenshot to capture the current page
            </div>
          )}
        </div>
      )}

      {view === "elements" && (
        <div>
          <div className="flex items-center justify-end mb-1">
            <button
              className="px-2 py-0.5 text-[10px] rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={takeSnapshot}
            >
              Snapshot
            </button>
          </div>
          {snapshot ? (
            <div className="space-y-0.5 max-h-[60vh] overflow-auto">
              {renderElements(snapshot, performAction, fillRef, setFillRef, fillValue, setFillValue, handleFillSubmit)}
            </div>
          ) : (
            <div className="text-center py-6 text-gray-500 text-xs">
              Click Snapshot to capture the element tree
            </div>
          )}
        </div>
      )}

      {view === "text" && (
        <div>
          <div className="flex items-center justify-end mb-1">
            <button
              className="px-2 py-0.5 text-[10px] rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={extractText}
            >
              Extract
            </button>
          </div>
          {pageText ? (
            <pre className="bg-gray-900 rounded p-2 text-[10px] text-gray-300 max-h-[60vh] overflow-auto font-mono whitespace-pre-wrap break-words">
              {pageText}
            </pre>
          ) : (
            <div className="text-center py-6 text-gray-500 text-xs">
              Click Extract to get page text
            </div>
          )}
        </div>
      )}

      {/* Fullscreen screenshot overlay */}
      {fullscreen && screenshotDataUrl && (
        <PinchTabFullscreen
          onClose={() => setFullscreen(false)}
          onRefresh={takeScreenshot}
          onNavigate={navigate}
          sendToAgent={sendToAgent}
          screenshotDataUrl={screenshotDataUrl}
          screenshotTime={screenshotTime}
          activeTab={tabs.find((t) => t.id === activeTabId)}
          urlSuggestions={urlSuggestions}
        />
      )}
    </div>
  )
}


interface UrlSuggestion {
  label: string
  url: string
  port: number
}

function PinchTabFullscreen({ onClose, onRefresh, onNavigate, sendToAgent, screenshotDataUrl, screenshotTime, activeTab, urlSuggestions }: {
  onClose: () => void
  onRefresh: () => Promise<void>
  onNavigate: (url: string) => Promise<void>
  sendToAgent: (instruction: string) => Promise<void>
  screenshotDataUrl: string
  screenshotTime: string | null
  activeTab?: { id: string; title: string; url: string }
  urlSuggestions: UrlSuggestion[]
}) {
  const [urlInput, setUrlInput] = useState(activeTab?.url || "")
  const [refreshing, setRefreshing] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [agentInput, setAgentInput] = useState("")
  const [sending, setSending] = useState(false)

  // Sync URL bar when tab changes
  useEffect(() => {
    if (activeTab?.url) setUrlInput(activeTab.url)
  }, [activeTab?.url])

  // Auto-refresh interval
  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(() => { onRefresh() }, 2000)
    return () => clearInterval(interval)
  }, [autoRefresh, onRefresh])

  const handleNav = async () => {
    const url = urlInput.trim()
    if (!url) return
    const fullUrl = url.startsWith("http") ? url : `https://${url}`
    await onNavigate(fullUrl)
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    await onRefresh()
    setRefreshing(false)
  }

  const handleAgentSend = async () => {
    if (!agentInput.trim()) return
    setSending(true)
    await sendToAgent(agentInput)
    setAgentInput("")
    setSending(false)
  }

  return (
    <div className="fixed inset-0 bg-gray-900 z-50 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-700 flex-shrink-0">
        <button
          className="text-sm text-gray-400 hover:text-gray-200 flex-shrink-0"
          onClick={onClose}
        >
          Minimize
        </button>

        <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-600 text-white flex-shrink-0">
          PinchTab
        </span>

        {/* URL bar */}
        <div className="flex-1 flex gap-1 min-w-0">
          <input
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleNav() }}
            className="flex-1 bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-blue-500 min-w-0"
          />
          <button
            className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-white flex-shrink-0"
            onClick={handleNav}
          >
            Go
          </button>
        </div>

        {/* Actions */}
        <div className="flex gap-1 flex-shrink-0 items-center">
          <button
            className={`px-2.5 py-1.5 text-xs rounded text-white ${refreshing ? "bg-gray-600" : "bg-blue-600 hover:bg-blue-700"}`}
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? "..." : "Refresh"}
          </button>
          <button
            className={`px-2.5 py-1.5 text-xs rounded text-white ${
              autoRefresh ? "bg-green-600 hover:bg-green-700" : "bg-gray-700 hover:bg-gray-600"
            }`}
            onClick={() => setAutoRefresh(!autoRefresh)}
            title="Auto-refresh every 2s"
          >
            {autoRefresh ? "Auto: ON" : "Auto"}
          </button>
          {screenshotTime && (
            <span className="text-[10px] text-gray-500">{screenshotTime}</span>
          )}
        </div>
      </div>

      {/* URL suggestions */}
      {urlSuggestions.length > 0 && (
        <div className="flex gap-1 px-3 py-1.5 border-b border-gray-800 flex-shrink-0 overflow-x-auto scrollbar-none">
          {urlSuggestions.map((s) => (
            <button
              key={s.url}
              className="px-2.5 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center gap-1.5 whitespace-nowrap flex-shrink-0"
              onClick={() => { setUrlInput(s.url); onNavigate(s.url) }}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
              {s.label}
              <span className="text-gray-500">:{s.port}</span>
            </button>
          ))}
        </div>
      )}

      {/* Screenshot — centered, scaled to fit */}
      <div className="flex-1 min-h-0 flex items-center justify-center p-4 overflow-auto">
        <img
          src={screenshotDataUrl}
          alt="Page screenshot"
          className="max-w-full max-h-full object-contain rounded shadow-2xl"
        />
      </div>

      {/* Agent instruction bar — bottom */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-gray-700 flex-shrink-0">
        <span className="text-[10px] text-purple-400 flex-shrink-0">Agent</span>
        <input
          type="text"
          value={agentInput}
          onChange={(e) => setAgentInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleAgentSend() }}
          placeholder="Tell the agent what to do on this page..."
          className="flex-1 bg-gray-800 border border-purple-600/40 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-purple-500 min-w-0"
        />
        <button
          className="px-3 py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50 flex-shrink-0"
          onClick={handleAgentSend}
          disabled={sending || !agentInput.trim()}
        >
          {sending ? "Sending..." : "Send"}
        </button>
      </div>
    </div>
  )
}


// Recursive element tree renderer
function renderElements(
  elements: { ref: string; role: string; name: string; children?: unknown[]; [key: string]: unknown }[],
  performAction: (type: string, ref: string | number, value?: string) => Promise<void>,
  fillRef: string | null,
  setFillRef: (ref: string | null) => void,
  fillValue: string,
  setFillValue: (v: string) => void,
  handleFillSubmit: (ref: string) => void,
  depth: number = 0,
): React.ReactNode[] {
  return elements.map((el, i) => {
    const isClickable = ["link", "button", "menuitem", "tab", "checkbox", "radio", "option"].includes(el.role)
    const isTextInput = ["textbox", "searchbox", "input", "textarea", "combobox"].includes(el.role)
    const children = el.children as typeof elements | undefined

    return (
      <div key={`${el.ref}-${i}`} style={{ paddingLeft: depth * 12 }}>
        <div className="flex items-center gap-1 py-0.5 text-[10px] hover:bg-gray-700/50 rounded px-1 group">
          <span className="text-blue-400 font-mono flex-shrink-0">[{el.ref}]</span>
          <span className="text-gray-500 flex-shrink-0">{el.role}</span>
          <span className="text-gray-300 truncate flex-1 min-w-0">{el.name || ""}</span>
          <span className="hidden group-hover:flex gap-1 flex-shrink-0">
            {isClickable && (
              <button
                className="px-1.5 py-0.5 rounded bg-blue-600 hover:bg-blue-700 text-white text-[9px]"
                onClick={() => performAction("click", el.ref)}
              >
                Click
              </button>
            )}
            {isTextInput && (
              <button
                className="px-1.5 py-0.5 rounded bg-green-600 hover:bg-green-700 text-white text-[9px]"
                onClick={() => { setFillRef(el.ref); setFillValue("") }}
              >
                Fill
              </button>
            )}
          </span>
        </div>
        {fillRef === el.ref && (
          <div className="flex gap-1 ml-4 my-0.5">
            <input
              type="text"
              value={fillValue}
              onChange={(e) => setFillValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleFillSubmit(el.ref) }}
              placeholder="Enter text..."
              className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-0.5 text-[10px] text-gray-200 outline-none"
              autoFocus
            />
            <button
              className="px-2 py-0.5 text-[9px] rounded bg-green-600 hover:bg-green-700 text-white"
              onClick={() => handleFillSubmit(el.ref)}
            >
              Go
            </button>
            <button
              className="px-2 py-0.5 text-[9px] rounded bg-gray-600 hover:bg-gray-500 text-white"
              onClick={() => setFillRef(null)}
            >
              X
            </button>
          </div>
        )}
        {children && children.length > 0 &&
          renderElements(children, performAction, fillRef, setFillRef, fillValue, setFillValue, handleFillSubmit, depth + 1)
        }
      </div>
    )
  })
}
