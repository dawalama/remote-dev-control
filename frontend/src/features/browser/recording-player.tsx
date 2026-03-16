import { useState, useEffect, useRef, useCallback } from "react"
import { GET } from "@/lib/api"
import "rrweb/dist/rrweb.min.css"

interface RecordingMeta {
  id: string
  session_id: string
  status: string
  started_at: string
  stopped_at?: string | null
  event_count: number
  chunk_count: number
}

const SKIP_MS = 5000

export function RecordingPlayer({
  recordingId,
  onBack,
}: {
  recordingId: string
  onBack: () => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const [meta, setMeta] = useState<RecordingMeta | null>(null)
  const [events, setEvents] = useState<unknown[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  // Replayer state
  const replayerRef = useRef<any>(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [totalTime, setTotalTime] = useState(0)
  const [speed, setSpeed] = useState(1)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const playingRef = useRef(false)
  const currentTimeRef = useRef(0)
  const totalTimeRef = useRef(0)

  // Keep refs in sync for keyboard handler
  useEffect(() => { playingRef.current = playing }, [playing])
  useEffect(() => { currentTimeRef.current = currentTime }, [currentTime])
  useEffect(() => { totalTimeRef.current = totalTime }, [totalTime])

  // Load metadata + all chunks
  const loadRecording = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const recMeta = await GET<RecordingMeta>(`/recordings/${recordingId}`)
      setMeta(recMeta)

      const allEvents: unknown[] = []
      for (let i = 0; i < recMeta.chunk_count; i++) {
        const chunk = await GET<{ events: unknown[] }>(`/recordings/${recordingId}/events?chunk=${i}`)
        allEvents.push(...chunk.events)
      }
      setEvents(allEvents)
    } catch (e) {
      setError("Failed to load recording")
    }
    setLoading(false)
  }, [recordingId])

  useEffect(() => {
    loadRecording()
  }, [loadRecording])

  // Scale replay iframe to fit container, centered
  const scaleReplay = useCallback(() => {
    if (!containerRef.current) return
    const wrapper = containerRef.current.querySelector(".replayer-wrapper") as HTMLElement
    const iframe = containerRef.current.querySelector("iframe")
    if (!wrapper || !iframe) return

    const containerRect = containerRef.current.getBoundingClientRect()
    const replayWidth = iframe.width ? parseInt(iframe.width) : 1280
    const replayHeight = iframe.height ? parseInt(iframe.height) : 900

    const scaleX = containerRect.width / replayWidth
    const scaleY = containerRect.height / replayHeight
    const scale = Math.min(scaleX, scaleY, 1)

    const scaledW = replayWidth * scale
    const scaledH = replayHeight * scale
    const offsetX = Math.max(0, (containerRect.width - scaledW) / 2)
    const offsetY = Math.max(0, (containerRect.height - scaledH) / 2)

    wrapper.style.transformOrigin = "top left"
    wrapper.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`
    wrapper.style.left = "0"
    wrapper.style.top = "0"
    wrapper.style.position = "absolute"
  }, [])

  // Initialize Replayer when events are loaded
  useEffect(() => {
    if (!containerRef.current || events.length < 2) return

    let cancelled = false
    ;(async () => {
      try {
        const { Replayer } = await import("rrweb")
        if (cancelled) return

        containerRef.current!.innerHTML = ""

        const replayer = new Replayer(events as any[], {
          root: containerRef.current!,
          skipInactive: true,
          showWarning: false,
          liveMode: false,
          insertStyleRules: [
            "html::-webkit-scrollbar { display: none; }",
            "html { scrollbar-width: none; }",
          ],
        })

        replayerRef.current = replayer

        const firstTs = (events[0] as any).timestamp
        const lastTs = (events[events.length - 1] as any).timestamp
        const duration = lastTs - firstTs
        setTotalTime(duration)
        totalTimeRef.current = duration

        // Scale + center, then auto-play
        requestAnimationFrame(() => {
          scaleReplay()
          // Auto-play
          replayer.play(0)
          setPlaying(true)
        })
      } catch (e) {
        console.error("Replayer init error:", e)
        if (!cancelled) setError(`Failed to initialize player: ${e}`)
      }
    })()

    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
      replayerRef.current?.destroy?.()
      replayerRef.current = null
    }
  }, [events, scaleReplay])

  // Resize observer to re-center on container resize
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => scaleReplay())
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [scaleReplay])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      // Don't capture if user is typing in an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      const replayer = replayerRef.current
      if (!replayer) return

      if (e.key === " ") {
        e.preventDefault()
        if (playingRef.current) {
          replayer.pause()
          setPlaying(false)
        } else {
          const t = currentTimeRef.current >= totalTimeRef.current ? 0 : currentTimeRef.current
          replayer.play(t)
          setPlaying(true)
        }
      } else if (e.key === "ArrowRight") {
        e.preventDefault()
        const t = Math.min(currentTimeRef.current + SKIP_MS, totalTimeRef.current)
        setCurrentTime(t)
        currentTimeRef.current = t
        if (playingRef.current) {
          replayer.play(t)
        } else {
          replayer.pause(t)
          requestAnimationFrame(scaleReplay)
        }
      } else if (e.key === "ArrowLeft") {
        e.preventDefault()
        const t = Math.max(currentTimeRef.current - SKIP_MS, 0)
        setCurrentTime(t)
        currentTimeRef.current = t
        if (playingRef.current) {
          replayer.play(t)
        } else {
          replayer.pause(t)
          requestAnimationFrame(scaleReplay)
        }
      }
    }

    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [scaleReplay])

  // Update timer while playing
  useEffect(() => {
    if (playing) {
      timerRef.current = setInterval(() => {
        const replayer = replayerRef.current
        if (!replayer) return
        const elapsed = replayer.getCurrentTime?.() ?? 0
        setCurrentTime(elapsed)
        currentTimeRef.current = elapsed
        if (elapsed >= totalTime) {
          setPlaying(false)
        }
      }, 100)
    } else {
      if (timerRef.current) clearInterval(timerRef.current)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [playing, totalTime])

  const handlePlay = () => {
    const replayer = replayerRef.current
    if (!replayer) return
    const t = currentTime >= totalTime ? 0 : currentTime
    replayer.play(t)
    setPlaying(true)
  }

  const handlePause = () => {
    replayerRef.current?.pause()
    setPlaying(false)
  }

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const time = parseInt(e.target.value)
    setCurrentTime(time)
    currentTimeRef.current = time
    replayerRef.current?.pause(time)
    setPlaying(false)
    requestAnimationFrame(scaleReplay)
  }

  const formatTime = (ms: number) => {
    const s = Math.floor(ms / 1000)
    const m = Math.floor(s / 60)
    const sec = s % 60
    return `${m}:${sec.toString().padStart(2, "0")}`
  }

  if (loading) {
    return (
      <div className="flex flex-col h-full items-center justify-center">
        <p className="text-gray-500 text-xs">Loading recording...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col h-full items-center justify-center gap-2">
        <p className="text-red-400 text-xs">{error}</p>
        <button className="px-2 py-1 text-xs rounded bg-gray-600 text-white" onClick={onBack}>Back</button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0" ref={wrapperRef} tabIndex={-1}>
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1.5 flex-shrink-0 border-b border-gray-700">
        <button
          className="text-xs text-blue-400 hover:text-blue-300"
          onClick={onBack}
        >
          Back to Live
        </button>
        <div className="text-[10px] text-gray-500">
          {events.length} events
          {meta?.started_at && ` | ${new Date(meta.started_at).toLocaleTimeString()}`}
        </div>
      </div>

      {/* Replay viewport — dark bg with centered replay */}
      <div
        ref={containerRef}
        className="flex-1 min-h-0 relative overflow-hidden"
        style={{ background: "#18181b" }}
      />

      {/* Controls */}
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-800 border-t border-gray-700 flex-shrink-0">
        <button
          className="w-7 h-7 flex items-center justify-center rounded bg-gray-700 hover:bg-gray-600 text-xs text-gray-200"
          onClick={playing ? handlePause : handlePlay}
          title="Space to toggle"
        >
          {playing ? "⏸" : "▶"}
        </button>

        <span className="text-[10px] text-gray-400 w-10 text-right tabular-nums">
          {formatTime(currentTime)}
        </span>

        <input
          type="range"
          min={0}
          max={totalTime || 1}
          value={currentTime}
          onChange={handleSeek}
          className="flex-1 h-1 accent-blue-500"
        />

        <span className="text-[10px] text-gray-400 w-10 tabular-nums">
          {formatTime(totalTime)}
        </span>

        <button
          className={`text-[10px] px-1.5 py-0.5 rounded text-gray-300 hover:bg-gray-600 ${speed === 1 ? "bg-blue-600 text-white" : "bg-gray-700"}`}
          onClick={() => { setSpeed(1); replayerRef.current?.setConfig?.({ speed: 1 }); if (playing) replayerRef.current?.play(currentTime) }}
        >1x</button>
        <button
          className={`text-[10px] px-1.5 py-0.5 rounded text-gray-300 hover:bg-gray-600 ${speed === 2 ? "bg-blue-600 text-white" : "bg-gray-700"}`}
          onClick={() => { setSpeed(2); replayerRef.current?.setConfig?.({ speed: 2 }); if (playing) replayerRef.current?.play(currentTime) }}
        >2x</button>
        <button
          className={`text-[10px] px-1.5 py-0.5 rounded text-gray-300 hover:bg-gray-600 ${speed === 4 ? "bg-blue-600 text-white" : "bg-gray-700"}`}
          onClick={() => { setSpeed(4); replayerRef.current?.setConfig?.({ speed: 4 }); if (playing) replayerRef.current?.play(currentTime) }}
        >4x</button>
        <button
          className={`text-[10px] px-1.5 py-0.5 rounded text-gray-300 hover:bg-gray-600 ${speed === 8 ? "bg-blue-600 text-white" : "bg-gray-700"}`}
          onClick={() => { setSpeed(8); replayerRef.current?.setConfig?.({ speed: 8 }); if (playing) replayerRef.current?.play(currentTime) }}
        >8x</button>
      </div>
    </div>
  )
}
