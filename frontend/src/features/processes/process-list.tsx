import { useEffect, useCallback } from "react"
import { useProcessStore } from "@/stores/process-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { Button } from "@/components/ui/button"

export function ProcessList() {
  const { processes, loading, actionInProgress, loadProcesses, startProcess, stopProcess, restartProcess, attachProcess } =
    useProcessStore()
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  useEffect(() => {
    loadProcesses()
    const interval = setInterval(loadProcesses, 5000)
    return () => clearInterval(interval)
  }, [loadProcesses])

  const filtered =
    currentProject === "all"
      ? processes
      : processes.filter((p) => p.project === currentProject)

  const sorted = [...filtered].sort((a, b) => {
    if (a.status === "running" && b.status !== "running") return -1
    if (b.status === "running" && a.status !== "running") return 1
    return (a.project || "").localeCompare(b.project || "")
  })

  const handleStart = useCallback((id: string, force = false) => {
    startProcess(id, { force, toast })
  }, [startProcess, toast])

  const handleStop = useCallback((id: string, force = false) => {
    stopProcess(id, { force, toast })
  }, [stopProcess, toast])

  const handleRestart = useCallback((id: string) => {
    restartProcess(id, { toast })
  }, [restartProcess, toast])

  const handleAttach = useCallback((id: string, port?: number) => {
    attachProcess(id, port, { toast })
  }, [attachProcess, toast])

  if (loading && processes.length === 0) {
    return <div className="text-muted-foreground text-sm">Loading...</div>
  }

  if (sorted.length === 0) {
    return (
      <div>
        <div className="text-muted-foreground text-sm mb-2">No processes configured</div>
        <Button variant="default" size="sm" className="text-xs" onClick={loadProcesses}>
          Sync
        </Button>
      </div>
    )
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs text-muted-foreground">Dev servers & services</span>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" className="text-xs h-7" onClick={loadProcesses}>
            Sync
          </Button>
        </div>
      </div>
      {sorted.map((p) => (
        <ProcessItem
          key={p.id}
          process={p}
          isLoading={actionInProgress === p.id}
          onStart={(force) => handleStart(p.id, force)}
          onStop={(force) => handleStop(p.id, force)}
          onRestart={() => handleRestart(p.id)}
          onAttach={() => handleAttach(p.id, p.port || undefined)}
        />
      ))}
    </div>
  )
}

function ProcessItem({
  process: p,
  isLoading,
  onStart,
  onStop,
  onRestart,
  onAttach,
}: {
  process: import("@/types").Action
  isLoading?: boolean
  onStart: (force?: boolean) => void
  onStop: (force?: boolean) => void
  onRestart: () => void
  onAttach?: () => void
}) {
  const isRunning = p.status === "running"
  const isFailed = p.status === "error"
  const statusDot = isRunning
    ? "bg-green-500 animate-pulse"
    : isFailed
      ? "bg-red-500"
      : isLoading
        ? "bg-yellow-500 animate-pulse"
        : "bg-gray-500"
  const statusText = isRunning
    ? "text-green-400"
    : isFailed
      ? "text-red-400"
      : "text-blue-400"
  const borderClass = isFailed ? "border-red-500/50" : "border-border"
  const displayName = p.project ? `${p.project}/${p.name || p.id}` : p.name || p.id

  return (
    <div className={`bg-card rounded-lg p-3 mb-2 border ${borderClass}`}>
      <div className="flex justify-between items-center mb-2">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${statusDot}`} />
          <span className="font-medium text-sm">{displayName}</span>
          {p.port && <span className="text-blue-400 text-xs">:{p.port}</span>}
        </div>
        <span className={`${statusText} text-xs`}>
          {isLoading ? "..." : p.status}
          {p.pid ? ` (${p.pid})` : ""}
        </span>
      </div>
      {p.command && (
        <div className="text-muted-foreground text-xs truncate mb-2 font-mono" title={p.command}>
          {p.command}
        </div>
      )}
      {p.error && (
        <div className="text-red-400 text-xs mb-2 truncate" title={p.error}>
          {p.error}
        </div>
      )}
      <div className="flex gap-1 flex-wrap">
        {isRunning ? (
          <>
            <Button
              variant="destructive"
              size="sm"
              className="text-xs h-6 px-2"
              onClick={() => onStop()}
              disabled={isLoading}
            >
              Stop
            </Button>
            <Button
              size="sm"
              className="text-xs h-6 px-2 bg-yellow-600 hover:bg-yellow-700"
              onClick={onRestart}
              disabled={isLoading}
            >
              Restart
            </Button>
          </>
        ) : (
          <>
            <Button
              size="sm"
              className="text-xs h-6 px-2 bg-green-600 hover:bg-green-700"
              onClick={() => onStart()}
              disabled={isLoading}
            >
              Start
            </Button>
            <Button
              size="sm"
              className="text-xs h-6 px-2 bg-blue-600 hover:bg-blue-700"
              onClick={onAttach}
              disabled={isLoading}
              title={p.port ? "Attach to process on this port" : "Attach to matching running process"}
            >
              Attach
            </Button>
            {isFailed && (
              <Button
                size="sm"
                className="text-xs h-6 px-2 bg-purple-600 hover:bg-purple-700"
                onClick={() => onStart(true)}
                disabled={isLoading}
                title="Force start (kills any process using the port)"
              >
                Force
              </Button>
            )}
          </>
        )}
        <Button variant="secondary" size="sm" className="text-xs h-6 px-2">
          Logs
        </Button>
        {p.port && isRunning && (
          <a
            href={`http://localhost:${p.port}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs bg-blue-600 hover:bg-blue-700 px-2 py-0.5 rounded text-white inline-flex items-center"
          >
            Open
          </a>
        )}
      </div>
    </div>
  )
}
