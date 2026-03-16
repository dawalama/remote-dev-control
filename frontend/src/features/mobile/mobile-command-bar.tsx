import { useState, useCallback, useRef } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST, api } from "@/lib/api"
import { getClientId } from "@/lib/client-id"
import { useVoice } from "@/hooks/use-voice"
import { useOrchestrator } from "@/hooks/use-orchestrator"

export function MobileCommandBar({
  onOpenTerminal,
  onCreateTask,
  onOpenBrowser,
  onOpenActivity,
  onOpenMenu,
}: {
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
  onOpenActivity?: () => void
  onOpenMenu?: () => void
}) {
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const phone = useStateStore((s) => s.phone)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const autoSubmitRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const orchestrator = useOrchestrator({
    channel: "mobile",
    onOpenTerminal,
    onCreateTask,
    onOpenBrowser,
    onOpenActivity,
    onOpenMenu,
  })

  const send = async (text?: string) => {
    const msg = (text || input).trim()
    if (!msg || loading) return
    setInput("")
    setLoading(true)
    try {
      const result = await orchestrator.send(msg)
      if (result?.response) {
        toast(result.response.slice(0, 100), "info")
      }
    } finally {
      setLoading(false)
    }
  }

  const handleVoiceFinal = useCallback((text: string) => {
    setInput(text)
    // Auto-submit after 600ms
    if (autoSubmitRef.current) clearTimeout(autoSubmitRef.current)
    autoSubmitRef.current = setTimeout(() => {
      send(text)
    }, 600)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleVoiceInterim = useCallback((text: string) => {
    setInput(text)
  }, [])

  const voice = useVoice({ onFinal: handleVoiceFinal, onInterim: handleVoiceInterim })

  const handlePhone = async () => {
    if (phone?.active) {
      try {
        await POST("/voice/hangup")
        toast("Call ended", "info")
      } catch { toast("Failed to hang up", "error") }
    } else if (phone?.configured) {
      try {
        await POST("/voice/call", { client_id: getClientId() })
        toast("Calling...", "info")
      } catch { toast("Failed to call", "error") }
    } else {
      toast("Phone not configured", "warning")
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ""
    const form = new FormData()
    form.append("file", file)
    if (currentProject && currentProject !== "all") form.append("project", currentProject)
    try {
      const res = await api<{ id: string; path: string }>("/context/upload", {
        method: "POST",
        body: form,
      })
      await navigator.clipboard.writeText(res.path).catch(() => {})
      toast(`Copied path: ${res.path}`, "success")
    } catch {
      toast("Upload failed", "error")
    }
  }

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 bg-gray-800 border-t border-gray-700 px-3 py-2 flex items-center gap-2">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") send()
        }}
        placeholder={voice.listening ? "Listening..." : "Type a command..."}
        className="flex-1 bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
        disabled={loading}
      />

      <input
        ref={fileInputRef}
        type="file"
        accept="*/*"
        className="hidden"
        onChange={handleUpload}
      />
      <button
        className="w-9 h-9 rounded-lg flex items-center justify-center text-lg bg-gray-700 text-gray-300"
        onClick={() => fileInputRef.current?.click()}
        title="Upload file"
      >
        📎
      </button>
      <button
        className="w-9 h-9 rounded-lg flex items-center justify-center text-sm bg-gray-700 text-gray-300 hover:bg-gray-600"
        onClick={() => useUIStore.getState().toggleAgentPanel()}
        title="Browser Agent"
      >
        🤖
      </button>
      <button
        className={`w-9 h-9 rounded-lg flex items-center justify-center text-lg ${
          voice.listening
            ? "bg-red-600 text-white animate-pulse"
            : "bg-gray-700 text-gray-300"
        }`}
        onClick={voice.toggle}
        title={voice.listening ? "Stop" : "Voice"}
      >
        🎤
      </button>
      <button
        className={`w-9 h-9 rounded-lg flex items-center justify-center text-lg ${
          phone?.active
            ? "bg-green-600 text-white animate-pulse"
            : phone?.configured
              ? "bg-gray-700 text-gray-300"
              : "bg-gray-700 text-gray-500 opacity-50"
        }`}
        onClick={handlePhone}
        disabled={!phone?.configured}
        title={phone?.active ? "Hang up" : "Call"}
      >
        📞
      </button>
    </div>
  )
}
