import { useRef, useEffect, useCallback } from "react"
import { useLogsStore } from "@/stores/logs-store"

export function LogsPanel() {
  const {
    panes,
    activePaneId,
    isOpen,
    height,
    maximized,
    setActivePane,
    closePane,
    togglePause,
    refreshPane,
    toggle,
    setHeight,
    toggleMaximize,
  } = useLogsStore()

  const contentRef = useRef<HTMLPreElement>(null)
  const resizeRef = useRef<{ startY: number; startH: number } | null>(null)

  const activePane = panes.find((p) => p.id === activePaneId)

  // Auto-scroll
  useEffect(() => {
    if (contentRef.current && activePane && !activePane.paused) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [activePane?.content, activePane?.paused])

  // Resize handler
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      resizeRef.current = { startY: e.clientY, startH: height }
      const onMouseMove = (ev: MouseEvent) => {
        if (!resizeRef.current) return
        const delta = resizeRef.current.startY - ev.clientY
        setHeight(resizeRef.current.startH + delta)
      }
      const onMouseUp = () => {
        resizeRef.current = null
        document.removeEventListener("mousemove", onMouseMove)
        document.removeEventListener("mouseup", onMouseUp)
      }
      document.addEventListener("mousemove", onMouseMove)
      document.addEventListener("mouseup", onMouseUp)
    },
    [height, setHeight]
  )

  if (panes.length === 0 || !isOpen) return null

  const panelHeight = maximized ? "calc(100vh - 56px)" : `${height}px`

  return (
    <div
      className="fixed bottom-[48px] left-0 right-0 bg-gray-800 border-t border-gray-700 z-30 flex flex-col"
      style={{ height: panelHeight }}
    >
      {/* Resize handle */}
      {!maximized && (
        <div
          className="h-1.5 cursor-ns-resize hover:bg-blue-500/30 flex-shrink-0"
          onMouseDown={onMouseDown}
        />
      )}

      {/* Tab bar */}
      <div className="flex items-center border-b border-gray-700 px-2 flex-shrink-0">
        <div className="flex-1 flex items-center gap-1 overflow-x-auto">
          {panes.map((pane) => (
            <button
              key={pane.id}
              className={`flex items-center gap-1 px-2 py-1 text-xs whitespace-nowrap rounded-t ${
                pane.id === activePaneId
                  ? "bg-gray-900 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => setActivePane(pane.id)}
            >
              <span>{pane.title}</span>
              <span
                className="ml-1 text-gray-500 hover:text-red-400"
                onClick={(e) => {
                  e.stopPropagation()
                  closePane(pane.id)
                }}
              >
                ×
              </span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 ml-2 flex-shrink-0">
          {activePane && (
            <>
              <button
                className={`px-1.5 py-0.5 text-xs rounded ${
                  activePane.paused
                    ? "bg-yellow-600 text-white"
                    : "bg-gray-600 text-gray-300"
                }`}
                onClick={() => togglePause(activePane.id)}
                title={activePane.paused ? "Resume" : "Pause"}
              >
                {activePane.paused ? "▶" : "⏸"}
              </button>
              <button
                className="px-1.5 py-0.5 text-xs rounded bg-gray-600 text-gray-300 hover:bg-gray-500"
                onClick={() => refreshPane(activePane.id)}
                title="Refresh"
              >
                ↻
              </button>
            </>
          )}
          <button
            className="px-1.5 py-0.5 text-xs rounded bg-gray-600 text-gray-300 hover:bg-gray-500"
            onClick={toggleMaximize}
            title={maximized ? "Restore" : "Maximize"}
          >
            {maximized ? "⊖" : "⊕"}
          </button>
          <button
            className="px-1.5 py-0.5 text-xs rounded bg-gray-600 text-gray-300 hover:bg-gray-500"
            onClick={toggle}
            title="Collapse"
          >
            ▼
          </button>
        </div>
      </div>

      {/* Content */}
      <pre
        ref={contentRef}
        className="flex-1 overflow-auto p-3 font-mono text-xs text-gray-300 whitespace-pre-wrap min-h-0"
      >
        {activePane?.content || ""}
      </pre>
    </div>
  )
}

// Small pill for command bar showing open log count
export function LogsPill() {
  const { panes, isOpen, toggle } = useLogsStore()
  if (panes.length === 0) return null

  return (
    <button
      className={`px-2 py-1 text-xs rounded-full ${
        isOpen ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300"
      }`}
      onClick={toggle}
    >
      Logs ({panes.length})
    </button>
  )
}
