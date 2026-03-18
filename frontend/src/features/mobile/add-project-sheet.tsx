import { useState, useEffect } from "react"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { GET } from "@/lib/api"
import { CollectionPicker, DEFAULT_COLLECTION_ID } from "@/components/collection-picker"
import { Sheet } from "./sheet"

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

export function AddProjectSheet({ onClose }: { onClose: () => void }) {
  const scaffoldProject = useProjectStore((s) => s.scaffoldProject)
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const collections = useProjectStore((s) => s.collections)
  const currentCollection = useProjectStore((s) => s.currentCollection)
  const toast = useUIStore((s) => s.toast)

  const [tab, setTab] = useState<"create" | "connect">("create")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [collectionId, setCollectionId] = useState(currentCollection !== "all" ? currentCollection : DEFAULT_COLLECTION_ID)
  const [connectPath, setConnectPath] = useState("")
  const [loading, setLoading] = useState(false)

  // File browser state
  const [showBrowser, setShowBrowser] = useState(false)
  const [browsingPath, setBrowsingPath] = useState("~")
  const [browseEntries, setBrowseEntries] = useState<DirEntry[]>([])
  const [browseParent, setBrowseParent] = useState<string | null>(null)
  const [browseCurrent, setBrowseCurrent] = useState("")
  const [browseLoading, setBrowseLoading] = useState(false)

  const handleCreate = async () => {
    if (!name.trim()) return
    setLoading(true)
    try {
      await scaffoldProject(name.trim(), description.trim(), collectionId || undefined)
      toast("Project created", "success")
      loadProjects()
      onClose()
    } catch {
      toast("Failed to create", "error")
    } finally {
      setLoading(false)
    }
  }

  const handleConnect = async () => {
    if (!connectPath.trim()) return
    setLoading(true)
    try {
      const pathVal = connectPath.trim()
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
      toast("Project connected", "success")
      loadProjects()
      onClose()
    } catch {
      toast("Failed to connect", "error")
    } finally {
      setLoading(false)
    }
  }

  // Fetch directory listing
  const fetchDir = async (path: string) => {
    setBrowseLoading(true)
    try {
      const data = await GET<BrowseResult>(`/browse?path=${encodeURIComponent(path)}`)
      setBrowseEntries(data.dirs)
      setBrowseParent(data.parent)
      setBrowseCurrent(data.current)
      setBrowsingPath(data.current)
    } catch {
      toast("Failed to browse directory", "error")
    } finally {
      setBrowseLoading(false)
    }
  }

  useEffect(() => {
    if (showBrowser) {
      fetchDir(browsingPath)
    }
  }, [showBrowser])

  const handleSelectPath = () => {
    setConnectPath(browseCurrent)
    setShowBrowser(false)
  }

  return (
    <Sheet title="Add Project" onClose={onClose}>
      {/* Tab toggle */}
      <div className="flex gap-1 mb-4 bg-gray-900 rounded-lg p-1">
        {(["create", "connect"] as const).map((t) => (
          <button
            key={t}
            className={`flex-1 py-1.5 text-xs font-medium rounded ${
              tab === t ? "bg-blue-600 text-white" : "text-gray-400"
            }`}
            onClick={() => setTab(t)}
          >
            {t === "create" ? "Create New" : "Connect Existing"}
          </button>
        ))}
      </div>

      {/* Collection picker */}
      <div className="mb-3">
        <label className="text-xs text-gray-400 mb-1 block">Collection</label>
        <CollectionPicker
          value={collectionId}
          onChange={setCollectionId}
          collections={collections}
        />
      </div>

      {tab === "create" ? (
        <div className="space-y-3">
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
          <button
            className="w-full py-2 text-sm font-medium rounded bg-blue-600 text-white disabled:opacity-50"
            disabled={!name.trim() || loading}
            onClick={handleCreate}
          >
            {loading ? "Creating..." : "Create Project"}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Local Path</label>
            <input
              value={connectPath}
              onChange={(e) => setConnectPath(e.target.value)}
              placeholder="/home/user/my-project"
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

          {/* File browser */}
          {showBrowser && (
            <div className="bg-gray-900 rounded-lg p-2 border border-gray-700 max-h-64 flex flex-col">
              {/* Current path */}
              <div className="text-[10px] text-gray-400 mb-1.5 truncate px-1">
                {browseCurrent || browsingPath}
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

          <button
            className="w-full py-2 text-sm font-medium rounded bg-blue-600 text-white disabled:opacity-50"
            disabled={!connectPath.trim() || loading}
            onClick={handleConnect}
          >
            {loading ? "Connecting..." : "Connect Project"}
          </button>
        </div>
      )}
    </Sheet>
  )
}
