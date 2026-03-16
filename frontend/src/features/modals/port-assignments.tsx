import { useState, useEffect } from "react"
import { GET, POST, DELETE as DEL } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"

interface PortAssignment {
  project: string
  service: string
  port: number
  active?: boolean
}

export function PortAssignmentsModal({ onClose }: { onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const [ports, setPorts] = useState<PortAssignment[]>([])
  const [edits, setEdits] = useState<Record<string, number>>({}) // key: "project/service" → port
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadPorts()
  }, [])

  const loadPorts = async () => {
    setLoading(true)
    try {
      const data = await GET<PortAssignment[]>("/ports")
      setPorts(data)
      const initial: Record<string, number> = {}
      data.forEach((p) => {
        initial[`${p.project}/${p.service}`] = p.port
      })
      setEdits(initial)
    } catch {
      toast("Failed to load ports", "error")
    }
    setLoading(false)
  }

  // Detect conflicts
  const portCounts: Record<number, string[]> = {}
  Object.entries(edits).forEach(([key, port]) => {
    if (!portCounts[port]) portCounts[port] = []
    portCounts[port].push(key)
  })
  const conflicts = Object.entries(portCounts).filter(([, keys]) => keys.length > 1)

  // Group by project
  const byProject: Record<string, PortAssignment[]> = {}
  ports.forEach((p) => {
    if (!byProject[p.project]) byProject[p.project] = []
    byProject[p.project].push(p)
  })

  const handleRemove = async (project: string, service: string) => {
    try {
      await DEL(`/ports/${encodeURIComponent(project)}/${encodeURIComponent(service)}`)
      toast("Port released", "success")
      loadPorts()
    } catch {
      toast("Failed", "error")
    }
  }

  const handleAutoAssign = async () => {
    try {
      await POST("/ports/assign")
      toast("Ports auto-assigned", "success")
      loadPorts()
    } catch {
      toast("Failed", "error")
    }
  }

  const handleSave = async () => {
    try {
      for (const [key, port] of Object.entries(edits)) {
        const [project, service] = key.split("/")
        const orig = ports.find((p) => p.project === project && p.service === service)
        if (orig && orig.port !== port) {
          await POST(`/ports/set`, { project, service, port })
        }
      }
      toast("Ports saved", "success")
      loadPorts()
    } catch {
      toast("Failed to save", "error")
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg w-full max-w-lg max-h-[80vh] flex flex-col shadow-xl border border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h3 className="text-sm font-semibold">Port Assignments</h3>
          <button className="text-gray-400 hover:text-gray-200" onClick={onClose}>&times;</button>
        </div>

        <div className="flex-1 overflow-auto p-4 space-y-3">
          {loading && <p className="text-xs text-gray-500 animate-pulse">Loading...</p>}

          {/* Conflicts */}
          {conflicts.length > 0 && (
            <div className="bg-red-900/30 border border-red-500/50 rounded-lg p-3">
              <h4 className="text-xs font-semibold text-red-400 mb-1">Port Conflicts</h4>
              {conflicts.map(([port, keys]) => (
                <p key={port} className="text-[10px] text-red-300">
                  Port {port}: {keys.join(", ")}
                </p>
              ))}
            </div>
          )}

          {/* Port list by project */}
          {Object.entries(byProject).map(([project, assignments]) => (
            <div key={project}>
              <h4 className="text-xs font-semibold text-gray-400 mb-1">{project}</h4>
              <div className="space-y-1">
                {assignments.map((p) => {
                  const key = `${p.project}/${p.service}`
                  return (
                    <div key={key} className="flex items-center gap-2">
                      <span
                        className={`w-2 h-2 rounded-full flex-shrink-0 ${p.active ? "bg-green-500" : "bg-gray-500"}`}
                      />
                      <span className="text-xs text-gray-300 flex-1 min-w-0 truncate">
                        {p.service}
                      </span>
                      <input
                        type="number"
                        className="w-20 bg-gray-900 border border-gray-600 rounded px-2 py-0.5 text-xs text-gray-200 outline-none focus:border-blue-500"
                        value={edits[key] ?? p.port}
                        onChange={(e) => setEdits((prev) => ({ ...prev, [key]: parseInt(e.target.value) || 0 }))}
                      />
                      <button
                        className="text-gray-500 hover:text-red-400 text-sm"
                        onClick={() => handleRemove(p.project, p.service)}
                      >
                        ×
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}

          {!loading && ports.length === 0 && (
            <p className="text-xs text-gray-500">No port assignments</p>
          )}
        </div>

        <div className="flex justify-between px-4 py-3 border-t border-gray-700">
          <button
            className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
            onClick={handleAutoAssign}
          >
            Auto-assign
          </button>
          <div className="flex gap-2">
            <button
              className="px-3 py-1.5 text-xs rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded bg-green-600 hover:bg-green-700 text-white"
              onClick={handleSave}
            >
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
