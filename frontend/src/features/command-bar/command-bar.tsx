import { useState, useCallback } from "react"
import { useUIStore } from "@/stores/ui-store"
import { useStateStore } from "@/stores/state-store"
import { POST } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { LogsPill } from "@/features/logs/logs-panel"
import { useVoice } from "@/hooks/use-voice"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"

export function CommandBar() {
  const toast = useUIStore((s) => s.toast)
  const phone = useStateStore((s) => s.phone)
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

        {/* Settings (rightmost) */}
        <SettingsButton />
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

function SettingsButton() {
  const [open, setOpen] = useState(false)
  const currentProject = useProjectStore((s) => s.currentProject)
  const theme = useUIStore((s) => s.theme)
  const setTheme = useUIStore((s) => s.setTheme)

  const themes = [
    { id: "default", label: "Dark" },
    { id: "modern", label: "Modern" },
    { id: "brutalist", label: "Brutal" },
  ]

  return (
    <div className="relative">
      <button
        className="w-8 h-8 rounded-full flex items-center justify-center text-sm bg-gray-600 text-gray-300 hover:bg-gray-500"
        onClick={() => setOpen(!open)}
        title="Settings"
      >
        ⚙
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute bottom-10 right-0 z-50 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 w-48">
            {currentProject !== "all" && (
              <button
                className="w-full px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 text-left"
                onClick={() => { setOpen(false); useUIStore.getState().setProjectSettingsOpen(true) }}
              >
                Project Settings
                <span className="text-[10px] text-gray-500 ml-1">{currentProject}</span>
              </button>
            )}
            <button
              className="w-full px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 text-left"
              onClick={() => { setOpen(false); useUIStore.getState().setSystemSettingsOpen(true) }}
            >
              System Settings
            </button>
            <div className="border-t border-gray-700 my-1" />
            <div className="px-3 py-1.5">
              <div className="text-[10px] text-gray-500 mb-1">Theme</div>
              <div className="flex gap-1">
                {themes.map((t) => (
                  <button
                    key={t.id}
                    className={`px-2 py-0.5 text-[10px] rounded ${
                      theme === t.id ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-400 hover:bg-gray-600"
                    }`}
                    onClick={() => setTheme(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
