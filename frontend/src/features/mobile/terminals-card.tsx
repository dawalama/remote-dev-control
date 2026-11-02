import { useState } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { useUIStore } from "@/stores/ui-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { useMountEffect } from "@/hooks/use-mount-effect"
import { POST } from "@/lib/api"

export function TerminalsCard({
  onOpenTerminal,
}: {
  onOpenTerminal: (id: string) => void
}) {
  const terminals = useStateStore((s) => s.terminals)
  const currentProject = useProjectStore((s) => s.currentProject)
  const killTerminal = useTerminalStore((s) => s.killTerminal)
  const toast = useUIStore((s) => s.toast)
  const [spawning, setSpawning] = useState<string | null>(null)
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  useMountEffect(() => {
    loadPresets()
  })

  const filtered =
    !currentProject
      ? terminals
      : terminals.filter((t) => t.project === currentProject)

  const labelFor = (command: string | undefined) => {
    const exact = presets.find((p) => p.command === command)?.label
    if (exact) return exact
    const base = (command || "").trim().split(/\s+/)[0] || ""
    const base2 = base.split("/").pop() || base
    const byBase = presets.find((p) => (p.command || "").trim().split(/\s+/)[0].split("/").pop() === base2)?.label
    return byBase ?? command ?? "Shell"
  }

  const handleSpawn = async (command: string) => {
    if (!currentProject) {
      toast("Select a project first", "warning")
      return
    }
    setSpawning(command)
    try {
      const url = `/terminals?project=${encodeURIComponent(currentProject)}&command=${encodeURIComponent(command)}`
      const session = await POST<{ id: string }>(url)
      if (session?.id) onOpenTerminal(session.id)
    } catch {
      toast("Failed to create terminal", "error")
    }
    setSpawning(null)
  }

  const getLabel = (terminal: (typeof filtered)[0]) => {
    if (!currentProject) return terminal.project
    const baseLabel = labelFor(terminal.command)
    const pid = terminal.pid ? ` (${terminal.pid})` : ""
    const sameLabel = filtered.filter((t) => labelFor(t.command) === baseLabel)
    if (sameLabel.length > 1) {
      return `${baseLabel} ${sameLabel.indexOf(terminal) + 1}${pid}`
    }
    return `${baseLabel}${pid}`
  }

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Goals &amp; Terminals
        </h3>
        {filtered.length > 0 && (
          <span className="text-[10px] text-gray-500">{filtered.length}</span>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            {!currentProject
              ? "Select a project to start a goal loop or open a shell"
              : "Start a supervised goal loop, or open a raw shell fallback"}
          </p>
          {currentProject && (
            <div className="flex flex-wrap gap-2">
              <button
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-xs font-medium text-white"
                onClick={() => useUIStore.getState().setSessionsHubOpen(true)}
              >
                + Start Goal Loop
              </button>
              <button
                className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-xs font-mono text-gray-200 disabled:opacity-50"
                onClick={() => handleSpawn("")}
                disabled={spawning !== null}
              >
                &gt;_ Raw Shell
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((t) => (
            <div
              key={t.id}
              className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-gray-700 cursor-pointer"
              onClick={() => onOpenTerminal(t.id)}
            >
              <span
                className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  t.waiting_for_input
                    ? "bg-orange-500 animate-pulse"
                    : t.status === "running"
                    ? "bg-green-500"
                    : "bg-gray-500"
                }`}
              />
              <span className="text-sm text-gray-200 flex-1 truncate">
                {getLabel(t)}
              </span>
              <span className="text-[10px] text-gray-500">{t.status}</span>
              <button
                className="text-gray-500 hover:text-red-400 text-sm"
                onClick={(e) => {
                  e.stopPropagation()
                  killTerminal(t.id)
                  toast("Terminal killed", "info")
                }}
              >
                &times;
              </button>
            </div>
          ))}
          <div className="flex gap-2 pt-1">
            <button
              className="flex-1 py-1.5 rounded bg-blue-600/20 border border-blue-500/40 text-blue-300 text-xs font-medium"
              onClick={() => useUIStore.getState().setSessionsHubOpen(true)}
            >
              + New Goal Loop
            </button>
            <button
              className="px-3 py-1.5 rounded border border-dashed border-gray-600 text-gray-400 hover:text-gray-200 text-xs font-mono"
              onClick={() => handleSpawn("")}
              disabled={spawning !== null}
            >
              + Shell
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
