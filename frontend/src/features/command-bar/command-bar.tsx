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
import { ChatScopeChip } from "@/components/chat-scope-chip"

export function CommandBar() {
  const toast = useUIStore((s) => s.toast)
  const phone = useStateStore((s) => s.phone)
  const toggleBottomPanel = useUIStore((s) => s.toggleBottomPanel)
  const [phoneCalling, setPhoneCalling] = useState(false)
  const [quickInput, setQuickInput] = useState("")
  const [sendingQuick, setSendingQuick] = useState(false)

  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const activeChannel = channels.find((c) => c.id === activeChannelId)
  const currentProject = useProjectStore((s) => s.currentProject)

  const orchestrator = useOrchestrator({
    channel: "desktop",
    sync: true,
    onOpenTerminal: () => toggleBottomPanel(),
  })

  const runChiefOfStaffPrompt = useCallback(async (text: string, openDrawer = true) => {
    const trimmed = text.trim()
    if (!trimmed) return
    setSendingQuick(true)
    try {
      const { activeChannelId: chId, postMessage } = useChannelStore.getState()
      if (chId) {
        await postMessage(chId, trimmed, "user")
      }
      if (openDrawer && chId && !useUIStore.getState().chatOpen) {
        useUIStore.getState().toggleChat()
      }
      const result = await orchestrator.send(trimmed)
      if (chId && result?.response) {
        await postMessage(chId, result.response, "orchestrator")
      } else if (!chId && result?.response) {
        toast(result.response.slice(0, 160), "info")
      }
    } finally {
      setSendingQuick(false)
    }
  }, [orchestrator, toast])

  const handleVoiceFinal = useCallback(async (text: string) => {
    toast(`Voice: "${text}"`, "info")
    await runChiefOfStaffPrompt(text, true)
  }, [toast, runChiefOfStaffPrompt])

  const voice = useVoice({ channel: "desktop", onFinal: handleVoiceFinal })
  const setVoicePanelOpen = useUIStore((s) => s.setVoicePanelOpen)
  const voicePanelOpen = useUIStore((s) => s.voicePanelOpen)
  const chatOpen = useUIStore((s) => s.chatOpen)

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

  const scopeLabel = activeChannel?.name?.replace(/^#/, "") || currentProject || "HomeBase"

  return (
    <div className="fixed bottom-0 left-0 right-0 h-[48px] bg-gray-800 border-t border-gray-700 z-40 flex items-center px-4 gap-2">
      {/* Left: logs pill + scope chip */}
      <LogsPill />
      {!chatOpen && <ChatScopeChip compact />}

      {/* Center: Always-ready Workstream Chief-of-Staff Bar */}
      <div className="flex-1 max-w-2xl mx-auto flex items-center gap-1.5">
        <div className="flex-1 flex items-center rounded-full border border-gray-600/80 bg-gray-900/90 px-3 py-1 focus-within:border-blue-500">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-blue-400 mr-2 flex-shrink-0">
            Chief of Staff
          </span>
          <input
            data-no-global-intercept
            value={quickInput}
            disabled={sendingQuick}
            onChange={(e) => setQuickInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && quickInput.trim() && !sendingQuick) {
                e.preventDefault()
                const msg = quickInput
                setQuickInput("")
                void runChiefOfStaffPrompt(msg, true)
              }
            }}
            placeholder={`Ask ${scopeLabel} — "Brief me", "Start a goal to...", or ask anything…`}
            className="min-w-0 flex-1 bg-transparent text-xs text-gray-100 placeholder-gray-500 focus:outline-none"
          />
          {quickInput.trim() ? (
            <button
              disabled={sendingQuick}
              onClick={() => {
                const msg = quickInput
                setQuickInput("")
                void runChiefOfStaffPrompt(msg, true)
              }}
              className="ml-1.5 rounded-full bg-blue-600 px-2.5 py-0.5 text-[10px] font-medium text-white hover:bg-blue-500"
            >
              {sendingQuick ? "…" : "Send"}
            </button>
          ) : (
            <button
              disabled={sendingQuick}
              onClick={() => { void runChiefOfStaffPrompt("Brief me", true) }}
              className="ml-1.5 rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 hover:border-blue-500/50 hover:text-blue-300 flex-shrink-0"
              title="Get an instant deterministic status brief for this workstream"
            >
              ⚡ Brief me
            </button>
          )}
        </div>
      </div>

      {/* Right: Unified Controls */}
      <div className="flex items-center gap-2">
        {/* Phone */}
        {Boolean(phone?.configured) && (
          <button
            className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${
              phone?.active
                ? "bg-green-600 text-white animate-pulse"
                : "bg-gray-600 text-gray-300 hover:bg-gray-500"
            }`}
            disabled={phoneCalling}
            onClick={handlePhone}
            title={phone?.active ? "Hang up" : "Call"}
          >
            📱
          </button>
        )}

        {/* Voice — opens HomeBase Voice panel */}
        <button
          className={`h-8 rounded-full flex items-center gap-1.5 px-3 text-xs font-medium transition-colors ${
            voicePanelOpen || voice.listening
              ? "bg-red-600 text-white animate-pulse"
              : "bg-gray-700 text-gray-300 hover:bg-gray-600"
          }`}
          onClick={() => setVoicePanelOpen(true)}
          title="HomeBase Voice — talk to Chief of Staff"
        >
          🎤
          <span className="hidden sm:inline">Voice</span>
        </button>

        {/* Chief of Staff History Drawer toggle */}
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
          : "bg-gray-700 text-gray-300 hover:bg-gray-600"
      }`}
      onClick={toggleChat}
      title="Toggle Workstream Chief-of-Staff drawer (⌘/)"
    >
      💬
      <span className="hidden md:inline">Thread</span>
      {!chatOpen && messages.length > 0 && (
        <span className="text-[10px] bg-gray-600 text-gray-200 px-1.5 rounded-full">{messages.length}</span>
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
        className="w-8 h-8 rounded-full flex items-center justify-center text-sm bg-gray-700 text-gray-300 hover:bg-gray-600"
        onClick={() => setOpen(!open)}
        title="Settings"
      >
        ⚙
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute bottom-10 right-0 z-50 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 w-48">
            {currentProject && (
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
