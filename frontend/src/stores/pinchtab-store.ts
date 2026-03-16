import { create } from "zustand"
import { GET, POST } from "@/lib/api"

interface PinchTab {
  id: string
  title: string
  url: string
}

interface SnapshotElement {
  ref: string
  role: string
  name: string
  children?: SnapshotElement[]
  [key: string]: unknown
}

interface TabCache {
  snapshot: SnapshotElement[] | null
  screenshotDataUrl: string | null
  screenshotTime: string | null
  pageText: string | null
}

interface FindResult {
  ref: string
  role: string
  name: string
  confidence: number
  [key: string]: unknown
}

interface PinchTabStore {
  available: boolean
  tabs: PinchTab[]
  activeTabId: string | null
  snapshot: SnapshotElement[] | null
  screenshotDataUrl: string | null
  screenshotTime: string | null
  pageText: string | null
  loading: boolean
  tabCache: Record<string, TabCache>
  findResults: FindResult[] | null

  loadStatus: () => Promise<void>
  navigate: (url: string) => Promise<void>
  takeSnapshot: () => Promise<void>
  takeScreenshot: () => Promise<void>
  extractText: () => Promise<void>
  performAction: (type: string, ref: string | number, value?: string) => Promise<void>
  setActiveTab: (id: string) => void
  closeTab: (tabId: string) => Promise<void>
  startPinchTab: () => Promise<void>
  findElements: (description: string) => Promise<void>
}

export const usePinchTabStore = create<PinchTabStore>((set, get) => ({
  available: false,
  tabs: [],
  activeTabId: null,
  snapshot: null,
  screenshotDataUrl: null,
  screenshotTime: null,
  pageText: null,
  loading: false,
  tabCache: {},
  findResults: null,

  loadStatus: async () => {
    try {
      const data = await GET<{ available: boolean; tabs: PinchTab[] }>("/pinchtab/status")
      const current = get().activeTabId
      const tabs = data.tabs || []
      set({
        available: data.available,
        tabs,
        // Keep active tab if still present, otherwise pick first
        activeTabId: tabs.find((t) => t.id === current) ? current : tabs[0]?.id || null,
      })
    } catch {
      set({ available: false, tabs: [] })
    }
  },

  navigate: async (url: string) => {
    set({ loading: true })
    try {
      const tabId = get().activeTabId
      await POST("/pinchtab/navigate", { url, tab_id: tabId || undefined })
      await get().loadStatus()
      await get().takeScreenshot()
    } catch { /* */ }
    set({ loading: false })
  },

  takeSnapshot: async () => {
    try {
      const tabId = get().activeTabId
      const params = tabId ? `?tab_id=${encodeURIComponent(tabId)}` : ""
      const data = await GET<{ elements?: SnapshotElement[]; nodes?: SnapshotElement[]; error?: string }>(`/pinchtab/snapshot${params}`)
      if (data.error) { set({ snapshot: null }); return }
      const snapshot = data.nodes || data.elements || (Array.isArray(data) ? data : null)
      set({ snapshot })
      // Update cache for active tab
      if (tabId) {
        const cache = get().tabCache
        set({ tabCache: { ...cache, [tabId]: { ...cache[tabId], snapshot } } })
      }
    } catch { /* */ }
  },

  takeScreenshot: async () => {
    try {
      const tabId = get().activeTabId
      const params = tabId ? `?tab_id=${encodeURIComponent(tabId)}` : ""
      const data = await GET<{ base64?: string; data?: string }>(`/pinchtab/screenshot${params}`)
      const b64 = data.base64 || data.data
      if (b64) {
        // Detect JPEG vs PNG from base64 header
        const mime = b64.startsWith("/9j/") ? "image/jpeg" : "image/png"
        const screenshotDataUrl = `data:${mime};base64,${b64}`
        const screenshotTime = new Date().toLocaleTimeString()
        set({ screenshotDataUrl, screenshotTime })
        // Update cache for active tab
        if (tabId) {
          const cache = get().tabCache
          set({ tabCache: { ...cache, [tabId]: { ...cache[tabId], screenshotDataUrl, screenshotTime } } })
        }
      }
    } catch { /* */ }
  },

  extractText: async () => {
    try {
      const tabId = get().activeTabId
      const params = tabId ? `?tab_id=${encodeURIComponent(tabId)}` : ""
      const data = await GET<{ text?: string; error?: string }>(`/pinchtab/text${params}`)
      if (data.error) {
        set({ pageText: `[PinchTab error] ${data.error}` })
        return
      }
      const pageText = data.text || "(no text extracted)"
      set({ pageText })
      // Update cache for active tab
      if (tabId) {
        const cache = get().tabCache
        set({ tabCache: { ...cache, [tabId]: { ...cache[tabId], pageText } } })
      }
    } catch { /* */ }
  },

  performAction: async (type: string, ref: string | number, value?: string) => {
    try {
      const tabId = get().activeTabId
      const params = tabId ? `?tab_id=${encodeURIComponent(tabId)}` : ""
      // Snapshot first so refs are registered
      await GET(`/pinchtab/snapshot${params}`)
      if (type === "fill") {
        // Click to focus, then type (fill is unreliable)
        await POST("/pinchtab/action", { type: "click", ref: String(ref), tab_id: tabId || undefined })
        await POST("/pinchtab/action", { type: "type", ref: String(ref), value, tab_id: tabId || undefined })
      } else {
        await POST("/pinchtab/action", { type, ref: String(ref), value, tab_id: tabId || undefined })
      }
      // Refresh screenshot after action
      await get().takeScreenshot()
    } catch { /* */ }
  },

  setActiveTab: (id: string) => {
    const { activeTabId, snapshot, screenshotDataUrl, screenshotTime, pageText, tabCache } = get()
    // Save current tab state to cache
    const newCache = { ...tabCache }
    if (activeTabId) {
      newCache[activeTabId] = { snapshot, screenshotDataUrl, screenshotTime, pageText }
    }
    // Restore from cache or clear
    const cached = newCache[id]
    set({
      activeTabId: id,
      snapshot: cached?.snapshot ?? null,
      screenshotDataUrl: cached?.screenshotDataUrl ?? null,
      screenshotTime: cached?.screenshotTime ?? null,
      pageText: cached?.pageText ?? null,
      tabCache: newCache,
    })
  },

  closeTab: async (tabId: string) => {
    try {
      await POST(`/pinchtab/tabs/${tabId}/close`)
      await get().loadStatus()
    } catch { /* */ }
  },

  startPinchTab: async () => {
    set({ loading: true })
    try {
      await POST("/pinchtab/navigate", { url: "about:blank" })
      await get().loadStatus()
    } catch { /* */ }
    set({ loading: false })
  },

  findElements: async (description: string) => {
    try {
      const tabId = get().activeTabId
      const data = await POST<{ results?: FindResult[] }>("/pinchtab/find", {
        description,
        tab_id: tabId || undefined,
      })
      set({ findResults: data.results || null })
    } catch { /* */ }
  },
}))
