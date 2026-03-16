import { create } from "zustand"
import { ManagedWebSocket } from "../lib/ws"
import type { AgentStep } from "../types"

type AgentRunStatus = "idle" | "running" | "completed" | "failed"

interface AgentRunState {
  activeTaskId: string | null
  steps: AgentStep[]
  status: AgentRunStatus

  startWatching: (taskId: string) => void
  stopWatching: () => void
  sendApproval: (approvalId: string, decision: "approve" | "reject", feedback?: string) => void
  cancelRun: () => void
}

let ws: ManagedWebSocket | null = null

export const useAgentRunStore = create<AgentRunState>((set, get) => ({
  activeTaskId: null,
  steps: [],
  status: "idle",

  startWatching: (taskId: string) => {
    // Clean up previous connection
    get().stopWatching()

    set({ activeTaskId: taskId, steps: [], status: "running" })

    ws = new ManagedWebSocket(`/ws/agent/${taskId}`, {
      reconnect: true,
      reconnectInterval: 2000,
    })

    ws.onMessage((data) => {
      if (!data || typeof data !== "object") return
      const msg = data as Record<string, unknown>

      if (msg.type === "ping" || msg.type === "pong") return

      const step = msg as unknown as AgentStep

      set((s) => {
        const newSteps = [...s.steps, step]

        // Update status based on step type
        let newStatus = s.status
        if (step.type === "status") {
          if (step.content === "Completed") newStatus = "completed"
          else if (step.content === "Cancelled") newStatus = "failed"
        } else if (step.type === "error") {
          newStatus = "failed"
        }

        return { steps: newSteps, status: newStatus }
      })
    })

    ws.connect()
  },

  stopWatching: () => {
    if (ws) {
      ws.close()
      ws = null
    }
    set({ activeTaskId: null })
  },

  sendApproval: (approvalId, decision, feedback) => {
    ws?.send({
      type: "approval_response",
      approval_id: approvalId,
      decision,
      feedback: feedback || "",
    })
  },

  cancelRun: () => {
    ws?.send({ type: "cancel" })
    set({ status: "failed" })
  },
}))
