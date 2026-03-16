import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useLogsStore } from "@/stores/logs-store"
import { POST, DELETE } from "@/lib/api"
import { CreateTaskForm, useModels, ModelSelector } from "./create-task-form"
import { TaskOutputOverlay } from "@/features/mobile/task-output-overlay"
import type { Task } from "@/types"
import { useState } from "react"

const statusDot: Record<string, string> = {
  pending: "bg-yellow-400",
  running: "bg-blue-400 animate-pulse",
  in_progress: "bg-blue-400 animate-pulse",
  completed: "bg-green-400",
  failed: "bg-red-400",
  needs_review: "bg-orange-400",
  blocked: "bg-orange-400",
  awaiting_review: "bg-orange-400",
}

export function TaskPanel() {
  const tasks = useStateStore((s) => s.tasks)
  const currentProject = useProjectStore((s) => s.currentProject)
  const selectedTaskId = useUIStore((s) => s.selectedTaskId)
  const selectTask = useUIStore((s) => s.selectTask)
  const toast = useUIStore((s) => s.toast)
  const [showAddModal, setShowAddModal] = useState(false)
  const [retryTask, setRetryTask] = useState<Task | null>(null)
  const [continueTask, setContinueTask] = useState<Task | null>(null)
  const [viewOutputTask, setViewOutputTask] = useState<{ id: string; title: string } | null>(null)
  const openTaskLog = useLogsStore((s) => s.openTaskLog)

  const filtered =
    currentProject === "all"
      ? tasks
      : tasks.filter((t) => t.project === currentProject || t.project_id === currentProject)

  const reviewItems = filtered.filter(
    (t) => t.status === "needs_review" || t.status === "awaiting_review"
  )

  const handleApprove = async (taskId: string) => {
    try {
      await POST(`/tasks/${taskId}/review`, { action: "approve" })
      toast("Task approved", "success")
    } catch {
      toast("Failed to approve task", "error")
    }
  }

  const handleReject = async (taskId: string) => {
    const reason = prompt("Reason for rejection:")
    if (reason === null) return
    try {
      await POST(`/tasks/${taskId}/review`, { action: "reject", reason })
      toast("Task rejected", "success")
    } catch {
      toast("Failed to reject task", "error")
    }
  }

  const handleRun = async (taskId: string) => {
    try {
      await POST(`/tasks/${taskId}/run`)
      toast("Task queued", "success")
    } catch {
      toast("Failed to run task", "error")
    }
  }

  const handleCancel = async (taskId: string) => {
    try {
      await POST(`/tasks/${taskId}/cancel`)
      toast("Task cancelled", "success")
    } catch {
      toast("Failed to cancel task", "error")
    }
  }

  const handleRetry = async (taskId: string) => {
    try {
      await POST(`/tasks/${taskId}/retry`)
      toast("Task retried", "success")
    } catch {
      toast("Failed to retry task", "error")
    }
  }

  const handleViewOutput = (taskId: string) => {
    const task = tasks.find((t) => t.id === taskId)
    const label = task?.metadata?.recipe_name as string
      || task?.title
      || task?.description?.slice(0, 30)
      || "Task"
    setViewOutputTask({ id: taskId, title: label })
  }

  const handleFixWithAI = async (task: Task) => {
    try {
      await POST(`/tasks`, {
        project: task.project || task.project_id,
        description: `Fix the following failed task and retry:\n\nOriginal: ${task.description}\n\nError: ${task.output || "Unknown error"}`,
      })
      toast("Fix task created", "success")
    } catch {
      toast("Failed to create fix task", "error")
    }
  }

  const handleDismiss = async (taskId: string) => {
    try {
      await DELETE(`/tasks/${taskId}`)
      toast("Task removed", "success")
    } catch {
      toast("Failed to remove task", "error")
    }
  }

  const handleClearFinished = async () => {
    try {
      const res = await POST<{ deleted: number }>("/tasks/cleanup", {})
      toast(`Cleared ${res.deleted} task(s)`, "success")
    } catch {
      toast("Failed to clear tasks", "error")
    }
  }

  const handleViewLiveLog = (taskId: string) => {
    const task = tasks.find((t) => t.id === taskId)
    const label = task?.metadata?.recipe_name as string
      || task?.title
      || task?.description?.slice(0, 30)
      || "Task"
    openTaskLog(taskId, label)
  }

  return (
    <div>
      {/* Review panel */}
      {reviewItems.length > 0 && (
        <div className="bg-yellow-900/50 rounded-lg p-4 mb-4">
          <h3 className="text-sm font-semibold text-yellow-300 mb-2">
            Pending Review ({reviewItems.length})
          </h3>
          <div className="space-y-2">
            {reviewItems.map((task) => (
              <div key={task.id} className="bg-yellow-800/50 rounded p-2 flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium">
                    {task.metadata?.recipe_id
                      ? ((task.metadata.recipe_name as string) || (task.metadata.recipe_id as string))
                      : (task.title || task.description?.slice(0, 60))
                    }
                  </span>
                  {task.project && (
                    <span className="text-xs text-yellow-400 ml-2">{task.project}</span>
                  )}
                </div>
                <div className="flex gap-1 ml-2 shrink-0">
                  <button
                    className="px-2 py-1 text-xs rounded bg-green-500/10 hover:bg-green-500/20 text-green-400"
                    onClick={() => handleApprove(task.id)}
                  >
                    Approve
                  </button>
                  <button
                    className="px-2 py-1 text-xs rounded bg-red-500/10 hover:bg-red-500/20 text-red-400"
                    onClick={() => handleReject(task.id)}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tasks panel */}
      <div className="bg-gray-800 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-lg font-semibold">Tasks</h2>
          <div className="flex gap-2">
            {filtered.some((t) => ["completed", "failed", "cancelled"].includes(t.status)) && (
              <button
                className="px-3 py-1 text-sm rounded bg-gray-600 hover:bg-gray-500 text-gray-300"
                onClick={handleClearFinished}
              >
                Clear finished
              </button>
            )}
            <button
              className="px-3 py-1 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={() => setShowAddModal(true)}
            >
              + Add Task
            </button>
          </div>
        </div>

        <div className="space-y-1.5 max-h-96 overflow-y-auto">
          {filtered.length === 0 && (
            <p className="text-gray-500 text-sm">No tasks yet.</p>
          )}
          {filtered.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              selected={selectedTaskId === task.id}
              showProject={currentProject === "all"}
              onSelect={() => selectTask(task.id === selectedTaskId ? null : task.id)}
              onRun={() => handleRun(task.id)}
              onCancel={() => handleCancel(task.id)}
              onRetry={() => handleRetry(task.id)}
              onViewOutput={() => handleViewOutput(task.id)}
              onViewLiveLog={() => handleViewLiveLog(task.id)}
              onDismiss={() => handleDismiss(task.id)}
              onFixWithAI={() => handleFixWithAI(task)}
              onEditRetry={() => setRetryTask(task)}
              onContinue={() => setContinueTask(task)}
            />
          ))}
        </div>
      </div>

      {/* Add Task Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowAddModal(false)}>
          <div
            className="bg-gray-800 rounded-lg p-6 w-full max-w-md shadow-xl border border-gray-700"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold mb-4">Add Task</h3>
            <CreateTaskForm onClose={() => setShowAddModal(false)} />
          </div>
        </div>
      )}
      {retryTask && <RetryTaskModal task={retryTask} onClose={() => setRetryTask(null)} />}
      {continueTask && <ContinueTaskModal task={continueTask} onClose={() => setContinueTask(null)} />}
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

function TaskCard({
  task,
  selected,
  showProject,
  onSelect,
  onRun,
  onCancel,
  onRetry,
  onViewOutput,
  onViewLiveLog,
  onDismiss,
  onFixWithAI,
  onEditRetry,
  onContinue,
}: {
  task: Task
  selected: boolean
  showProject: boolean
  onSelect: () => void
  onRun: () => void
  onCancel: () => void
  onRetry: () => void
  onViewOutput: () => void
  onViewLiveLog: () => void
  onDismiss: () => void
  onFixWithAI: () => void
  onEditRetry: () => void
  onContinue: () => void
}) {
  const dot = statusDot[task.status] || "bg-gray-400"

  const statusPill: Record<string, { label: string; color: string }> = {
    running: { label: "running", color: "blue" },
    in_progress: { label: "running", color: "blue" },
    failed: { label: "failed", color: "red" },
    needs_review: { label: "review", color: "orange" },
    awaiting_review: { label: "review", color: "orange" },
    blocked: { label: "blocked", color: "orange" },
  }
  const pill = statusPill[task.status]

  return (
    <div
      className={`rounded p-2 cursor-pointer transition-colors ${
        selected
          ? "border-l-2 border-blue-400 bg-gray-700/80"
          : "bg-gray-700/50 hover:bg-gray-700"
      }`}
      onClick={onSelect}
    >
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
        <span className="text-sm font-medium flex-1 min-w-0 truncate">
          {showProject && task.project && (
            <span className="text-gray-400 mr-1">{task.project}</span>
          )}
          {typeof task.metadata?.recipe_id === "string" && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/60 text-purple-200 mr-1">
              {(task.metadata.recipe_name as string) || task.metadata.recipe_id}
            </span>
          )}
          {task.metadata?.recipe_id
            ? (task.title || (task.metadata.recipe_name as string) || task.metadata.recipe_id as string)
            : (task.title || task.description?.slice(0, 60))
          }
        </span>
        {pill && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap bg-${pill.color}-400/10 text-${pill.color}-400`}>
            {pill.label}
          </span>
        )}
      </div>
      {!task.metadata?.recipe_id && task.description && (
        <p className="text-xs text-gray-400 mt-1 line-clamp-1">
          {task.description}
        </p>
      )}

      {/* Action buttons */}
      {selected && (
        <div className="flex gap-1 mt-2 flex-wrap" onClick={(e) => e.stopPropagation()}>
          {task.status === "pending" && (
            <button
              className="px-2 py-1 text-xs rounded bg-green-500/10 hover:bg-green-500/20 text-green-400"
              onClick={onRun}
            >
              Run
            </button>
          )}
          {(task.status === "running" || task.status === "in_progress" || task.status === "pending") && (
            <button
              className="px-2 py-1 text-xs rounded bg-red-500/10 hover:bg-red-500/20 text-red-400"
              onClick={onCancel}
            >
              Cancel
            </button>
          )}
          {task.status === "failed" && (
            <>
              <button
                className="px-2 py-1 text-xs rounded bg-yellow-500/10 hover:bg-yellow-500/20 text-yellow-400"
                onClick={onRetry}
              >
                Retry
              </button>
              <button
                className="px-2 py-1 text-xs rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-400"
                onClick={onEditRetry}
              >
                Edit & Retry
              </button>
              <button
                className="px-2 py-1 text-xs rounded bg-purple-500/10 hover:bg-purple-500/20 text-purple-400"
                onClick={onFixWithAI}
              >
                Fix with AI
              </button>
            </>
          )}
          {task.status === "completed" && (
            <>
              <button
                className="px-2 py-1 text-xs rounded bg-blue-500/10 hover:bg-blue-500/20 text-blue-400"
                onClick={onViewOutput}
              >
                View Output
              </button>
              <button
                className="px-2 py-1 text-xs rounded bg-green-500/10 hover:bg-green-500/20 text-green-400"
                onClick={onContinue}
              >
                Continue
              </button>
            </>
          )}
          {(task.status === "running" || task.status === "in_progress") && (
            <button
              className="px-2 py-1 text-xs rounded bg-gray-500/10 hover:bg-gray-500/20 text-gray-400"
              onClick={onViewLiveLog}
            >
              Live Log
            </button>
          )}
          {["completed", "failed", "cancelled"].includes(task.status) && (
            <button
              className="px-2 py-1 text-xs rounded bg-red-500/10 hover:bg-red-500/20 text-red-400 ml-auto"
              onClick={onDismiss}
            >
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function RetryTaskModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const { models } = useModels()
  const [description, setDescription] = useState(task.description)
  const [model, setModel] = useState((task.metadata?.model as string) || "")
  const [submitting, setSubmitting] = useState(false)

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
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg p-6 w-full max-w-md shadow-xl border border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold mb-4">Edit & Retry Task</h3>

        {task.output && (
          <div className="mb-3 p-2 bg-red-900/30 rounded text-xs text-red-300 max-h-24 overflow-y-auto">
            <span className="font-medium">Error: </span>
            {task.output}
          </div>
        )}

        <div className="mb-3">
          <label className="block text-sm text-gray-400 mb-1">Description</label>
          <textarea
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 min-h-[100px] resize-y"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            autoFocus
          />
        </div>

        <ModelSelector
          models={models}
          value={model}
          onChange={setModel}
          className="mb-3"
        />

        <div className="flex justify-end gap-2">
          <button
            className="px-3 py-1.5 text-sm rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="px-3 py-1.5 text-sm rounded bg-yellow-600 hover:bg-yellow-700 text-white disabled:opacity-50"
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

function ContinueTaskModal({ task, onClose }: { task: Task; onClose: () => void }) {
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
      toast("Failed to create follow-up task", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg p-6 w-full max-w-md shadow-xl border border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold mb-4">Continue Task</h3>

        <div className="mb-3 p-2 bg-gray-700/50 rounded text-xs text-gray-300">
          <span className="font-medium">Previous: </span>
          {task.title || task.description?.slice(0, 100)}
        </div>

        <div className="mb-3">
          <label className="block text-sm text-gray-400 mb-1">Follow-up instructions</label>
          <textarea
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 min-h-[100px] resize-y"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What should happen next..."
            autoFocus
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer mb-4">
          <input
            type="checkbox"
            checked={includeOutput}
            onChange={(e) => setIncludeOutput(e.target.checked)}
            className="rounded border-gray-600"
          />
          Include previous output as context
        </label>

        <div className="flex justify-end gap-2">
          <button
            className="px-3 py-1.5 text-sm rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="px-3 py-1.5 text-sm rounded bg-green-600 hover:bg-green-700 text-white disabled:opacity-50"
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

