/**
 * Terminal starter list: agents (Cursor, Claude, Gemini) and shell.
 * This is the list shown in the "+" launcher and empty-state launcher.
 * Project settings only configure the *default* (one command); the list itself is here.
 */

export interface AgentPreset {
  id: string
  label: string
  command: string
  icon: string
  description: string
}

export const AGENT_PRESETS: AgentPreset[] = [
  {
    id: "cursor",
    label: "Cursor",
    command: "cursor-agent",
    icon: "C",
    description: "Cursor AI agent",
  },
  {
    id: "gemini",
    label: "Gemini",
    command: "gemini",
    icon: "G",
    description: "Google Gemini CLI",
  },
  {
    id: "claude",
    label: "Claude",
    command: "claude --continue",
    icon: "A",
    description: "Anthropic Claude Code",
  },
  {
    id: "shell",
    label: "Shell",
    command: "",
    icon: "$",
    description: "Plain login shell",
  },
]

/** Find preset matching a command string, or undefined. */
export function presetForCommand(command: string | undefined): AgentPreset | undefined {
  if (command === undefined) return undefined
  return AGENT_PRESETS.find((p) => p.command === command)
}

/** Get a display label for a command. Falls back to the raw command or "Terminal". */
export function labelForCommand(command: string | undefined): string {
  if (command === undefined) return "Terminal"
  const preset = presetForCommand(command)
  if (preset) return preset.label
  return command || "Shell"
}
