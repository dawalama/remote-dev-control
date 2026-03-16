import { useCallback } from "react"
import { POST } from "@/lib/api"
import { getClientId, setClientName } from "@/lib/client-id"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useStateStore } from "@/stores/state-store"
import type { TabId } from "@/types"

interface OrchestratorAction {
  action: string
  success?: boolean
  error?: string
  project?: string
  collection?: string
  collection_id?: string
  tab?: string
  url?: string
  process_id?: string
  process_name?: string
  title?: string
  session_id?: string
  [key: string]: unknown
}

interface OrchestratorResult {
  response?: string
  actions?: OrchestratorAction[]
  executed?: OrchestratorAction[]
  options?: unknown
  usage?: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Declarative command table — each entry defines trigger phrases + execution
// ---------------------------------------------------------------------------

interface CommandContext {
  currentProject: string
  selectProject: (p: string) => void
  loadProjects: () => void
  setTab: (tab: string) => void
  setLayout: (layout: string) => void
  setTheme: (theme: string) => void
  toggleSidebar: () => void
  toggleChat: () => void
  toast: (msg: string, level: "info" | "success" | "warning" | "error") => void
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
  onOpenActivity?: () => void
  onOpenMenu?: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
}

interface Command {
  id: string
  triggers: string[]
  hasSlot?: boolean // true if trigger is a prefix and the rest is a slot value
  execute: (rest: string, ctx: CommandContext) => OrchestratorResult | null
}

const TAB_ALIASES: Record<string, string> = {
  tasks: "tasks", task: "tasks",
  processes: "processes", process: "processes", procs: "processes",
  activity: "activity", activities: "activity", events: "activity",
  workers: "workers", worker: "workers",
  browser: "browser", preview: "browser",
  system: "system", settings: "system",
  chat: "chat", conversation: "chat",
  pinchtab: "pinchtab",
  attachments: "attachments", attachment: "attachments", contexts: "attachments", context: "attachments",
  docs: "docs", knowledge: "docs", wiki: "docs",
  project: "project",
}

const LAYOUT_ALIASES: Record<string, string> = {
  desktop: "desktop", kiosk: "kiosk", mobile: "mobile",
  "kiosk mode": "kiosk", "mobile mode": "mobile", "desktop mode": "desktop",
}

const THEME_ALIASES: Record<string, string> = {
  default: "default", modern: "modern", brutalist: "brutalist",
  dark: "default", "dark mode": "default",
}

const COMMANDS: Command[] = [
  {
    id: "open_terminal",
    triggers: ["open terminal for", "open terminal", "new terminal for", "new terminal"],
    hasSlot: true,
    execute: (rest, ctx) => {
      const project = rest || ctx.currentProject
      ctx.onOpenTerminal?.(project)
      return { response: `Opening terminal for ${project}`, actions: [] }
    },
  },
  {
    id: "close_terminal",
    triggers: ["close terminal", "hide terminal"],
    execute: (_rest, _ctx) => {
      return { response: "Closing terminal", actions: [] }
    },
  },
  {
    id: "select_project",
    triggers: ["switch to", "change to", "change project to", "select project", "select", "project"],
    hasSlot: true,
    execute: (rest, ctx) => {
      if (!rest) return null
      ctx.selectProject(rest)
      return { response: `Switched to ${rest}`, actions: [] }
    },
  },
  {
    id: "show_tab",
    triggers: ["show", "view", "go to", "open tab", "switch tab to"],
    hasSlot: true,
    execute: (rest, ctx) => {
      const tab = TAB_ALIASES[rest.toLowerCase()]
      if (!tab) return null // Falls through to server LLM for natural language
      ctx.setTab(tab)
      return { response: `Showing ${tab}`, actions: [] }
    },
  },
  {
    id: "refresh",
    triggers: ["refresh", "reload", "sync"],
    execute: (_rest, ctx) => {
      ctx.loadProjects()
      return { response: "Refreshing...", actions: [] }
    },
  },
  {
    id: "create_task",
    triggers: ["create task", "new task", "add task"],
    execute: (_rest, ctx) => {
      ctx.onCreateTask?.()
      return { response: "Opening task creator", actions: [] }
    },
  },
  {
    id: "open_browser",
    triggers: ["open browser", "start browser"],
    execute: (_rest, ctx) => {
      ctx.onOpenBrowser?.()
      return { response: "Opening browser", actions: [] }
    },
  },
  {
    id: "open_activity",
    triggers: ["show activity", "show activities", "open activity", "activity", "activities"],
    execute: (_rest, ctx) => {
      ctx.onOpenActivity?.()
      return { response: "Opening activity", actions: [] }
    },
  },
  {
    id: "open_menu",
    triggers: ["open menu", "menu"],
    execute: (_rest, ctx) => {
      ctx.onOpenMenu?.()
      return { response: "Opening menu", actions: [] }
    },
  },
  {
    id: "rename_client",
    triggers: ["rename me to", "call me", "my name is", "rename device to", "rename to"],
    hasSlot: true,
    execute: (rest, ctx) => {
      if (!rest) return null
      const name = rest.trim()
      setClientName(name)
      const clientId = getClientId()
      // Re-register with server so it sees the new name
      const stateStore = useStateStore.getState()
      stateStore.sendEvent("register", { client_id: clientId, client_name: name })
      ctx.toast(`Device renamed to "${name}"`, "success")
      return { response: `Done! This device is now "${name}".`, actions: [] }
    },
  },
  {
    id: "set_layout",
    triggers: ["layout", "set layout", "switch layout", "change layout", "use layout"],
    hasSlot: true,
    execute: (rest, ctx) => {
      const layout = LAYOUT_ALIASES[rest.toLowerCase()]
      if (!layout) return null
      ctx.setLayout(layout)
      ctx.toast(`Layout: ${layout}`, "success")
      return { response: `Switched to ${layout} layout`, actions: [] }
    },
  },
  {
    id: "set_theme",
    triggers: ["theme", "set theme", "switch theme", "change theme", "use theme"],
    hasSlot: true,
    execute: (rest, ctx) => {
      const theme = THEME_ALIASES[rest.toLowerCase()]
      if (!theme) return null
      ctx.setTheme(theme)
      ctx.toast(`Theme: ${theme}`, "success")
      return { response: `Switched to ${theme} theme`, actions: [] }
    },
  },
  {
    id: "toggle_sidebar",
    triggers: ["toggle sidebar", "hide sidebar", "show sidebar", "sidebar"],
    execute: (_rest, ctx) => {
      ctx.toggleSidebar()
      return { response: "Toggling sidebar", actions: [] }
    },
  },
  {
    id: "toggle_chat",
    triggers: ["toggle chat", "hide chat", "show chat"],
    execute: (_rest, ctx) => {
      ctx.toggleChat()
      return { response: "Toggling chat", actions: [] }
    },
  },
  {
    id: "open_project_settings",
    triggers: ["project settings", "open project settings", "edit project"],
    execute: (_rest, ctx) => {
      ctx.onEditProject?.()
      return { response: "Opening project settings", actions: [] }
    },
  },
  {
    id: "open_system_settings",
    triggers: ["system settings", "open system settings", "admin settings"],
    execute: (_rest, ctx) => {
      ctx.onSystemSettings?.()
      return { response: "Opening system settings", actions: [] }
    },
  },
  {
    id: "help",
    triggers: ["help", "what can you do", "commands"],
    execute: (_rest, ctx) => {
      ctx.toast(
        "Commands: show <tab>, open terminal, new task, open browser, activity, menu, refresh, select <project>",
        "info",
      )
      return { response: "Showing available commands", actions: [] }
    },
  },
]

function matchCommand(input: string): { command: Command; rest: string } | null {
  const normalized = input.toLowerCase().trim().replace(/\s+/g, " ")
  for (const cmd of COMMANDS) {
    for (const trigger of cmd.triggers) {
      if (normalized === trigger) {
        return { command: cmd, rest: "" }
      }
      if (cmd.hasSlot && normalized.startsWith(trigger + " ")) {
        const rest = normalized.slice(trigger.length + 1).trim()
        if (rest) return { command: cmd, rest }
      }
    }
  }
  return null
}

/**
 * Hook that sends messages to the orchestrator and executes returned client-side actions.
 *
 * Conversation history lives server-side (per-project threads). The client sends
 * a `client_id` so the server can track which device sent each message.
 *
 * Accepts optional callbacks for actions that require the parent component
 * to do something (e.g. open a terminal overlay, open preview).
 */
export function useOrchestrator(opts: {
  channel: "desktop" | "mobile"
  onOpenTerminal?: (project: string) => void
  onOpenPreview?: (sessionId: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
  onOpenActivity?: () => void
  onOpenMenu?: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
} = { channel: "desktop" }) {
  const selectProject = useProjectStore((s) => s.selectProject)
  const selectCollection = useProjectStore((s) => s.selectCollection)
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const currentProject = useProjectStore((s) => s.currentProject)
  const setTab = useUIStore((s) => s.setTab)
  const setLayout = useUIStore((s) => s.setLayout)
  const setTheme = useUIStore((s) => s.setTheme)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const toggleChat = useUIStore((s) => s.toggleChat)
  const toast = useUIStore((s) => s.toast)
  const sendEvent = useStateStore((s) => s.sendEvent)

  const executeAction = useCallback((action: OrchestratorAction) => {
    if (action.success === false && action.error) {
      toast(`Failed: ${action.error}`, "error")
      return
    }

    switch (action.action) {
      case "select_project":
        if (action.collection_id) selectCollection(action.collection_id)
        if (action.project) selectProject(action.project)
        break

      case "select_collection":
        if (action.collection) selectCollection(action.collection)
        break

      case "open_terminal":
        if (action.project) selectProject(action.project)
        opts.onOpenTerminal?.(action.project || currentProject)
        break

      case "show_tab":
        if (action.tab) setTab(action.tab as TabId)
        break

      case "navigate":
        if (action.url) window.location.href = action.url
        break

      case "start_process":
        if (action.success) toast(`Started: ${action.process_id || action.process_name || "process"}`, "success")
        break

      case "stop_process":
        if (action.success) toast(`Stopped: ${action.process_id || action.process_name || "process"}`, "success")
        break

      case "create_task":
        if (action.success) toast(`Created task: ${action.title || "task"}`, "success")
        break

      case "create_project":
        if (action.success && action.project) {
          toast(`Created project: ${action.project}`, "success")
          selectProject(action.project)
          loadProjects()
        }
        break

      case "start_preview":
        if (action.success && action.session_id) {
          opts.onOpenPreview?.(action.session_id)
        }
        break

      case "search_projects":
        break

      case "show_activity":
        opts.onOpenActivity?.()
        break

      case "open_browser":
        opts.onOpenBrowser?.()
        break

      case "open_project_settings":
        opts.onEditProject?.()
        break

      case "open_system_settings":
        opts.onSystemSettings?.()
        break

      case "focus_terminal":
        if (action.project) selectProject(action.project)
        opts.onOpenTerminal?.(action.project || currentProject)
        break

      case "send_to_terminal":
        // Voice/command requested text be sent to terminal
        if (action.text && typeof action.text === "string") {
          opts.onOpenTerminal?.(currentProject)
        }
        break

      case "show_logs":
      case "show_process_logs":
        toast(`Logs: ${action.process_name || action.process_id || "system"}`, "info")
        break

      case "rename_client":
        if (action.name && typeof action.name === "string") {
          setClientName(action.name)
          // Re-register with server so it sees the new name
          sendEvent("register", { client_id: getClientId(), client_name: action.name })
          toast(`Device renamed to "${action.name}"`, "success")
        }
        break

      case "set_layout":
        if (action.layout && typeof action.layout === "string") {
          setLayout(action.layout)
          toast(`Layout: ${action.layout}`, "success")
        }
        break

      case "set_theme":
        if (action.theme && typeof action.theme === "string") {
          setTheme(action.theme)
          toast(`Theme: ${action.theme}`, "success")
        }
        break

      case "toggle_sidebar":
        toggleSidebar()
        break

      case "toggle_chat":
        toggleChat()
        break

      case "restart_server":
        if (action.success) toast("Server restarting...", "info")
        break

      case "server_status":
        if (action.success) {
          toast(`Server: ${action.uptime_seconds}s uptime, ${action.memory_mb}MB RAM`, "info")
        }
        break

      case "kill_terminal":
        if (action.success) toast(`Terminal killed: ${action.terminal_id || ""}`, "success")
        break

      case "restart_terminal":
        if (action.success) toast(`Terminal restarted: ${action.terminal_id || ""}`, "success")
        break

      case "restart_process":
        if (action.success) toast(`Restarted: ${action.process_id || "process"}`, "success")
        break

      case "stop_all_processes":
        if (action.success) toast(`Stopped ${action.count || 0} processes`, "success")
        break

      case "start_all_processes":
        if (action.success) toast(`Started ${action.count || 0} processes`, "success")
        break

      default:
        break
    }

    // Report executed action to server via WS
    sendEvent("client_action", {
      action: action.action,
      project: currentProject !== "all" ? currentProject : undefined,
      status: action.success === false ? "error" : "ok",
    })
  }, [selectProject, selectCollection, loadProjects, currentProject, setTab, setLayout, setTheme, toggleSidebar, toggleChat, toast, opts, sendEvent])

  // Try to handle a command locally via declarative command table (instant, no server call)
  const tryLocalCommand = useCallback((text: string): OrchestratorResult | null => {
    const match = matchCommand(text)
    if (!match) return null

    const ctx: CommandContext = {
      currentProject,
      selectProject,
      loadProjects,
      setTab: (tab) => setTab(tab as TabId),
      setLayout,
      setTheme,
      toggleSidebar,
      toggleChat,
      toast,
      onOpenTerminal: opts.onOpenTerminal,
      onCreateTask: opts.onCreateTask,
      onOpenBrowser: opts.onOpenBrowser,
      onOpenActivity: opts.onOpenActivity,
      onOpenMenu: opts.onOpenMenu,
      onEditProject: opts.onEditProject,
      onSystemSettings: opts.onSystemSettings,
    }

    return match.command.execute(match.rest, ctx)
  }, [currentProject, selectProject, setTab, setLayout, setTheme, toggleSidebar, toggleChat, loadProjects, toast, opts])

  const send = useCallback(async (message: string, project?: string): Promise<OrchestratorResult | null> => {
    // Try local command parsing first for instant execution
    const localResult = tryLocalCommand(message)
    if (localResult) {
      // Report local command execution
      sendEvent("client_action", {
        action: "command_local",
        project: currentProject !== "all" ? currentProject : undefined,
        status: "ok",
      })
      return localResult
    }

    // Fall back to server orchestrator — server owns conversation history
    const proj = project || (currentProject !== "all" ? currentProject : undefined)

    try {
      const result = await POST<OrchestratorResult>("/orchestrator", {
        message,
        channel: opts.channel,
        project: proj,
        client_id: getClientId(),
      })

      // Server returns executed actions in the flattened {action, tab, ...} format
      // that executeAction expects. result.actions has {name, params} format (raw tool calls).
      const executedActions = (result?.executed || result?.actions || []) as OrchestratorAction[]
      for (const action of executedActions) {
        // Normalize: executed items use "action" key, raw tool calls use "name" + "params"
        if (!action.action && (action as any).name) {
          const raw = action as any
          action.action = raw.name
          if (raw.params) Object.assign(action, raw.params)
        }
        executeAction(action)
      }
      return result ?? null
    } catch {
      toast("Failed to send", "error")
      return null
    }
  }, [opts.channel, currentProject, executeAction, toast, tryLocalCommand, sendEvent])

  const clearHistory = useCallback(async () => {
    const proj = currentProject !== "all" ? currentProject : undefined
    try {
      await POST("/conversation/clear", { project: proj })
    } catch {
      // Ignore — server may not have a thread yet
    }
  }, [currentProject])

  return { send, executeAction, clearHistory }
}
