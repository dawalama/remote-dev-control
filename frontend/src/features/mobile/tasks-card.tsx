import { useState } from "react"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST, DELETE } from "@/lib/api"
import { TaskOutputOverlay } from "./task-output-overlay"
import { useModels, ModelSelector } from "@/features/tasks/create-task-form"
import type { Task } from "@/types"

const MAX_VISIBLE = 5

const statusOrder: Record<string, number> = {
  in_progress: 0,
  running: 0,
  blocked: 1,
  needs_review: 2,
  awaiting_review: 2,
  pending: 3,
  failed: 4,
  completed: 5,
}

export function TasksCard() {
  const tasks = useStateStore((s) => s.tasks)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const [expanded, setExpanded] = useState(true)
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null)
  const [retryTask, setRetryTask] = useState<Task | null>(null)
  const [continueTask, setContinueTask] = useState<Task | null>(null)
  const [viewOutputTask, setViewOutputTask] = useState<{ id: string; title: string } | null>(null)

  const filtered = (
    currentProject === "all"
      ? tasks
      : tasks.filter((t) => t.project === currentProject || t.project_id === currentProject)
  ).sort((a, b) => (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9))

  // Auto-expand if active tasks
  const hasActive = filtered.some((t) => t.status === "in_progress" || t.status === "running" || t.status === "blocked")

  const visible = expanded ? filtered.slice(0, MAX_VISIBLE) : []
  const remaining = filtered.length - MAX_VISIBLE

  const statusColor: Record<string, string> = {
    pending: "bg-gray-500",
    running: "bg-blue-500",
    in_progress: "bg-blue-500",
    completed: "bg-green-500",
    failed: "bg-red-500",
    blocked: "bg-yellow-500",
    needs_review: "bg-purple-500",
    awaiting_review: "bg-purple-500",
  }

  const handleAction = async (taskId: string, action: string) => {
    try {
      await POST(`/tasks/${taskId}/${action}`)
      toast(`Task ${action}`, "success")
    } catch {
      toast("Failed", "error")
    }
  }
  const handleDismiss = async (taskId: string) => {
    try { await DELETE(`/tasks/${taskId}`) } catch { toast("Failed to delete", "error") }
  }



  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <button
        className="flex items-center justify-between w-full mb-2"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Tasks
          </h3>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-700 text-gray-400">
            {filtered.length}
          </span>
        </div>
        <span className="text-gray-500 text-xs">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {(expanded || hasActive) && (
        <div className="space-y-1.5">
          {filtered.length === 0 && (
            <p className="text-[10px] text-gray-500 text-center py-2">No tasks</p>
          )}
          {visible.map((t) => (
            <div key={t.id}>
              <button
                className="w-full flex items-center gap-2 py-1.5 px-2 rounded hover:bg-gray-700 text-left"
                onClick={() =>
                  setExpandedTaskId(expandedTaskId === t.id ? null : t.id)
                }
              >
                <span
                  className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor[t.status] || "bg-gray-500"}`}
                />
                <span className="text-sm text-gray-200 flex-1 truncate">
                  {typeof t.metadata?.recipe_id === "string" && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/60 text-purple-200 mr-1">
                      {(t.metadata.recipe_name as string) || t.metadata.recipe_id}
                    </span>
                  )}
                  {t.metadata?.recipe_id
                    ? (t.title || (t.metadata.recipe_name as string) || t.metadata.recipe_id as string)
                    : (t.title || t.description?.slice(0, 50))
                  }
                </span>
                <span className="text-[10px] text-gray-500">{t.status}</span>
              </button>

              {/* Expanded detail */}
              {expandedTaskId === t.id && (
                <div className="ml-6 mt-1 mb-2 space-y-2">
                  <p className="text-xs text-gray-400">
                    {t.description && t.description.length > 150
                      ? t.description.slice(0, 150) + "..."
                      : t.description}
                  </p>
                  {t.project && (
                    <p className="text-[10px] text-gray-500">
                      Project: {t.project}
                    </p>
                  )}
                  <div className="flex gap-1 flex-wrap">
                    {t.status === "pending" && (
                      <>
                        <Btn color="green" onClick={() => handleAction(t.id, "run")}>Run</Btn>
                        <Btn color="red" onClick={() => handleAction(t.id, "cancel")}>Cancel</Btn>
                      </>
                    )}
                    {(t.status === "running" || t.status === "in_progress") && (
                      <>
                        <Btn color="red" onClick={() => handleAction(t.id, "cancel")}>Stop</Btn>
                        <Btn color="blue" onClick={() => setViewOutputTask({
                          id: t.id,
                          title: (t.metadata?.recipe_name as string) || t.title || t.description?.slice(0, 30) || "Task"
                        })}>View Output</Btn>
                      </>
                    )}
                    {t.status === "failed" && (
                      <>
                        <Btn color="blue" onClick={() => setViewOutputTask({
                          id: t.id,
                          title: (t.metadata?.recipe_name as string) || t.title || t.description?.slice(0, 30) || "Task"
                        })}>View Output</Btn>
                        <Btn color="yellow" onClick={() => handleAction(t.id, "retry")}>Retry</Btn>
                        <Btn color="yellow" onClick={() => setRetryTask(t)}>Edit & Retry</Btn>
                        <Btn color="red" onClick={() => handleDismiss(t.id)}>Delete</Btn>
                      </>
                    )}
                    {t.status === "completed" && (
                      <>
                        <Btn color="blue" onClick={() => setViewOutputTask({
                          id: t.id,
                          title: (t.metadata?.recipe_name as string) || t.title || t.description?.slice(0, 30) || "Task"
                        })}>View Output</Btn>
                        <Btn color="green" onClick={() => setContinueTask(t)}>Continue</Btn>
                        <Btn color="red" onClick={() => handleDismiss(t.id)}>Delete</Btn>
                      </>
                    )}
                    {(t.status === "needs_review" || t.status === "awaiting_review") && (
                      <>
                        <Btn color="green" onClick={() => POST(`/tasks/${t.id}/review`, { action: "approve" })}>Approve</Btn>
                        <Btn color="red" onClick={() => POST(`/tasks/${t.id}/review`, { action: "reject", reason: "rejected" })}>Reject</Btn>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
          {remaining > 0 && expanded && (
            <p className="text-[10px] text-gray-500 text-center py-1">
              +{remaining} more
            </p>
          )}
        </div>
      )}
      {retryTask && <MobileRetryModal task={retryTask} onClose={() => setRetryTask(null)} />}
      {continueTask && <MobileContinueModal task={continueTask} onClose={() => setContinueTask(null)} />}
      {viewOutputTask && (
        <TaskOutputOverlay
          taskId={viewOutputTask.id}
          taskTitle={viewOutputTask.title}
          onClose={() => setViewOutputTask(null)}
        />
      )}
    </div>
  )
}

function MobileRetryModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const { models } = useModels()
  const [description, setDescription] = useState(task.description)
  const [submitting, setSubmitting] = useState(false)

  const [model, setModel] = useState((task.metadata?.model as string) || "")

  const handleSubmit = async () => {
    if (!description.trim()) return
    setSubmitting(true)
    try {
      await POST("/tasks", {
        project: task.project || task.project_id,
        description: description.trim(),
        model: model || undefined,
      })
      toast("Retry task created", "success")
      onClose()
    } catch {
      toast("Failed to create retry task", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-end z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-t-xl p-4 w-full max-h-[70vh] overflow-y-auto border-t border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold mb-3">Edit & Retry</h3>
        {task.output && (
          <div className="mb-2 p-2 bg-red-900/30 rounded text-[10px] text-red-300 max-h-16 overflow-y-auto">
            {task.output}
          </div>
        )}
        <textarea
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-xs text-gray-200 outline-none focus:border-blue-500 min-h-[80px] resize-y mb-2"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          autoFocus
        />
        <ModelSelector models={models} value={model} onChange={setModel} className="mb-3" />
        <div className="flex gap-2">
          <button className="flex-1 py-2 text-xs rounded bg-gray-700 text-gray-300" onClick={onClose}>Cancel</button>
          <button
            className="flex-1 py-2 text-xs rounded bg-yellow-600 text-white disabled:opacity-50"
            onClick={handleSubmit}
            disabled={submitting || !description.trim()}
          >
            {submitting ? "Creating..." : "Retry"}
          </button>
        </div>
      </div>
    </div>
  )
}

function MobileContinueModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const [description, setDescription] = useState("")
  const [includeOutput, setIncludeOutput] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!description.trim()) return
    setSubmitting(true)
    try {
      await POST("/tasks", {
        project: task.project || task.project_id,
        description: description.trim(),
        parent_task_id: task.id,
        include_parent_output: includeOutput,
      })
      toast("Follow-up task created", "success")
      onClose()
    } catch {
      toast("Failed to create follow-up", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-end z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-t-xl p-4 w-full max-h-[70vh] overflow-y-auto border-t border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold mb-3">Continue Task</h3>
        <div className="mb-2 p-2 bg-gray-700/50 rounded text-[10px] text-gray-300">
          Previous: {task.title || task.description?.slice(0, 80)}
        </div>
        <textarea
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-xs text-gray-200 outline-none focus:border-blue-500 min-h-[80px] resize-y mb-2"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Follow-up instructions..."
          autoFocus
        />
        <label className="flex items-center gap-2 text-[10px] text-gray-300 cursor-pointer mb-3">
          <input
            type="checkbox"
            checked={includeOutput}
            onChange={(e) => setIncludeOutput(e.target.checked)}
            className="rounded border-gray-600"
          />
          Include previous output as context
        </label>
        <div className="flex gap-2">
          <button className="flex-1 py-2 text-xs rounded bg-gray-700 text-gray-300" onClick={onClose}>Cancel</button>
          <button
            className="flex-1 py-2 text-xs rounded bg-green-600 text-white disabled:opacity-50"
            onClick={handleSubmit}
            disabled={submitting || !description.trim()}
          >
            {submitting ? "Creating..." : "Continue"}
          </button>
        </div>
      </div>
    </div>
  )
}

function Btn({
  children,
  color,
  onClick,
}: {
  children: React.ReactNode
  color: string
  onClick: () => void
}) {
  const colors: Record<string, string> = {
    green: "bg-green-600/20 text-green-400",
    red: "bg-red-600/20 text-red-400",
    yellow: "bg-yellow-600/20 text-yellow-400",
    blue: "bg-blue-600/20 text-blue-400",
  }
  return (
    <button
      className={`px-2 py-0.5 text-[10px] rounded ${colors[color] || colors.blue}`}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
