import { useEffect, useRef, useCallback, useState, type PointerEvent as ReactPointerEvent } from "react"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import { WebLinksAddon } from "@xterm/addon-web-links"
import { WebglAddon } from "@xterm/addon-webgl"
import { SerializeAddon } from "@xterm/addon-serialize"
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
  const layout = useUIStore((s) => s.layout)
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const serializeRef = useRef<SerializeAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<{ timer: ReturnType<typeof setTimeout> | null; attempts: number }>({
    timer: null,
    attempts: 0,
  })
  const intentionalCloseRef = useRef(false)
  const isAtBottomRef = useRef(true)
  const pendingFitRef = useRef<number | null>(null)
  const startupTimersRef = useRef<number[]>([])
  const startupRafRef = useRef<number | null>(null)
  const connectStartedRef = useRef(false)
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
    const container = containerRef.current
    const term = termRef.current
    const fit = fitRef.current
    const ws = wsRef.current
    if (!container || !term || !fit) return
    if (container.clientWidth <= 0 || container.clientHeight <= 0) return
    try { fit.fit() } catch {}
    try { term.refresh(0, Math.max(term.rows - 1, 0)) } catch {}

    if (ws?.readyState === WebSocket.OPEN && term.cols > 0 && term.rows > 0) {
      try {
        // First ensure the server knows our current dims, then ask it to
        // force a SIGWINCH via the rows-bounce in redraw_for_client.
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))
        ws.send(JSON.stringify({ type: "redraw" }))
      } catch {
        // Best-effort repaint trigger only.
      }
    }
  }, [])

  useEffect(() => {
    onRedrawReady?.(redraw)
  }, [onRedrawReady, redraw])

  const connectWs = useCallback(() => {
    if (!sessionId || connectStartedRef.current) return
    connectStartedRef.current = true

    const token = localStorage.getItem("rdc_token")
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
    const base = `${proto}//${window.location.host}`
    const url = `${base}/terminals/${encodeURIComponent(sessionId)}/ws${token ? `?token=${token}` : ""}`

    const ws = new WebSocket(url)
    ws.binaryType = "arraybuffer"

    ws.onopen = () => {
      reconnectRef.current.attempts = 0

      const term = termRef.current

      // Handshake: send current dimensions. Do NOT call term.reset() here —
      // the server prepends its own reset sequence on redraw, and reset()
      // would wipe xterm's local scrollback on every reconnect.
      if (term) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))
      }

      // After replay completes, re-fit and send resize to trigger
      // SIGWINCH so programs like Claude Code redraw their UI.
      const sendResize = () => {
        if (ws.readyState !== WebSocket.OPEN) return
        const t = termRef.current
        const f = fitRef.current
        if (t && f) {
          try { f.fit() } catch {}
          if (t.cols > 0 && t.rows > 0) {
            ws.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }))
          }
        }
      }
      setTimeout(sendResize, 300)
      setTimeout(sendResize, 1500)

      // Auto-focus the terminal so keystrokes go to it immediately
      setTimeout(() => { termRef.current?.focus() }, 100)

      // Periodically send screen snapshots to server so other clients
      // (or reconnects at different dimensions) can restore cleanly.
      // Only visible screen + small scrollback; sent every 15s to limit bandwidth.
      const snapshotInterval = setInterval(() => {
        if (ws.readyState !== WebSocket.OPEN) return
        const t = termRef.current
        const s = serializeRef.current
        if (!t || !s) return
        try {
          const data = s.serialize({ scrollback: 50 })
          if (data) {
            ws.send(JSON.stringify({
              type: "snapshot",
              cols: t.cols,
              rows: t.rows,
              data,
            }))
          }
        } catch {}
      }, 15000)

      ;(ws as WebSocket & { _snapshotInterval?: ReturnType<typeof setInterval> })._snapshotInterval = snapshotInterval

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
      connectStartedRef.current = false
      // Clear snapshot interval
      const si = (ws as WebSocket & { _snapshotInterval?: ReturnType<typeof setInterval> })._snapshotInterval
      if (si) clearInterval(si)

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
    connectStartedRef.current = false

    const term = new Terminal({
      fontSize,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      scrollback: 10000,
      cursorBlink: true,
      convertEol: true,
      theme: XTERM_THEME,
    })

    const fitAddon = new FitAddon()
    const serializeAddon = new SerializeAddon()
    term.loadAddon(fitAddon)
    term.loadAddon(serializeAddon)
    term.loadAddon(new WebLinksAddon())

    term.open(containerRef.current)

    // WebGL is useful in kiosk browsers where canvas jitter is visible,
    // but on desktop refresh/reconnect it can leave the glyph layer blank
    // until a later resize. Use the default renderer outside kiosk mode.
    if (layout === "kiosk") {
      try {
        const webgl = new WebglAddon()
        webgl.onContextLoss(() => { webgl.dispose() })
        term.loadAddon(webgl)
      } catch {
        // WebGL not available — canvas renderer remains active
      }
    }

    const connectWhenReady = () => {
      if (connectStartedRef.current) return
      if (term.cols <= 0 || term.rows <= 0) return
      term.focus()
      connectWs()
    }

    requestAnimationFrame(() => {
      try { fitAddon.fit() } catch {}
      connectWhenReady()
    })

    termRef.current = term
    fitRef.current = fitAddon
    serializeRef.current = serializeAddon

    // Track scroll position
    term.onScroll(() => checkIfAtBottom(term))

    // Keystroke forwarding — chunk large pastes to avoid PTY buffer overflow
    const CHUNK_SIZE = 256
    const CHUNK_DELAY = 5 // ms between chunks
    let chunkQueue: string[] = []
    let chunking = false

    const flushChunks = () => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) { chunkQueue = []; chunking = false; return }
      const chunk = chunkQueue.shift()
      if (chunk) {
        ws.send(chunk)
        setTimeout(flushChunks, CHUNK_DELAY)
      } else {
        chunking = false
      }
    }

    term.onData((data) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return

      if (data.length <= CHUNK_SIZE) {
        // Normal keystroke — send immediately
        ws.send(data)
      } else {
        // Large paste — chunk it
        for (let i = 0; i < data.length; i += CHUNK_SIZE) {
          chunkQueue.push(data.slice(i, i + CHUNK_SIZE))
        }
        if (!chunking) {
          chunking = true
          flushChunks()
        }
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
          connectWhenReady()
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

    const runStartupRecovery = () => {
      if (startupRafRef.current) cancelAnimationFrame(startupRafRef.current)
      startupRafRef.current = requestAnimationFrame(() => {
        startupRafRef.current = null
        connectWhenReady()
        redraw()
      })
    }

    // Refresh reloads and BFCache restores can leave xterm mounted before the
    // final viewport size is stable. A short recovery window removes the need
    // for the user to toggle fullscreen just to force the first repaint.
    const queueStartupRecovery = () => {
      startupTimersRef.current.forEach(clearTimeout)
      startupTimersRef.current = [0, 120, 400, 1200].map((delay) =>
        window.setTimeout(runStartupRecovery, delay)
      )
    }
    queueStartupRecovery()

    // VisualViewport resize — safety net for mobile browsers where the
    // visible viewport changes (Safari address bar, Android toolbar) but
    // ResizeObserver on the container doesn't fire quickly enough.
    const vv = window.visualViewport
    if (vv) vv.addEventListener("resize", debouncedFit)

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") queueStartupRecovery()
    }
    const onPageShow = () => queueStartupRecovery()
    window.addEventListener("pageshow", onPageShow)
    document.addEventListener("visibilitychange", onVisibilityChange)
    window.addEventListener("focus", runStartupRecovery)

    if ("fonts" in document) {
      void (document.fonts as FontFaceSet).ready.then(runStartupRecovery).catch(() => {})
    }

    // Post-layout verification — on slow devices (Tesla kiosk, low-end
    // tablets), the grid+flex layout may not settle before the initial fit
    // or ResizeObserver fires. We verify the fit at increasing intervals
    // and re-fit if the container dimensions don't match xterm's row/col
    // count. Stops as soon as a check finds no mismatch.
    const verifyFit = () => {
      const el = containerRef.current
      if (!el || !term.element) return false
      const core = (term as unknown as { _core: { _renderService: { dimensions: { css: { cell: { width: number; height: number } } } } } })._core
      const cellH = core?._renderService?.dimensions?.css?.cell?.height
      const cellW = core?._renderService?.dimensions?.css?.cell?.width
      if (!cellH || !cellW) return false
      const expectedRows = Math.floor(el.clientHeight / cellH)
      const expectedCols = Math.floor(el.clientWidth / cellW)
      if (expectedRows > 0 && expectedCols > 0 &&
          (Math.abs(expectedRows - term.rows) > 1 || Math.abs(expectedCols - term.cols) > 1)) {
        debouncedFit()
        return true // mismatch found, keep checking
      }
      return false // dimensions match
    }
    const verifyTimers = [500, 1000, 2000].map((delay) =>
      setTimeout(() => { verifyFit() }, delay)
    )

    // Touch scroll — xterm-screen captures touch events, preventing native scroll
    // on the underlying xterm-viewport. Translate touch swipes to scrollLines().
    const touchState = { startY: 0, lastY: 0, accum: 0 }

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

      // Read the live fontSize so touch-scroll tracks runtime layout flips
      // (desktop 13 ↔ kiosk 15) — this effect doesn't re-run on fontSize change.
      const lineHeight = (termRef.current?.options.fontSize ?? fontSize) * 1.2
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

    return () => {
      intentionalCloseRef.current = true
      if (reconnectRef.current.timer) clearTimeout(reconnectRef.current.timer)
      if (pendingFitRef.current) cancelAnimationFrame(pendingFitRef.current)
      if (startupRafRef.current) cancelAnimationFrame(startupRafRef.current)
      startupTimersRef.current.forEach(clearTimeout)
      startupTimersRef.current = []
      wsRef.current?.close()
      observer.disconnect()
      if (vv) vv.removeEventListener("resize", debouncedFit)
      window.removeEventListener("pageshow", onPageShow)
      document.removeEventListener("visibilitychange", onVisibilityChange)
      window.removeEventListener("focus", runStartupRecovery)
      verifyTimers.forEach(clearTimeout)
      if (screenEl) {
        screenEl.removeEventListener("touchstart", onTouchStart)
        screenEl.removeEventListener("touchmove", onTouchMove)
      }
      term.dispose()
      termRef.current = null
      fitRef.current = null
      wsRef.current = null
    }
    // fontSize/layout intentionally excluded — they're consumed at init only,
    // and reincluding them would dispose+recreate xterm (wiping scrollback and
    // reconnecting the PTY) on every layout/fontSize change. Runtime fontSize
    // updates are handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, connectWs, checkIfAtBottom])

  // Live fontSize updates: mutate xterm in place and refit rather than
  // recreating the terminal.
  useEffect(() => {
    const t = termRef.current
    const f = fitRef.current
    if (!t || !f) return
    if (t.options.fontSize === fontSize) return
    t.options.fontSize = fontSize
    try { f.fit() } catch {}
  }, [fontSize])

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
    if (currentProject) params.set("project", currentProject)
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
    if (currentProject) form.append("project", currentProject)
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
