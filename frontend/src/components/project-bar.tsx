import { useState, useRef, useEffect, useMemo } from "react"
import { useProjectStore, getActiveProjectNames } from "@/stores/project-store"
import { useStateStore } from "@/stores/state-store"
import { useUIStore } from "@/stores/ui-store"
import { GET } from "@/lib/api"
import { CollectionsManagerModal } from "@/features/modals/collections-manager"

export function ProjectBar() {
  const { projects, collections, currentProject, currentCollection, selectProject, selectCollection, scaffoldProject, loadProjects, activeOnly, toggleActiveFilter } =
    useProjectStore()
  const addProjectOpen = useUIStore((s) => s.addProjectOpen)
  const setAddProjectOpen = useUIStore((s) => s.setAddProjectOpen)
  const [showCollections, setShowCollections] = useState(false)

  // Subscribe to state-store fields that affect activity detection
  const terminals = useStateStore((s) => s.terminals)
  const processes = useStateStore((s) => s.processes)
  const tasks = useStateStore((s) => s.tasks)
  const agents = useStateStore((s) => s.agents)

  const activeSet = useMemo(() => {
    // Re-derive when any dependency changes
    void terminals; void processes; void tasks; void agents
    return new Set(getActiveProjectNames())
  }, [terminals, processes, tasks, agents])

  // When activeOnly, show all active projects across collections; otherwise filter by collection
  const filteredProjects = activeOnly
    ? projects.filter((p) => activeSet.has(p.name))
    : currentCollection === "all"
      ? projects
      : projects.filter((p) => p.collection_id === currentCollection)

  const names = ["all", ...filteredProjects.map((p) => p.name)]

  const hasActiveProjects = activeSet.size > 0

  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 flex-wrap">
        {/* Collection dropdown */}
        {collections.length > 0 && (
          <>
            <select
              value={currentCollection}
              onChange={(e) => selectCollection(e.target.value)}
              className="bg-gray-700 rounded px-2 py-1 text-sm text-gray-200 outline-none cursor-pointer"
            >
              <option value="all">All ({projects.length})</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.project_count || c.projects?.length || 0})
                </option>
              ))}
            </select>
            <button
              className="text-gray-500 text-[10px] hover:text-gray-300"
              onClick={() => setShowCollections(true)}
              title="Manage collections"
            >
              ⚙
            </button>
            <span className="text-gray-500 text-sm">›</span>
          </>
        )}
        {names.map((name) => (
          <button
            key={name}
            className={`relative px-3 py-1 rounded text-sm transition-colors ${
              currentProject === name
                ? "bg-blue-600 text-white"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600"
            }`}
            onClick={() => selectProject(name)}
          >
            {name === "all" ? "All" : name}
            {name !== "all" && activeSet.has(name) && (
              <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-green-400" />
            )}
          </button>
        ))}
        {activeOnly && !hasActiveProjects && names.length <= 1 && (
          <span className="text-xs text-gray-500 italic">No active projects</span>
        )}
        <button
          className="px-3 py-1 rounded text-sm bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200"
          onClick={() => setAddProjectOpen(true)}
          title="Add Project"
        >
          +
        </button>
        <button
          className={`px-2 py-1 rounded text-sm transition-colors ${
            activeOnly
              ? "bg-green-600/30 text-green-400 border border-green-600/50"
              : "bg-gray-700 text-gray-500 hover:text-gray-300"
          }`}
          onClick={toggleActiveFilter}
          title={activeOnly ? "Show all projects" : "Show active projects only"}
        >
          ⚡
        </button>
      </div>
      {addProjectOpen && (
        <AddProjectModal
          onClose={() => setAddProjectOpen(false)}
          onCreated={() => {
            loadProjects()
            setAddProjectOpen(false)
          }}
          scaffoldProject={scaffoldProject}
        />
      )}
      {showCollections && <CollectionsManagerModal onClose={() => setShowCollections(false)} />}
    </div>
  )
}

// ---- Command Palette (⌘K) ----

export function CommandPalette() {
  const open = useUIStore((s) => s.commandPaletteOpen)
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen)
  const { projects, collections, selectProject, selectCollection } = useProjectStore()
  const [query, setQuery] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  // Subscribe to state-store for activity detection
  const terminals = useStateStore((s) => s.terminals)
  const processes = useStateStore((s) => s.processes)
  const tasks = useStateStore((s) => s.tasks)
  const agents = useStateStore((s) => s.agents)

  const activeSet = useMemo(() => {
    void terminals; void processes; void tasks; void agents
    return new Set(getActiveProjectNames())
  }, [terminals, processes, tasks, agents])

  useEffect(() => {
    if (open) {
      setQuery("")
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  if (!open) return null

  const q = query.toLowerCase()

  // Build grouped results: collections first, then ungrouped projects
  const matchingCollections = q
    ? collections.filter((c) => c.name.toLowerCase().includes(q))
    : collections
  const allNames = ["all", ...projects.map((p) => p.name)]
  const filtered = q
    ? allNames.filter((n) => n.toLowerCase().includes(q))
    : allNames
  // Sort active projects to top (keep "all" first)
  const matchingProjects = filtered.sort((a, b) => {
    if (a === "all") return -1
    if (b === "all") return 1
    const aActive = activeSet.has(a) ? 0 : 1
    const bActive = activeSet.has(b) ? 0 : 1
    return aActive - bActive
  })

  const pick = (name: string) => {
    selectProject(name)
    setOpen(false)
  }

  const pickCollection = (id: string) => {
    selectCollection(id)
    setOpen(false)
  }

  const hasResults = matchingCollections.length > 0 || matchingProjects.length > 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-[420px] bg-gray-800 border border-gray-600 rounded-lg shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-3 border-b border-gray-700">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false)
              if (e.key === "Enter" && matchingProjects.length > 0) pick(matchingProjects[0])
            }}
            placeholder="Search projects & collections..."
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
          />
        </div>
        <div className="max-h-[300px] overflow-auto">
          {!hasResults && (
            <div className="p-4 text-center text-sm text-gray-500">No matches</div>
          )}
          {/* Collections */}
          {matchingCollections.length > 0 && (
            <>
              <div className="px-4 py-1.5 text-[10px] font-semibold text-gray-500 uppercase tracking-wider bg-gray-800/50">
                Collections
              </div>
              {matchingCollections.map((c) => (
                <button
                  key={c.id}
                  className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 flex items-center gap-2"
                  onClick={() => pickCollection(c.id)}
                >
                  <span className="w-2 h-2 rounded-sm bg-blue-600 flex-shrink-0" />
                  {c.name}
                  <span className="text-[10px] text-gray-500 ml-auto">
                    {c.project_count || c.projects?.length || 0} projects
                  </span>
                </button>
              ))}
            </>
          )}
          {/* Projects */}
          {matchingProjects.length > 0 && (
            <>
              <div className="px-4 py-1.5 text-[10px] font-semibold text-gray-500 uppercase tracking-wider bg-gray-800/50">
                Projects
              </div>
              {matchingProjects.map((name) => {
                const proj = projects.find((p) => p.name === name)
                const col = proj?.collection_id
                  ? collections.find((c) => c.id === proj.collection_id)
                  : null
                const isActive = name !== "all" && activeSet.has(name)
                return (
                  <button
                    key={name}
                    className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 flex items-center gap-2"
                    onClick={() => pick(name)}
                  >
                    <span className={`w-2 h-2 rounded-full flex-shrink-0 ${isActive ? "bg-green-400" : "bg-blue-500"}`} />
                    {name === "all" ? "All Projects" : name}
                    {col && (
                      <span className="text-[10px] text-gray-500 ml-auto">{col.name}</span>
                    )}
                  </button>
                )
              })}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ---- Add Project Modal ----

interface DirEntry {
  name: string
  path: string
  is_project: boolean
}

interface BrowseResult {
  current: string
  parent: string | null
  dirs: DirEntry[]
}

function AddProjectModal({
  onClose,
  onCreated,
  scaffoldProject,
}: {
  onClose: () => void
  onCreated: () => void
  scaffoldProject: (name: string, description: string, collectionId?: string) => Promise<void>
}) {
  const { collections, currentCollection } = useProjectStore()
  const [tab, setTab] = useState<"create" | "connect">("create")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [collectionId, setCollectionId] = useState(currentCollection !== "all" ? currentCollection : "general")
  const [gitUrl, setGitUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  // File browser state
  const [showBrowser, setShowBrowser] = useState(false)
  const [browseEntries, setBrowseEntries] = useState<DirEntry[]>([])
  const [browseParent, setBrowseParent] = useState<string | null>(null)
  const [browseCurrent, setBrowseCurrent] = useState("")
  const [browseLoading, setBrowseLoading] = useState(false)

  const fetchDir = async (path: string) => {
    setBrowseLoading(true)
    try {
      const data = await GET<BrowseResult>(`/browse?path=${encodeURIComponent(path)}`)
      setBrowseEntries(data.dirs)
      setBrowseParent(data.parent)
      setBrowseCurrent(data.current)
    } catch {
      setError("Failed to browse directory")
    } finally {
      setBrowseLoading(false)
    }
  }

  useEffect(() => {
    if (showBrowser) fetchDir("~")
  }, [showBrowser])

  const handleSelectPath = () => {
    setGitUrl(browseCurrent)
    setShowBrowser(false)
  }

  const handleCreate = async () => {
    if (!name.trim()) return
    setLoading(true)
    setError("")
    try {
      await scaffoldProject(name.trim(), description.trim(), collectionId || undefined)
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create project")
    } finally {
      setLoading(false)
    }
  }

  const handleConnect = async () => {
    if (!gitUrl.trim()) return
    setLoading(true)
    setError("")
    try {
      const pathVal = gitUrl.trim()
      const segments = pathVal.replace(/\/+$/, "").split("/")
      const pathName = segments[segments.length - 1] || pathVal
      const token = localStorage.getItem("rdc_token")
      const resp = await fetch("/projects", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ path: pathVal, name: pathName, collection_id: collectionId || undefined }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to connect project")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      <div
        className="w-[440px] bg-gray-800 border border-gray-600 rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h3 className="text-sm font-semibold text-gray-200">Add Project</h3>
          <button className="text-gray-400 hover:text-gray-200" onClick={onClose}>
            &times;
          </button>
        </div>

        {/* Tab selector */}
        <div className="flex border-b border-gray-700">
          {(["create", "connect"] as const).map((t) => (
            <button
              key={t}
              className={`flex-1 px-4 py-2 text-xs font-medium ${
                tab === t
                  ? "text-blue-400 border-b-2 border-blue-400"
                  : "text-gray-400 hover:text-gray-200"
              }`}
              onClick={() => setTab(t)}
            >
              {t === "create" ? "Create New" : "Connect Existing"}
            </button>
          ))}
        </div>

        <div className="p-4 space-y-3">
          {/* Collection picker (shared between tabs) */}
          {collections.length > 0 && (
            <div>
              <label className="text-xs text-gray-400 mb-1 block">Collection</label>
              <select
                value={collectionId}
                onChange={(e) => setCollectionId(e.target.value)}
                className="input-cls"
              >
                <option value="general">General</option>
                {collections.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}

          {tab === "create" ? (
            <>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">Project Name</label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="my-project"
                  className="input-cls"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">Description</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What does this project do?"
                  rows={3}
                  className="input-cls resize-none"
                />
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">Git URL or Local Path</label>
                <input
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                  placeholder="https://github.com/user/repo or /path/to/project"
                  className="input-cls"
                  autoFocus
                />
              </div>
              <button
                type="button"
                className="text-xs text-blue-400 hover:text-blue-300"
                onClick={() => setShowBrowser(!showBrowser)}
              >
                {showBrowser ? "Hide browser" : "Browse..."}
              </button>
              {showBrowser && (
                <div className="bg-gray-900 rounded-lg p-2 border border-gray-700 max-h-64 flex flex-col">
                  <div className="text-[10px] text-gray-400 mb-1.5 truncate px-1">
                    {browseCurrent || "~"}
                  </div>
                  {browseLoading ? (
                    <div className="text-xs text-gray-500 text-center py-4">Loading...</div>
                  ) : (
                    <div className="flex-1 overflow-y-auto space-y-0.5">
                      {browseParent && (
                        <button
                          className="w-full text-left px-2 py-1 text-xs rounded hover:bg-gray-800 text-gray-400"
                          onClick={() => fetchDir(browseParent)}
                        >
                          ..
                        </button>
                      )}
                      {browseEntries.map((entry) => (
                        <button
                          key={entry.path}
                          className={`w-full text-left px-2 py-1 text-xs rounded hover:bg-gray-800 flex items-center gap-1.5 ${
                            entry.is_project ? "text-green-400" : "text-gray-300"
                          }`}
                          onClick={() => fetchDir(entry.path)}
                        >
                          <span>{entry.is_project ? "📁" : "📂"}</span>
                          <span className="truncate">{entry.name}</span>
                          {entry.is_project && (
                            <span className="ml-auto text-[9px] bg-green-600/20 text-green-400 px-1 rounded shrink-0">
                              project
                            </span>
                          )}
                        </button>
                      ))}
                      {browseEntries.length === 0 && !browseLoading && (
                        <p className="text-[10px] text-gray-500 text-center py-2">No subdirectories</p>
                      )}
                    </div>
                  )}
                  <button
                    className="mt-2 w-full py-1 text-xs font-medium rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
                    onClick={handleSelectPath}
                  >
                    Select this directory
                  </button>
                </div>
              )}
            </>
          )}

          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex justify-end gap-2 pt-1">
            <button
              className="px-3 py-1.5 text-xs rounded bg-gray-700 text-gray-300 hover:bg-gray-600"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
              disabled={loading || (tab === "create" ? !name.trim() : !gitUrl.trim())}
              onClick={tab === "create" ? handleCreate : handleConnect}
            >
              {loading ? "..." : tab === "create" ? "Create" : "Connect"}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
