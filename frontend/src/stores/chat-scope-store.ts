import { create } from "zustand"
import { useChannelStore } from "@/stores/channel-store"

/**
 * HomeBase conversation scope.
 * Default is whole-machine (HomeBase). Pin a workstream only when the user opts in.
 */
export type ChatScopeMode = "homebase" | "pinned"

interface ChatScopeState {
  mode: ChatScopeMode
  /** Workstream/channel id when mode === "pinned" */
  pinnedWorkstreamId: string | null
  setHomebase: () => void
  pinWorkstream: (workstreamId: string) => void
  /** Pin currently active channel if any */
  pinActive: () => boolean
}

const STORAGE_KEY = "rdc_chat_scope"

function loadInitial(): Pick<ChatScopeState, "mode" | "pinnedWorkstreamId"> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { mode: "homebase", pinnedWorkstreamId: null }
    const parsed = JSON.parse(raw) as { mode?: string; pinnedWorkstreamId?: string | null }
    if (parsed.mode === "pinned" && parsed.pinnedWorkstreamId) {
      return { mode: "pinned", pinnedWorkstreamId: parsed.pinnedWorkstreamId }
    }
  } catch {
    /* ignore */
  }
  return { mode: "homebase", pinnedWorkstreamId: null }
}

function persist(mode: ChatScopeMode, pinnedWorkstreamId: string | null) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ mode, pinnedWorkstreamId }))
  } catch {
    /* ignore */
  }
}

export const useChatScopeStore = create<ChatScopeState>((set, get) => ({
  ...loadInitial(),

  setHomebase: () => {
    persist("homebase", null)
    set({ mode: "homebase", pinnedWorkstreamId: null })
  },

  pinWorkstream: (workstreamId: string) => {
    if (!workstreamId) return
    persist("pinned", workstreamId)
    set({ mode: "pinned", pinnedWorkstreamId: workstreamId })
  },

  pinActive: () => {
    const id = useChannelStore.getState().activeChannelId
    if (!id) return false
    get().pinWorkstream(id)
    return true
  },
}))

/** Resolve optional workstream filter for Brief me / homebase tools. */
export function briefWorkstreamFilter(): string | null {
  const { mode, pinnedWorkstreamId } = useChatScopeStore.getState()
  if (mode === "pinned" && pinnedWorkstreamId) return pinnedWorkstreamId
  return null
}

/** Follow-up workstream: the pin when set, otherwise the channel on screen. */
export function orchestratorWorkstreamId(activeChannelId: string | null): string | null {
  return briefWorkstreamFilter() || activeChannelId || null
}
