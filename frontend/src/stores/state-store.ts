import { create } from "zustand"
import { ManagedWebSocket } from "@/lib/ws"
import { getClientId, getClientName, setClientName } from "@/lib/client-id"
import { POST } from "@/lib/api"
import { useDictationStore } from "@/stores/dictation-store"
import { useProjectStore } from "@/stores/project-store"
import { useTerminalStore } from "@/stores/terminal-store"
import { useLogsStore } from "@/stores/logs-store"
import { useUIStore } from "@/stores/ui-store"
import { useChannelStore } from "@/stores/channel-store"
import { dispatchNavAction, type NavAction } from "@/lib/orchestrator-dispatch"
import type { Action, Task, Agent, TabId } from "@/types"

interface QueueStats {
  total: number
  pending: number
  in_progress: number
  completed: number
  failed: number
  by_project: Record<string, unknown>
}

export interface Terminal {
  id: string
  project: string
  status: string
  pid?: number
  waiting_for_input?: boolean
  command?: string
}

interface StateSnapshot {
  server_state: string
  tasks: Task[]
  actions: Action[]
  agents: Agent[]
  sessions: unknown[]
  terminals: Terminal[]
  collections: { id: string; name: string; description?: string; sort_order?: number; project_count?: number }[]
  terminal_channels: Record<string, string[]>  // terminal_id -> [channel_ids]
  phone: Record<string, unknown>
  voice: Record<string, unknown>
  queue_stats: QueueStats
  timestamp: string
}

interface StateStoreData {
  connected: boolean
  serverState: string
  actions: Action[]
  tasks: Task[]
  agents: Agent[]
  terminals: Terminal[]
  collections: { id: string; name: string; description?: string; sort_order?: number; project_count?: number }[]
  terminalChannels: Record<string, string[]>  // terminal_id -> [channel_ids]
  queueStats: QueueStats
  phone: Record<string, unknown>
  voice: Record<string, unknown>
  timestamp: string | null

  // Methods
  connect: () => void
  disconnect: () => void
  sendEvent: (type: string, data?: Record<string, unknown>) => void
}

let ws: ManagedWebSocket | null = null

// Phone action subscriber — components register callbacks for actions that need component-level handling
type PhoneActionHandler = (action: Record<string, unknown>) => void
const phoneActionHandlers = new Set<PhoneActionHandler>()
export function onPhoneAction(handler: PhoneActionHandler): () => void {
  phoneActionHandlers.add(handler)
  return () => phoneActionHandlers.delete(handler)
}

// Orchestrator-complete subscriber — fires when the server's async _background
// task finishes, regardless of where (or whether) the reply was posted.
// Pollers in send flows use this as a terminal signal so they don't wait the
// full 30s when the reply was routed to #system or suppressed entirely.
type OrchestratorCompleteHandler = (data: { channel_id?: string; client_id?: string }) => void
const orchestratorCompleteHandlers = new Set<OrchestratorCompleteHandler>()
export function onOrchestratorComplete(handler: OrchestratorCompleteHandler): () => void {
  orchestratorCompleteHandlers.add(handler)
  return () => { orchestratorCompleteHandlers.delete(handler) }
}

function executePhoneAction(action: Record<string, unknown>) {
  const actionName = action.action as string

  switch (actionName) {
    case "select_project": {
      if (action.project) {
        useProjectStore.getState().selectProject(action.project as string)
      }
      break
    }
    case "select_collection": {
      if (action.collection) {
        useProjectStore.getState().selectCollection(action.collection as string)
      }
      break
    }
    case "show_tab": {
      if (action.tab) {
        useUIStore.getState().setTab(action.tab as TabId)
      }
      break
    }
    case "rename_client": {
      if (action.name && typeof action.name === "string") {
        setClientName(action.name)
        ws?.send({ type: "register", client_id: getClientId(), client_name: action.name })
      }
      break
    }
    case "open_terminal": {
      const project = (action.project as string) || undefined
      if (project) {
        useTerminalStore.getState().spawnTerminal(project)
      }
      break
    }
    case "focus_terminal": {
      const terminalId = action.terminal_id as string
      if (terminalId) {
        useTerminalStore.getState().setActiveTerminalId(terminalId)
      } else if (action.project) {
        const terminals = useStateStore.getState().terminals
        const match = terminals.find((t) => t.project === action.project && t.status === "running")
        if (match) {
          useTerminalStore.getState().setActiveTerminalId(match.id)
        }
      }
      break
    }
    case "send_to_terminal": {
      const text = action.text as string
      const activeId = useTerminalStore.getState().activeTerminalId
      if (activeId && text) {
        POST(`/terminals/${activeId}/input`, { text })
      }
      break
    }
    case "open_browser": {
      useUIStore.getState().setTab("browser" as TabId)
      break
    }
    case "focus_input": {
      const target = action.target as string
      if (target === "command_bar" || target === "search") {
        useUIStore.getState().toggleCommandPalette()
      } else if (target === "terminal") {
        useUIStore.getState().openTextInput(
          (text: string) => {
            const activeId = useTerminalStore.getState().activeTerminalId
            if (activeId) {
              POST(`/terminals/${activeId}/input`, { text })
            }
          },
          "Terminal input",
        )
      } else if (target === "browser_url") {
        useUIStore.getState().setTab("browser" as TabId)
      }
      break
    }
    case "show_activity": {
      useUIStore.getState().setTab("activity" as TabId)
      break
    }
    case "show_logs": {
      if (action.process_id) {
        useLogsStore.getState().openProcessLog(action.process_id as string, (action.process_name as string) || (action.process_id as string))
      } else {
        useUIStore.getState().setTab("system" as TabId)
      }
      break
    }
    case "show_action_logs": {
      useLogsStore.getState().openProcessLog(action.process_id as string, (action.process_name as string) || (action.process_id as string))
      break
    }
    case "navigate": {
      if (action.url && typeof action.url === "string") {
        window.location.href = action.url
      }
      break
    }
    case "open_preview": {
      // Handled by component-level handlers that have access to preview state
      break
    }
    default:
      break
  }

  // Dispatch to any registered component-level handlers (for actions needing component state)
  for (const handler of phoneActionHandlers) {
    handler(action)
  }
}

export const useStateStore = create<StateStoreData>((set) => ({
  connected: false,
  serverState: "unknown",
  actions: [],
  tasks: [],
  agents: [],
  terminals: [],
  collections: [],
  terminalChannels: {},
  queueStats: { total: 0, pending: 0, in_progress: 0, completed: 0, failed: 0, by_project: {} },
  phone: {},
  voice: {},
  timestamp: null,

  connect: () => {
    if (ws) ws.close()

    const clientId = getClientId()

    ws = new ManagedWebSocket("/ws/state", {
      onOpen: () => {
        // Register this client with the backend (same as old UX)
        ws?.send({ type: "register", client_id: clientId, client_name: getClientName() || clientId })
        set({ connected: true })
      },
      onClose: () => set({ connected: false }),
      reconnect: true,
      reconnectInterval: 3000,
    })

    ws.on("state", (raw) => {
      const msg = raw as { type: "state"; data: StateSnapshot }
      const s = msg.data
      set({
        serverState: s.server_state,
        actions: s.actions || [],
        tasks: s.tasks,
        agents: s.agents,
        terminals: s.terminals,
        collections: s.collections,
        terminalChannels: s.terminal_channels || {},
        queueStats: s.queue_stats,
        phone: s.phone,
        voice: s.voice || {},
        timestamp: s.timestamp,
      })
    })

    ws.on("phone_type_mode", (raw) => {
      const msg = raw as { type: "phone_type_mode"; enabled: boolean; target?: string }
      useDictationStore.getState().setActive(msg.enabled, msg.target)
    })

    ws.on("phone_type", (raw) => {
      const msg = raw as { type: "phone_type"; text: string }
      useDictationStore.getState().addBlock(msg.text)
    })

    ws.on("phone_unpaired", () => {
      useDictationStore.getState().setActive(false)
    })

    // Instant message delivery from async orchestrator
    ws.on("channel_message", (raw) => {
      const msg = raw as { type: "channel_message"; data: { channel_id: string } }
      const { activeChannelId, loadMessages } = useChannelStore.getState()
      if (msg.data?.channel_id === activeChannelId) {
        loadMessages(activeChannelId)
      }
    })

    // Client-side nav actions from async orchestrator (voice, background runs).
    // Server routes these to a specific client_id to avoid broadcasting UI
    // changes to every connected device.
    ws.on("orchestrator_dispatch", (raw) => {
      const msg = raw as {
        type: "orchestrator_dispatch"
        data: { actions: NavAction[]; client_id?: string }
      }
      const targetId = msg.data?.client_id
      if (targetId && targetId !== clientId) return
      for (const action of msg.data?.actions || []) {
        dispatchNavAction(action)
      }
    })

    ws.on("phone_action", (raw) => {
      const msg = raw as { type: "phone_action"; actions: Record<string, unknown>[] }
      for (const action of msg.actions || []) {
        executePhoneAction(action)
      }
    })

    ws.on("orchestrator_complete", (raw) => {
      const msg = raw as {
        type: "orchestrator_complete"
        data?: { channel_id?: string; client_id?: string }
      }
      const targetId = msg.data?.client_id
      if (targetId && targetId !== clientId) return
      for (const handler of orchestratorCompleteHandlers) {
        try {
          handler(msg.data || {})
        } catch {
          // ignore handler errors
        }
      }
    })

    // Server-pushed toast (e.g. "Created project foo — see #system").  Used
    // when a control-plane intent is routed to the #system channel so the
    // originating client still gets visible feedback.
    ws.on("toast", (raw) => {
      const msg = raw as {
        type: "toast"
        data?: { text?: string; level?: "info" | "success" | "warning" | "error" }
      }
      const text = msg.data?.text
      if (!text) return
      const level = msg.data?.level ?? "info"
      useUIStore.getState().toast(text, level)
    })

    ws.connect()
  },

  disconnect: () => {
    ws?.close()
    ws = null
    set({ connected: false })
  },

  sendEvent: (type, data) => {
    ws?.send({ type, ...data })
  },
}))
