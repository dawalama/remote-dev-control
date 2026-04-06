import { useState, useCallback } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useStateStore } from "@/stores/state-store"
import { POST } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { LogsPill } from "@/features/logs/logs-panel"
import { useVoice } from "@/hooks/use-voice"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelStore } from "@/stores/channel-store"

export function CommandBar() {
  const toast = useUIStore((s) => s.toast)
  const phone = useStateStore((s) => s.phone)
  const theme = useUIStore((s) => s.theme)
  const setTheme = useUIStore((s) => s.setTheme)
  const toggleBottomPanel = useUIStore((s) => s.toggleBottomPanel)
  const [phoneCalling, setPhoneCalling] = useState(false)

  const orchestrator = useOrchestrator({
    channel: "desktop",
    onOpenTerminal: () => toggleBottomPanel(),
  })

  const handleVoiceFinal = useCallback((text: string) => {
    toast(`Voice: "${text}"`, "info")
    orchestrator.send(text).then((r) => {
      if (r?.response) toast(r.response.slice(0, 120), "info")
    })
  }, [toast, orchestrator])

  const voice = useVoice({ onFinal: handleVoiceFinal })

  const handlePhone = async () => {
    if (phone?.active) {
      try {
        await POST("/voice/hangup")
        toast("Call ended", "info")
      } catch { toast("Failed to hang up", "error") }
    } else {
      setPhoneCalling(true)
      try {
        await POST("/voice/call", { client_id: getClientId() })
        toast("Calling...", "info")
      } catch { toast("Failed to call", "error") }
      finally { setPhoneCalling(false) }
    }
  }

  const themes = [
    { id: "default", label: "STD" },
    { id: "modern", label: "MOD" },
    { id: "brutalist", label: "BRT" },
  ]

  return (
    <div className="fixed bottom-0 left-0 right-0 h-[48px] bg-gray-800 border-t border-gray-700 z-40 flex items-center px-4 gap-2">
      {/* Left: logs pill */}
      <LogsPill />

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right: FABs */}
      <div className="flex items-center gap-2">
        {/* Phone */}
        <button
          className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${
            phone?.active
              ? "bg-green-600 text-white animate-pulse"
              : phone?.configured
                ? "bg-gray-600 text-gray-300 hover:bg-gray-500"
                : "bg-gray-700 text-gray-500 opacity-50 cursor-not-allowed"
          }`}
          disabled={!phone?.configured || phoneCalling}
          onClick={handlePhone}
          title={phone?.active ? "Hang up" : "Call"}
        >
          📱
        </button>

        {/* Voice */}
        <button
          className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${
            voice.listening
              ? "bg-red-600 text-white animate-pulse"
              : "bg-gray-600 text-gray-300 hover:bg-gray-500"
          }`}
          onClick={voice.toggle}
          title={voice.listening ? "Stop listening" : "Voice input"}
        >
          🎤
        </button>
        {voice.interim && (
          <span className="text-xs text-gray-400 italic max-w-[200px] truncate">
            {voice.interim}
          </span>
        )}

        {/* Chat toggle */}
        <ChatToggle />

        {/* Theme picker */}
        <div className="flex rounded overflow-hidden border border-gray-600">
          {themes.map((t) => (
            <button
              key={t.id}
              className={`px-2 py-1 text-xs font-medium ${
                theme === t.id
                  ? "bg-blue-600 text-white"
                  : "bg-gray-700 text-gray-400 hover:bg-gray-600"
              }`}
              onClick={() => setTheme(t.id)}
              title={t.id}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function ChatToggle() {
  const chatOpen = useUIStore((s) => s.chatOpen)
  const toggleChat = useUIStore((s) => s.toggleChat)
  const messages = useChannelStore((s) => s.messages)

  return (
    <button
      className={`h-8 rounded-full flex items-center gap-1.5 px-3 text-xs font-medium transition-colors ${
        chatOpen
          ? "bg-blue-600 text-white"
          : "bg-gray-600 text-gray-300 hover:bg-gray-500"
      }`}
      onClick={toggleChat}
      title="Toggle chat (⌘/)"
    >
      💬
      {!chatOpen && messages.length > 0 && (
        <span className="text-[10px] bg-gray-500 text-white px-1 rounded-full">{messages.length}</span>
      )}
    </button>
  )
}
