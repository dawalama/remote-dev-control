import { useState, useEffect, useRef } from "react"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { GET, DELETE as DEL, api } from "@/lib/api"
import { ContextViewerModal } from "@/features/modals/context-viewer"

interface ContextSnapshot {
  id: string
  title?: string
  url?: string
  timestamp?: string
  screenshot_path?: string
}

export function ContextsCard({ defaultExpanded = false }: { defaultExpanded?: boolean } = {}) {
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const [contexts, setContexts] = useState<ContextSnapshot[]>([])
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [viewId, setViewId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Expand when defaultExpanded changes to true (e.g. Ctx button clicked)
  useEffect(() => {
    if (defaultExpanded) setExpanded(true)
  }, [defaultExpanded])

  const load = () => {
    const params = new URLSearchParams({ limit: "20" })
    if (currentProject !== "all") params.set("project", currentProject)
    GET<ContextSnapshot[]>(`/context?${params}`)
      .then(setContexts)
      .catch(() => {})
  }

  useEffect(() => { load() }, [currentProject])

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
      load()
    } catch {
      toast("Upload failed", "error")
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await DEL(`/context/${id}`)
      setContexts((prev) => prev.filter((c) => c.id !== id))
      toast("Deleted", "success")
    } catch {
      toast("Failed", "error")
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <button
        className="flex items-center justify-between w-full mb-2"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Attachments
          </h3>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-700 text-gray-400">
            {contexts.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="*/*"
            className="hidden"
            onChange={handleUpload}
          />
          <button
            className="text-[10px] text-blue-400"
            onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click() }}
          >
            Upload
          </button>
          <button
            className="text-[10px] text-blue-400"
            onClick={(e) => { e.stopPropagation(); load() }}
          >
            Refresh
          </button>
          <span className="text-gray-500 text-xs">{expanded ? "▲" : "▼"}</span>
        </div>
      </button>

      {expanded && (
        <div className="space-y-2">
          {contexts.length === 0 && (
            <p className="text-[10px] text-gray-500 text-center py-2">No attachments</p>
          )}
          {contexts.map((ctx) => (
            <div key={ctx.id} className="flex items-center gap-2">
              <div
                className="w-12 h-8 bg-gray-700 rounded flex-shrink-0 overflow-hidden cursor-pointer"
                onClick={() => setViewId(ctx.id)}
              >
                <img
                  src={`/context/${ctx.id}/screenshot`}
                  alt=""
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              </div>
              <div className="flex-1 min-w-0" onClick={() => setViewId(ctx.id)}>
                <p className="text-xs text-gray-200 truncate">{ctx.title || ctx.id}</p>
                {ctx.url && <p className="text-[10px] text-gray-500 truncate">{ctx.url}</p>}
              </div>
              <button
                className="text-[10px] text-blue-400 hover:text-blue-300 flex-shrink-0"
                onClick={() => {
                  const path = ctx.screenshot_path || `/context/${ctx.id}/screenshot`
                  navigator.clipboard.writeText(path).then(
                    () => toast(`Copied: ${path}`, "success"),
                    () => toast("Copy failed", "error")
                  )
                }}
                title="Copy file path"
              >
                Copy
              </button>
              <button
                className="text-gray-500 hover:text-red-400 text-sm flex-shrink-0"
                onClick={() => handleDelete(ctx.id)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {viewId && (
        <ContextViewerModal
          contextId={viewId}
          onClose={() => setViewId(null)}
          onDeleted={load}
        />
      )}
    </div>
  )
}
