import { useEffect, useState, useCallback } from "react"
import { GET, PATCH } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import type { Task } from "@/types"

const statusColors: Record<string, string> = {
  pending: "bg-gray-500",
  running: "bg-blue-500 animate-pulse",
  completed: "bg-green-500",
  failed: "bg-red-500",
  needs_review: "bg-yellow-500 animate-pulse",
}

const statusTextColors: Record<string, string> = {
  pending: "text-gray-400",
  running: "text-blue-400",
  completed: "text-green-400",
  failed: "text-red-400",
  needs_review: "text-yellow-400",
}

export function TaskList() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  const loadTasks = useCallback(() => {
    GET<Task[]>("/tasks")
      .then(setTasks)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    loadTasks()
    const interval = setInterval(loadTasks, 5000)
    return () => clearInterval(interval)
  }, [loadTasks])

  const filtered =
    currentProject === "all"
      ? tasks
      : tasks.filter((t) => t.project === currentProject)

  const handleApprove = async (taskId: string) => {
    await PATCH(`/tasks/${taskId}`, { status: "running" })
    toast("Task approved", "success")
    loadTasks()
  }

  const handleReject = async (taskId: string) => {
    await PATCH(`/tasks/${taskId}`, { status: "failed" })
    toast("Task rejected", "info")
    loadTasks()
  }

  if (loading) {
    return <div className="text-muted-foreground text-sm">Loading...</div>
  }

  if (filtered.length === 0) {
    return <div className="text-muted-foreground text-sm">No tasks.</div>
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs text-muted-foreground">Task queue & history</span>
        <Button variant="secondary" size="sm" className="text-xs h-7" onClick={loadTasks}>
          Refresh
        </Button>
      </div>
      {filtered.map((task) => (
        <div
          key={task.id}
          className={`bg-card rounded-lg p-3 mb-2 border ${task.status === "needs_review" ? "border-yellow-500/50" : "border-border"}`}
        >
          <div className="flex justify-between items-center mb-1">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${statusColors[task.status] || "bg-gray-500"}`} />
              <span className="font-medium text-sm">
                {task.title || task.description?.slice(0, 60)}
              </span>
            </div>
            <span className={`text-xs ${statusTextColors[task.status] || "text-gray-400"}`}>
              {task.status}
            </span>
          </div>
          {task.description && (
            <p className="text-xs text-muted-foreground mb-2 line-clamp-2">{task.description}</p>
          )}
          {task.project && (
            <Badge variant="secondary" className="text-xs mr-2">{task.project}</Badge>
          )}
          {task.status === "needs_review" && (
            <div className="flex gap-1 mt-2">
              <Button
                size="sm"
                className="text-xs h-6 px-2 bg-green-600 hover:bg-green-700"
                onClick={() => handleApprove(task.id)}
              >
                Approve
              </Button>
              <Button
                variant="destructive"
                size="sm"
                className="text-xs h-6 px-2"
                onClick={() => handleReject(task.id)}
              >
                Reject
              </Button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
