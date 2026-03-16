import { create } from "zustand"
import { GET, POST, PUT } from "@/lib/api"

export interface WikiNode {
  id: string
  name: string
  type: string
  summary?: string
  file?: string
  tags?: string[]
  children?: WikiNode[]
  content?: string
}

interface WikiState {
  tree: WikiNode | null
  selectedNodeId: string | null
  nodeContent: string | null
  searchQuery: string
  searchResults: WikiNode[]
  loading: boolean
  editing: boolean
  editContent: string

  fetchTree: (project?: string) => Promise<void>
  fetchNode: (nodeId: string) => Promise<void>
  search: (query: string, project?: string) => Promise<void>
  setSelectedNodeId: (id: string | null) => void
  setSearchQuery: (q: string) => void
  refresh: () => Promise<void>
  createDoc: (project: string, filename: string, content: string) => Promise<void>
  updateDoc: (nodeId: string, content: string) => Promise<void>
  setEditing: (editing: boolean) => void
  setEditContent: (content: string) => void
}

export const useWikiStore = create<WikiState>((set, get) => ({
  tree: null,
  selectedNodeId: null,
  nodeContent: null,
  searchQuery: "",
  searchResults: [],
  loading: false,
  editing: false,
  editContent: "",

  fetchTree: async (project?: string) => {
    set({ loading: true })
    try {
      const url = project ? `/knowledge/project/${encodeURIComponent(project)}` : "/knowledge"
      const tree = await GET<WikiNode>(url)
      set({ tree, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  fetchNode: async (nodeId: string) => {
    set({ selectedNodeId: nodeId, nodeContent: null, loading: true })
    try {
      const node = await GET<WikiNode & { content?: string }>(`/knowledge/node/${encodeURIComponent(nodeId)}`)
      set({ nodeContent: node.content ?? null, loading: false })
    } catch {
      set({ nodeContent: null, loading: false })
    }
  },

  search: async (query: string, project?: string) => {
    if (!query.trim()) {
      set({ searchResults: [], searchQuery: "" })
      return
    }
    set({ searchQuery: query, loading: true })
    try {
      const params = new URLSearchParams({ q: query })
      if (project) params.set("project", project)
      const results = await GET<WikiNode[]>(`/knowledge/search?${params}`)
      set({ searchResults: results, loading: false })
    } catch {
      set({ searchResults: [], loading: false })
    }
  },

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setSearchQuery: (q) => set({ searchQuery: q }),

  refresh: async () => {
    set({ loading: true })
    try {
      await POST("/knowledge/refresh")
      await get().fetchTree()
    } catch {
      set({ loading: false })
    }
  },

  createDoc: async (project: string, filename: string, content: string) => {
    set({ loading: true })
    try {
      await POST("/knowledge/docs", { project, filename, content })
      await get().fetchTree(project)
      set({ loading: false })
    } catch {
      set({ loading: false })
      throw new Error("Failed to create document")
    }
  },

  updateDoc: async (nodeId: string, content: string) => {
    set({ loading: true })
    try {
      await PUT(`/knowledge/docs/${encodeURIComponent(nodeId)}`, { content })
      set({ nodeContent: content, editing: false, loading: false })
    } catch {
      set({ loading: false })
      throw new Error("Failed to update document")
    }
  },

  setEditing: (editing) => {
    const state = get()
    set({ editing, editContent: editing ? (state.nodeContent || "") : "" })
  },

  setEditContent: (content) => set({ editContent: content }),
}))
