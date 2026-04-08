import { create } from "zustand"
import { POST, DELETE } from "@/lib/api"

const ACTIVE_TERMINAL_KEY = "rdc_active_terminal_id"
const SCOPED_TERMINAL_KEY = "rdc_scoped_terminal_ids"

function loadScopedTerminalIds(): Record<string, string> {
  try {
    const raw = localStorage.getItem(SCOPED_TERMINAL_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" ? parsed : {}
  } catch {
    return {}
  }
}

function saveScopedTerminalIds(value: Record<string, string>) {
  localStorage.setItem(SCOPED_TERMINAL_KEY, JSON.stringify(value))
}

export interface TerminalSession {
  id: string
  project: string
  status: string
  pid?: number
  waiting_for_input?: boolean
}

type TerminalMode = "embedded" | "fullscreen" | "minimized"

interface TerminalStoreState {
  activeProject: string | null
  activeTerminalId: string | null
  scopedTerminalIds: Record<string, string>
  mode: TerminalMode
  // Track which projects have local xterm connections (managed by component)
  connectedProjects: Set<string>
  // Whether the terminal area currently has user focus (for voice routing)
  terminalFocused: boolean

  setActiveProject: (project: string | null) => void
  setActiveTerminalId: (id: string | null) => void
  rememberTerminalForScope: (scope: string, id: string | null) => void
  setMode: (mode: TerminalMode) => void
  markConnected: (project: string) => void
  markDisconnected: (project: string) => void
  setTerminalFocused: (focused: boolean) => void
  spawnTerminal: (project: string, command?: string, channelId?: string) => Promise<TerminalSession | null>
  killTerminal: (sessionId: string) => Promise<void>
  restartTerminal: (sessionId: string) => Promise<TerminalSession | null>
}

export const useTerminalStore = create<TerminalStoreState>((set) => ({
  activeProject: null,
  activeTerminalId: localStorage.getItem(ACTIVE_TERMINAL_KEY),
  scopedTerminalIds: loadScopedTerminalIds(),
  mode: "embedded",
  connectedProjects: new Set(),
  terminalFocused: false,

  setActiveProject: (project) => set({ activeProject: project }),

  setActiveTerminalId: (id) => {
    if (id) localStorage.setItem(ACTIVE_TERMINAL_KEY, id)
    else localStorage.removeItem(ACTIVE_TERMINAL_KEY)
    set({ activeTerminalId: id })
  },

  rememberTerminalForScope: (scope, id) =>
    set((s) => {
      const next = { ...s.scopedTerminalIds }
      if (id) next[scope] = id
      else delete next[scope]
      saveScopedTerminalIds(next)
      return { scopedTerminalIds: next }
    }),

  setMode: (mode) => set({ mode }),

  setTerminalFocused: (focused) => set({ terminalFocused: focused }),

  markConnected: (project) =>
    set((s) => {
      const next = new Set(s.connectedProjects)
      next.add(project)
      return { connectedProjects: next }
    }),

  markDisconnected: (project) =>
    set((s) => {
      const next = new Set(s.connectedProjects)
      next.delete(project)
      return { connectedProjects: next }
    }),

  spawnTerminal: async (project, command, channelId) => {
    try {
      let url = `/terminals?project=${encodeURIComponent(project)}`
      if (command !== undefined) {
        url += `&command=${encodeURIComponent(command)}`
      }
      if (channelId) {
        url += `&channel_id=${encodeURIComponent(channelId)}`
      }
      const session = await POST<TerminalSession>(url)
      if (session?.id) localStorage.setItem(ACTIVE_TERMINAL_KEY, session.id)
      else localStorage.removeItem(ACTIVE_TERMINAL_KEY)
      set({ activeProject: project, activeTerminalId: session?.id || null })
      return session
    } catch {
      return null
    }
  },

  killTerminal: async (sessionId) => {
    try {
      await DELETE(`/terminals/${encodeURIComponent(sessionId)}`)
    } catch {
      // ignore
    }
  },

  restartTerminal: async (sessionId) => {
    try {
      const session = await POST<TerminalSession>(
        `/terminals/${encodeURIComponent(sessionId)}/restart`
      )
      return session
    } catch {
      return null
    }
  },
}))
