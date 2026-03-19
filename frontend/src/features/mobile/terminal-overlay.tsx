import { useState, useRef, useCallback, useEffect } from "react"
import { TerminalView } from "@/features/terminal/terminal-view"
import { useTerminalStore } from "@/stores/terminal-store"
import { useStateStore } from "@/stores/state-store"
import { useUIStore } from "@/stores/ui-store"
import { GET } from "@/lib/api"
import { Sheet } from "./sheet"

const VIRTUAL_KEYS = [
  { label: "↑", data: "\x1b[A" },
  { label: "↓", data: "\x1b[B" },
  { label: "←", data: "\x1b[D" },
  { label: "→", data: "\x1b[C" },
  { label: "Enter", data: "\r" },
  { label: "Tab", data: "\t" },
  { label: "Esc", data: "\x1b" },
  { label: "C-c", data: "\x03" },
  { label: "y", data: "y" },
  { label: "n", data: "n" },
]

interface ContextSnapshot {
  id: string
  title?: string
  url?: string
  timestamp: string
  screenshot_path?: string
  description?: string
}

export function TerminalOverlay({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) {
  const killTerminal = useTerminalStore((s) => s.killTerminal)
  const restartTerminal = useTerminalStore((s) => s.restartTerminal)
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)
  const terminals = useStateStore((s) => s.terminals)
  const isWaiting = terminals.find((t) => t.id === sessionId)?.waiting_for_input
  const openTextInput = useUIStore((s) => s.openTextInput)
  const sendRef = useRef<((data: string) => void) | null>(null)
  const redrawRef = useRef<(() => void) | null>(null)
  const [contextPickerOpen, setContextPickerOpen] = useState(false)

  const handleSendReady = useCallback((send: (data: string) => void) => {
    sendRef.current = send
  }, [])

  const handleRedrawReady = useCallback((redraw: () => void) => {
    redrawRef.current = redraw
  }, [])

  const sendToTerminal = useCallback((data: string) => {
    sendRef.current?.(data)
  }, [])

  const activateTextInput = useCallback(() => {
    const name = sessionId ? `Terminal (${sessionId.slice(0, 8)})` : "Terminal"
    openTextInput((text) => sendRef.current?.(text + "\r"), name, "", true)
  }, [openTextInput, sessionId])

  const handleContextPick = (ctx: ContextSnapshot) => {
    let instruction: string
    if (ctx.url) {
      // Browser capture — has a11y tree and metadata
      instruction = `Use the get_browser_context tool with context_id="${ctx.id}" to see the current browser state`
    } else {
      // Uploaded file — just point to the file path
      instruction = `Read the file at ${ctx.screenshot_path || `~/.rdc/contexts/${ctx.id}`}`
    }
    sendToTerminal(instruction + "\r")
    toast("Context injected", "success")
    setContextPickerOpen(false)
  }

  return (
    <div
      className="fixed inset-0 z-[100] bg-gray-900 flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className={`flex items-center justify-between bg-gray-800 border-b border-gray-700 flex-shrink-0 ${layout === "kiosk" ? "px-4 py-3" : "px-3 py-2"}`}>
        <button className={`text-blue-400 btn-touch ${layout === "kiosk" ? "text-base px-3 py-2" : "text-sm"}`} onClick={onClose}>
          ← Back
        </button>
        <span className={`text-gray-300 font-medium truncate mx-2 ${layout === "kiosk" ? "text-base" : "text-sm"}`}>
          Terminal
        </span>
        <div className={`flex ${layout === "kiosk" ? "gap-2" : "gap-1"}`}>
          <button
            className={`rounded btn-touch bg-gray-600 text-gray-300 ${layout === "kiosk" ? "px-4 py-2 text-sm" : "px-2 py-0.5 text-[10px]"}`}
            onClick={activateTextInput}
            title="Text input mode"
          >
            Txt
          </button>
          <button
            className={`rounded bg-gray-600 text-gray-300 btn-touch ${layout === "kiosk" ? "px-4 py-2 text-sm" : "px-2 py-0.5 text-[10px]"}`}
            onClick={() => setContextPickerOpen(true)}
            title="Insert context"
          >
            Ctx
          </button>
          <button
            className={`rounded bg-gray-600 text-gray-300 btn-touch ${layout === "kiosk" ? "px-4 py-2 text-sm" : "px-2 py-0.5 text-[10px]"}`}
            onClick={() => {
              redrawRef.current?.()
              toast("Terminal redrawn", "success")
            }}
            title="Redraw terminal display (fix garbled output)"
          >
            Redraw
          </button>
          <button
            className={`rounded bg-yellow-600 text-white btn-touch ${layout === "kiosk" ? "px-4 py-2 text-sm" : "px-2 py-0.5 text-[10px]"}`}
            onClick={async () => {
              await restartTerminal(sessionId)
              toast("Restarted", "success")
            }}
          >
            Restart
          </button>
          <button
            className={`rounded bg-red-600 text-white btn-touch ${layout === "kiosk" ? "px-4 py-2 text-sm" : "px-2 py-0.5 text-[10px]"}`}
            onClick={async () => {
              await killTerminal(sessionId)
              toast("Killed", "info")
              onClose()
            }}
          >
            Kill
          </button>
        </div>
      </div>

      {/* Waiting for input banner */}
      {isWaiting && (
        <div className="px-3 py-1.5 bg-yellow-900/60 border-b border-yellow-700/50 flex items-center gap-2 flex-shrink-0">
          <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
          <span className="text-xs text-yellow-300">Waiting for input</span>
        </div>
      )}

      {/* Terminal — tap to activate text input */}
      <div
        className="flex-1 min-h-0"
        onClick={activateTextInput}
      >
        <TerminalView sessionId={sessionId} project="" fontSize={layout === "kiosk" ? 18 : 11} onSendReady={handleSendReady} onRedrawReady={handleRedrawReady} />
      </div>

      {/* Virtual key bar */}
      <div className={`flex items-center gap-1 px-2 py-1.5 bg-gray-800 border-t border-gray-700 overflow-x-auto flex-shrink-0 ${layout === "kiosk" ? "gap-2 py-2 px-3" : ""}`}>
        {VIRTUAL_KEYS.map((k) => (
          <button
            key={k.label}
            className={`rounded bg-gray-700 text-gray-300 active:bg-gray-600 whitespace-nowrap select-none ${layout === "kiosk" ? "px-4 py-3 text-sm font-medium" : "px-2.5 py-1.5 text-xs"}`}
            onPointerDown={(e) => {
              e.preventDefault()
              sendToTerminal(k.data)
            }}
          >
            {k.label}
          </button>
        ))}
        <button
          className={`rounded bg-gray-700 text-gray-300 active:bg-gray-600 select-none ${layout === "kiosk" ? "px-4 py-3 text-sm font-medium" : "px-2.5 py-1.5 text-xs"}`}
          onPointerDown={async (e) => {
            e.preventDefault()
            try {
              const text = await navigator.clipboard.readText()
              if (text) { sendToTerminal(text); return }
            } catch { /* clipboard denied — fall through to text input */ }
            activateTextInput()
          }}
        >
          Paste
        </button>
      </div>

      {/* Context picker sheet */}
      {contextPickerOpen && (
        <ContextPickerSheet
          onClose={() => setContextPickerOpen(false)}
          onPick={handleContextPick}
        />
      )}
    </div>
  )
}

// ─── Context Picker Sheet ─────────────────────────────────────────────

function ContextPickerSheet({
  onClose,
  onPick,
}: {
  onClose: () => void
  onPick: (ctx: ContextSnapshot) => void
}) {
  const [contexts, setContexts] = useState<ContextSnapshot[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    GET<ContextSnapshot[]>("/context?limit=20")
      .then(setContexts)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <Sheet title="Insert Context" onClose={onClose}>
      {loading ? (
        <p className="text-xs text-gray-500 animate-pulse py-4 text-center">Loading...</p>
      ) : contexts.length === 0 ? (
        <p className="text-xs text-gray-500 py-4 text-center">No contexts available</p>
      ) : (
        <div className="space-y-2 max-h-[50vh] overflow-auto">
          {contexts.map((ctx) => (
            <button
              key={ctx.id}
              className="w-full flex items-center gap-3 p-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-left"
              onClick={() => onPick(ctx)}
            >
              <img
                src={`/context/${ctx.id}/screenshot`}
                alt=""
                className="w-16 h-10 object-cover rounded flex-shrink-0"
                onError={(e) => (e.currentTarget.style.display = "none")}
              />
              <div className="min-w-0 flex-1">
                <div className="text-xs text-gray-200 truncate">
                  {ctx.title || ctx.url || ctx.description || ctx.id}
                </div>
                <div className="text-[10px] text-gray-500">
                  {new Date(ctx.timestamp).toLocaleTimeString()}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </Sheet>
  )
}

