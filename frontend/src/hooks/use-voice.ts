import { useState, useRef, useCallback } from "react"
import { POST } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { useProjectStore } from "@/stores/project-store"
import { createAuthenticatedWebSocket } from "@/lib/ws"

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
  /** Called once when capture fails. Kiosk has no inline error line. */
  onError?: (message: string) => void
  /** Channel context to attach to the shared voice runtime */
  channel?: "desktop" | "mobile"
}

/**
 * Tesla's touchscreen sends a second click a few hundred milliseconds after
 * one tap. A toggle treats that echo as "stop", so the mic flashes on and dies.
 */
export const MIC_TOGGLE_GUARD_MS = 500

export function isEchoMicToggle(lastAt: number, now: number, guardMs = MIC_TOGGLE_GUARD_MS): boolean {
  return lastAt > 0 && now - lastAt < guardMs
}

function createAudioContext(): AudioContext {
  const Ctor = window.AudioContext
    ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) throw new Error("This browser cannot capture microphone audio.")
  return new Ctor()
}

function pcmBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength)
  let binary = ""
  const chunk = 0x2000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

export function micErrorMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : ""
  const raw = err instanceof Error ? err.message : "Microphone access denied"
  if (name === "NotAllowedError" || /permission|denied|not allowed/i.test(raw)) {
    return "Microphone blocked. Allow mic access for this site and tap again."
  }
  if (name === "NotFoundError") return "No microphone is available in this browser."
  if (name === "NotReadableError") return "Microphone is busy in another app."
  if (name === "SecurityError") return "Voice needs a secure (HTTPS) page before the mic can start."
  if (name === "OverconstrainedError") return "This browser rejected the microphone settings."
  return raw || "Microphone access denied"
}

export function useVoice(options: UseVoiceOptions = {}) {
  const onFinalRef = useRef(options.onFinal)
  const onInterimRef = useRef(options.onInterim)
  const onErrorRef = useRef(options.onError)

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
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const muteRef = useRef<GainNode | null>(null)
  const oscRef = useRef<OscillatorNode | null>(null)
  const voiceSessionIdRef = useRef<string | null>(null)
  const listeningTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wantListeningRef = useRef(false)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const generationRef = useRef(0)
  const reconnectsRef = useRef(0)
  const lastToggleAtRef = useRef(0)
  const lastErrorRef = useRef("")
  const lastErrorAtRef = useRef(0)
  const channelRef = useRef(options.channel)
  const connectRef = useRef<(gen: number) => Promise<void>>(async () => {})

  const releaseAudio = useCallback(() => {
    try { oscRef.current?.stop() } catch { /* already stopped */ }
    oscRef.current?.disconnect()
    oscRef.current = null
    sourceRef.current?.disconnect()
    sourceRef.current = null
    processorRef.current?.disconnect()
    processorRef.current = null
    muteRef.current?.disconnect()
    muteRef.current = null
    if (audioCtxRef.current) {
      void audioCtxRef.current.close().catch(() => undefined)
      audioCtxRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }, [])

  const stop = useCallback(() => {
    generationRef.current += 1
    wantListeningRef.current = false
    reconnectsRef.current = 0
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (listeningTimerRef.current) {
      clearTimeout(listeningTimerRef.current)
      listeningTimerRef.current = null
    }

    const ws = wsRef.current
    wsRef.current = null
    if (ws) {
      ws.onclose = null
      ws.onerror = null
      ws.onmessage = null
      try { ws.send(JSON.stringify({ type: "CloseStream" })) } catch { /* ignore */ }
      ws.close()
    }

    releaseAudio()

    if (voiceSessionIdRef.current) {
      const sessionId = voiceSessionIdRef.current
      voiceSessionIdRef.current = null
      void POST(`/voice/sessions/${sessionId}/end`).catch(() => undefined)
    }

    setState((s) => ({ ...s, listening: false, interim: "", error: null }))
  }, [releaseAudio])

  const reportError = useCallback((message: string) => {
    const now = performance.now()
    const duplicate = lastErrorRef.current === message && now - lastErrorAtRef.current < 1000
    lastErrorRef.current = message
    lastErrorAtRef.current = now
    if (wantListeningRef.current) stop()
    setState((s) => ({ ...s, error: message, listening: false, interim: "" }))
    if (!duplicate) onErrorRef.current?.(message)
  }, [stop])

  // Called synchronously from the tap, before any await. A context created
  // after getUserMedia or the socket opens stays suspended on car browsers,
  // so the button looks armed and never hears anything. The monitor path is
  // silent: playing the mic out the speakers flips the car's audio source.
  const ensureGraph = useCallback(() => {
    const ctx = audioCtxRef.current
    if (ctx && ctx.state !== "closed" && processorRef.current && muteRef.current) {
      void ctx.resume().catch(() => undefined)
      return ctx
    }
    if (ctx && ctx.state !== "closed") {
      void ctx.close().catch(() => undefined)
    }
    const audioCtx = createAudioContext()
    audioCtxRef.current = audioCtx
    void audioCtx.resume().catch(() => undefined)
    if (typeof audioCtx.createScriptProcessor !== "function") {
      throw new Error("This browser cannot capture microphone audio.")
    }

    const mute = audioCtx.createGain()
    mute.gain.value = 0
    mute.connect(audioCtx.destination)
    muteRef.current = mute

    const processor = audioCtx.createScriptProcessor(4096, 1, 1)
    processorRef.current = processor
    const targetRate = 16000
    processor.onaudioprocess = (e) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return
      const f32 = e.inputBuffer.getChannelData(0)
      const sourceRate = audioCtx.sampleRate || targetRate
      const ratio = sourceRate / targetRate
      const outLen = Math.max(1, Math.floor(f32.length / ratio))
      const i16 = new Int16Array(outLen)
      for (let i = 0; i < outLen; i++) {
        const srcIdx = Math.min(f32.length - 1, Math.floor(i * ratio))
        const sample = Math.max(-1, Math.min(1, f32[srcIdx] || 0))
        i16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
      }
      // Text frames, not binary. Some car browsers drop a binary socket
      // payload and then close it, which turns the mic straight back off.
      try {
        ws.send(JSON.stringify({ type: "audio", audio: pcmBase64(i16) }))
      } catch { /* socket closing */ }
    }
    processor.connect(mute)

    const osc = audioCtx.createOscillator()
    const oscMute = audioCtx.createGain()
    oscMute.gain.value = 0
    osc.connect(oscMute)
    oscMute.connect(processor)
    osc.start()
    oscRef.current = osc
    return audioCtx
  }, [])

  const connectMic = useCallback((stream: MediaStream) => {
    const ctx = audioCtxRef.current
    const processor = processorRef.current
    if (!ctx || !processor) return
    sourceRef.current?.disconnect()
    const source = ctx.createMediaStreamSource(stream)
    source.connect(processor)
    sourceRef.current = source
    try { oscRef.current?.stop() } catch { /* already stopped */ }
    oscRef.current?.disconnect()
    oscRef.current = null
  }, [])

  const connect = useCallback(async (gen: number) => {
    let stream = streamRef.current
    if (!stream || stream.getAudioTracks().every((t) => t.readyState === "ended")) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: { ideal: 1 },
            echoCancellation: { ideal: true },
            noiseSuppression: { ideal: true },
          },
        })
      } catch (err) {
        if (err instanceof DOMException && err.name === "OverconstrainedError") {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        } else {
          throw err
        }
      }
      if (gen !== generationRef.current || !wantListeningRef.current) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      streamRef.current = stream
    }
    if (gen !== generationRef.current || !wantListeningRef.current) return

    const track = stream.getAudioTracks()[0]
    if (track) {
      track.onended = () => {
        if (wantListeningRef.current && gen === generationRef.current) {
          reportError("The browser closed the microphone.")
        }
      }
    }

    const ws = await createAuthenticatedWebSocket("/stt/stream")
    if (gen !== generationRef.current || !wantListeningRef.current) {
      ws.close()
      return
    }
    const previous = wsRef.current
    if (previous && previous !== ws) {
      previous.onclose = null
      previous.onmessage = null
      previous.onerror = null
      previous.close()
    }
    wsRef.current = ws

    let opened = false
    ws.onmessage = (event) => {
      if (gen !== generationRef.current || wsRef.current !== ws) return
      try {
        const data = JSON.parse(event.data)
        if (data.error) {
          reportError(typeof data.error === "string" ? data.error : "Voice service unavailable")
          return
        }
        if (!data.transcript) return
        if (data.is_final) {
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
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null
      if (!opened) return
      if (gen !== generationRef.current || !wantListeningRef.current) return
      if (reconnectsRef.current >= 2) {
        reportError("Voice connection failed")
        return
      }
      reconnectsRef.current += 1
      setState((s) => ({ ...s, interim: "Reconnecting…" }))
      onInterimRef.current?.("Reconnecting…")
      reconnectTimerRef.current = setTimeout(() => {
        if (!wantListeningRef.current || gen !== generationRef.current) return
        void connectRef.current(gen).catch((err) => {
          if (gen === generationRef.current && wantListeningRef.current) {
            reportError(micErrorMessage(err))
          }
        })
      }, 500)
    }

    // Browsers fire error alongside close. Closing here used to cancel the
    // session immediately, so a car browser's socket blip looked like a tap
    // that flipped the mic off.
    ws.onerror = () => undefined

    try {
      await new Promise<void>((resolve, reject) => {
        if (ws.readyState === WebSocket.OPEN) {
          opened = true
          resolve()
          return
        }
        const timer = setTimeout(() => {
          ws.close()
          reject(new Error("Voice connection timed out"))
        }, 8000)
        ws.addEventListener("open", () => {
          opened = true
          clearTimeout(timer)
          resolve()
        }, { once: true })
        ws.addEventListener("error", () => {
          clearTimeout(timer)
          reject(new Error("Voice connection failed"))
        }, { once: true })
        ws.addEventListener("close", () => {
          clearTimeout(timer)
          if (!opened) reject(new Error("Voice connection failed"))
        }, { once: true })
      })
    } catch (err) {
      if (gen === generationRef.current && wantListeningRef.current) throw err
      return
    }
    if (gen !== generationRef.current || !wantListeningRef.current) return

    const ctx = audioCtxRef.current
    if (!ctx || ctx.state === "closed") {
      throw new Error("This browser blocked audio capture. Tap the mic again.")
    }
    await ctx.resume()
    if (ctx.state !== "running") {
      throw new Error("This browser blocked audio capture. Tap the mic again.")
    }
    connectMic(stream)

    try {
      const project = useProjectStore.getState().currentProject
      const channel = channelRef.current || "desktop"
      const externalId = `browser-${channel}-${getClientId()}`
      const session = await POST<{ id: string }>("/voice/sessions", {
        transport: "browser",
        channel,
        client_id: getClientId(),
        project: project ?? undefined,
        external_id: externalId,
        state: "listening",
      })
      if (gen === generationRef.current) voiceSessionIdRef.current = session.id
    } catch {
      voiceSessionIdRef.current = null
    }
    if (gen !== generationRef.current || !wantListeningRef.current) return

    reconnectsRef.current = 0
    lastErrorRef.current = ""
    onInterimRef.current?.("")
    setState({
      listening: true,
      transcript: "",
      interim: "",
      error: null,
    })
  }, [connectMic, reportError])

  const start = useCallback(async () => {
    onFinalRef.current = options.onFinal
    onInterimRef.current = options.onInterim
    onErrorRef.current = options.onError
    channelRef.current = options.channel
    connectRef.current = connect
    if (wantListeningRef.current) return
    if (!navigator.mediaDevices?.getUserMedia) {
      reportError("Microphone is not supported in this browser")
      return
    }
    const isLocalHost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    if (!window.isSecureContext && !isLocalHost) {
      reportError("Voice needs a secure (HTTPS) page before the mic can start.")
      return
    }

    const gen = ++generationRef.current
    wantListeningRef.current = true
    try {
      ensureGraph()
      await connect(gen)
    } catch (err) {
      if (gen === generationRef.current) reportError(micErrorMessage(err))
    }
  }, [connect, ensureGraph, options.channel, options.onError, options.onFinal, options.onInterim, reportError])

  const toggle = useCallback(() => {
    const now = performance.now()
    if (isEchoMicToggle(lastToggleAtRef.current, now)) return
    lastToggleAtRef.current = now
    if (wantListeningRef.current) stop()
    else void start()
  }, [start, stop])

  return {
    ...state,
    start,
    stop,
    toggle,
  }
}
