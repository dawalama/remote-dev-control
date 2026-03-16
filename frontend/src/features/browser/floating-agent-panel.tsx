import { useState, useRef, useCallback, useEffect } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useBrowserAgent } from "@/hooks/use-browser-agent"
import { useBrowserStore } from "@/stores/browser-store"
import { usePinchTabStore } from "@/stores/pinchtab-store"
import { ChatRenderer } from "@/features/chat/chat-renderer"
import type { ChatMessage } from "@/features/chat/chat-renderer"
import type { Spec } from "@json-render/core"

const STORAGE_KEY = "rdc_agent_panel_pos"
const DEFAULT_POS = { x: -1, y: -1 } // -1 = auto (bottom-right)

function loadPos(): { x: number; y: number } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return DEFAULT_POS
}

function savePos(pos: { x: number; y: number }) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pos))
  } catch { /* ignore */ }
}

/**
 * Floating, draggable agent panel for browser automation.
 * Works in all three layouts. Shows conversation history with the browser agent.
 */
export function FloatingAgentPanel({ channel }: { channel: "desktop" | "mobile" | "kiosk" }) {
  const open = useUIStore((s) => s.agentPanelOpen)
  const setOpen = useUIStore((s) => s.setAgentPanelOpen)

  const activeSession = useBrowserStore((s) => s.activeSession)
  const ptAvailable = usePinchTabStore((s) => s.available)

  const {
    agentInput,
    setAgentInput,
    sendingToAgent,
    sendToAgent,
    conversationHistory,
    clearHistory,
  } = useBrowserAgent(channel)

  const [minimized, setMinimized] = useState(false)
  const [pos, setPos] = useState(loadPos)
  const dragging = useRef(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Focus input when panel opens
  useEffect(() => {
    if (open && !minimized) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [open, minimized])

  // Drag handlers
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    dragging.current = true
    const rect = panelRef.current?.getBoundingClientRect()
    if (rect) {
      dragOffset.current = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }, [])

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current) return
    const x = e.clientX - dragOffset.current.x
    const y = e.clientY - dragOffset.current.y
    setPos({ x, y })
  }, [])

  const onPointerUp = useCallback(() => {
    if (dragging.current) {
      dragging.current = false
      // Persist position
      if (panelRef.current) {
        const rect = panelRef.current.getBoundingClientRect()
        savePos({ x: rect.left, y: rect.top })
      }
    }
  }, [])

  const handleSend = async () => {
    const text = agentInput.trim()
    if (!text || sendingToAgent) return
    setAgentInput("")
    await sendToAgent(text)
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  if (!open) return null

  const hasSession = (activeSession && activeSession.status === "running") || ptAvailable
  const statusText = activeSession?.target_url
    ? new URL(activeSession.target_url).hostname
    : ptAvailable
      ? "PinchTab"
      : "No browser"

  // Convert conversation history to ChatMessage format
  const messages: ChatMessage[] = conversationHistory.map((h) => ({
    role: h.role,
    content: h.content,
    spec: h.spec as Spec | undefined,
    actions: h.actions,
  }))

  const isMobile = channel === "mobile"

  // Auto-position: bottom-right if no saved position (desktop/kiosk only)
  const posStyle: React.CSSProperties | undefined = isMobile
    ? undefined
    : pos.x < 0
      ? { bottom: 80, right: 16 }
      : { left: pos.x, top: pos.y }

  if (minimized) {
    return (
      <div
        className="fixed z-[95] cursor-pointer"
        style={isMobile ? { bottom: 80, right: 12 } : (pos.x < 0 ? { bottom: 80, right: 16 } : { left: pos.x, top: pos.y })}
        onClick={() => setMinimized(false)}
      >
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-purple-600 text-white text-xs shadow-lg">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${hasSession ? "bg-green-400" : "bg-gray-400"}`} />
          Agent
          {messages.length > 0 && (
            <span className="bg-purple-800 rounded-full px-1.5 text-[10px]">{messages.length}</span>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={panelRef}
      className={`fixed z-[95] bg-gray-800 shadow-xl flex flex-col border border-gray-700 overflow-hidden ${
        isMobile
          ? "inset-x-0 bottom-0 max-h-[40vh] rounded-t-xl"
          : "w-[380px] h-[440px] rounded-lg"
      }`}
      style={posStyle}
    >
      {/* Title bar — draggable on desktop/kiosk only */}
      <div
        className={`flex items-center justify-between px-3 py-2 border-b border-gray-700 select-none flex-shrink-0 ${
          isMobile ? "" : "cursor-grab active:cursor-grabbing"
        }`}
        onPointerDown={isMobile ? undefined : onPointerDown}
        onPointerMove={isMobile ? undefined : onPointerMove}
        onPointerUp={isMobile ? undefined : onPointerUp}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${hasSession ? "bg-green-400" : "bg-gray-500"}`} />
          <span className="text-sm font-medium text-gray-200">Browser Agent</span>
          <span className="text-[10px] text-gray-500 truncate">{statusText}</span>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {messages.length > 0 && (
            <button
              className="text-[10px] text-gray-500 hover:text-gray-300 px-1"
              onClick={clearHistory}
              title="Clear conversation"
            >
              Clear
            </button>
          )}
          <button
            className="text-gray-400 hover:text-gray-200 text-xs px-1"
            onClick={() => setMinimized(true)}
            title="Minimize"
          >
            _
          </button>
          <button
            className="text-gray-400 hover:text-gray-200 text-lg leading-none px-1"
            onClick={() => setOpen(false)}
            title="Close"
          >
            &times;
          </button>
        </div>
      </div>

      {/* Messages */}
      <ChatRenderer
        messages={messages}
        loading={sendingToAgent}
        emptyText={
          hasSession
            ? "Tell the agent what to do on this page."
            : "No browser session active. Start a browser to use the agent."
        }
      />

      {/* Input area */}
      <div className="p-3 border-t border-gray-700 flex gap-2 flex-shrink-0">
        <input
          ref={inputRef}
          type="text"
          value={agentInput}
          onChange={(e) => setAgentInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSend() }}
          placeholder="Tell the agent what to do..."
          className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-purple-500 min-w-0"
          disabled={sendingToAgent || !hasSession}
        />
        <button
          className="px-3 py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50 flex-shrink-0"
          onClick={handleSend}
          disabled={sendingToAgent || !agentInput.trim() || !hasSession}
        >
          {sendingToAgent ? "..." : "Send"}
        </button>
      </div>
    </div>
  )
}
