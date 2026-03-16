import { useState, useRef, useCallback, useEffect } from "react"

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
}

export function useVoice(options: UseVoiceOptions = {}) {
  // Use refs for callbacks to avoid stale closures in the WebSocket handler
  const onFinalRef = useRef(options.onFinal)
  const onInterimRef = useRef(options.onInterim)
  useEffect(() => {
    onFinalRef.current = options.onFinal
    onInterimRef.current = options.onInterim
  }, [options.onFinal, options.onInterim])
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

  const stop = useCallback(() => {
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

    setState((s) => ({ ...s, listening: false, interim: "" }))
  }, [])

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

      // Request mic
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream

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
              setState((s) => ({
                ...s,
                transcript: data.transcript,
                interim: "",
              }))
              onFinalRef.current?.(data.transcript)
            } else {
              setState((s) => ({ ...s, interim: data.transcript }))
              onInterimRef.current?.(data.transcript)
            }
          }
        } catch { /* ignore parse errors */ }
      }

      ws.onclose = () => stop()
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

      setState({
        listening: true,
        transcript: "",
        interim: "",
        error: null,
      })
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : "Microphone access denied",
        listening: false,
      }))
      stop()
    }
  }, [stop])

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
