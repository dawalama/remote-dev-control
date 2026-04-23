import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST } from "@/lib/api"
import { AddActionForm } from "@/features/processes/add-action-form"
import type { Action } from "@/types"

export function ProcessesCard({
  onLogs,
}: {
  onLogs?: (processId: string, processName: string) => void
}) {
  const actions = useStateStore((s) => s.actions)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  // Actions are per-project; empty state when no project is selected.
  const filtered = currentProject
    ? actions.filter((p) => p.project === currentProject)
    : []

  const services = filtered
    .filter((p) => (p.kind || "service") === "service")
    .sort((a, b) => (a.status === "running" ? -1 : 1) - (b.status === "running" ? -1 : 1))

  const commands = filtered
    .filter((p) => p.kind === "command")
    .sort((a, b) => (a.status === "running" ? -1 : 1) - (b.status === "running" ? -1 : 1))

  const handleAction = async (processId: string, action: string) => {
    try {
      await POST(`/actions/${processId}/${action}`)
      toast(`Action ${action === "stop" ? "stopped" : `${action}ed`}`, "success")
    } catch {
      toast(`Failed to ${action}`, "error")
    }
  }

  const handleRunCommand = async (id: string, name: string) => {
    try {
      await POST(`/actions/${encodeURIComponent(id)}/start`)
      toast("Running...", "success")
      onLogs?.(id, name)
    } catch { toast("Execute failed", "error") }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-3 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Actions
        </h3>
        {currentProject && (
          <button
            className="text-[10px] text-blue-400"
            onClick={async () => {
              try {
                await POST(`/projects/${encodeURIComponent(currentProject)}/detect-actions?force_rediscover=true`)
                toast("Synced", "success")
              } catch { toast("Sync failed", "error") }
            }}
          >
            Sync
          </button>
        )}
      </div>

      <AddActionForm />

      {/* ── Services ── */}
      {services.length > 0 && (
        <div>
          <h4 className="text-[10px] uppercase text-gray-500 mb-1.5 tracking-wider">Services</h4>
          <div className="space-y-1.5">
            {services.map((p) => (
              <ServiceItem key={p.id} p={p} onAction={handleAction} onLogs={onLogs} toast={toast} />
            ))}
          </div>
        </div>
      )}

      {/* ── Commands ── */}
      {commands.length > 0 && (
        <div>
          <h4 className="text-[10px] uppercase text-gray-500 mb-1.5 tracking-wider">Commands</h4>
          <div className="space-y-1.5">
            {commands.map((p) => (
              <CommandChip key={p.id} p={p} onRun={handleRunCommand} onStop={handleAction} onLogs={onLogs} />
            ))}
          </div>
        </div>
      )}

      {services.length === 0 && commands.length === 0 && (
        <p className="text-[10px] text-gray-500 text-center py-2">No actions</p>
      )}
    </div>
  )
}

/** Service: full card with status, port, start/stop/restart/attach/logs */
function ServiceItem({
  p,
  onAction,
  onLogs,
  toast,
}: {
  p: Action
  onAction: (id: string, action: string) => void
  onLogs?: (id: string, name: string) => void
  toast: (msg: string, type?: "success" | "error" | "warning" | "info") => void
}) {
  const statusDot =
    p.status === "running" ? "bg-green-500" :
    p.status === "error" || p.status === "failed" ? "bg-red-500" :
    "bg-gray-500"

  return (
    <div className={`border border-gray-700 rounded-lg p-2.5 ${p.status === "error" || p.status === "failed" ? "ring-1 ring-red-500/30" : ""}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusDot}`} />
        <span className="text-sm text-gray-200 flex-1 truncate">{p.name || p.id}</span>
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
        {p.status === "running" ? (
          <>
            <button className="px-2 py-0.5 text-[10px] rounded bg-red-600/20 text-red-400"
              onClick={() => onAction(p.id, "stop")}>Stop</button>
            <button className="px-2 py-0.5 text-[10px] rounded bg-yellow-600/20 text-yellow-400"
              onClick={() => onAction(p.id, "restart")}>Restart</button>
          </>
        ) : (
          <>
            <button className="px-2 py-0.5 text-[10px] rounded bg-green-600/20 text-green-400"
              onClick={() => onAction(p.id, "start")}>Start</button>
            <button className="px-2 py-0.5 text-[10px] rounded bg-blue-600/20 text-blue-400"
              onClick={async () => {
                try {
                  const url = p.port
                    ? `/actions/${encodeURIComponent(p.id)}/attach?port=${p.port}`
                    : `/actions/${encodeURIComponent(p.id)}/attach`
                  await POST(url)
                  toast("Attached to running process", "success")
                } catch (err: unknown) {
                  const msg = err instanceof Error ? err.message : "Failed to attach"
                  toast(msg, "error")
                }
              }}>Attach</button>
          </>
        )}
        {onLogs && (
          <button className="px-2 py-0.5 text-[10px] rounded bg-gray-600/20 text-gray-400"
            onClick={() => onLogs(p.id, `${p.project}/${p.name || p.id}`)}>Logs</button>
        )}
        {p.status === "error" && (
          <button className="px-2 py-0.5 text-[10px] rounded bg-purple-600/20 text-purple-400"
            onClick={() => POST(`/actions/${encodeURIComponent(p.id)}/create-fix-task`).then(() => toast("Fix task created", "success")).catch(() => toast("Failed", "error"))}>
            Fix with AI
          </button>
        )}
      </div>
    </div>
  )
}

/** Command: card with run/stop + logs access */
function CommandChip({
  p,
  onRun,
  onStop,
  onLogs,
}: {
  p: Action
  onRun: (id: string, name: string) => void
  onStop: (id: string, action: string) => void
  onLogs?: (id: string, name: string) => void
}) {
  const label = p.name || p.id.split("-").pop() || p.id
  const logName = `${p.project}/${p.name || p.id}`

  const isRunning = p.status === "running"
  const isError = p.status === "error" || p.status === "failed"
  const isDone = p.status === "completed"

  const ringStyle = isRunning ? "ring-1 ring-blue-500/30" : isError ? "ring-1 ring-red-500/30" : ""

  return (
    <div className={`border border-gray-700 rounded-lg p-2.5 ${ringStyle}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
          isRunning ? "bg-blue-400 animate-pulse" :
          isError ? "bg-red-400" :
          isDone ? "bg-green-400" :
          "bg-gray-500"
        }`} />
        <span className="text-sm text-gray-200 flex-1 truncate" title={label}>{label}</span>
      </div>
      {p.command && <p className="text-[10px] text-gray-500 truncate mb-1 ml-4">{p.command}</p>}
      <div className="flex gap-1 ml-4 flex-wrap">
        {isRunning ? (
          <button className="px-2 py-0.5 text-[10px] rounded bg-red-600/20 text-red-400"
            onClick={() => onStop(p.id, "stop")}>Stop</button>
        ) : (
          <button className="px-2 py-0.5 text-[10px] rounded bg-green-600/20 text-green-400"
            onClick={() => onRun(p.id, logName)}>Run</button>
        )}
        {(isRunning || isDone || isError) && onLogs && (
          <button className="px-2 py-0.5 text-[10px] rounded bg-gray-600/20 text-gray-400"
            onClick={() => onLogs(p.id, logName)}>Logs</button>
        )}
      </div>
    </div>
  )
}
