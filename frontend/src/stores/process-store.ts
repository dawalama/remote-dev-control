import { create } from "zustand"
import { GET, POST } from "@/lib/api"
import type { Action } from "@/types"

type ToastFn = (msg: string, type?: "success" | "error" | "warning" | "info") => void

interface ProcessState {
  processes: Action[]
  loading: boolean
  actionInProgress: string | null

  loadProcesses: () => Promise<void>
  startProcess: (id: string, options?: { force?: boolean; toast?: ToastFn }) => Promise<boolean>
  stopProcess: (id: string, options?: { force?: boolean; toast?: ToastFn }) => Promise<boolean>
  restartProcess: (id: string, options?: { toast?: ToastFn }) => Promise<boolean>
  attachProcess: (id: string, port: number, options?: { toast?: ToastFn }) => Promise<boolean>
}

function extractErrorMessage(err: unknown): string {
  if (err instanceof Error) {
    const match = err.message.match(/API \d+: (.+)/)
    if (match) return match[1]
    return err.message
  }
  return String(err)
}

export const useProcessStore = create<ProcessState>((set, get) => ({
  processes: [],
  loading: false,
  actionInProgress: null,

  loadProcesses: async () => {
    set({ loading: true })
    try {
      const data = await GET<Action[]>("/actions")
      set({ processes: data })
    } finally {
      set({ loading: false })
    }
  },

  startProcess: async (id: string, options?: { force?: boolean; toast?: ToastFn }) => {
    const { actionInProgress } = get()
    if (actionInProgress) return false

    set({ actionInProgress: id })
    try {
      const query = options?.force ? "?force=true" : ""
      await POST(`/actions/${encodeURIComponent(id)}/start${query}`)
      options?.toast?.("Process started", "success")
      await get().loadProcesses()
      return true
    } catch (err) {
      const msg = extractErrorMessage(err)
      if (msg.includes("already running")) {
        options?.toast?.("Process is already running", "warning")
      } else if (msg.includes("port") || msg.includes("Address already in use")) {
        options?.toast?.(`Port conflict — try force start. ${msg}`, "error")
      } else {
        options?.toast?.(`Failed to start: ${msg}`, "error")
      }
      await get().loadProcesses()
      return false
    } finally {
      set({ actionInProgress: null })
    }
  },

  stopProcess: async (id: string, options?: { force?: boolean; toast?: ToastFn }) => {
    const { actionInProgress } = get()
    if (actionInProgress) return false

    set({ actionInProgress: id })
    try {
      const query = options?.force ? "?force=true" : ""
      await POST(`/actions/${encodeURIComponent(id)}/stop${query}`)
      options?.toast?.("Process stopped", "success")
      await get().loadProcesses()
      return true
    } catch (err) {
      options?.toast?.(`Failed to stop: ${extractErrorMessage(err)}`, "error")
      await get().loadProcesses()
      return false
    } finally {
      set({ actionInProgress: null })
    }
  },

  restartProcess: async (id: string, options?: { toast?: ToastFn }) => {
    const { actionInProgress } = get()
    if (actionInProgress) return false

    set({ actionInProgress: id })
    try {
      await POST(`/actions/${encodeURIComponent(id)}/restart`)
      options?.toast?.("Process restarted", "success")
      await get().loadProcesses()
      return true
    } catch (err) {
      options?.toast?.(`Failed to restart: ${extractErrorMessage(err)}`, "error")
      await get().loadProcesses()
      return false
    } finally {
      set({ actionInProgress: null })
    }
  },

  attachProcess: async (id: string, port: number, options?: { toast?: ToastFn }) => {
    const { actionInProgress } = get()
    if (actionInProgress) return false

    set({ actionInProgress: id })
    try {
      await POST(`/actions/${encodeURIComponent(id)}/attach?port=${port}`)
      options?.toast?.("Attached to running process", "success")
      await get().loadProcesses()
      return true
    } catch (err) {
      options?.toast?.(`Failed to attach: ${extractErrorMessage(err)}`, "error")
      await get().loadProcesses()
      return false
    } finally {
      set({ actionInProgress: null })
    }
  },
}))
