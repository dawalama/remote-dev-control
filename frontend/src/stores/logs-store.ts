import { create } from "zustand"
import { GET } from "@/lib/api"
import { ManagedWebSocket } from "@/lib/ws"

export interface LogPane {
  id: string
  title: string
  type: "process-log" | "task-output" | "task-log" | "system-log"
  content: string
  sourceId?: string // processId or taskId
  paused: boolean
  ws?: ManagedWebSocket
}

interface LogsStoreState {
  panes: LogPane[]
  activePaneId: string | null
  isOpen: boolean
  height: number
  maximized: boolean

  openProcessLog: (processId: string, processName: string) => void
  openTaskOutput: (taskId: string, taskTitle: string) => void
  openTaskLog: (taskId: string, taskTitle: string) => void
  openSystemLog: () => void
  closePane: (id: string) => void
  setActivePane: (id: string) => void
  togglePause: (id: string) => void
  refreshPane: (id: string) => void
  toggle: () => void
  setHeight: (h: number) => void
  toggleMaximize: () => void
  closeAll: () => void
}

export const useLogsStore = create<LogsStoreState>((set, get) => ({
  panes: [],
  activePaneId: null,
  isOpen: false,
  height: 220,
  maximized: false,

  openProcessLog: (processId, processName) => {
    const existing = get().panes.find(
      (p) => p.type === "process-log" && p.sourceId === processId
    )
    if (existing) {
      set({ activePaneId: existing.id, isOpen: true })
      return
    }

    const id = `process-${processId}`
    const pane: LogPane = {
      id,
      title: processName,
      type: "process-log",
      content: "",
      sourceId: processId,
      paused: false,
    }

    // Connect WS for streaming
    const ws = new ManagedWebSocket(
      `/ws/process-logs/${encodeURIComponent(processId)}`,
      { reconnect: true, reconnectInterval: 3000 }
    )

    ws.onMessage((data) => {
      if (!data || typeof data !== "object") return
      const msg = data as { type?: string; lines?: string[]; line?: string }
      const state = get()
      const currentPane = state.panes.find((p) => p.id === id)
      if (currentPane?.paused) return

      if (msg.type === "initial" && msg.lines) {
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id ? { ...p, content: msg.lines!.join("\n") } : p
          ),
        }))
      } else if (msg.type === "line" && msg.line) {
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id
              ? { ...p, content: p.content + "\n" + msg.line }
              : p
          ),
        }))
      }
    })

    ws.connect()
    pane.ws = ws

    set((s) => ({
      panes: [...s.panes, pane],
      activePaneId: id,
      isOpen: true,
    }))
  },

  openTaskLog: (taskId, taskTitle) => {
    const existing = get().panes.find(
      (p) => p.type === "task-log" && p.sourceId === taskId
    )
    if (existing) {
      set({ activePaneId: existing.id, isOpen: true })
      return
    }

    const id = `task-live-${taskId}`
    const pane: LogPane = {
      id,
      title: `${taskTitle || `Task ${taskId.slice(0, 8)}`}`,
      type: "task-log",
      content: "",
      sourceId: taskId,
      paused: false,
    }

    const ws = new ManagedWebSocket(
      `/ws/task-logs/${encodeURIComponent(taskId)}`,
      { reconnect: true, reconnectInterval: 3000 }
    )

    ws.onMessage((data) => {
      if (!data || typeof data !== "object") return
      const msg = data as { type?: string; lines?: string[]; line?: string; status?: string }
      const state = get()
      const currentPane = state.panes.find((p) => p.id === id)
      if (currentPane?.paused) return

      if (msg.type === "initial" && msg.lines) {
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id ? { ...p, content: msg.lines!.join("\n") } : p
          ),
        }))
      } else if (msg.type === "line" && msg.line) {
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id
              ? { ...p, content: p.content + "\n" + msg.line }
              : p
          ),
        }))
      } else if (msg.type === "completed") {
        const statusLine = `\n--- Task ${msg.status || "finished"} ---`
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id
              ? { ...p, content: p.content + statusLine }
              : p
          ),
        }))
        // Stop reconnecting once task is done
        ws.close()
      }
    })

    ws.connect()
    pane.ws = ws

    set((s) => ({
      panes: [...s.panes, pane],
      activePaneId: id,
      isOpen: true,
    }))
  },

  openTaskOutput: async (taskId, taskTitle) => {
    const existing = get().panes.find(
      (p) => p.type === "task-output" && p.sourceId === taskId
    )
    if (existing) {
      set({ activePaneId: existing.id, isOpen: true })
      return
    }

    const id = `task-${taskId}`
    let content = "Loading..."

    try {
      const result = await GET<{ output?: string; text?: string; result?: string } | string>(
        `/tasks/${taskId}/output`
      )
      if (typeof result === "string") {
        content = result
      } else {
        content = result?.output || result?.text || result?.result || "No output available."
      }
    } catch {
      content = "Failed to load output."
    }

    set((s) => ({
      panes: [
        ...s.panes,
        {
          id,
          title: taskTitle || `Task ${taskId.slice(0, 8)}`,
          type: "task-output",
          content,
          sourceId: taskId,
          paused: false,
        },
      ],
      activePaneId: id,
      isOpen: true,
    }))
  },

  openSystemLog: () => {
    const existing = get().panes.find((p) => p.type === "system-log")
    if (existing) {
      set({ activePaneId: existing.id, isOpen: true })
      return
    }

    const id = "system-logs"
    const pane: LogPane = {
      id,
      title: "System Logs",
      type: "system-log",
      content: "",
      paused: false,
    }

    const ws = new ManagedWebSocket("/ws/logs", {
      reconnect: true,
      reconnectInterval: 3000,
    })

    ws.onMessage((data) => {
      const state = get()
      const currentPane = state.panes.find((p) => p.id === id)
      if (currentPane?.paused) return

      // Backend sends plain text lines (not JSON objects)
      if (typeof data === "string") {
        const line = data.trim()
        if (!line) return
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id
              ? { ...p, content: p.content ? p.content + "\n" + line : line }
              : p
          ),
        }))
        return
      }

      if (!data || typeof data !== "object") return
      const msg = data as { type?: string; lines?: string[]; line?: string }

      if (msg.type === "initial" && msg.lines) {
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id ? { ...p, content: msg.lines!.join("\n") } : p
          ),
        }))
      } else if (msg.type === "line" && msg.line) {
        set((s) => ({
          panes: s.panes.map((p) =>
            p.id === id
              ? { ...p, content: p.content + "\n" + msg.line }
              : p
          ),
        }))
      }
    })

    ws.connect()
    pane.ws = ws

    set((s) => ({
      panes: [...s.panes, pane],
      activePaneId: id,
      isOpen: true,
    }))
  },

  closePane: (id) => {
    const pane = get().panes.find((p) => p.id === id)
    if (pane?.ws) pane.ws.close()

    set((s) => {
      const next = s.panes.filter((p) => p.id !== id)
      const newActive =
        s.activePaneId === id
          ? next.length > 0
            ? next[next.length - 1].id
            : null
          : s.activePaneId
      return {
        panes: next,
        activePaneId: newActive,
        isOpen: next.length > 0 ? s.isOpen : false,
      }
    })
  },

  setActivePane: (id) => set({ activePaneId: id }),

  togglePause: (id) =>
    set((s) => ({
      panes: s.panes.map((p) =>
        p.id === id ? { ...p, paused: !p.paused } : p
      ),
    })),

  refreshPane: (id) => {
    const pane = get().panes.find((p) => p.id === id)
    if (!pane) return
    if (pane.type === "task-output" && pane.sourceId) {
      // Re-fetch
      GET<{ output?: string; text?: string; result?: string } | string>(
        `/tasks/${pane.sourceId}/output`
      ).then((result) => {
        const content =
          typeof result === "string"
            ? result
            : result?.output || result?.text || result?.result || "No output."
        set((s) => ({
          panes: s.panes.map((p) => (p.id === id ? { ...p, content } : p)),
        }))
      })
    }
  },

  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  setHeight: (h) => set({ height: Math.max(150, Math.min(600, h)) }),
  toggleMaximize: () => set((s) => ({ maximized: !s.maximized })),
  closeAll: () => {
    get().panes.forEach((p) => p.ws?.close())
    set({ panes: [], activePaneId: null, isOpen: false })
  },
}))
