import { create } from "zustand"
import { useChannelStore } from "@/stores/channel-store"

/**
 * HomeBase conversation scope.
 * Derived from open workstream: HomeBase when ch-system or none, open channel otherwise.
 */
export type ChatScopeMode = "homebase" | "pinned"

interface ChatScopeState {
  mode: ChatScopeMode
  pinnedWorkstreamId: string | null
  setHomebase: () => void
  pinWorkstream: (workstreamId: string) => void
  pinActive: () => boolean
}

export const useChatScopeStore = create<ChatScopeState>((_set) => ({
  mode: "homebase",
  pinnedWorkstreamId: null,

  setHomebase: () => {
    useChannelStore.getState().selectChannel("ch-system")
  },

  pinWorkstream: (workstreamId: string) => {
    if (!workstreamId) return
    useChannelStore.getState().selectChannel(workstreamId)
  },

  pinActive: () => {
    const id = useChannelStore.getState().activeChannelId
    if (!id || id === "ch-system") return false
    return true
  },
}))

/** Resolve optional workstream filter for Brief me / homebase tools. */
export function briefWorkstreamFilter(): string | null {
  const activeChannelId = useChannelStore.getState().activeChannelId
  if (activeChannelId && activeChannelId !== "ch-system") return activeChannelId
  return null
}

/** Follow-up workstream: the open non-system channel, or null for ch-system / missing. */
export function orchestratorWorkstreamId(activeChannelId: string | null): string | null {
  if (activeChannelId && activeChannelId !== "ch-system") return activeChannelId
  return null
}
