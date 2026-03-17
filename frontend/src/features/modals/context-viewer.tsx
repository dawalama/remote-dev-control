import { useState, useEffect } from "react"
import { GET, DELETE as DEL } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"

interface ContextDetail {
  id: string
  title?: string
  url?: string
  timestamp?: string
  screenshot_path?: string
  a11y_tree?: A11yNode[]
}

interface A11yNode {
  role: string
  name?: string
  children?: A11yNode[]
}

export function ContextViewerModal({
  contextId,
  onClose,
  onDeleted,
}: {
  contextId: string
  onClose: () => void
  onDeleted?: () => void
}) {
  const toast = useUIStore((s) => s.toast)
  const [detail, setDetail] = useState<ContextDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    GET<ContextDetail>(`/context/${contextId}`)
      .then(setDetail)
      .catch(() => toast("Failed to load context", "error"))
      .finally(() => setLoading(false))
  }, [contextId, toast])

  const handleDelete = async () => {
    try {
      await DEL(`/context/${contextId}`)
      toast("Context deleted", "success")
      onDeleted?.()
      onClose()
    } catch {
      toast("Failed to delete", "error")
    }
  }

  const screenshotUrl = `/context/${contextId}/screenshot`

  return (
    <div className="fixed inset-0 bg-black/80 z-[120] flex" onClick={onClose}>
      <div
        className="flex-1 flex bg-gray-900 m-4 rounded-lg overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-sm text-gray-500 animate-pulse">Loading...</p>
          </div>
        ) : (
          <>
            {/* Left: screenshot */}
            <div className="flex-1 min-w-0 bg-black flex items-center justify-center overflow-auto p-4">
              <img
                src={screenshotUrl}
                alt="Context screenshot"
                className="max-w-full max-h-full object-contain"
              />
            </div>

            {/* Right: metadata + a11y tree */}
            <div className="w-80 bg-gray-800 border-l border-gray-700 flex flex-col">
              {/* Header */}
              <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
                <span className="text-sm font-medium text-gray-200">Context</span>
                <button className="text-gray-400 hover:text-gray-200" onClick={onClose}>
                  &times;
                </button>
              </div>

              {/* Metadata */}
              <div className="p-3 border-b border-gray-700 space-y-1">
                {detail?.title && (
                  <div>
                    <span className="text-[10px] text-gray-500">Title</span>
                    <p className="text-xs text-gray-200">{detail.title}</p>
                  </div>
                )}
                {detail?.url && (
                  <div>
                    <span className="text-[10px] text-gray-500">URL</span>
                    <p className="text-xs text-blue-400 truncate">{detail.url}</p>
                  </div>
                )}
                {detail?.timestamp && (
                  <div>
                    <span className="text-[10px] text-gray-500">Captured</span>
                    <p className="text-xs text-gray-300">
                      {new Date(detail.timestamp).toLocaleString()}
                    </p>
                  </div>
                )}
              </div>

              {/* A11y tree */}
              <div className="flex-1 overflow-auto p-3">
                <h4 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">
                  Accessibility Tree
                </h4>
                {detail?.a11y_tree && detail.a11y_tree.length > 0 ? (
                  <div className="space-y-0.5">
                    {renderA11yTree(detail.a11y_tree, 0)}
                  </div>
                ) : (
                  <p className="text-xs text-gray-500">No tree data</p>
                )}
              </div>

              {/* Actions */}
              <div className="p-3 border-t border-gray-700 flex gap-2">
                <button
                  className="flex-1 px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
                  onClick={() => {
                    const path = detail?.screenshot_path || `/context/${contextId}/screenshot`
                    navigator.clipboard.writeText(path).then(
                      () => toast(`Copied: ${path}`, "success"),
                      () => toast("Copy failed", "error")
                    )
                  }}
                >
                  Copy Path
                </button>
                <button
                  className="px-3 py-1.5 text-xs rounded bg-red-600 hover:bg-red-700 text-white"
                  onClick={handleDelete}
                >
                  Delete
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function renderA11yTree(nodes: A11yNode[], depth: number): React.ReactNode[] {
  return nodes.flatMap((node, i) => {
    const items: React.ReactNode[] = [
      <div
        key={`${depth}-${i}`}
        className="text-[10px] text-gray-300"
        style={{ paddingLeft: `${depth * 12}px` }}
      >
        <span className="text-purple-400">{node.role}</span>
        {node.name && <span className="text-gray-400 ml-1">"{node.name}"</span>}
      </div>,
    ]
    if (node.children) {
      items.push(...renderA11yTree(node.children, depth + 1))
    }
    return items
  })
}
