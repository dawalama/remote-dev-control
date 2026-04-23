/**
 * Pure, store-level dispatcher for orchestrator navigation actions.
 *
 * Lives outside any React component so WebSocket handlers (state-store) can
 * execute nav actions directly without needing a hook instance. Handles the
 * subset of actions whose entire effect is zustand store mutation or
 * window-level navigation — no UI callbacks required.
 *
 * The authoritative list of nav action names lives in _TOOL_SCOPES
 * (src/remote_dev_ctrl/server/intent.py); dispatch here is by switch on
 * action.action, so the backend list is the single source of truth.
 */

import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import type { TabId } from "@/types"

export interface NavAction {
  action: string
  [key: string]: unknown
}

export function dispatchNavAction(a: NavAction): boolean {
  switch (a.action) {
    case "switch_workstream": {
      const channelId = a.channel_id as string | undefined
      if (!channelId) return false
      useChannelStore.getState().selectChannel(channelId)
      const ch = useChannelStore.getState().channels.find((c) => c.id === channelId)
      const firstProject = ch?.project_names?.[0]
      if (firstProject) useProjectStore.getState().selectProject(firstProject)
      return true
    }
    case "select_project": {
      const collectionId = a.collection_id as string | undefined
      const project = a.project as string | undefined
      if (collectionId) useProjectStore.getState().selectCollection(collectionId)
      if (project) useProjectStore.getState().selectProject(project)
      return true
    }
    case "select_collection": {
      const collection = a.collection as string | undefined
      if (collection) useProjectStore.getState().selectCollection(collection)
      return true
    }
    case "show_tab": {
      const tab = a.tab as string | undefined
      if (tab) useUIStore.getState().setTab(tab as TabId)
      return true
    }
    case "set_layout": {
      const layout = a.layout as string | undefined
      if (layout) useUIStore.getState().setLayout(layout)
      return true
    }
    case "set_theme": {
      const theme = a.theme as string | undefined
      if (theme) useUIStore.getState().setTheme(theme)
      return true
    }
    case "toggle_sidebar":
      useUIStore.getState().toggleSidebar()
      return true
    case "toggle_chat":
      useUIStore.getState().toggleChat()
      return true
    case "navigate": {
      const url = a.url as string | undefined
      if (url) window.location.href = url
      return true
    }
    default:
      return false
  }
}
