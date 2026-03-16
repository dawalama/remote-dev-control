import { useEffect, useState, useCallback } from "react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useWikiStore, type WikiNode } from "./wiki-store"
import { useProjectStore } from "@/stores/project-store"
import { useStateStore } from "@/stores/state-store"

export function DocsPage() {
  const tree = useWikiStore((s) => s.tree)
  const selectedNodeId = useWikiStore((s) => s.selectedNodeId)
  const nodeContent = useWikiStore((s) => s.nodeContent)
  const searchQuery = useWikiStore((s) => s.searchQuery)
  const searchResults = useWikiStore((s) => s.searchResults)
  const loading = useWikiStore((s) => s.loading)
  const editing = useWikiStore((s) => s.editing)
  const editContent = useWikiStore((s) => s.editContent)
  const fetchTree = useWikiStore((s) => s.fetchTree)
  const fetchNode = useWikiStore((s) => s.fetchNode)
  const search = useWikiStore((s) => s.search)
  const setSearchQuery = useWikiStore((s) => s.setSearchQuery)
  const refresh = useWikiStore((s) => s.refresh)
  const createDoc = useWikiStore((s) => s.createDoc)
  const updateDoc = useWikiStore((s) => s.updateDoc)
  const setEditing = useWikiStore((s) => s.setEditing)
  const setEditContent = useWikiStore((s) => s.setEditContent)

  const currentProject = useProjectStore((s) => s.currentProject)
  const projects = useProjectStore((s) => s.projects)
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const connect = useStateStore((s) => s.connect)
  const disconnect = useStateStore((s) => s.disconnect)

  const [searchInput, setSearchInput] = useState("")
  const [showNewDoc, setShowNewDoc] = useState(false)
  const [newProject, setNewProject] = useState("")
  const [newFilename, setNewFilename] = useState("")
  const [newContent, setNewContent] = useState("")
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    connect()
    loadProjects()
    return () => disconnect()
  }, [connect, disconnect, loadProjects])

  useEffect(() => {
    fetchTree()
  }, [fetchTree])

  const handleSearch = useCallback(() => {
    const proj = currentProject !== "all" ? currentProject : undefined
    search(searchInput, proj)
  }, [search, searchInput, currentProject])

  const handleCreate = async () => {
    if (!newProject || !newFilename.trim() || !newContent.trim()) return
    setCreating(true)
    try {
      await createDoc(newProject, newFilename.trim(), newContent.trim())
      setShowNewDoc(false)
      setNewProject("")
      setNewFilename("")
      setNewContent("")
      // Refresh the full tree
      await fetchTree()
    } catch {
      // store already handles error
    }
    setCreating(false)
  }

  const handleSave = async () => {
    if (!selectedNodeId) return
    try {
      await updateDoc(selectedNodeId, editContent)
    } catch {
      // store handles
    }
  }

  // Find selected node name for display
  const selectedNodeName = selectedNodeId && tree ? findNodeName(tree, selectedNodeId) : null

  return (
    <div className="h-screen flex flex-col bg-gray-900 text-gray-100 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 flex-shrink-0">
        <div className="flex items-center gap-3">
          <a
            href="/"
            className="text-sm text-gray-400 hover:text-gray-200"
          >
            ← Back
          </a>
          <h1 className="text-lg font-bold tracking-tight">KNOWLEDGE BASE</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
            onClick={refresh}
          >
            Refresh
          </button>
          <button
            className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
            onClick={() => setShowNewDoc(true)}
          >
            + New Doc
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left sidebar: tree nav */}
        <div className="w-64 flex-shrink-0 border-r border-gray-800 flex flex-col min-h-0">
          {/* Search */}
          <div className="p-3 flex-shrink-0">
            <div className="flex gap-1">
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleSearch() }}
                placeholder="Search docs..."
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-500"
              />
            </div>
          </div>

          {/* Search results */}
          {searchQuery && (
            <div className="px-3 pb-2 flex-shrink-0">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-gray-500">{searchResults.length} results</span>
                <button className="text-[10px] text-blue-400" onClick={() => { setSearchQuery(""); setSearchInput("") }}>Clear</button>
              </div>
              <div className="space-y-0.5 max-h-40 overflow-auto">
                {searchResults.map((r) => (
                  <button
                    key={r.id}
                    className="w-full text-left px-2 py-1 text-xs rounded hover:bg-gray-700 text-gray-300 truncate"
                    onClick={() => fetchNode(r.id)}
                  >
                    {r.name}
                    {r.summary && <span className="text-gray-600 ml-1 text-[10px]">— {r.summary.slice(0, 40)}</span>}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Tree */}
          <div className="flex-1 overflow-auto px-2 pb-3">
            {loading && !tree && <p className="text-xs text-gray-500 py-4 text-center">Loading...</p>}
            {tree && <TreeView node={tree} selectedId={selectedNodeId} onSelect={(id) => { setEditing(false); fetchNode(id) }} depth={0} />}
            {!loading && !tree && <p className="text-xs text-gray-500 py-4 text-center">No knowledge index</p>}
          </div>
        </div>

        {/* Right content */}
        <div className="flex-1 flex flex-col min-h-0 min-w-0">
          {showNewDoc ? (
            <NewDocForm
              projects={projects}
              project={newProject}
              filename={newFilename}
              content={newContent}
              creating={creating}
              onProjectChange={setNewProject}
              onFilenameChange={setNewFilename}
              onContentChange={setNewContent}
              onCreate={handleCreate}
              onCancel={() => setShowNewDoc(false)}
            />
          ) : selectedNodeId ? (
            <div className="flex-1 flex flex-col min-h-0">
              {/* Doc header */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800 flex-shrink-0">
                <h2 className="text-sm font-semibold text-gray-200 truncate">
                  {selectedNodeName || selectedNodeId}
                </h2>
                <div className="flex gap-2">
                  {editing ? (
                    <>
                      <button
                        className="px-3 py-1 text-xs rounded bg-green-600 hover:bg-green-700 text-white"
                        onClick={handleSave}
                        disabled={loading}
                      >
                        Save
                      </button>
                      <button
                        className="px-3 py-1 text-xs rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
                        onClick={() => setEditing(false)}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      className="px-3 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
                      onClick={() => setEditing(true)}
                    >
                      Edit
                    </button>
                  )}
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-auto px-5 py-4">
                {loading && <p className="text-xs text-gray-500">Loading...</p>}
                {editing ? (
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="w-full h-full bg-gray-800 border border-gray-700 rounded p-3 text-sm text-gray-200 font-mono outline-none focus:border-blue-500 resize-none"
                  />
                ) : nodeContent ? (
                  <div className="prose prose-invert prose-sm max-w-none">
                    <Markdown remarkPlugins={[remarkGfm]}>{nodeContent}</Markdown>
                  </div>
                ) : !loading ? (
                  <p className="text-sm text-gray-500">No content available for this node</p>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <p className="text-sm text-gray-500 mb-2">Select a document from the sidebar</p>
                <p className="text-xs text-gray-600">or create a new one with "+ New Doc"</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function NewDocForm({
  projects,
  project,
  filename,
  content,
  creating,
  onProjectChange,
  onFilenameChange,
  onContentChange,
  onCreate,
  onCancel,
}: {
  projects: { name: string }[]
  project: string
  filename: string
  content: string
  creating: boolean
  onProjectChange: (v: string) => void
  onFilenameChange: (v: string) => void
  onContentChange: (v: string) => void
  onCreate: () => void
  onCancel: () => void
}) {
  return (
    <div className="flex-1 flex flex-col p-5 min-h-0">
      <h2 className="text-sm font-semibold mb-4">Create New Document</h2>
      <div className="space-y-3 flex-shrink-0">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Project</label>
          <select
            value={project}
            onChange={(e) => onProjectChange(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
          >
            <option value="">Select project...</option>
            {projects.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Filename</label>
          <input
            type="text"
            value={filename}
            onChange={(e) => onFilenameChange(e.target.value)}
            placeholder="e.g. architecture.md"
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
          />
          <p className="text-[10px] text-gray-600 mt-1">Will be created in the project's .ai/ directory</p>
        </div>
      </div>
      <div className="flex-1 mt-3 min-h-0 flex flex-col">
        <label className="block text-xs text-gray-400 mb-1">Content</label>
        <textarea
          value={content}
          onChange={(e) => onContentChange(e.target.value)}
          placeholder="# Document Title&#10;&#10;Write your content here..."
          className="flex-1 w-full bg-gray-800 border border-gray-700 rounded p-3 text-sm text-gray-200 font-mono outline-none focus:border-blue-500 resize-none"
        />
      </div>
      <div className="flex justify-end gap-2 mt-3 flex-shrink-0">
        <button
          className="px-4 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          className="px-4 py-2 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={onCreate}
          disabled={creating || !project || !filename.trim() || !content.trim()}
        >
          {creating ? "Creating..." : "Create Document"}
        </button>
      </div>
    </div>
  )
}

function TreeView({
  node,
  selectedId,
  onSelect,
  depth,
}: {
  node: WikiNode
  selectedId: string | null
  onSelect: (id: string) => void
  depth: number
}) {
  const [expanded, setExpanded] = useState(depth < 2)
  const hasChildren = node.children && node.children.length > 0
  const isSelected = node.id === selectedId
  const isLeaf = node.type === "document" || node.type === "section" || node.type === "skill" || node.type === "tool"

  const typeIcon: Record<string, string> = {
    root: "",
    category: "",
    project: "#",
    document: "",
    section: "",
    skill: "",
    tool: "",
  }

  return (
    <div>
      <button
        className={`w-full text-left flex items-center gap-1.5 py-1 px-2 rounded text-xs ${
          isSelected ? "bg-blue-600/20 text-blue-300" : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        onClick={() => {
          if (isLeaf) {
            onSelect(node.id)
          } else {
            setExpanded(!expanded)
          }
        }}
      >
        {hasChildren && !isLeaf && (
          <span className="text-gray-600 text-[10px] w-3 flex-shrink-0">{expanded ? "▾" : "▸"}</span>
        )}
        {(!hasChildren || isLeaf) && <span className="w-3 flex-shrink-0" />}
        {typeIcon[node.type] && (
          <span className="text-gray-600 font-mono text-[10px] flex-shrink-0">{typeIcon[node.type]}</span>
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {expanded && hasChildren && node.children!.map((child) => (
        <TreeView
          key={child.id}
          node={child}
          selectedId={selectedId}
          onSelect={onSelect}
          depth={depth + 1}
        />
      ))}
    </div>
  )
}

function findNodeName(node: WikiNode, id: string): string | null {
  if (node.id === id) return node.name
  if (node.children) {
    for (const child of node.children) {
      const found = findNodeName(child, id)
      if (found) return found
    }
  }
  return null
}
