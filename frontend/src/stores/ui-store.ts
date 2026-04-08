import { create } from "zustand"
import type { TabId } from "@/types"

interface Toast {
  id: string
  message: string
  type: "info" | "success" | "error" | "warning"
}

interface UIState {
  currentTab: TabId
  sidebarOpen: boolean
  bottomPanelOpen: boolean
  bottomPanelHeight: number
  chatOpen: boolean
  commandPaletteOpen: boolean
  addProjectOpen: boolean
  theme: string
  layout: string
  toasts: Toast[]
  selectedTaskId: string | null
  agentPanelOpen: boolean
  viewingSessionId: string | null
  projectSettingsOpen: boolean
  systemSettingsOpen: boolean

  // Global text input — any component can register as the target
  textInputOpen: boolean
  textInputLabel: string
  textInputCallback: ((text: string) => void) | null
  textInputInitialValue: string
  textInputKeepOpen: boolean
  textInputTargetEl: HTMLElement | null
  // Monotonically increasing counter + text to append (watched by GlobalTextInput)
  textInputAppendSeq: number
  textInputAppendText: string

  setTab: (tab: TabId) => void
  toggleSidebar: () => void
  toggleBottomPanel: () => void
  setBottomPanelHeight: (h: number) => void
  toggleChat: () => void
  toggleCommandPalette: () => void
  setCommandPaletteOpen: (open: boolean) => void
  setAddProjectOpen: (open: boolean) => void
  setTheme: (theme: string) => void
  setLayout: (layout: string) => void
  toast: (message: string, type?: Toast["type"]) => void
  dismissToast: (id: string) => void
  selectTask: (id: string | null) => void
  toggleAgentPanel: () => void
  setAgentPanelOpen: (open: boolean) => void
  setViewingSessionId: (id: string | null) => void
  setProjectSettingsOpen: (open: boolean) => void
  setSystemSettingsOpen: (open: boolean) => void
  openTextInput: (callback: (text: string) => void, label?: string, initialValue?: string, keepOpen?: boolean, targetEl?: HTMLElement | null) => void
  appendTextInput: (text: string) => void
  closeTextInput: () => void
}

let toastCounter = 0

export const useUIStore = create<UIState>((set, get) => ({
  currentTab: (localStorage.getItem("rdc_tab") as TabId) || "processes",
  sidebarOpen: true,
  bottomPanelOpen: false,
  bottomPanelHeight: 300,
  chatOpen: localStorage.getItem("rdc_chat_open") !== "false",
  commandPaletteOpen: false,
  addProjectOpen: false,
  theme: localStorage.getItem("rdc_theme") || "default",
  layout: localStorage.getItem("rdc_layout") || "desktop",
  toasts: [],
  selectedTaskId: null,
  agentPanelOpen: false,
  viewingSessionId: null,
  projectSettingsOpen: false,
  systemSettingsOpen: false,
  textInputOpen: false,
  textInputLabel: "Terminal",
  textInputCallback: null,
  textInputInitialValue: "",
  textInputKeepOpen: false,
  textInputTargetEl: null,
  textInputAppendSeq: 0,
  textInputAppendText: "",

  setTab: (tab) => {
    localStorage.setItem("rdc_tab", tab)
    set({ currentTab: tab })
  },

  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  toggleBottomPanel: () =>
    set((s) => ({ bottomPanelOpen: !s.bottomPanelOpen })),

  setBottomPanelHeight: (h) => set({ bottomPanelHeight: h }),

  toggleChat: () => set((s) => {
    const next = !s.chatOpen
    localStorage.setItem("rdc_chat_open", String(next))
    return { chatOpen: next }
  }),

  toggleCommandPalette: () =>
    set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),

  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),

  setAddProjectOpen: (open) => set({ addProjectOpen: open }),

  setTheme: (theme) => {
    localStorage.setItem("rdc_theme", theme)
    document.documentElement.setAttribute("data-theme", theme)
    set({ theme })
  },

  setLayout: (layout) => {
    localStorage.setItem("rdc_layout", layout)
    document.documentElement.setAttribute("data-layout", layout)
    set({ layout })
  },

  toast: (message, type = "info") => {
    const id = `toast-${++toastCounter}`
    set((s) => ({ toasts: [...s.toasts, { id, message, type }] }))
    setTimeout(() => get().dismissToast(id), 4000)
  },

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  selectTask: (id) => set({ selectedTaskId: id }),

  toggleAgentPanel: () => set((s) => ({ agentPanelOpen: !s.agentPanelOpen })),
  setAgentPanelOpen: (open) => set({ agentPanelOpen: open }),
  setViewingSessionId: (id) => set({ viewingSessionId: id }),
  setProjectSettingsOpen: (open) => set({ projectSettingsOpen: open }),
  setSystemSettingsOpen: (open) => set({ systemSettingsOpen: open }),

  openTextInput: (callback, label, initialValue, keepOpen, targetEl) =>
    set({ textInputOpen: true, textInputCallback: callback, textInputLabel: label || "Input", textInputInitialValue: initialValue || "", textInputKeepOpen: keepOpen || false, textInputTargetEl: targetEl || null }),

  appendTextInput: (text) =>
    set((s) => ({ textInputAppendSeq: s.textInputAppendSeq + 1, textInputAppendText: text })),

  closeTextInput: () => {
    const targetEl = get().textInputTargetEl
    set({ textInputOpen: false, textInputCallback: null, textInputTargetEl: null })
    if (targetEl && document.contains(targetEl)) {
      setTimeout(() => targetEl.focus(), 250)
    }
  },
}))
