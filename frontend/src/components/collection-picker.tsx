import { useState } from "react"
import { POST } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import type { Collection } from "@/types"

export const DEFAULT_COLLECTION_ID = "general"

export function CollectionPicker({
  value,
  onChange,
  collections,
}: {
  value: string
  onChange: (id: string) => void
  collections: Collection[]
}) {
  const toast = useUIStore((s) => s.toast)
  const loadCollections = useProjectStore((s) => s.loadCollections)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    const name = newName.trim()
    if (!name) { toast("Name is required", "error"); return }
    setSaving(true)
    try {
      const created = await POST<Collection>("/collections", { name })
      setNewName("")
      setCreating(false)
      toast(`Collection "${name}" created`, "success")
      await loadCollections()
      onChange(created.id)
    } catch {
      toast("Failed to create collection", "error")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex gap-1.5 items-center">
      {creating ? (
        <>
          <input
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); handleCreate() }
              if (e.key === "Escape") { setCreating(false); setNewName("") }
            }}
            placeholder="Collection name"
            autoFocus
            disabled={saving}
          />
          <button
            className="px-2 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 shrink-0"
            onClick={handleCreate}
            disabled={saving || !newName.trim()}
          >
            {saving ? "..." : "Add"}
          </button>
          <button
            className="px-1.5 py-1.5 text-xs text-gray-400 hover:text-gray-200 shrink-0"
            onClick={() => { setCreating(false); setNewName("") }}
          >
            &times;
          </button>
        </>
      ) : (
        <>
          <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="input-cls"
          >
            <option value={DEFAULT_COLLECTION_ID}>General</option>
            {collections.filter((c) => c.id !== DEFAULT_COLLECTION_ID).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <button
            type="button"
            className="px-2 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300 shrink-0"
            onClick={() => setCreating(true)}
            title="Create new collection"
          >
            +
          </button>
        </>
      )}
    </div>
  )
}
