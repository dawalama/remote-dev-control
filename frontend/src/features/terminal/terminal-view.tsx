import { useEffect, useRef, useCallback, useState, type PointerEvent as ReactPointerEvent } from "react"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import { WebLinksAddon } from "@xterm/addon-web-links"
import { WebglAddon } from "@xterm/addon-webgl"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useMountEffect } from "@/hooks/use-mount-effect"
import { api, GET } from "@/lib/api"
import type { TabId } from "@/types"
import "@xterm/xterm/css/xterm.css"

const XTERM_THEME = {
  background: "#1e1e2e",
  foreground: "#cdd6f4",
  cursor: "#f5e0dc",
  cursorAccent: "#1e1e2e",
  selectionBackground: "#585b7066",
  black: "#45475a",
  red: "#f38ba8",
  green: "#a6e3a1",
  yellow: "#f9e2af",
  blue: "#89b4fa",
  magenta: "#cba6f7",
  cyan: "#94e2d5",
  white: "#bac2de",
  brightBlack: "#585b70",
  brightRed: "#f38ba8",
  brightGreen: "#a6e3a1",
  brightYellow: "#f9e2af",
  brightBlue: "#89b4fa",
  brightMagenta: "#cba6f7",
  brightCyan: "#94e2d5",
  brightWhite: "#a6adc8",
}

interface TerminalViewProps {
  sessionId: string
  project: string
  onDisconnect?: () => void
  onSendReady?: (send: (data: string) => void) => void
  onRedrawReady?: (redraw: () => void) => void
  className?: string
  fontSize?: number
}

export function TerminalView({
  sessionId,
  className = "",
  fontSize = 13,
  onSendReady,
  onRedrawReady,
}: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<{ timer: ReturnType<typeof setTimeout> | null; attempts: number }>({
    timer: null,
    attempts: 0,
  })
  const intentionalCloseRef = useRef(false)
  const isAtBottomRef = useRef(true)
  const pendingFitRef = useRef<number | null>(null)
  const onSendReadyRef = useRef(onSendReady)
  onSendReadyRef.current = onSendReady
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hasNewOutput, setHasNewOutput] = useState(false)

  const checkIfAtBottom = useCallback((term: Terminal) => {
    const buf = term.buffer.active
    const atBottom = buf.viewportY >= buf.baseY
    isAtBottomRef.current = atBottom
    setIsAtBottom(atBottom)
    if (atBottom) setHasNewOutput(false)
  }, [])

  const scrollToBottom = useCallback(() => {
    termRef.current?.scrollToBottom()
    isAtBottomRef.current = true
    setIsAtBottom(true)
    setHasNewOutput(false)
  }, [])

  const scrollLines = useCallback((n: number) => {
    termRef.current?.scrollLines(n)
  }, [])

  const scrollPages = useCallback((n: number) => {
    termRef.current?.scrollPages(n)
  }, [])

  const redraw = useCallback(() => {
    const term = termRef.current
    const fit = fitRef.current
    const ws = wsRef.current
    if (!term || !fit) return
    // Force a resize cycle: shrink by 1 col, send resize to PTY,
    // then fit back to correct size. The PTY resize triggers SIGWINCH
    // which makes the running program redraw its output.
    const { cols, rows } = term
    term.resize(Math.max(1, cols - 1), rows)
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "resize", cols: cols - 1, rows }))
    }
    requestAnimationFrame(() => {
      try { fit.fit() } catch {}
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))
      }
    })
  }, [])

  useEffect(() => {
    onRedrawReady?.(redraw)
  }, [onRedrawReady, redraw])

  const connectWs = useCallback(() => {
    if (!sessionId) return

    const token = localStorage.getItem("rdc_token")
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
    const base = `${proto}//${window.location.host}`
    const url = `${base}/terminals/${encodeURIComponent(sessionId)}/ws${token ? `?token=${token}` : ""}`

    const ws = new WebSocket(url)
    ws.binaryType = "arraybuffer"

    ws.onopen = () => {
      reconnectRef.current.attempts = 0

      const term = termRef.current

      // Reset terminal state before buffer replay to avoid parser errors
      // (e.g. reconnecting mid-escape-sequence). ESC c = RIS (Reset to Initial State)
      term?.write('\x1bc')

      // Send initial resize
      if (term) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))
      }

      // After buffer replay, re-fit and re-send resize so the PTY
      // adopts the client's actual dimensions. This triggers SIGWINCH
      // so programs like Claude Code redraw their UI correctly.
      // Use setTimeout to wait for buffer replay, then debounced fit
      // to consolidate with any concurrent ResizeObserver events.
      setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) return
        const t = termRef.current
        const f = fitRef.current
        if (t && f) {
          if (pendingFitRef.current) cancelAnimationFrame(pendingFitRef.current)
          pendingFitRef.current = requestAnimationFrame(() => {
            pendingFitRef.current = null
            try { f.fit() } catch {}
            if (t.cols > 0 && t.rows > 0 && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }))
            }
          })
        }
      }, 300)

      // Expose send function to parent (use ref to avoid stale closure)
      onSendReadyRef.current?.((data: string) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(data)
      })
    }

    ws.onmessage = (event) => {
      const term = termRef.current
      if (!term) return

      if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data))
      } else {
        term.write(event.data)
      }

      // Only auto-scroll if user is already at the bottom
      if (isAtBottomRef.current) {
        term.scrollToBottom()
      } else {
        setHasNewOutput(true)
      }
    }

    ws.onclose = (event) => {
      if (intentionalCloseRef.current) return

      const term = termRef.current

      // Server rejected with a terminal error — don't retry
      if (event.code === 4004) {
        term?.write("\r\n\x1b[31m● Terminal session not found.\x1b[0m\r\n")
        return
      }
      if (event.code === 4005) {
        term?.write("\r\n\x1b[31m● Terminal process has exited.\x1b[0m\r\n")
        return
      }

      // Transient disconnect — reconnect with backoff
      term?.write("\r\n\x1b[33m● Connection lost, reconnecting...\x1b[0m\r\n")
      const attempts = reconnectRef.current.attempts
      if (attempts < 10) {
        const delay = Math.min(1000 * Math.pow(2, attempts), 8000)
        reconnectRef.current.attempts++
        reconnectRef.current.timer = setTimeout(() => {
          connectWs()
        }, delay)
      } else {
        term?.write(
          "\r\n\x1b[31m● Could not reconnect after 10 attempts.\x1b[0m\r\n"
        )
      }
    }

    ws.onerror = () => {
      // onclose will handle reconnect
    }

    wsRef.current = ws
  }, [sessionId])

  // Initialize xterm
  useEffect(() => {
    if (!containerRef.current) return

    // Reset on re-run so reconnect logic works after effect cleanup/re-init
    intentionalCloseRef.current = false

    const term = new Terminal({
      fontSize,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      scrollback: 10000,
      cursorBlink: true,
      convertEol: true,
      theme: XTERM_THEME,
    })

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.loadAddon(new WebLinksAddon())

    term.open(containerRef.current)

    // WebGL renderer: GPU-accelerated glyph rendering, eliminates canvas
    // jitter on devices like Tesla browser. Falls back to default canvas
    // renderer if WebGL is unavailable.
    try {
      const webgl = new WebglAddon()
      webgl.onContextLoss(() => { webgl.dispose() })
      term.loadAddon(webgl)
    } catch {
      // WebGL not available — canvas renderer remains active
    }

    // Defer first fit to next frame so the browser has fully computed
    // the container layout (critical on mobile where viewport can shift).
    requestAnimationFrame(() => {
      try { fitAddon.fit() } catch {}
    })

    termRef.current = term
    fitRef.current = fitAddon

    // Track scroll position
    term.onScroll(() => checkIfAtBottom(term))

    // Keystroke forwarding
    term.onData((data) => {
      const ws = wsRef.current
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(data)
      }
    })

    // ResizeObserver — debounce via rAF to avoid firing with stale/zero
    // dimensions during layout transitions (e.g. embedded → fullscreen).
    const debouncedFit = () => {
      if (pendingFitRef.current) cancelAnimationFrame(pendingFitRef.current)
      pendingFitRef.current = requestAnimationFrame(() => {
        pendingFitRef.current = null
        try {
          fitAddon.fit()
          // Guard: don't send zero dimensions to the PTY
          if (term.cols > 0 && term.rows > 0) {
            const ws = wsRef.current
            if (ws?.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))
            }
          }
        } catch {
          // fit() can throw if element is not visible
        }
      })
    }
    const observer = new ResizeObserver(debouncedFit)
    observer.observe(containerRef.current)

    // Touch scroll — xterm-screen captures touch events, preventing native scroll
    // on the underlying xterm-viewport. Translate touch swipes to scrollLines().
    const touchState = { startY: 0, lastY: 0, accum: 0 }
    const lineHeight = fontSize * 1.2 // approximate

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      touchState.startY = e.touches[0].clientY
      touchState.lastY = e.touches[0].clientY
      touchState.accum = 0
    }

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const y = e.touches[0].clientY
      const delta = touchState.lastY - y // positive = scroll down
      touchState.lastY = y
      touchState.accum += delta

      // Scroll in line increments
      const lines = Math.trunc(touchState.accum / lineHeight)
      if (lines !== 0) {
        term.scrollLines(lines)
        touchState.accum -= lines * lineHeight
      }

      // Prevent page bounce when we have scrollback content
      const buf = term.buffer.active
      const atTop = buf.viewportY <= 0
      const atBottom = buf.viewportY >= buf.baseY
      if (!(atTop && delta < 0) && !(atBottom && delta > 0)) {
        e.preventDefault()
      }
    }

    const screenEl = containerRef.current.querySelector(".xterm-screen") as HTMLElement | null
    if (screenEl) {
      screenEl.addEventListener("touchstart", onTouchStart, { passive: true })
      screenEl.addEventListener("touchmove", onTouchMove, { passive: false })
    }

    // Connect WS
    connectWs()

    return () => {
      intentionalCloseRef.current = true
      if (reconnectRef.current.timer) clearTimeout(reconnectRef.current.timer)
      if (pendingFitRef.current) cancelAnimationFrame(pendingFitRef.current)
      wsRef.current?.close()
      observer.disconnect()
      if (screenEl) {
        screenEl.removeEventListener("touchstart", onTouchStart)
        screenEl.removeEventListener("touchmove", onTouchMove)
      }
      term.dispose()
      termRef.current = null
      fitRef.current = null
      wsRef.current = null
    }
  }, [sessionId, connectWs, checkIfAtBottom, fontSize])

  return (
    <div className={`w-full h-full min-h-0 relative ${className}`}>
      <div
        ref={containerRef}
        className="w-full h-full overflow-hidden"
        style={{ backgroundColor: XTERM_THEME.background }}
      />
      {/* Scroll controls — stopPropagation prevents parent onClick (e.g. text input activation) */}
      <div className="absolute right-2 bottom-2 flex flex-col gap-1 z-10" onClick={(e) => e.stopPropagation()}>
        <ScrollButton direction={-1} scrollLines={scrollLines} scrollPages={scrollPages} />
        <ScrollButton direction={1} scrollLines={scrollLines} scrollPages={scrollPages} />
        {!isAtBottom && (
          <button
            className={`w-10 h-10 rounded-lg text-sm flex items-center justify-center backdrop-blur-sm touch-manipulation ${
              hasNewOutput
                ? "bg-blue-600/90 hover:bg-blue-500 text-white animate-pulse"
                : "bg-gray-700/80 hover:bg-gray-600 text-gray-300"
            }`}
            onClick={scrollToBottom}
            title="Scroll to bottom"
          >
            ⬇
          </button>
        )}
      </div>
    </div>
  )
}

// Hold-to-scroll button: taps scroll 3 lines, hold scrolls continuously (accelerates), double-tap scrolls a full page
function ScrollButton({
  direction,
  scrollLines: doScrollLines,
  scrollPages: doScrollPages,
}: {
  direction: 1 | -1
  scrollLines: (n: number) => void
  scrollPages: (n: number) => void
}) {
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tickRef = useRef(0)
  const lastTapRef = useRef(0)

  const stopScroll = useCallback(() => {
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null }
    tickRef.current = 0
  }, [])

  const handlePointerDown = useCallback((e: ReactPointerEvent) => {
    e.preventDefault()

    // Double-tap → full page
    const now = Date.now()
    if (now - lastTapRef.current < 300) {
      doScrollPages(direction)
      lastTapRef.current = 0
      return
    }
    lastTapRef.current = now

    // Immediate scroll on press
    doScrollLines(direction * 3)

    // After 300ms hold, start continuous scroll
    timeoutRef.current = setTimeout(() => {
      tickRef.current = 0
      intervalRef.current = setInterval(() => {
        tickRef.current++
        // Accelerate: 3 lines for first 10 ticks, then 6, then 12
        const lines = tickRef.current < 10 ? 3 : tickRef.current < 25 ? 6 : 12
        doScrollLines(direction * lines)
      }, 50)
    }, 300)
  }, [direction, doScrollLines, doScrollPages])

  useMountEffect(() => stopScroll)

  return (
    <button
      className="w-10 h-10 rounded-lg bg-gray-700/80 hover:bg-gray-600 active:bg-gray-500 text-gray-300 text-sm flex items-center justify-center backdrop-blur-sm select-none touch-manipulation"
      onPointerDown={handlePointerDown}
      onPointerUp={stopScroll}
      onPointerLeave={stopScroll}
      onPointerCancel={stopScroll}
      title={direction < 0 ? "Scroll up (hold for continuous, double-tap for page)" : "Scroll down (hold for continuous, double-tap for page)"}
    >
      {direction < 0 ? "▲" : "▼"}
    </button>
  )
}

// Standalone toolbar for terminal actions
type TerminalMode = "embedded" | "fullscreen" | "minimized"

export function TerminalToolbar({
  project,
  connected,
  onRestart,
  onDisconnect,
  onKill,
  onReset,
  mode = "embedded",
  onModeChange,
}: {
  project: string
  sessionId: string
  connected: boolean
  onRestart: () => void
  onDisconnect: () => void
  onKill: () => void
  onReset?: () => void
  mode?: TerminalMode
  onModeChange?: (mode: TerminalMode) => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const setTab = useUIStore((s) => s.setTab)
  const [ctxCount, setCtxCount] = useState(0)

  // Load attachment count
  useEffect(() => {
    const params = new URLSearchParams({ limit: "100" })
    if (currentProject !== "all") params.set("project", currentProject)
    GET<{ id: string }[]>(`/context?${params}`)
      .then((list) => setCtxCount(list.length))
      .catch(() => {})
  }, [currentProject])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ""
    const form = new FormData()
    form.append("file", file)
    if (currentProject && currentProject !== "all") form.append("project", currentProject)
    try {
      const res = await api<{ id: string; path: string }>("/context/upload", {
        method: "POST",
        body: form,
      })
      await navigator.clipboard.writeText(res.path).catch(() => {})
      toast(`Uploaded — path copied: ${res.path}`, "success")
      setCtxCount((c) => c + 1)
    } catch {
      toast("Upload failed", "error")
    }
  }

  const handleOpenAttachments = () => {
    setTab("attachments" as TabId)
  }

  return (
    <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800 flex-shrink-0">
      <div className="flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
        />
        <span className="text-sm font-medium text-gray-200">{project}</span>
      </div>
      <div className="flex items-center gap-1">
        {/* Attachment / Context buttons */}
        <input
          ref={fileInputRef}
          type="file"
          accept="*/*"
          className="hidden"
          onChange={handleUpload}
        />
        <button
          className="px-2 py-0.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-white"
          onClick={() => fileInputRef.current?.click()}
          title="Upload file to context"
        >
          📎
        </button>
        <button
          className="relative px-2 py-0.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-white"
          onClick={handleOpenAttachments}
          title="View attachments"
        >
          Ctx
          {ctxCount > 0 && (
            <span className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 rounded-full bg-blue-500 text-[10px] text-white flex items-center justify-center leading-none">
              {ctxCount}
            </span>
          )}
        </button>
        <span className="w-px h-4 bg-gray-600" />

        {/* Mode buttons */}
        {onModeChange && (
          <>
            {mode !== "embedded" && (
              <button
                className="px-2 py-0.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-white"
                onClick={() => onModeChange("embedded")}
                title="Embed"
              >
                Embed
              </button>
            )}
            {mode !== "fullscreen" && (
              <button
                className="px-2 py-0.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-white"
                onClick={() => onModeChange("fullscreen")}
                title="Fullscreen"
              >
                Full
              </button>
            )}
            <span className="w-px h-4 bg-gray-600" />
          </>
        )}
        {onReset && (
          <button
            className="px-2 py-0.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-white"
            onClick={onReset}
            title="Redraw terminal display (fix garbled output)"
          >
            Redraw
          </button>
        )}
        <button
          className="px-2 py-0.5 text-xs rounded bg-yellow-600 hover:bg-yellow-700 text-white"
          onClick={onRestart}
          title="Restart"
        >
          Restart
        </button>
        <button
          className="px-2 py-0.5 text-xs rounded bg-orange-600 hover:bg-orange-700 text-white"
          onClick={onDisconnect}
          title="Minimize"
        >
          Min
        </button>
        <button
          className="px-2 py-0.5 text-xs rounded bg-red-600 hover:bg-red-700 text-white"
          onClick={onKill}
          title="Kill"
        >
          Kill
        </button>
      </div>
    </div>
  )
}
