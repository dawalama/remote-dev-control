import { useState, useEffect } from "react"
import { GET, POST, PATCH, DELETE as DEL } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import type { Collection, Project } from "@/types"

export function CollectionsManagerModal({ onClose }: { onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const loadCollections = useProjectStore((s) => s.loadCollections)
  const loadProjects = useProjectStore((s) => s.loadProjects)

  const [collections, setCollections] = useState<Collection[]>([])
  const [allProjects, setAllProjects] = useState<Project[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState("")
  const [newDesc, setNewDesc] = useState("")
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const [colls, projs] = await Promise.all([
        GET<Collection[]>("/collections"),
        GET<Project[]>("/projects"),
      ])
      setCollections(colls || [])
      setAllProjects(projs || [])
    } catch { /* */ }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!newName.trim()) { toast("Name is required", "error"); return }
    try {
      await POST("/collections", { name: newName.trim(), description: newDesc.trim() || null })
      setNewName("")
      setNewDesc("")
      setShowNew(false)
      await load()
      loadCollections()
      toast("Collection created", "success")
    } catch {
      toast("Failed to create", "error")
    }
  }

  const handleEdit = async (c: Collection) => {
    const name = prompt("Collection name:", c.name)
    if (name === null) return
    const description = prompt("Description:", "")
    if (description === null) return
    try {
      await PATCH(`/collections/${c.id}`, { name: name.trim() || c.name, description: description.trim() || null })
      await load()
      loadCollections()
      toast("Collection updated", "success")
    } catch {
      toast("Failed to update", "error")
    }
  }

  const handleDelete = async (c: Collection) => {
    if (!confirm(`Delete collection "${c.name}"? Projects will be moved to General.`)) return
    try {
      await DEL(`/collections/${c.id}`)
      if (expanded === c.id) setExpanded(null)
      await load()
      loadCollections()
      toast("Collection deleted", "success")
    } catch {
      toast("Failed to delete", "error")
    }
  }

  const handleMove = async (projectName: string, collectionId: string) => {
    if (!collectionId) return
    try {
      await POST(`/projects/${encodeURIComponent(projectName)}/move`, { collection_id: collectionId })
      await load()
      loadCollections()
      loadProjects()
      toast(`Moved "${projectName}"`, "success")
    } catch {
      toast("Failed to move", "error")
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg w-full max-w-lg shadow-xl border border-gray-700 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
          <h3 className="text-sm font-semibold">Collections</h3>
          <div className="flex items-center gap-2">
            <button
              className="px-2 py-1 text-xs rounded bg-green-600 hover:bg-green-700 text-white"
              onClick={() => setShowNew(!showNew)}
            >
              + New
            </button>
            <button className="text-gray-400 hover:text-gray-200" onClick={onClose}>&times;</button>
          </div>
        </div>

        {/* New collection form */}
        {showNew && (
          <div className="px-4 py-3 border-b border-gray-700 flex gap-2 flex-shrink-0">
            <input
              className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Collection name"
              autoFocus
            />
            <input
              className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Description (optional)"
            />
            <button
              className="px-2 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={handleCreate}
            >
              Create
            </button>
          </div>
        )}

        {/* Collections list */}
        <div className="flex-1 min-h-0 overflow-auto p-3">
          {loading ? (
            <p className="text-xs text-gray-500 animate-pulse">Loading...</p>
          ) : collections.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-4">No collections yet.</p>
          ) : (
            <div className="space-y-1">
              {collections.map((c) => {
                const isExpanded = expanded === c.id
                const collProjects = allProjects.filter((p) => p.collection_id === c.id)
                const isGeneral = c.id === "general"

                return (
                  <div key={c.id} className={`rounded-lg ${isExpanded ? "bg-gray-700/50" : ""}`}>
                    {/* Collection row */}
                    <div
                      className="flex items-center justify-between p-2 cursor-pointer hover:bg-gray-700/30 rounded"
                      onClick={() => setExpanded(isExpanded ? null : c.id)}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400">{isExpanded ? "\u25BC" : "\u25B6"}</span>
                        <span className="text-xs font-medium">{c.name}</span>
                        <span className="text-[10px] text-gray-500">
                          {c.project_count || collProjects.length} project{(c.project_count || collProjects.length) !== 1 ? "s" : ""}
                        </span>
                      </div>
                      {!isGeneral && (
                        <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
                          <button
                            className="px-1.5 py-0.5 text-[10px] rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
                            onClick={() => handleEdit(c)}
                          >
                            Edit
                          </button>
                          <button
                            className="px-1.5 py-0.5 text-[10px] rounded bg-red-600 hover:bg-red-700 text-white"
                            onClick={() => handleDelete(c)}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Expanded projects */}
                    {isExpanded && (
                      <div className="border-t border-gray-700 p-2 pl-6">
                        {collProjects.length === 0 ? (
                          <p className="text-[10px] text-gray-500">No projects in this collection</p>
                        ) : (
                          <div className="flex flex-wrap gap-1.5">
                            {collProjects.map((p) => (
                              <div key={p.name} className="flex items-center gap-1 bg-gray-600/50 rounded-full px-2.5 py-1 text-xs">
                                <span>{p.name}</span>
                                <select
                                  className="bg-transparent text-[10px] text-gray-400 outline-none cursor-pointer ml-1"
                                  value=""
                                  onChange={(e) => handleMove(p.name, e.target.value)}
                                >
                                  <option value="">Move to...</option>
                                  {collections.filter((cc) => cc.id !== c.id).map((cc) => (
                                    <option key={cc.id} value={cc.id}>{cc.name}</option>
                                  ))}
                                </select>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
