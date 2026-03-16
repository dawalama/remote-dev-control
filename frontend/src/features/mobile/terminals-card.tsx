import { useState, useEffect } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { useUIStore } from "@/stores/ui-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
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
  const [showInlinePicker, setShowInlinePicker] = useState(false)
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  const filtered =
    currentProject === "all"
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
    if (currentProject === "all") {
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
    if (currentProject === "all") return terminal.project
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
          Terminals
        </h3>
        {filtered.length > 0 && (
          <span className="text-[10px] text-gray-500">{filtered.length}</span>
        )}
      </div>

      {filtered.length === 0 ? (
        // Empty state: show agent launcher buttons
        <div className="space-y-1.5">
          <p className="text-xs text-gray-500 mb-2">
            {currentProject === "all"
              ? "Select a project to open a terminal"
              : "Launch a terminal agent"}
          </p>
          {currentProject !== "all" && (
            <div className="grid grid-cols-2 gap-1.5">
              {presets.map((preset) => (
                <button
                  key={preset.id}
                  className="flex items-center gap-2 px-3 py-2 rounded bg-gray-700 hover:bg-gray-600 text-left disabled:opacity-50"
                  onClick={() => handleSpawn(preset.command)}
                  disabled={spawning !== null}
                >
                  <span className="w-6 h-6 rounded bg-gray-800 flex items-center justify-center text-[10px] font-mono font-bold text-gray-300 flex-shrink-0">
                    {preset.icon}
                  </span>
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-gray-200">{preset.label}</div>
                    <div className="text-[10px] text-gray-500 truncate">{preset.command || "$SHELL"}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        // Active terminals list
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
          {/* Add more button */}
          <button
            className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded border border-dashed border-gray-600 text-gray-500 hover:text-gray-300 hover:border-gray-500 text-xs"
            onClick={() => {
              if (currentProject === "all") { toast("Select a project first", "warning"); return }
              // Quick: show inline preset picker
              setShowInlinePicker((v) => !v)
            }}
          >
            + New Terminal
          </button>
          <InlineAgentPicker
            show={showInlinePicker}
            presets={presets}
            onSelect={(cmd) => {
              setShowInlinePicker(false)
              handleSpawn(cmd)
            }}
            disabled={spawning !== null}
          />
        </div>
      )}
    </div>
  )
}

// Small inline picker that expands below the "+ New Terminal" button
function InlineAgentPicker({
  show,
  presets,
  onSelect,
  disabled,
}: {
  show: boolean
  presets: { id: string; label: string; command: string; icon: string }[]
  onSelect: (command: string) => void
  disabled: boolean
}) {
  if (!show) return null
  return (
    <div className="grid grid-cols-2 gap-1.5 pt-1">
      {presets.map((preset) => (
        <button
          key={preset.id}
          className="flex items-center gap-2 px-2.5 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-left disabled:opacity-50"
          onClick={() => onSelect(preset.command)}
          disabled={disabled}
        >
          <span className="w-5 h-5 rounded bg-gray-800 flex items-center justify-center text-[10px] font-mono font-bold text-gray-300 flex-shrink-0">
            {preset.icon}
          </span>
          <span className="text-xs text-gray-200">{preset.label}</span>
        </button>
      ))}
    </div>
  )
}
