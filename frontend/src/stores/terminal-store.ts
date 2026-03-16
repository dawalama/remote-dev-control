import { create } from "zustand"
import { POST, DELETE } from "@/lib/api"

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
  mode: TerminalMode
  // Track which projects have local xterm connections (managed by component)
  connectedProjects: Set<string>
  // Whether the terminal area currently has user focus (for voice routing)
  terminalFocused: boolean

  setActiveProject: (project: string | null) => void
  setActiveTerminalId: (id: string | null) => void
  setMode: (mode: TerminalMode) => void
  markConnected: (project: string) => void
  markDisconnected: (project: string) => void
  setTerminalFocused: (focused: boolean) => void
  spawnTerminal: (project: string, command?: string) => Promise<TerminalSession | null>
  killTerminal: (sessionId: string) => Promise<void>
  restartTerminal: (sessionId: string) => Promise<TerminalSession | null>
}

export const useTerminalStore = create<TerminalStoreState>((set) => ({
  activeProject: null,
  activeTerminalId: null,
  mode: "embedded",
  connectedProjects: new Set(),
  terminalFocused: false,

  setActiveProject: (project) => set({ activeProject: project }),

  setActiveTerminalId: (id) => set({ activeTerminalId: id }),

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

  spawnTerminal: async (project, command) => {
    try {
      let url = `/terminals?project=${encodeURIComponent(project)}`
      if (command !== undefined) {
        url += `&command=${encodeURIComponent(command)}`
      }
      const session = await POST<TerminalSession>(url)
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
