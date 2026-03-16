import { useState, useEffect } from "react"
import { GET, POST, DELETE as DEL } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"

interface Screenshot {
  id: string
  filename: string
  project?: string
  description?: string
  timestamp: string
}

export function ScreenshotsModal({ onClose }: { onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const currentProject = useProjectStore((s) => s.currentProject)
  const [screenshots, setScreenshots] = useState<Screenshot[]>([])
  const [loading, setLoading] = useState(true)
  const [viewId, setViewId] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (currentProject !== "all") params.set("project", currentProject)
      const data = await GET<Screenshot[]>(`/screenshots?${params}`)
      setScreenshots(data)
    } catch {
      toast("Failed to load screenshots", "error")
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [currentProject])

  const handleCapture = async (fullPage: boolean) => {
    try {
      const body: Record<string, unknown> = { full_page: fullPage }
      if (currentProject !== "all") body.project = currentProject
      await POST("/screenshots", body)
      toast("Screenshot captured", "success")
      load()
    } catch {
      toast("Capture failed", "error")
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await DEL(`/screenshots/${id}`)
      toast("Deleted", "success")
      setScreenshots((prev) => prev.filter((s) => s.id !== id))
    } catch {
      toast("Failed to delete", "error")
    }
  }

  const handleCopyPath = (s: Screenshot) => {
    navigator.clipboard.writeText(`/screenshots/${s.id}/image`).then(
      () => toast("Path copied", "success"),
      () => toast("Copy failed", "error")
    )
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg w-full max-w-2xl max-h-[80vh] flex flex-col shadow-xl border border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h3 className="text-sm font-semibold">Screenshots</h3>
          <div className="flex gap-2 items-center">
            <button
              className="px-2 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={() => handleCapture(false)}
            >
              Capture Viewport
            </button>
            <button
              className="px-2 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={() => handleCapture(true)}
            >
              Full Page
            </button>
            <button className="text-gray-400 hover:text-gray-200 text-lg" onClick={onClose}>
              &times;
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-4">
          {loading && <p className="text-xs text-gray-500 animate-pulse">Loading...</p>}

          {!loading && screenshots.length === 0 && (
            <p className="text-xs text-gray-500 text-center py-8">No screenshots yet</p>
          )}

          <div className="grid grid-cols-2 gap-3">
            {screenshots.map((s) => (
              <div key={s.id} className="bg-gray-700 rounded-lg overflow-hidden">
                <div
                  className="aspect-video bg-gray-900 cursor-pointer relative group"
                  onClick={() => setViewId(s.id)}
                >
                  <img
                    src={`/screenshots/${s.id}/image`}
                    alt={s.description || s.filename}
                    className="w-full h-full object-cover"
                    loading="lazy"
                  />
                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center">
                    <span className="text-white text-sm opacity-0 group-hover:opacity-100 transition-opacity">
                      View
                    </span>
                  </div>
                </div>
                <div className="p-2">
                  <p className="text-xs text-gray-300 truncate">{s.description || s.filename}</p>
                  <p className="text-[10px] text-gray-500">
                    {new Date(s.timestamp).toLocaleString()}
                  </p>
                  <div className="flex gap-1 mt-1">
                    <button
                      className="px-2 py-0.5 text-[10px] rounded bg-gray-600 text-gray-300"
                      onClick={() => handleCopyPath(s)}
                    >
                      Copy Path
                    </button>
                    <button
                      className="px-2 py-0.5 text-[10px] rounded bg-red-600/20 text-red-400"
                      onClick={() => handleDelete(s.id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Fullscreen viewer */}
        {viewId && (
          <div
            className="fixed inset-0 bg-black/90 z-[60] flex items-center justify-center cursor-pointer"
            onClick={() => setViewId(null)}
          >
            <img
              src={`/screenshots/${viewId}/image`}
              alt="Screenshot"
              className="max-w-[90vw] max-h-[90vh] object-contain"
            />
          </div>
        )}
      </div>
    </div>
  )
}
