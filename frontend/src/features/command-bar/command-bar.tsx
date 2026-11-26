import { useState, useCallback, useRef } from "react"
import { useUIStore } from "@/stores/ui-store"
import { LogsPill } from "@/features/logs/logs-panel"
import { useVoice } from "@/hooks/use-voice"
import { useOrchestrator } from "@/hooks/use-orchestrator"
import { useChannelSend } from "@/hooks/use-channel-send"
import { useFileUpload } from "@/hooks/use-file-upload"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"

export function CommandBar() {
  const toast = useUIStore((s) => s.toast)
  const toggleBottomPanel = useUIStore((s) => s.toggleBottomPanel)
  const chatOpen = useUIStore((s) => s.chatOpen)
  const toggleChat = useUIStore((s) => s.toggleChat)
  const setVoicePanelOpen = useUIStore((s) => s.setVoicePanelOpen)
  const voicePanelOpen = useUIStore((s) => s.voicePanelOpen)

  const [quickInput, setQuickInput] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)

  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const messages = useChannelStore((s) => s.messages)
  const postMessage = useChannelStore((s) => s.postMessage)
  const activeChannel = channels.find((c) => c.id === activeChannelId)
  const currentProject = useProjectStore((s) => s.currentProject)

  const isHomebase = !activeChannelId || activeChannelId === "ch-system"
  const rawName = activeChannel?.name?.replace(/^#/, "") || currentProject || "HomeBase"
  const scopeBadge = isHomebase ? "HomeBase" : `#${rawName}`
  const projectName =
    activeChannel?.project_names?.[0] ||
    (isHomebase ? currentProject || "" : rawName.split("/")[0] || "")

  const orchestrator = useOrchestrator({
    channel: "desktop",
    onOpenTerminal: () => toggleBottomPanel(),
  })

  const { handleSend: sendToChannel, sending } = useChannelSend({
    activeChannelId,
    projectName,
    orchestrator,
    postMessage,
  })

  const { uploadMultiple } = useFileUpload()

  const sendPrompt = useCallback(
    async (text: string, openDrawer = true) => {
      const trimmed = text.trim()
      if (!trimmed) return
      if (openDrawer && !useUIStore.getState().chatOpen) {
        useUIStore.getState().toggleChat()
      }
      if (activeChannelId) {
        await sendToChannel(trimmed)
      } else {
        const result = await orchestrator.send(trimmed)
        if (result?.response) {
          toast(String(result.response).slice(0, 160), "info")
        }
      }
    },
    [activeChannelId, sendToChannel, orchestrator, toast],
  )

  const handleVoiceFinal = useCallback(
    async (text: string) => {
      toast(`Voice: "${text}"`, "info")
      await sendPrompt(text, true)
    },
    [toast, sendPrompt],
  )

  const voice = useVoice({ channel: "desktop", onFinal: handleVoiceFinal })

  return (
    <div className="fixed bottom-0 left-0 right-0 h-[48px] bg-gray-800 border-t border-gray-700 z-40 flex items-center px-4 gap-2">
      {/* Hidden file input for single docked composer */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) {
            void uploadMultiple(e.target.files)
            if (!chatOpen) toggleChat()
          }
          e.target.value = ""
        }}
      />

      {/* Left: logs pill */}
      <LogsPill />

      {/* Center: Single Docked Scope Composer + Inline Thread Toggle */}
      <div className="flex-1 max-w-2xl mx-auto flex items-center gap-1.5">
        <div
          className={`flex-1 flex items-center rounded-full border bg-gray-900/95 px-2.5 py-1 transition-colors ${
            chatOpen
              ? "border-blue-500/70 ring-1 ring-blue-500/20"
              : "border-gray-600/80 focus-within:border-blue-500"
          }`}
        >
          <span
            className={`mr-2 flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
              isHomebase
                ? "bg-blue-500/15 text-blue-300 border border-blue-500/30"
                : "bg-amber-500/15 text-amber-200 border border-amber-500/30"
            }`}
            title={isHomebase ? "Whole-machine HomeBase scope" : `Workstream ${scopeBadge}`}
          >
            {scopeBadge}
          </span>

          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="mr-1.5 text-gray-500 hover:text-gray-300 text-xs flex-shrink-0"
            title="Attach file to thread"
          >
            📎
          </button>

          <input
            data-no-global-intercept
            value={quickInput}
            disabled={sending}
            onChange={(e) => setQuickInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && quickInput.trim() && !sending) {
                e.preventDefault()
                const msg = quickInput
                setQuickInput("")
                void sendPrompt(msg, true)
              }
            }}
            placeholder={`Ask ${scopeBadge}… ("Brief me", "Start a goal…", or ⌘/ for history)`}
            className="min-w-0 flex-1 bg-transparent text-xs text-gray-100 placeholder-gray-500 focus:outline-none"
          />

          {quickInput.trim() ? (
            <button
              type="button"
              disabled={sending}
              onClick={() => {
                const msg = quickInput
                setQuickInput("")
                void sendPrompt(msg, true)
              }}
              className="ml-1.5 rounded-full bg-blue-600 px-2.5 py-0.5 text-[10px] font-medium text-white hover:bg-blue-500 flex-shrink-0"
            >
              {sending ? "…" : "Send"}
            </button>
          ) : (
            <button
              type="button"
              disabled={sending}
              onClick={() => {
                void sendPrompt("Brief me", true)
              }}
              className="ml-1.5 rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 hover:border-blue-500/50 hover:text-blue-300 flex-shrink-0"
              title="Get an instant status brief"
            >
              ⚡ Brief me
            </button>
          )}

          <button
            type="button"
            onClick={toggleChat}
            title={chatOpen ? "Hide thread history (⌘/)" : "Show thread history (⌘/)"}
            className={`ml-1.5 flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors flex-shrink-0 ${
              chatOpen
                ? "bg-blue-600/25 text-blue-200 border border-blue-500/40"
                : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            }`}
          >
            <span>{chatOpen ? "⌄" : "⌃"}</span>
            {messages.length > 0 && <span className="font-mono">{messages.length}</span>}
          </button>
        </div>
      </div>

      {/* Right: Voice & Settings */}
      <div className="flex items-center gap-2">
        <button
          className={`h-8 rounded-full flex items-center gap-1.5 px-3 text-xs font-medium transition-colors ${
            voicePanelOpen || voice.listening
              ? "bg-red-600 text-white animate-pulse"
              : "bg-gray-700 text-gray-300 hover:bg-gray-600"
          }`}
          onClick={() => setVoicePanelOpen(true)}
          title="Voice input"
        >
          🎤
          <span className="hidden sm:inline">Voice</span>
        </button>

        <SettingsButton />
      </div>
    </div>
  )
}

function SettingsButton() {
  const [open, setOpen] = useState(false)
  const currentProject = useProjectStore((s) => s.currentProject)
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const activeWorkstream = channels.find((channel) => channel.id === activeChannelId)
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
          <div className="absolute bottom-10 right-0 z-50 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 w-56">
            {currentProject && (
              <button
                className="w-full px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 text-left"
                onClick={() => {
                  setOpen(false)
                  useUIStore.getState().setProjectSettingsOpen(true)
                }}
              >
                Project Settings
                <span className="text-[10px] text-gray-500 ml-1">{currentProject}</span>
              </button>
            )}
            {activeWorkstream && (
              <button
                className="w-full px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 text-left truncate"
                onClick={() => {
                  setOpen(false)
                  useUIStore.getState().setWorkstreamSettingsOpen(true)
                }}
              >
                Workstream Settings
                <span className="text-[10px] text-gray-500 ml-1">
                  {activeWorkstream.name.replace(/^#/, "")}
                </span>
              </button>
            )}
            <button
              className="w-full px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 text-left"
              onClick={() => {
                setOpen(false)
                useUIStore.getState().setSystemSettingsOpen(true)
              }}
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
                      theme === t.id
                        ? "bg-blue-600 text-white"
                        : "bg-gray-700 text-gray-400 hover:bg-gray-600"
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
