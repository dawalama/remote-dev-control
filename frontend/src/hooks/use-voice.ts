import { useState, useRef, useCallback } from "react"
import { POST } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { useProjectStore } from "@/stores/project-store"

interface VoiceState {
  listening: boolean
  transcript: string
  interim: string
  error: string | null
}

interface UseVoiceOptions {
  /** Called with final transcript text */
  onFinal?: (text: string) => void
  /** Called with interim transcript text */
  onInterim?: (text: string) => void
  /** Channel context to attach to the shared voice runtime */
  channel?: "desktop" | "mobile"
}

export function useVoice(options: UseVoiceOptions = {}) {
  // Use refs for callbacks to avoid stale closures in the WebSocket handler
  const onFinalRef = useRef(options.onFinal)
  const onInterimRef = useRef(options.onInterim)
  onFinalRef.current = options.onFinal
  onInterimRef.current = options.onInterim
  const [state, setState] = useState<VoiceState>({
    listening: false,
    transcript: "",
    interim: "",
    error: null,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const voiceSessionIdRef = useRef<string | null>(null)
  const listeningTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Track whether the user intentionally wants to be listening (for auto-reconnect)
  const wantListeningRef = useRef(false)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stop = useCallback(() => {
    wantListeningRef.current = false
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (listeningTimerRef.current) {
      clearTimeout(listeningTimerRef.current)
      listeningTimerRef.current = null
    }

    // Close WS
    if (wsRef.current) {
      try {
        wsRef.current.send(JSON.stringify({ type: "CloseStream" }))
      } catch { /* ignore */ }
      wsRef.current.close()
      wsRef.current = null
    }

    // Stop audio
    if (processorRef.current) {
      processorRef.current.disconnect()
      processorRef.current = null
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close()
      audioCtxRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }

    if (voiceSessionIdRef.current) {
      const sessionId = voiceSessionIdRef.current
      voiceSessionIdRef.current = null
      void POST(`/voice/sessions/${sessionId}/end`).catch(() => undefined)
    }

    setState((s) => ({ ...s, listening: false, interim: "" }))
  }, [])

  // Internal connect — sets up WS + audio. Separated from `start` so reconnect can reuse it.
  const connect = useCallback(async () => {
    // Request mic (reuse existing stream if still active)
    let stream = streamRef.current
    if (!stream || stream.getTracks().every((t) => t.readyState === "ended")) {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream
    }

    // Connect WS to backend STT relay
    const token = localStorage.getItem("rdc_token")
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
    const url = `${proto}//${window.location.host}/stt/stream${token ? `?token=${token}` : ""}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.error) {
          setState((s) => ({
            ...s,
            error: typeof data.error === "string" ? data.error : "Voice service unavailable",
          }))
          stop()
          return
        }
        if (data.transcript) {
          if (data.is_final) {
            // New transcript supersedes any pending "listening" transition
            if (listeningTimerRef.current) {
              clearTimeout(listeningTimerRef.current)
              listeningTimerRef.current = null
            }
            if (voiceSessionIdRef.current) {
              void POST(`/voice/sessions/${voiceSessionIdRef.current}/event`, {
                state: "processing",
                transcript: data.transcript,
                increment_turn: true,
              }).catch(() => undefined)
            }
            setState((s) => ({
              ...s,
              transcript: data.transcript,
              interim: "",
            }))
            onFinalRef.current?.(data.transcript)
            const sessionId = voiceSessionIdRef.current
            if (sessionId) {
              listeningTimerRef.current = setTimeout(() => {
                listeningTimerRef.current = null
                if (voiceSessionIdRef.current === sessionId) {
                  void POST(`/voice/sessions/${sessionId}/event`, {
                    state: "listening",
                  }).catch(() => undefined)
                }
              }, 1200)
            }
          } else {
            setState((s) => ({ ...s, interim: data.transcript }))
            onInterimRef.current?.(data.transcript)
          }
        }
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      // Clean up audio for this session
      if (processorRef.current) {
        processorRef.current.disconnect()
        processorRef.current = null
      }
      if (audioCtxRef.current) {
        audioCtxRef.current.close()
        audioCtxRef.current = null
      }
      wsRef.current = null

      // Auto-reconnect if user still wants to be listening
      if (wantListeningRef.current) {
        setState((s) => ({ ...s, interim: "Reconnecting…" }))
        reconnectTimerRef.current = setTimeout(() => {
          if (wantListeningRef.current) {
            connect().catch(() => {
              stop()
            })
          }
        }, 500)
      } else {
        setState((s) => ({ ...s, listening: false, interim: "" }))
      }
    }

    ws.onerror = () => {
      setState((s) => ({ ...s, error: "Voice connection failed" }))
      stop()
    }

    // Wait for WS open
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve()
      const prevError = ws.onerror
      ws.onerror = (e) => {
        reject(e)
        if (prevError) (prevError as (e: Event) => void)(e as Event)
      }
    })

    // Set up audio processing
    const audioCtx = new AudioContext()
    audioCtxRef.current = audioCtx
    const source = audioCtx.createMediaStreamSource(stream)
    const processor = audioCtx.createScriptProcessor(4096, 1, 1)
    processorRef.current = processor

    const targetRate = 16000
    const sourceRate = audioCtx.sampleRate

    processor.onaudioprocess = (e) => {
      if (ws.readyState !== WebSocket.OPEN) return

      const f32 = e.inputBuffer.getChannelData(0)
      const ratio = sourceRate / targetRate
      const outLen = Math.floor(f32.length / ratio)
      const i16 = new Int16Array(outLen)

      for (let i = 0; i < outLen; i++) {
        const srcIdx = Math.floor(i * ratio)
        const sample = Math.max(-1, Math.min(1, f32[srcIdx] || 0))
        i16[i] = sample < 0 ? sample * 32768 : sample * 32767
      }

      ws.send(i16.buffer)
    }

    source.connect(processor)
    processor.connect(audioCtx.destination)

    try {
      const project = useProjectStore.getState().currentProject
      const session = await POST<{ id: string }>("/voice/sessions", {
        transport: "browser",
        channel: options.channel || "desktop",
        client_id: getClientId(),
        project: project ?? undefined,
        state: "listening",
      })
      voiceSessionIdRef.current = session.id
    } catch {
      voiceSessionIdRef.current = null
    }

    setState({
      listening: true,
      transcript: "",
      interim: "",
      error: null,
    })
  }, [stop, options.channel])

  const start = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setState((s) => ({
          ...s,
          error: "Microphone is not supported in this browser",
          listening: false,
        }))
        return
      }

      const isLocalHost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
      if (!window.isSecureContext && !isLocalHost) {
        setState((s) => ({
          ...s,
          error: "Voice input requires HTTPS",
          listening: false,
        }))
        return
      }

      wantListeningRef.current = true
      await connect()
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : "Microphone access denied",
        listening: false,
      }))
      stop()
    }
  }, [stop, connect])

  const toggle = useCallback(() => {
    if (state.listening) {
      stop()
    } else {
      start()
    }
  }, [state.listening, start, stop])

  return {
    ...state,
    start,
    stop,
    toggle,
  }
}
