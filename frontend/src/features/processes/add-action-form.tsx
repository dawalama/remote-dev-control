import { useState } from "react"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST } from "@/lib/api"

export function AddActionForm({ onAdded }: { onAdded?: () => void }) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<"service" | "command">("command")
  const [name, setName] = useState("")
  const [command, setCommand] = useState("")
  const [port, setPort] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // AI suggest
  const [prompt, setPrompt] = useState("")
  const [suggesting, setSuggesting] = useState(false)

  const currentProject = useProjectStore((s) => s.currentProject)
  const projects = useProjectStore((s) => s.projects)
  const toast = useUIStore((s) => s.toast)

  const projectPath = projects.find((p) => p.name === currentProject)?.path || ""

  if (currentProject === "all") return null

  if (!open) {
    return (
      <button
        className="w-full py-1.5 text-[10px] rounded border border-dashed border-gray-600 text-gray-400 hover:text-gray-200 hover:border-gray-400"
        onClick={() => setOpen(true)}
      >
        + Add Action
      </button>
    )
  }

  const handleSuggest = async () => {
    if (!prompt.trim()) return
    setSuggesting(true)
    try {
      const res = await POST<{
        name: string
        command: string
        kind: string
        port: number | null
        cwd: string | null
      }>("/actions/suggest", {
        project: currentProject,
        description: prompt.trim(),
      })
      setName(res.name || "")
      setCommand(res.command || "")
      setKind((res.kind === "service" ? "service" : "command"))
      setPort(res.port ? String(res.port) : "")
      toast("Suggestion filled — review and save", "success")
    } catch {
      toast("AI suggestion failed", "error")
    } finally {
      setSuggesting(false)
    }
  }

  const handleSubmit = async () => {
    if (!name.trim() || !command.trim()) return
    setSubmitting(true)
    try {
      await POST("/actions/register", {
        project: currentProject,
        name: name.trim(),
        command: command.trim(),
        cwd: projectPath,
        port: kind === "service" && port.trim() ? parseInt(port.trim(), 10) : null,
        kind,
      })
      toast(`Added: ${name}`, "success")
      setName("")
      setCommand("")
      setPort("")
      setPrompt("")
      setOpen(false)
      onAdded?.()
    } catch {
      toast("Failed to add action", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="border border-gray-600 rounded-lg p-2.5 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-gray-400 uppercase tracking-wider">New Action</span>
        <button
          className="text-[10px] text-gray-500 hover:text-gray-300"
          onClick={() => setOpen(false)}
        >
          Cancel
        </button>
      </div>

      {/* AI suggest */}
      <div className="flex gap-1">
        <input
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSuggest() }}
          placeholder="Describe what you need (e.g. lint, run tests, db migrate)"
          className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-purple-500 min-w-0"
          autoFocus
        />
        <button
          className="px-2 py-1.5 text-[10px] rounded bg-purple-600 hover:bg-purple-700 text-white font-medium disabled:opacity-50 flex-shrink-0"
          onClick={handleSuggest}
          disabled={suggesting || !prompt.trim()}
        >
          {suggesting ? "..." : "Ask AI"}
        </button>
      </div>

      <div className="border-t border-gray-700 pt-2 space-y-2">
        {/* Kind toggle */}
        <div className="flex rounded overflow-hidden border border-gray-600">
          <button
            className={`flex-1 py-1 text-[10px] font-medium ${
              kind === "service" ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-400"
            }`}
            onClick={() => setKind("service")}
          >
            Service
          </button>
          <button
            className={`flex-1 py-1 text-[10px] font-medium ${
              kind === "command" ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-400"
            }`}
            onClick={() => setKind("command")}
          >
            Command
          </button>
        </div>

        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name (e.g. api-server, lint, test)"
          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
        />
        <input
          type="text"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Command (e.g. npm run dev, make lint)"
          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
        />
        {kind === "service" && (
          <input
            type="text"
            value={port}
            onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
            placeholder="Port (optional, e.g. 3000)"
            className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
          />
        )}

        <button
          className="w-full py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white font-medium disabled:opacity-50"
          onClick={handleSubmit}
          disabled={submitting || !name.trim() || !command.trim()}
        >
          {submitting ? "Adding..." : "Add"}
        </button>
      </div>
    </div>
  )
}
