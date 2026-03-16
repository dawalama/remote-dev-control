import { create } from "zustand"
import { GET, POST } from "@/lib/api"
import type { BrowserSession } from "@/types"

interface BrowserStore {
  sessions: BrowserSession[]
  activeSession: BrowserSession | null
  loading: boolean

  loadSessions: () => Promise<void>
  startSession: (url: string, project?: string) => Promise<BrowserSession | null>
  startFromProcess: (processId: string) => Promise<BrowserSession | null>
  stopSession: (sessionId: string) => Promise<void>
  navigate: (url: string) => Promise<void>
  reload: () => Promise<void>
  goBack: () => Promise<void>
  setActiveSession: (session: BrowserSession | null) => void
}

export const useBrowserStore = create<BrowserStore>((set, get) => ({
  sessions: [],
  activeSession: null,
  loading: false,

  loadSessions: async () => {
    try {
      const sessions = await GET<BrowserSession[]>("/browser/sessions")
      const active = get().activeSession
      const runningSessions = sessions.filter((s) => s.status === "running")

      if (active) {
        // Update active session with fresh data, or clear if gone
        const updated = runningSessions.find((s) => s.id === active.id)
        if (updated) {
          set({ sessions, activeSession: updated })
        } else {
          set({ sessions, activeSession: null })
        }
      } else if (runningSessions.length > 0) {
        // Auto-select a running session when none is active
        set({ sessions, activeSession: runningSessions[0] })
      } else {
        set({ sessions })
      }
    } catch { /* */ }
  },

  startSession: async (url, project) => {
    set({ loading: true })
    try {
      const params = new URLSearchParams({ target_url: url })
      if (project) params.set("project", project)
      const session = await POST<BrowserSession>(`/browser/start?${params}`)
      if (session) {
        set({ activeSession: session, loading: false })
        get().loadSessions()
        return session
      }
    } catch { /* */ }
    set({ loading: false })
    return null
  },

  startFromProcess: async (processId) => {
    set({ loading: true })
    try {
      const session = await POST<BrowserSession>(`/browser/start/${encodeURIComponent(processId)}`)
      if (session) {
        set({ activeSession: session, loading: false })
        get().loadSessions()
        return session
      }
    } catch { /* */ }
    set({ loading: false })
    return null
  },

  stopSession: async (sessionId) => {
    try {
      await POST(`/browser/sessions/${sessionId}/stop`)
      const active = get().activeSession
      if (active?.id === sessionId) {
        set({ activeSession: null })
      }
      get().loadSessions()
    } catch { /* */ }
  },

  navigate: async (url) => {
    const active = get().activeSession
    if (!active) return
    try {
      await POST(`/browser/sessions/${active.id}/navigate?url=${encodeURIComponent(url)}`)
      set({
        activeSession: { ...active, target_url: url },
      })
    } catch { /* */ }
  },

  reload: async () => {
    const active = get().activeSession
    if (!active) return
    try {
      await POST(`/browser/sessions/${active.id}/reload`)
    } catch { /* */ }
  },

  goBack: async () => {
    const active = get().activeSession
    if (!active) return
    try {
      await POST(`/browser/sessions/${active.id}/back`)
    } catch { /* */ }
  },

  setActiveSession: (session) => set({ activeSession: session }),
}))
