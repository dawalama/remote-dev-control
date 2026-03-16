import { create } from "zustand"
import { GET, PATCH } from "@/lib/api"
import { AGENT_PRESETS } from "@/lib/agent-presets"

export interface TerminalPreset {
  id: string
  label: string
  command: string
  icon: string
  description: string
}

const PRESETS_PATH = "/settings/terminal-presets"

interface TerminalPresetsState {
  presets: TerminalPreset[]
  loaded: boolean
  load: () => Promise<void>
  save: (presets: TerminalPreset[]) => Promise<void>
}

export const useTerminalPresetsStore = create<TerminalPresetsState>((set, get) => ({
  presets: AGENT_PRESETS,
  loaded: false,

  load: async () => {
    if (get().loaded) return
    try {
      const data = await GET<TerminalPreset[]>(PRESETS_PATH)
      set({
        presets: Array.isArray(data)
          ? data.map((p) => ({
              id: String(p.id ?? ""),
              label: String(p.label ?? ""),
              command: String(p.command ?? ""),
              icon: String(p.icon ?? "•"),
              description: String(p.description ?? ""),
            }))
          : AGENT_PRESETS,
        loaded: true,
      })
    } catch {
      set({ presets: AGENT_PRESETS, loaded: true })
    }
  },

  save: async (presets) => {
    const payload = presets.map((p) => ({
      id: p.id,
      label: p.label,
      command: p.command,
      icon: p.icon,
      description: p.description,
    }))
    const saved = await PATCH<TerminalPreset[]>(PRESETS_PATH, payload)
    set({
      presets: Array.isArray(saved)
        ? saved.map((p) => ({
            id: String(p.id ?? ""),
            label: String(p.label ?? ""),
            command: String(p.command ?? ""),
            icon: String(p.icon ?? "•"),
            description: String(p.description ?? ""),
          }))
        : payload,
    })
  },
}))
