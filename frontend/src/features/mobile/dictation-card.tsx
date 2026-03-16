import { useState, useEffect } from "react"
import { useStateStore } from "@/stores/state-store"
import { useDictationStore } from "@/stores/dictation-store"
import { useUIStore } from "@/stores/ui-store"
import { POST } from "@/lib/api"

export function DictationCard() {
  const { active, blocks, clearAll, removeBlock, editBlock } = useDictationStore()
  const terminals = useStateStore((s) => s.terminals)
  const toast = useUIStore((s) => s.toast)
  const [toggling, setToggling] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState("")
  const [targetTerminal, setTargetTerminal] = useState("")

  useEffect(() => {
    if (!targetTerminal && terminals.length > 0) setTargetTerminal(terminals[0].id)
  }, [terminals, targetTerminal])

  const toggleDictation = async () => {
    setToggling(true)
    try {
      await POST("/voice/type-mode", { enabled: !active, target: "terminal" })
    } catch {
      toast("Failed to toggle dictation", "error")
    }
    setToggling(false)
  }

  const insertText = async (text: string) => {
    if (!targetTerminal) { toast("Select a terminal", "warning"); return }
    try {
      await POST(`/terminals/${encodeURIComponent(targetTerminal)}/input`, { text })
      toast("Inserted", "success")
    } catch {
      toast("Insert failed", "error")
    }
  }

  const copyText = (text: string) => {
    navigator.clipboard.writeText(text).then(
      () => toast("Copied", "success"),
      () => toast("Copy failed", "error"),
    )
  }

  const startEdit = (id: string, text: string) => {
    setEditingId(id)
    setEditText(text)
  }

  const commitEdit = () => {
    if (editingId) {
      editBlock(editingId, editText)
      setEditingId(null)
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Dictation
        </h3>
        {blocks.length > 0 && (
          <span className="text-[10px] text-gray-500">{blocks.length} blocks</span>
        )}
      </div>

      {/* Toggle + terminal picker */}
      <div className="space-y-2 mb-2">
        <button
          className={`w-full py-2 text-sm font-medium rounded-lg text-white ${
            active ? "bg-red-600" : "bg-green-600"
          } disabled:opacity-50`}
          onClick={toggleDictation}
          disabled={toggling}
        >
          {active ? "Stop Dictating" : "Start Dictating"}
        </button>

        {terminals.length > 0 && (
          <select
            className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none"
            value={targetTerminal}
            onChange={(e) => setTargetTerminal(e.target.value)}
          >
            {terminals.map((t) => (
              <option key={t.id} value={t.id}>
                {t.project} — {t.id.slice(0, 8)}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Blocks */}
      {blocks.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-2">
          {active ? "Listening..." : "Start dictating to capture voice as text"}
        </p>
      ) : (
        <div className="space-y-1.5">
          {blocks.map((b) => (
            <div key={b.id} className="bg-gray-700 rounded p-2 space-y-1">
              {editingId === b.id ? (
                <textarea
                  className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none resize-y min-h-[36px]"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  onBlur={commitEdit}
                  autoFocus
                />
              ) : (
                <div
                  className="text-xs text-gray-200"
                  onClick={() => startEdit(b.id, b.text)}
                >
                  {b.text}
                </div>
              )}
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-gray-500 flex-1">
                  {new Date(b.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
                <button className="px-1.5 py-0.5 text-[10px] rounded bg-blue-600 text-white" onClick={() => copyText(b.text)}>Copy</button>
                <button className="px-1.5 py-0.5 text-[10px] rounded bg-green-600 text-white" onClick={() => insertText(b.text)}>Insert</button>
                <button className="px-1.5 py-0.5 text-[10px] rounded bg-red-600 text-white" onClick={() => removeBlock(b.id)}>Del</button>
              </div>
            </div>
          ))}

          {/* Bulk actions */}
          <div className="flex gap-1 pt-1">
            <button
              className="flex-1 py-1 text-[10px] rounded bg-blue-600 text-white"
              onClick={() => copyText(blocks.map((b) => b.text).join(" "))}
            >
              Copy All
            </button>
            <button
              className="flex-1 py-1 text-[10px] rounded bg-green-600 text-white"
              onClick={() => insertText(blocks.map((b) => b.text).join(" "))}
            >
              Insert All
            </button>
            <button
              className="flex-1 py-1 text-[10px] rounded bg-gray-600 text-white"
              onClick={clearAll}
            >
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
