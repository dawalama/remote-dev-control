import { useCallback, useRef, useState } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useChannelStore } from "@/stores/channel-store"
import { briefWorkstreamFilter, orchestratorWorkstreamId } from "@/stores/chat-scope-store"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useVoice } from "@/hooks/use-voice"
import { ChatScopeChip } from "@/components/chat-scope-chip"
import { GET } from "@/lib/api"
import {
  dismissVoiceUpgradeBanner,
  getLastServerTtsError,
  getSpeakRepliesEnabled,
  isVoiceUpgradeBannerDismissed,
  setSpeakRepliesEnabled,
  speakText,
  stopSpeaking,
} from "@/lib/speak"

const QUICK_PHRASES = [
  "Brief me",
  "What needs me",
  "What's running",
]

/**
 * HomeBase Voice — explicit connect surface for listen + spoken replies.
 */
export function HomeBaseVoicePanel() {
  const open = useUIStore((s) => s.voicePanelOpen)
  const setOpen = useUIStore((s) => s.setVoicePanelOpen)
  const toast = useUIStore((s) => s.toast)
  const setSessionsHubOpen = useUIStore((s) => s.setSessionsHubOpen)
  const setSystemSettingsOpen = useUIStore((s) => s.setSystemSettingsOpen)

  const [lastHeard, setLastHeard] = useState("")
  const [lastReply, setLastReply] = useState("")
  const [busy, setBusy] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [speakOn, setSpeakOn] = useState(() => getSpeakRepliesEnabled())
  const [ttsPath, setTtsPath] = useState<string | null>(null)
  const [showUpgrade, setShowUpgrade] = useState(() => !isVoiceUpgradeBannerDismissed())
  const [serverTtsError, setServerTtsError] = useState<string | null>(null)

  const orchestrator = useOrchestrator({ channel: "desktop", sync: true })
  const runTextRef = useRef<(text: string) => Promise<void>>(async () => {})

  const voice = useVoice({
    channel: "desktop",
    onFinal: (text) => {
      void runTextRef.current(text)
    },
  })

  const playReply = useCallback(
    async (reply: string) => {
      if (!speakOn || !reply.trim()) return
      // Avoid feedback: stop mic while speaking
      if (voice.listening) voice.stop()
      setSpeaking(true)
      try {
        const path = await speakText(reply)
        setTtsPath(path === "skipped" ? null : path)
        if (path === "browser") {
          setServerTtsError(getLastServerTtsError())
          setShowUpgrade(true)
        } else if (path === "server") {
          setServerTtsError(null)
        } else if (path === "error") {
          toast("Could not play voice reply", "warning")
        }
      } finally {
        setSpeaking(false)
      }
    },
    [speakOn, toast, voice],
  )

  const runText = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed) return
      setLastHeard(trimmed)
      setBusy(true)
      setLastReply("")
      setTtsPath(null)
      try {
        const { activeChannelId, postMessage } = useChannelStore.getState()
        const postChannelId = orchestratorWorkstreamId(activeChannelId)
        if (postChannelId) {
          await postMessage(postChannelId, trimmed, "user")
        }
        const result = await orchestrator.send(trimmed)
        if (result?.async) {
          setBusy(false)
          return
        }
        const reply = result?.response || "Done."
        setLastReply(reply)
        if (postChannelId && result?.response) {
          await postMessage(postChannelId, result.response, "orchestrator")
        } else if (!postChannelId && result?.response) {
          toast(result.response.slice(0, 160), "info")
        }
        setBusy(false)
        await playReply(reply)
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Voice command failed"
        setLastReply(msg)
        toast(msg, "error")
        setBusy(false)
      }
    },
    [orchestrator, toast, playReply],
  )
  runTextRef.current = runText

  const runBrief = async () => {
    setBusy(true)
    setLastHeard("Brief me")
    setTtsPath(null)
    try {
      const filter = briefWorkstreamFilter()
      const qs = filter ? `?workstream_id=${encodeURIComponent(filter)}` : ""
      const brief = await GET<{ spoken_summary?: string }>(`/homebase/brief${qs}`)
      const text = brief.spoken_summary || "No brief available."
      setLastReply(text)
      const { activeChannelId, postMessage } = useChannelStore.getState()
      const postChannelId = filter || activeChannelId
      if (postChannelId) {
        await postMessage(postChannelId, "Brief me", "user")
        await postMessage(postChannelId, text, "orchestrator")
      }
      setBusy(false)
      await playReply(text)
    } catch {
      setLastReply("Failed to load HomeBase brief")
      toast("Failed to load HomeBase brief", "error")
      setBusy(false)
    }
  }

  const closePanel = () => {
    voice.stop()
    stopSpeaking()
    setSpeaking(false)
    setOpen(false)
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[190] flex items-end justify-center bg-black/50 p-3 sm:items-center"
      onMouseDown={closePanel}
    >
      <section
        className="w-full max-w-md overflow-hidden rounded-2xl border border-gray-600 bg-gray-900 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-2 border-b border-gray-800 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-gray-100">HomeBase Voice</h2>
            <p className="text-[11px] text-gray-500">Listen · reply out loud · control this machine</p>
          </div>
          <ChatScopeChip compact />
          <button
            type="button"
            className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            onClick={closePanel}
          >
            Close
          </button>
        </header>

        <div className="space-y-4 px-4 py-5">
          <div className="flex flex-col items-center gap-3">
            <button
              type="button"
              onClick={() => {
                if (voice.listening) {
                  voice.stop()
                } else {
                  stopSpeaking()
                  setSpeaking(false)
                  void voice.start()
                }
              }}
              className={`flex h-20 w-20 items-center justify-center rounded-full text-3xl shadow-lg transition-all ${
                voice.listening
                  ? "bg-red-600 text-white ring-4 ring-red-500/40 animate-pulse"
                  : speaking
                    ? "bg-purple-600 text-white ring-4 ring-purple-500/30"
                    : "bg-blue-600 text-white hover:bg-blue-500 ring-4 ring-blue-500/20"
              }`}
              title={voice.listening ? "Stop listening" : "Start listening"}
            >
              {voice.listening ? "⏹" : speaking ? "🔊" : "🎤"}
            </button>
            <div className="text-center">
              <div
                className={`text-sm font-medium ${
                  voice.listening ? "text-red-300" : speaking ? "text-purple-300" : "text-gray-200"
                }`}
              >
                {voice.listening
                  ? "Listening… speak now"
                  : speaking
                    ? "Speaking reply…"
                    : "Tap to connect voice"}
              </div>
              <div className="mt-0.5 text-[11px] text-gray-500">
                {speaking
                  ? ttsPath === "server"
                    ? "Server TTS"
                    : ttsPath === "browser"
                      ? "Browser voice (fallback)"
                      : "Playing audio"
                  : "Mic in · spoken answer out"}
              </div>
            </div>
          </div>

          {showUpgrade && ttsPath === "browser" && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5">
              <div className="text-xs font-semibold text-amber-100">
                Using browser voice (lower quality)
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-amber-100/80">
                Cloud TTS failed or isn&apos;t configured. For natural speech, set{" "}
                <span className="font-medium text-amber-50">ElevenLabs</span> or{" "}
                <span className="font-medium text-amber-50">Deepgram</span> under
                Voice &amp; TTS, then try again.
              </p>
              {serverTtsError && (
                <p className="mt-1.5 break-all font-mono text-[10px] text-amber-200/70">
                  {serverTtsError}
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded bg-amber-500/20 px-2.5 py-1 text-[11px] font-medium text-amber-50 hover:bg-amber-500/30"
                  onClick={() => setSystemSettingsOpen(true)}
                >
                  Open Voice &amp; TTS settings
                </button>
                <button
                  type="button"
                  className="rounded px-2.5 py-1 text-[11px] text-amber-200/80 hover:text-amber-50"
                  onClick={() => {
                    dismissVoiceUpgradeBanner()
                    setShowUpgrade(false)
                  }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}

          <label className="flex cursor-pointer items-center justify-between rounded-lg border border-gray-700 bg-gray-950/60 px-3 py-2">
            <div>
              <div className="text-xs font-medium text-gray-200">Speak replies</div>
              <div className="text-[10px] text-gray-500">
                {ttsPath === "server"
                  ? "Using server TTS (good quality)"
                  : ttsPath === "browser"
                    ? "Using browser fallback — configure cloud TTS for better quality"
                    : "Uses System Settings TTS, or browser voice if unavailable"}
              </div>
            </div>
            <input
              type="checkbox"
              className="h-4 w-4 accent-blue-500"
              checked={speakOn}
              onChange={(e) => {
                const next = e.target.checked
                setSpeakOn(next)
                setSpeakRepliesEnabled(next)
                if (!next) {
                  stopSpeaking()
                  setSpeaking(false)
                }
              }}
            />
          </label>

          <div className="min-h-[3.5rem] rounded-lg border border-gray-700 bg-gray-950/80 px-3 py-2">
            {voice.interim ? (
              <p className="text-sm italic text-gray-400">{voice.interim}</p>
            ) : lastHeard ? (
              <p className="text-sm text-gray-200">
                <span className="text-[10px] uppercase tracking-wide text-gray-500">You · </span>
                {lastHeard}
              </p>
            ) : (
              <p className="text-sm text-gray-600">Transcript appears here…</p>
            )}
            {busy && <p className="mt-1 text-[11px] text-blue-400">Working…</p>}
            {lastReply && !busy && (
              <div className="mt-2 border-t border-gray-800 pt-2">
                <p className="text-xs text-green-300/90">
                  <span className="text-[10px] uppercase tracking-wide text-gray-500">HomeBase · </span>
                  {lastReply}
                </p>
                {speakOn && (
                  <button
                    type="button"
                    className="mt-1.5 text-[11px] text-purple-300 hover:text-purple-200"
                    onClick={() => { void playReply(lastReply) }}
                  >
                    {speaking ? "Speaking…" : "▶ Replay voice"}
                  </button>
                )}
              </div>
            )}
            {voice.error && (
              <p className="mt-2 text-xs text-red-400">{voice.error}</p>
            )}
          </div>

          <div>
            <div className="mb-1.5 text-[10px] uppercase tracking-wide text-gray-500">Quick</div>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                disabled={busy || speaking}
                onClick={() => { void runBrief() }}
                className="rounded-full border border-blue-500/40 bg-blue-500/10 px-3 py-1 text-xs text-blue-200 hover:bg-blue-500/20 disabled:opacity-50"
              >
                Brief me
              </button>
              {QUICK_PHRASES.filter((p) => p !== "Brief me").map((phrase) => (
                <button
                  key={phrase}
                  type="button"
                  disabled={busy || speaking}
                  onClick={() => { void runText(phrase) }}
                  className="rounded-full border border-gray-600 bg-gray-800 px-3 py-1 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                >
                  {phrase}
                </button>
              ))}
            </div>
          </div>

          <p className="text-[11px] leading-relaxed text-gray-500">
            Configure cloud voices under System Settings → Voice &amp; TTS.
            If no API key is set, replies use the browser&apos;s built-in voice.
          </p>
        </div>

        <footer className="flex items-center justify-between border-t border-gray-800 px-4 py-2">
          <button
            type="button"
            className="text-[11px] text-gray-500 hover:text-gray-300"
            onClick={() => {
              closePanel()
              setSessionsHubOpen(true)
            }}
          >
            Sessions
          </button>
          <button
            type="button"
            className="text-[11px] text-gray-500 hover:text-gray-300"
            onClick={() => {
              closePanel()
              setSessionsHubOpen(false)
            }}
          >
            HomeBase desk
          </button>
        </footer>
      </section>
    </div>
  )
}
