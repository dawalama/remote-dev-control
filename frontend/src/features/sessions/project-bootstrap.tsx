import { useState } from "react"
import { GET, POST } from "@/lib/api"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"

interface CreatedProject {
  id: string
  name: string
  path: string
  scaffold?: { scaffolded?: boolean; intelligence?: string }
}
interface CreatedSession { id: string }
interface ProviderSettings {
  policy?: { preferred_provider?: string }
  providers?: { id: string; ready?: boolean; label?: string }[]
}
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

/**
 * Unified Add Project modal:
 * - "Existing Folder": connect an existing local repo (with inline folder browser)
 * - "New Project": scaffold a brand-new project + workstream
 */
export function ProjectBootstrap({
  onClose,
  initialMode = "existing",
}: {
  onClose: () => void
  initialMode?: "existing" | "new"
}) {
  const [mode, setMode] = useState<"existing" | "new">(initialMode)

  // Existing folder state
  const [existingPath, setExistingPath] = useState("")
  const [existingName, setExistingName] = useState("")
  const [showBrowser, setShowBrowser] = useState(false)
  const [browseCurrent, setBrowseCurrent] = useState("")
  const [browseParent, setBrowseParent] = useState<string | null>(null)
  const [browseDirs, setBrowseDirs] = useState<DirEntry[]>([])
  const [browseLoading, setBrowseLoading] = useState(false)

  // New project state
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [path, setPath] = useState("")
  const [scaffoldWithIntel, setScaffoldWithIntel] = useState(true)
  const [startAgentSession, setStartAgentSession] = useState(false)
  const [goal, setGoal] = useState("")

  const [creating, setCreating] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const toast = useUIStore((state) => state.toast)
  const openSession = useUIStore((state) => state.openSessionFromHub)

  const browseTo = async (targetPath: string) => {
    setBrowseLoading(true)
    setErrorMsg(null)
    try {
      const res = await GET<BrowseResult>(`/browse?path=${encodeURIComponent(targetPath)}`)
      setBrowseCurrent(res.current)
      setBrowseParent(res.parent)
      setBrowseDirs(res.dirs || [])
    } catch {
      setErrorMsg("Could not list folder contents.")
    } finally {
      setBrowseLoading(false)
    }
  }

  const openBrowser = () => {
    const next = !showBrowser
    setShowBrowser(next)
    if (next && !browseCurrent) {
      void browseTo(existingPath.trim() || "~")
    }
  }

  const selectProjectWorkstream = async (projectName: string) => {
    await Promise.all([
      useProjectStore.getState().loadProjects(),
      useChannelStore.getState().loadChannels(),
    ])
    useProjectStore.getState().selectProject(projectName)
    const channel = useChannelStore.getState().channels.find((item) =>
      item.project_names.includes(projectName),
    )
    if (channel) {
      useChannelStore.getState().selectChannel(channel.id)
    }
    return channel
  }

  const connectExisting = async () => {
    const cleanedPath = existingPath.trim()
    if (!cleanedPath) return
    setCreating(true)
    setErrorMsg(null)
    try {
      const inferredName =
        existingName.trim() || cleanedPath.replace(/\/+$/, "").split("/").pop() || cleanedPath
      const project = await POST<CreatedProject>("/projects", {
        path: cleanedPath,
        name: inferredName,
      })
      await selectProjectWorkstream(project.name || inferredName)
      toast(`Connected ${project.name || inferredName}`, "success")
      onClose()
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Could not connect project"
      setErrorMsg(msg.replace(/^API \d+:\s*/, ""))
    } finally {
      setCreating(false)
    }
  }

  const createNew = async () => {
    if (!name.trim() || !description.trim()) return
    setCreating(true)
    setErrorMsg(null)
    try {
      const project = await POST<CreatedProject>("/projects/bootstrap", {
        name: name.trim(),
        description: description.trim(),
        path: path.trim() || undefined,
        scaffold: scaffoldWithIntel,
      })
      const channel = await selectProjectWorkstream(project.name)
      if (!channel) throw new Error("Project workstream was not created")

      const scaffoldNote = project.scaffold?.scaffolded ? " Starter files generated." : ""

      if (!startAgentSession) {
        onClose()
        toast(`${project.name} is ready.${scaffoldNote}`, "success")
        return
      }

      let provider = "orchestrated"
      try {
        const settings = await GET<ProviderSettings>("/settings/agent-providers")
        const preferred = settings.policy?.preferred_provider || "codex"
        const ready = settings.providers?.find((p) => p.id === preferred && p.ready)
        if (!ready) {
          const labels = (settings.providers || [])
            .filter((p) => p.ready)
            .map((p) => p.label || p.id)
          toast(
            `${project.name} created.${scaffoldNote} Coding agent not ready` +
              (labels.length ? ` (available: ${labels.join(", ")})` : " — open Setup") +
              ".",
            "warning",
          )
          onClose()
          return
        }
        provider = preferred
      } catch {
        toast(`${project.name} created.${scaffoldNote}`, "warning")
        onClose()
        return
      }

      const session = await POST<CreatedSession>("/sessions", {
        channel_id: channel.id,
        project: project.name,
        goal: goal.trim() || `Build initial version of ${project.name}. ${description.trim()}`,
        acceptance_criteria: [],
        provider,
        recipe_id: "feature",
        routing_profile: "balanced",
      })
      try {
        await POST(`/sessions/${session.id}/start`)
        toast(`${project.name} created and first goal started.`, "success")
      } catch {
        toast(`${project.name} created; configure an agent in Setup before running.`, "warning")
      }
      onClose()
      openSession(session.id)
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Could not create project"
      setErrorMsg(msg.replace(/^API \d+:\s*/, ""))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[190] flex bg-black/70 p-3" onMouseDown={onClose}>
      <div
        onMouseDown={(event) => event.stopPropagation()}
        className="m-auto w-full max-w-xl overflow-hidden rounded-xl border border-gray-700 bg-gray-900 shadow-2xl"
      >
        {/* Header + Tabs */}
        <div className="border-b border-gray-800 px-5 pt-4 pb-0">
          <div className="flex items-center justify-between gap-2 pb-3">
            <div>
              <h2 className="text-base font-semibold text-gray-100">Add a Project</h2>
              <p className="text-xs text-gray-500">
                Connect an existing local folder or scaffold a brand-new repository.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-200"
            >
              ✕
            </button>
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => { setMode("existing"); setErrorMsg(null) }}
              className={`border-b-2 px-3 py-2 text-xs font-medium transition-colors ${
                mode === "existing"
                  ? "border-blue-500 text-white"
                  : "border-transparent text-gray-400 hover:text-gray-200"
              }`}
            >
              📂 Existing Folder
            </button>
            <button
              type="button"
              onClick={() => { setMode("new"); setErrorMsg(null) }}
              className={`border-b-2 px-3 py-2 text-xs font-medium transition-colors ${
                mode === "new"
                  ? "border-blue-500 text-white"
                  : "border-transparent text-gray-400 hover:text-gray-200"
              }`}
            >
              ✨ New Project
            </button>
          </div>
        </div>

        {errorMsg && (
          <div className="mx-5 mt-3 rounded border border-red-800/70 bg-red-950/40 px-3 py-2 text-xs text-red-200">
            {errorMsg}
          </div>
        )}

        {mode === "existing" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void connectExisting()
            }}
            className="space-y-4 p-5"
          >
            <div className="space-y-1.5">
              <label className="block text-xs text-gray-300">Local folder path</label>
              <div className="flex gap-2">
                <input
                  autoFocus
                  required
                  value={existingPath}
                  onChange={(e) => setExistingPath(e.target.value)}
                  placeholder="~/code/my-project"
                  className="min-w-0 flex-1 rounded border border-gray-600 bg-gray-950 px-3 py-2 font-mono text-xs text-gray-100 focus:border-blue-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={openBrowser}
                  className="rounded border border-gray-600 bg-gray-800 px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 whitespace-nowrap"
                >
                  {showBrowser ? "Hide Browser" : "Browse…"}
                </button>
              </div>
            </div>

            {showBrowser && (
              <div className="rounded-lg border border-gray-800 bg-gray-950 p-2.5 space-y-2">
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="truncate font-mono text-gray-400">{browseCurrent || "~"}</span>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    {browseParent && (
                      <button
                        type="button"
                        onClick={() => void browseTo(browseParent)}
                        className="rounded border border-gray-700 px-2 py-0.5 text-[10px] text-gray-300 hover:bg-gray-800"
                      >
                        ↑ Up
                      </button>
                    )}
                    {browseCurrent && (
                      <button
                        type="button"
                        onClick={() => {
                          setExistingPath(browseCurrent)
                          setShowBrowser(false)
                        }}
                        className="rounded bg-blue-600 px-2 py-0.5 text-[10px] font-medium text-white hover:bg-blue-500"
                      >
                        Use this folder
                      </button>
                    )}
                  </div>
                </div>
                <div className="max-h-48 overflow-y-auto divide-y divide-gray-900">
                  {browseLoading && (
                    <div className="py-4 text-center text-xs text-gray-500">Loading folders…</div>
                  )}
                  {!browseLoading && browseDirs.length === 0 && (
                    <div className="py-4 text-center text-xs text-gray-600">No subdirectories</div>
                  )}
                  {!browseLoading &&
                    browseDirs.map((dir) => (
                      <div
                        key={dir.path}
                        className="flex items-center justify-between gap-2 px-2 py-1.5 text-xs hover:bg-gray-900"
                      >
                        <button
                          type="button"
                          onClick={() => void browseTo(dir.path)}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left text-gray-200"
                        >
                          <span>{dir.is_project ? "📦" : "📁"}</span>
                          <span className="truncate font-mono">{dir.name}</span>
                          {dir.is_project && (
                            <span className="rounded bg-emerald-950 px-1.5 py-0.2 text-[9px] text-emerald-300 border border-emerald-800/50">
                              project
                            </span>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setExistingPath(dir.path)
                            setExistingName(dir.name)
                            setShowBrowser(false)
                          }}
                          className="rounded border border-gray-700 px-2 py-0.5 text-[10px] text-blue-300 hover:bg-gray-800"
                        >
                          Select
                        </button>
                      </div>
                    ))}
                </div>
              </div>
            )}

            <label className="block text-xs text-gray-300">
              Display name <span className="text-gray-500">(optional — defaults to folder name)</span>
              <input
                value={existingName}
                onChange={(e) => setExistingName(e.target.value)}
                placeholder="my-project"
                className="mt-1 w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-xs text-gray-100 focus:border-blue-500 focus:outline-none"
              />
            </label>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creating || !existingPath.trim()}
                className="rounded bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
              >
                {creating ? "Connecting…" : "Connect Project"}
              </button>
            </div>
          </form>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void createNew()
            }}
            className="space-y-4 p-5"
          >
            <label className="block text-xs text-gray-300">
              Project name
              <input
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-new-project"
                className="mt-1 w-full rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100"
              />
            </label>

            <label className="block text-xs text-gray-300">
              What are you building?
              <textarea
                required
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe the app, stack, and goal…"
                className="mt-1 w-full resize-y rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100"
              />
            </label>

            <label className="block text-xs text-gray-300">
              Directory <span className="text-gray-500">(optional)</span>
              <input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="~/projects/my-new-project"
                className="mt-1 w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 font-mono text-xs text-gray-100"
              />
            </label>

            <div className="space-y-2 rounded-lg border border-gray-800 bg-gray-950/60 p-3 text-xs">
              <label className="flex items-center gap-2 text-gray-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={scaffoldWithIntel}
                  onChange={(e) => setScaffoldWithIntel(e.target.checked)}
                />
                Generate starter files &amp; <code className="text-[11px] text-gray-400">rdc.yaml</code>
              </label>
              <label className="flex items-center gap-2 text-gray-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={startAgentSession}
                  onChange={(e) => setStartAgentSession(e.target.checked)}
                />
                Start first Goal immediately after creating
              </label>
              {startAgentSession && (
                <input
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  placeholder="Optional first goal (defaults to building the starter app)"
                  className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-gray-100"
                />
              )}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creating || !name.trim() || !description.trim()}
                className="rounded bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
              >
                {creating ? "Creating…" : "Create Project"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
