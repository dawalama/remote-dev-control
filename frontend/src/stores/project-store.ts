import { create } from "zustand"
import { GET, POST, DELETE as DEL } from "@/lib/api"
import { useStateStore } from "@/stores/state-store"
import type { Project, Collection } from "@/types"

/** Returns sorted list of project names that have running terminals, processes, in-progress tasks, or active agents */
export function getActiveProjectNames(): string[] {
  const { terminals, actions: processes, tasks, agents } = useStateStore.getState()
  const { projects } = useProjectStore.getState()
  const projectNames = new Set(projects.map((p) => p.name))
  const active = new Set<string>()

  for (const t of terminals) {
    if (t.status === "running" && projectNames.has(t.project)) active.add(t.project)
  }
  for (const p of processes) {
    if (p.status === "running" && projectNames.has(p.project)) active.add(p.project)
  }
  for (const t of tasks) {
    if (t.status === "in_progress" && t.project && projectNames.has(t.project)) active.add(t.project)
  }
  for (const a of agents) {
    if ((a.status === "working" || a.status === "running") && projectNames.has(a.project)) active.add(a.project)
  }

  return [...active].sort()
}

interface ProjectState {
  projects: Project[]
  collections: Collection[]
  currentProject: string | null
  currentCollection: string | null
  activeOnly: boolean
  loading: boolean

  loadProjects: () => Promise<void>
  loadCollections: () => Promise<void>
  selectProject: (name: string | null) => void
  selectCollection: (id: string | null) => void
  deleteProject: (name: string) => Promise<void>
  scaffoldProject: (name: string, description: string, collectionId?: string) => Promise<void>
  toggleActiveFilter: () => void
  cycleActiveProject: (direction: 1 | -1) => void
}

// Migrate legacy "all" sentinel (from pre-refactor localStorage) to null.
const _rawProject = localStorage.getItem("rdc_current_project")
const _rawCollection = localStorage.getItem("rdc_current_collection")
const _storedProject = _rawProject && _rawProject !== "all" ? _rawProject : null
const _storedCollection = _rawCollection && _rawCollection !== "all" ? _rawCollection : null
if (!_storedProject) localStorage.removeItem("rdc_current_project")
if (!_storedCollection) localStorage.removeItem("rdc_current_collection")

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  collections: [],
  currentProject: _storedProject,
  currentCollection: _storedCollection,
  activeOnly: localStorage.getItem("rdc_active_filter") === "true",
  loading: false,

  loadProjects: async () => {
    set({ loading: true })
    try {
      const data = await GET<Project[]>("/projects")
      set({ projects: data })
    } finally {
      set({ loading: false })
    }
  },

  loadCollections: async () => {
    try {
      const data = await GET<Collection[]>("/collections")
      set({ collections: data })
    } catch {
      // Collections may not be available
    }
  },

  selectProject: (name) => {
    if (name === null) {
      localStorage.removeItem("rdc_current_project")
      set({ currentProject: null })
      useStateStore.getState().sendEvent("select_project", { project: null })
      return
    }
    localStorage.setItem("rdc_current_project", name)
    // Auto-switch collection to match project's collection
    const { projects, currentCollection } = get()
    const proj = projects.find((p) => p.name === name)
    if (proj?.collection_id && proj.collection_id !== currentCollection) {
      localStorage.setItem("rdc_current_collection", proj.collection_id)
      set({ currentProject: name, currentCollection: proj.collection_id })
    } else {
      set({ currentProject: name })
    }
    // Notify server so paired phone calls can resolve the current project
    useStateStore.getState().sendEvent("select_project", { project: name })
  },

  selectCollection: (id) => {
    if (id === null) {
      localStorage.removeItem("rdc_current_collection")
      set({ currentCollection: null, currentProject: null })
      localStorage.removeItem("rdc_current_project")
      return
    }
    localStorage.setItem("rdc_current_collection", id)
    // Auto-select first project in collection; if empty, leave project unselected
    const { projects } = get()
    const filtered = projects.filter((p) => p.collection_id === id)
    const firstProject = filtered.length > 0 ? filtered[0].name : null
    if (firstProject) {
      localStorage.setItem("rdc_current_project", firstProject)
    } else {
      localStorage.removeItem("rdc_current_project")
    }
    set({ currentCollection: id, currentProject: firstProject })
  },

  deleteProject: async (name: string) => {
    await DEL(`/projects/${encodeURIComponent(name)}`)
    const { currentProject } = get()
    if (currentProject === name) {
      localStorage.removeItem("rdc_current_project")
      set({ currentProject: null })
    }
    await get().loadProjects()
  },

  scaffoldProject: async (name: string, description: string, collectionId?: string) => {
    const body: Record<string, string> = { name, description }
    if (collectionId) body.collection_id = collectionId
    await POST("/projects/scaffold", body)
    await get().loadProjects()
    set({ currentProject: name })
  },

  toggleActiveFilter: () => {
    const next = !get().activeOnly
    localStorage.setItem("rdc_active_filter", String(next))
    set({ activeOnly: next })
  },

  cycleActiveProject: (direction: 1 | -1) => {
    const active = getActiveProjectNames()
    if (active.length === 0) return
    const current = get().currentProject
    const idx = current ? active.indexOf(current) : -1
    const next = idx === -1
      ? active[0]
      : active[(idx + direction + active.length) % active.length]
    get().selectProject(next)
  },
}))
