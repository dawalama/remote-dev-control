import { useState, useEffect } from "react"
import { GET, POST, PATCH, PUT } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import type { Collection, ProjectProfile } from "@/types"

interface ProjectDetail {
  name: string
  path: string
  description?: string
  collection_id?: string
  config?: ProjectConfig
}

interface ProjectConfig {
  terminal_command?: string
  [key: string]: unknown
}

interface ProcessDef {
  name: string
  command: string
  port?: number | null
  cwd?: string | null
  description?: string | null
  discovered_by?: string
  kind?: string
}

type Section = "general" | "profile" | "terminal" | "processes" | "danger"

export function ProjectSettingsModal({
  projectName,
  onClose,
  fullPage = false,
  initialSection,
}: {
  projectName: string
  onClose: () => void
  fullPage?: boolean
  initialSection?: Section
}) {
  const toast = useUIStore((s) => s.toast)
  const deleteProject = useProjectStore((s) => s.deleteProject)
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const collections = useProjectStore((s) => s.collections)

  const [section, setSection] = useState<Section>(initialSection || (fullPage ? "profile" : "general"))
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [processes, setProcesses] = useState<ProcessDef[]>([])
  const [loading, setLoading] = useState(true)
  const [currentName, setCurrentName] = useState(projectName)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      GET<ProjectDetail>(`/projects/${encodeURIComponent(projectName)}`),
      GET<ProcessDef[]>(`/projects/${encodeURIComponent(projectName)}/processes`).catch(() => []),
    ]).then(([proj, procs]) => {
      setProject(proj)
      setProcesses(procs || [])
    }).catch(() => {
      toast("Failed to load project", "error")
    }).finally(() => setLoading(false))
  }, [projectName, toast])

  const sections: { id: Section; label: string }[] = [
    { id: "general", label: "General" },
    { id: "profile", label: "Profile" },
    { id: "terminal", label: "Terminal" },
    { id: "processes", label: "Actions" },
    { id: "danger", label: "Danger" },
  ]

  const content = (
    <>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
        {fullPage ? (
          <button className="text-sm text-gray-400 hover:text-gray-200" onClick={onClose}>
            ← Back
          </button>
        ) : (
          <h3 className="text-sm font-semibold">
            Settings: <span className="text-blue-400">{currentName}</span>
          </h3>
        )}
        {fullPage ? (
          <span className="text-sm font-semibold text-blue-400">{currentName}</span>
        ) : (
          <button className="text-gray-400 hover:text-gray-200" onClick={onClose}>&times;</button>
        )}
      </div>

      {/* Section tabs */}
      <div className="flex border-b border-gray-700 flex-shrink-0 px-2 overflow-x-auto">
        {sections.map((s) => (
          <button
            key={s.id}
            className={`px-3 py-2 text-xs transition-colors whitespace-nowrap ${
              section === s.id
                ? "text-white border-b-2 border-blue-500"
                : s.id === "danger"
                  ? "text-red-400 hover:text-red-300"
                  : "text-gray-400 hover:text-gray-200"
            }`}
            onClick={() => setSection(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-auto p-4">
        {loading ? (
          <p className="text-xs text-gray-500 animate-pulse">Loading...</p>
        ) : !project ? (
          <p className="text-xs text-red-400">Failed to load project</p>
        ) : (
          <>
            {section === "general" && (
              <GeneralSection
                project={project}
                collections={collections}
                currentName={currentName}
                toast={toast}
                onUpdate={(p) => { setProject(p); setCurrentName(p.name); loadProjects() }}
              />
            )}
            {section === "profile" && (
              <ProfileSection
                project={project}
                currentName={currentName}
                toast={toast}
                onUpdate={(cfg) => setProject((p) => p ? { ...p, config: cfg } : p)}
              />
            )}
            {section === "terminal" && (
              <TerminalSection
                project={project}
                currentName={currentName}
                toast={toast}
                onUpdate={(cfg) => setProject((p) => p ? { ...p, config: cfg } : p)}
              />
            )}
            {section === "processes" && (
              <ProcessesSection
                processes={processes}
                currentName={currentName}
                toast={toast}
                onUpdate={setProcesses}
              />
            )}
            {section === "danger" && (
              <DangerSection
                currentName={currentName}
                deleteProject={deleteProject}
                toast={toast}
                onClose={onClose}
              />
            )}
          </>
        )}
      </div>
    </>
  )

  if (fullPage) {
    return (
      <div className="fixed inset-0 z-50 bg-gray-900 flex flex-col">
        {content}
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg w-full max-w-xl shadow-xl border border-gray-700 max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {content}
      </div>
    </div>
  )
}

// ─── General ──────────────────────────────────────────────────────────

function GeneralSection({
  project,
  collections,
  currentName,
  toast,
  onUpdate,
}: {
  project: ProjectDetail
  collections: Collection[]
  currentName: string
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
  onUpdate: (p: ProjectDetail) => void
}) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description || "")
  const [path, setPath] = useState(project.path)
  const [collectionId, setCollectionId] = useState(project.collection_id || "general")
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!name.trim()) { toast("Name is required", "error"); return }
    if (!path.trim()) { toast("Path is required", "error"); return }
    setSaving(true)
    try {
      const result = await PATCH<ProjectDetail>(
        `/projects/${encodeURIComponent(currentName)}`,
        { name: name.trim(), description, path: path.trim() }
      )
      // Move collection if changed
      if (collectionId !== (project.collection_id || "general")) {
        await POST(`/projects/${encodeURIComponent(result.name)}/move`, { collection_id: collectionId })
        result.collection_id = collectionId
      }
      onUpdate(result)
      toast("General settings saved", "success")
    } catch {
      toast("Failed to save", "error")
    }
    setSaving(false)
  }

  return (
    <div className="space-y-3">
      <div>
        <Label>Name</Label>
        <input className="input-cls" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <Label>Description</Label>
        <textarea
          className="input-cls resize-none"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What does this project do?"
        />
      </div>
      <div>
        <Label>Path</Label>
        <input className="input-cls font-mono text-xs" value={path} onChange={(e) => setPath(e.target.value)} />
      </div>
      <div>
        <Label>Collection</Label>
        <select className="input-cls" value={collectionId} onChange={(e) => setCollectionId(e.target.value)}>
          {collections.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>
      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving..." : "Save General"}
        </button>
      </div>
    </div>
  )
}

// ─── Terminal ─────────────────────────────────────────────────────────

function TerminalSection({
  project,
  currentName,
  toast,
  onUpdate,
}: {
  project: ProjectDetail
  currentName: string
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
  onUpdate: (cfg: ProjectConfig) => void
}) {
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  const current = project.config?.terminal_command ?? "cursor-agent"
  const isCustom = !presets.some((p) => p.command === current)
  const [selectedPreset, setSelectedPreset] = useState(isCustom ? "__custom__" : current)
  const [customCommand, setCustomCommand] = useState(isCustom ? current : "")
  const [saving, setSaving] = useState(false)

  const resolvedCommand = selectedPreset === "__custom__" ? customCommand.trim() : selectedPreset

  const save = async () => {
    const cmd = resolvedCommand || "cursor-agent"
    setSaving(true)
    try {
      await PATCH(`/projects/${encodeURIComponent(currentName)}/config`, {
        terminal_command: cmd,
      })
      onUpdate({ ...project.config, terminal_command: cmd })
      toast("Terminal settings saved", "success")
    } catch {
      toast("Failed to save", "error")
    }
    setSaving(false)
  }

  return (
    <div className="space-y-3">
      <div>
        <Label>Default Agent</Label>
        <select
          className="input-cls"
          value={selectedPreset}
          onChange={(e) => {
            setSelectedPreset(e.target.value)
            if (e.target.value !== "__custom__") setCustomCommand("")
          }}
        >
          {presets.map((p) => (
            <option key={p.id} value={p.command}>
              {p.label} {p.command ? `(${p.command})` : "(login shell)"}
            </option>
          ))}
          <option value="__custom__">Custom...</option>
        </select>
        <p className="text-[10px] text-gray-500 mt-1">
          The default agent launched via the "+" button in the terminal tab bar.
        </p>
        <p className="text-[10px] text-gray-500 mt-1">
          The starter list in the "+" menu is configured in System Settings → Terminal.
        </p>
      </div>
      {selectedPreset === "__custom__" && (
        <div>
          <Label>Custom Command</Label>
          <input
            className="input-cls font-mono text-xs"
            value={customCommand}
            onChange={(e) => setCustomCommand(e.target.value)}
            placeholder="/path/to/agent"
          />
        </div>
      )}

      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving..." : "Save Terminal"}
        </button>
      </div>
    </div>
  )
}

// ─── Processes ────────────────────────────────────────────────────────

function ProcessesSection({
  processes,
  currentName,
  toast,
  onUpdate,
}: {
  processes: ProcessDef[]
  currentName: string
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
  onUpdate: (procs: ProcessDef[]) => void
}) {
  const [local, setLocal] = useState<ProcessDef[]>(processes)
  const [saving, setSaving] = useState(false)
  const [rediscovering, setRediscovering] = useState(false)

  const updateField = (idx: number, field: keyof ProcessDef, value: unknown) => {
    setLocal((prev) => prev.map((p, i) => i === idx ? { ...p, [field]: value } : p))
  }

  const addProcess = () => {
    setLocal((prev) => [...prev, { name: "", command: "", port: null, cwd: null, description: null, discovered_by: "manual", kind: "command" }])
  }

  const removeProcess = (idx: number) => {
    setLocal((prev) => prev.filter((_, i) => i !== idx))
  }

  const save = async () => {
    const valid = local.filter((p) => p.name && p.command)
    if (valid.length !== local.length) {
      toast("Each action needs a name and command", "error")
      return
    }
    setSaving(true)
    try {
      await PUT(`/projects/${encodeURIComponent(currentName)}/processes`, local)
      const updated = await GET<ProcessDef[]>(`/projects/${encodeURIComponent(currentName)}/processes`).catch(() => local)
      setLocal(updated)
      onUpdate(updated)
      toast("Actions saved", "success")
    } catch {
      toast("Failed to save", "error")
    }
    setSaving(false)
  }

  const rediscover = async () => {
    setRediscovering(true)
    try {
      await POST(`/projects/${encodeURIComponent(currentName)}/detect-processes?force_rediscover=true`)
      const updated = await GET<ProcessDef[]>(`/projects/${encodeURIComponent(currentName)}/processes`).catch(() => [])
      setLocal(updated)
      onUpdate(updated)
      toast("Processes re-discovered", "success")
    } catch {
      toast("Discovery failed", "error")
    }
    setRediscovering(false)
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        <button
          className="px-2 py-1 text-xs rounded bg-yellow-600 hover:bg-yellow-700 text-white disabled:opacity-50"
          onClick={rediscover}
          disabled={rediscovering}
        >
          {rediscovering ? "Scanning..." : "Re-discover"}
        </button>
        <button
          className="px-2 py-1 text-xs rounded bg-green-600 hover:bg-green-700 text-white"
          onClick={addProcess}
        >
          + Add Action
        </button>
      </div>

      {local.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-4">
          No actions configured. Click "Add Action" or "Re-discover" to get started.
        </p>
      ) : (
        <div className="space-y-2">
          {local.map((p, i) => (
            <div key={i} className="bg-gray-700 rounded p-2 space-y-1.5">
              <div className="flex items-center gap-1">
                <input
                  className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
                  value={p.name}
                  onChange={(e) => updateField(i, "name", e.target.value)}
                  placeholder="Name"
                />
                <input
                  type="number"
                  className="w-16 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
                  value={p.port ?? ""}
                  onChange={(e) => updateField(i, "port", e.target.value ? parseInt(e.target.value) : null)}
                  placeholder="Port"
                />
                <div className="flex rounded overflow-hidden border border-gray-600 flex-shrink-0">
                  <button
                    className={`px-1.5 py-0.5 text-[10px] font-medium ${
                      (p.kind || "service") === "service" ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400"
                    }`}
                    onClick={() => updateField(i, "kind", "service")}
                  >
                    Svc
                  </button>
                  <button
                    className={`px-1.5 py-0.5 text-[10px] font-medium ${
                      p.kind === "command" ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400"
                    }`}
                    onClick={() => updateField(i, "kind", "command")}
                  >
                    Cmd
                  </button>
                </div>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  p.discovered_by === "manual" ? "bg-blue-900 text-blue-300" : "bg-gray-600 text-gray-400"
                }`}>
                  {p.discovered_by || "auto"}
                </span>
                <button
                  className="text-red-400 hover:text-red-300 text-sm px-1"
                  onClick={() => removeProcess(i)}
                  title="Delete"
                >
                  &times;
                </button>
              </div>
              <input
                className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none font-mono"
                value={p.command}
                onChange={(e) => updateField(i, "command", e.target.value)}
                placeholder="Command (e.g. npm run dev)"
              />
              <div className="flex gap-1">
                <input
                  className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none font-mono"
                  value={p.cwd || ""}
                  onChange={(e) => updateField(i, "cwd", e.target.value || null)}
                  placeholder="Working dir (optional)"
                />
                <input
                  className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
                  value={p.description || ""}
                  onChange={(e) => updateField(i, "description", e.target.value || null)}
                  placeholder="Description (optional)"
                />
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving..." : "Save Actions"}
        </button>
      </div>
    </div>
  )
}

// ─── Profile ──────────────────────────────────────────────────────────

function ProfileSection({
  project,
  currentName,
  toast,
  onUpdate,
}: {
  project: ProjectDetail
  currentName: string
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
  onUpdate: (cfg: ProjectConfig) => void
}) {
  const loadProjects = useProjectStore((s) => s.loadProjects)
  const existing: ProjectProfile = (project.config as Record<string, unknown>)?.profile as ProjectProfile || {}
  const [purpose, setPurpose] = useState(existing.purpose || "")
  const [stack, setStack] = useState<string[]>(existing.stack || [])
  const [stackInput, setStackInput] = useState("")
  const [conventions, setConventions] = useState(existing.conventions || "")
  const [testCommand, setTestCommand] = useState(existing.test_command || "")
  const [sourceDir, setSourceDir] = useState(existing.source_dir || "")
  const [testDir, setTestDir] = useState(existing.test_dir || "")
  const [saving, setSaving] = useState(false)
  const [detecting, setDetecting] = useState(false)

  const addStack = () => {
    const tag = stackInput.trim().toLowerCase()
    if (tag && !stack.includes(tag)) {
      setStack((prev) => [...prev, tag])
    }
    setStackInput("")
  }

  const removeStack = (tag: string) => {
    setStack((prev) => prev.filter((s) => s !== tag))
  }

  const detect = async () => {
    setDetecting(true)
    try {
      const result = await POST<{ stack: string[]; test_command: string | null; source_dir: string | null; test_dir: string | null }>(
        `/projects/${encodeURIComponent(currentName)}/detect-stack`
      )
      if (result) {
        if (result.stack?.length) setStack(result.stack)
        if (result.test_command) setTestCommand(result.test_command)
        if (result.source_dir) setSourceDir(result.source_dir)
        if (result.test_dir) setTestDir(result.test_dir)
        toast("Stack detected", "success")
      }
    } catch {
      toast("Detection failed", "error")
    }
    setDetecting(false)
  }

  const save = async () => {
    setSaving(true)
    try {
      const profile: ProjectProfile = {
        purpose: purpose.trim() || undefined,
        stack: stack.length ? stack : undefined,
        conventions: conventions.trim() || undefined,
        test_command: testCommand.trim() || undefined,
        source_dir: sourceDir.trim() || undefined,
        test_dir: testDir.trim() || undefined,
      }
      await PATCH(`/projects/${encodeURIComponent(currentName)}/config`, { profile })
      onUpdate({ ...project.config, profile })
      loadProjects()
      toast("Profile saved", "success")
    } catch {
      toast("Failed to save", "error")
    }
    setSaving(false)
  }

  return (
    <div className="space-y-3">
      <div>
        <Label>Purpose</Label>
        <textarea
          className="input-cls resize-none"
          rows={2}
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="e.g. FastAPI backend with React dashboard"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <Label>Stack</Label>
          <button
            className="text-[10px] text-yellow-400 hover:text-yellow-300 disabled:opacity-50"
            onClick={detect}
            disabled={detecting}
          >
            {detecting ? "Detecting..." : "Detect"}
          </button>
        </div>
        <div className="flex flex-wrap gap-1 mb-1.5">
          {stack.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full bg-blue-900/60 text-blue-300 border border-blue-700/40"
            >
              {tag}
              <button
                className="text-blue-400 hover:text-red-400 text-xs leading-none"
                onClick={() => removeStack(tag)}
              >
                &times;
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-1">
          <input
            className="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 outline-none"
            value={stackInput}
            onChange={(e) => setStackInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addStack() } }}
            placeholder="Add tag..."
          />
          <button
            className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300"
            onClick={addStack}
          >
            +
          </button>
        </div>
      </div>

      <div>
        <Label>Conventions</Label>
        <textarea
          className="input-cls resize-none font-mono text-xs"
          rows={4}
          value={conventions}
          onChange={(e) => setConventions(e.target.value)}
          placeholder="Use Zustand for state. snake_case in Python..."
        />
      </div>

      <div>
        <Label>Test Command</Label>
        <input
          className="input-cls font-mono text-xs"
          value={testCommand}
          onChange={(e) => setTestCommand(e.target.value)}
          placeholder="pytest / npm test / vitest"
        />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label>Source Dir</Label>
          <input
            className="input-cls font-mono text-xs"
            value={sourceDir}
            onChange={(e) => setSourceDir(e.target.value)}
            placeholder="src"
          />
        </div>
        <div>
          <Label>Test Dir</Label>
          <input
            className="input-cls font-mono text-xs"
            value={testDir}
            onChange={(e) => setTestDir(e.target.value)}
            placeholder="tests"
          />
        </div>
      </div>

      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving..." : "Save Profile"}
        </button>
      </div>
    </div>
  )
}

// ─── Danger Zone ──────────────────────────────────────────────────────

function DangerSection({
  currentName,
  deleteProject,
  toast,
  onClose,
}: {
  currentName: string
  deleteProject: (name: string) => Promise<void>
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
  onClose: () => void
}) {
  const handleDisconnect = async () => {
    if (!confirm(`Disconnect project "${currentName}"? This removes it from RDC but does not delete files.`)) return
    try {
      await deleteProject(currentName)
      toast("Project disconnected", "success")
      onClose()
    } catch {
      toast("Failed to disconnect", "error")
    }
  }

  return (
    <div className="border border-red-500/30 rounded-lg p-4 space-y-3">
      <h4 className="text-xs font-semibold text-red-400">Danger Zone</h4>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-300">Disconnect Project</p>
          <p className="text-[10px] text-gray-500">
            Remove this project from the dashboard. Your files will NOT be deleted.
          </p>
        </div>
        <button
          className="px-3 py-1.5 text-xs rounded bg-red-900 hover:bg-red-800 text-red-200 border border-red-700"
          onClick={handleDisconnect}
        >
          Disconnect
        </button>
      </div>
    </div>
  )
}

// ─── Shared ───────────────────────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs text-gray-400 mb-1">{children}</label>
}
