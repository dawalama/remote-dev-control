import { create } from "zustand"
import { GET, PATCH } from "@/lib/api"
import { AGENT_PRESETS } from "@/lib/agent-presets"

export interface TerminalPreset {
  id: string
  label: string
  command: string
  icon: string
  description: string
  goal_command?: string
  runs_goals?: boolean
  agent_id?: string
}

function normalizePreset(preset: Partial<TerminalPreset>): TerminalPreset {
  return {
    id: String(preset.id ?? ""),
    label: String(preset.label ?? ""),
    command: String(preset.command ?? ""),
    icon: String(preset.icon ?? "•"),
    description: String(preset.description ?? ""),
    goal_command: String(preset.goal_command ?? ""),
    runs_goals: Boolean(preset.runs_goals),
    agent_id: String(preset.agent_id ?? ""),
  }
}

const PRESETS_PATH = "/settings/terminal-presets"

interface TerminalPresetsState {
  presets: TerminalPreset[]
  loaded: boolean
  load: () => Promise<TerminalPreset[]>
  save: (presets: TerminalPreset[]) => Promise<TerminalPreset[]>
}

export const useTerminalPresetsStore = create<TerminalPresetsState>((set, get) => ({
  presets: AGENT_PRESETS,
  loaded: false,

  load: async () => {
    if (get().loaded) return get().presets
    try {
      const data = await GET<TerminalPreset[]>(PRESETS_PATH)
      const presets = Array.isArray(data) ? data.map((preset) => normalizePreset(preset)) : AGENT_PRESETS
      set({ presets, loaded: true })
      return presets
    } catch {
      set({ presets: AGENT_PRESETS, loaded: true })
      return AGENT_PRESETS
    }
  },

  save: async (presets) => {
    const payload = presets.map((preset) => ({
      id: preset.id,
      label: preset.label,
      command: preset.command,
      icon: preset.icon,
      description: preset.description,
      goal_command: preset.goal_command ?? "",
    }))
    const saved = await PATCH<TerminalPreset[]>(PRESETS_PATH, payload)
    const next = Array.isArray(saved) ? saved.map((preset) => normalizePreset(preset)) : payload
    set({ presets: next, loaded: true })
    return next
  },
}))
