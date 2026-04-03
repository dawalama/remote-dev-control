import { create } from "zustand"
import { GET, POST, PATCH, DELETE as DEL } from "@/lib/api"

export interface Channel {
  id: string
  name: string
  type: "project" | "mission" | "ephemeral" | "system" | "event"
  parent_channel_id: string | null
  project_ids: string[]
  project_names: string[]
  collection_ids: string[]
  auto_mode: boolean
  token_spent: number
  token_budget: number | null
  created_at: string
  archived_at: string | null
}

export interface ChannelMessage {
  id: string
  channel_id: string
  role: "user" | "orchestrator" | "system" | "agent"
  content: string | null
  metadata: Record<string, unknown> | null
  created_at: string
}

interface ChannelState {
  channels: Channel[]
  activeChannelId: string | null
  messages: ChannelMessage[] // messages for active channel
  loading: boolean

  loadChannels: () => Promise<void>
  selectChannel: (id: string) => void
  createChannel: (name: string, type?: string, projectIds?: string[]) => Promise<Channel | null>
  archiveChannel: (id: string) => Promise<void>
  deleteChannel: (id: string) => Promise<void>
  loadMessages: (channelId: string) => Promise<void>
  postMessage: (channelId: string, content: string, role?: string) => Promise<ChannelMessage | null>
  toggleAutoMode: (channelId: string) => Promise<void>
}

export const useChannelStore = create<ChannelState>((set, get) => ({
  channels: [],
  activeChannelId: null,
  messages: [],
  loading: false,

  loadChannels: async () => {
    set({ loading: true })
    try {
      const data = await GET<Channel[]>("/channels")
      set({ channels: data ?? [], loading: false })
    } catch {
      set({ loading: false })
    }
  },

  selectChannel: (id) => {
    set({ activeChannelId: id, messages: [] })
    // Load messages for this channel
    get().loadMessages(id)
  },

  createChannel: async (name, type = "ephemeral", projectIds = []) => {
    try {
      const ch = await POST<Channel>("/channels", {
        name,
        type,
        project_ids: projectIds,
      })
      if (ch) {
        set((s) => ({ channels: [ch, ...s.channels] }))
      }
      return ch ?? null
    } catch {
      return null
    }
  },

  archiveChannel: async (id) => {
    try {
      await POST(`/channels/${id}/archive`, {})
      set((s) => ({
        channels: s.channels.filter((c) => c.id !== id),
        activeChannelId: s.activeChannelId === id ? null : s.activeChannelId,
      }))
    } catch {
      // ignore
    }
  },

  deleteChannel: async (id) => {
    try {
      await DEL(`/channels/${id}`)
      set((s) => ({
        channels: s.channels.filter((c) => c.id !== id),
        activeChannelId: s.activeChannelId === id ? null : s.activeChannelId,
        messages: s.activeChannelId === id ? [] : s.messages,
      }))
    } catch {
      // ignore
    }
  },

  loadMessages: async (channelId) => {
    try {
      const data = await GET<ChannelMessage[]>(`/channels/${channelId}/messages?limit=100`)
      set({ messages: data ?? [] })
    } catch {
      set({ messages: [] })
    }
  },

  postMessage: async (channelId, content, role = "user") => {
    try {
      const msg = await POST<ChannelMessage>(`/channels/${channelId}/messages`, {
        content,
        role,
      })
      if (msg) {
        set((s) => ({ messages: [...s.messages, msg] }))
      }
      return msg ?? null
    } catch {
      return null
    }
  },

  toggleAutoMode: async (channelId) => {
    const ch = get().channels.find((c) => c.id === channelId)
    if (!ch) return
    try {
      await PATCH(`/channels/${channelId}`, { auto_mode: !ch.auto_mode })
      set((s) => ({
        channels: s.channels.map((c) =>
          c.id === channelId ? { ...c, auto_mode: !c.auto_mode } : c
        ),
      }))
    } catch {
      // ignore
    }
  },
}))
