import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST } from "@/lib/api"

export function ProcessesCard({
  onLogs,
}: {
  onLogs?: (processId: string, processName: string) => void
}) {
  const actions = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  const filtered = (currentProject === "all"
    ? actions
    : actions.filter((p) => p.project === currentProject))
    .slice()
    .sort((a, b) => {
      if (a.kind !== b.kind) return (a.kind || "service") === "service" ? -1 : 1
      return (a.status === "running" ? -1 : 1) - (b.status === "running" ? -1 : 1)
    })

  const handleAction = async (processId: string, action: string) => {
    try {
      await POST(`/processes/${processId}/${action}`)
      toast(`Process ${action}ed`, "success")
    } catch {
      toast(`Failed to ${action}`, "error")
    }
  }

  const handleExecute = async (id: string) => {
    try {
      await POST(`/actions/${encodeURIComponent(id)}/execute`)
      toast("Running...", "success")
    } catch { toast("Execute failed", "error") }
  }

  const statusDot = (s: string) => {
    if (s === "running") return "bg-green-500"
    if (s === "completed") return "bg-blue-500"
    if (s === "error" || s === "failed") return "bg-red-500"
    return "bg-gray-500"
  }

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Actions
        </h3>
        {currentProject !== "all" && (
          <button
            className="text-[10px] text-blue-400"
            onClick={async () => {
              try {
                await POST(`/projects/${encodeURIComponent(currentProject)}/detect-processes?force_rediscover=true`)
                toast("Synced", "success")
              } catch { toast("Sync failed", "error") }
            }}
          >
            Sync
          </button>
        )}
      </div>
      <div className="space-y-2">
        {filtered.length === 0 && (
          <p className="text-[10px] text-gray-500 text-center py-2">No actions</p>
        )}
        {filtered.map((p) => (
          <div key={p.id} className={`border border-gray-700 rounded-lg p-2.5 ${p.status === "error" || p.status === "failed" ? "ring-1 ring-red-500/30" : ""}`}>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusDot(p.status)}`} />
              <span className="text-sm text-gray-200 flex-1 truncate">{p.project}/{p.name || p.id}</span>
              {p.kind === "command" && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-400">cmd</span>
              )}
              {p.port && (
                <a href={`http://localhost:${p.port}`} target="_blank" rel="noopener noreferrer"
                  className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-blue-400 hover:text-blue-300">
                  :{p.port}
                </a>
              )}
            </div>
            {p.command && (
              <p className="text-[10px] text-gray-500 truncate mb-1 ml-4">{p.command}</p>
            )}
            {p.preview_url && p.status === "running" && (
              <a href={p.preview_url} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1 ml-4 mb-1 text-[10px] text-green-400 hover:text-green-300 truncate">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                {p.preview_url.replace("https://", "")}
              </a>
            )}
            <div className="flex gap-1 ml-4 flex-wrap">
              {p.kind === "command" ? (
                /* Command buttons */
                p.status === "running" ? (
                  <button className="px-2 py-0.5 text-[10px] rounded bg-red-600/20 text-red-400"
                    onClick={() => handleAction(p.id, "stop")}>Stop</button>
                ) : (
                  <button className="px-2 py-0.5 text-[10px] rounded bg-green-600/20 text-green-400"
                    onClick={() => handleExecute(p.id)}>
                    {p.status === "completed" || p.status === "failed" ? "Re-run" : "Run"}
                  </button>
                )
              ) : (
                /* Service buttons */
                p.status === "running" ? (
                  <>
                    <button className="px-2 py-0.5 text-[10px] rounded bg-red-600/20 text-red-400"
                      onClick={() => handleAction(p.id, "stop")}>Stop</button>
                    <button className="px-2 py-0.5 text-[10px] rounded bg-yellow-600/20 text-yellow-400"
                      onClick={() => handleAction(p.id, "restart")}>Restart</button>
                  </>
                ) : (
                  <>
                    <button className="px-2 py-0.5 text-[10px] rounded bg-green-600/20 text-green-400"
                      onClick={() => handleAction(p.id, "start")}>Start</button>
                    {p.port && (
                      <button className="px-2 py-0.5 text-[10px] rounded bg-blue-600/20 text-blue-400"
                        onClick={async () => {
                          try {
                            await POST(`/processes/${encodeURIComponent(p.id)}/attach?port=${p.port}`)
                            toast("Attached to running process", "success")
                          } catch (err: unknown) {
                            const msg = err instanceof Error ? err.message : "Failed to attach"
                            toast(msg, "error")
                          }
                        }}>Attach</button>
                    )}
                  </>
                )
              )}
              {onLogs && (
                <button className="px-2 py-0.5 text-[10px] rounded bg-gray-600/20 text-gray-400"
                  onClick={() => onLogs(p.id, `${p.project}/${p.name || p.id}`)}>Logs</button>
              )}
              {p.status === "error" && (p.kind || "service") === "service" && (
                <button className="px-2 py-0.5 text-[10px] rounded bg-purple-600/20 text-purple-400"
                  onClick={() => POST(`/processes/${encodeURIComponent(p.id)}/create-fix-task`).then(() => toast("Fix task created", "success")).catch(() => toast("Failed", "error"))}>
                  Fix with AI
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
