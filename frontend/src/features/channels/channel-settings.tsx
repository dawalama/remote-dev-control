import { useState } from "react"
import { useChannelStore, type Channel } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { PATCH, POST, DELETE as DEL } from "@/lib/api"

export function ChannelSettings({
  channelId,
  onClose,
}: {
  channelId: string
  onClose: () => void
}) {
  const channels = useChannelStore((s) => s.channels)
  const loadChannels = useChannelStore((s) => s.loadChannels)
  const archiveChannel = useChannelStore((s) => s.archiveChannel)
  const deleteChannel = useChannelStore((s) => s.deleteChannel)
  const toggleAutoMode = useChannelStore((s) => s.toggleAutoMode)
  const projects = useProjectStore((s) => s.projects)

  const channel = channels.find((c) => c.id === channelId)
  if (!channel) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-lg border border-gray-700 shadow-xl w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-gray-200">Workspace Settings</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-sm">Close</button>
        </div>

        <div className="p-4 space-y-4">
          <RenameSection channel={channel} onDone={loadChannels} />
          <ProjectsSection channel={channel} projects={projects} onDone={loadChannels} />
          <AutoModeSection channel={channel} onToggle={() => toggleAutoMode(channelId)} />
          <InfoSection channel={channel} />

          {channel.type !== "system" && (
            <DangerSection
              channel={channel}
              onArchive={() => { archiveChannel(channelId); onClose() }}
              onDelete={() => { deleteChannel(channelId); onClose() }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

// ── Rename ──

function RenameSection({ channel, onDone }: { channel: Channel; onDone: () => void }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(channel.name)

  const save = async () => {
    const n = name.trim().startsWith("#") ? name.trim() : `#${name.trim()}`
    if (n && n !== channel.name) {
      await PATCH(`/channels/${channel.id}`, { name: n })
      onDone()
    }
    setEditing(false)
  }

  return (
    <div>
      <label className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold">Name</label>
      {editing ? (
        <div className="flex gap-1 mt-1">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false) }}
            className="flex-1 px-2 py-1 text-sm bg-gray-700 border border-gray-600 rounded text-gray-200 outline-none"
          />
          <button onClick={save} className="px-2 py-1 text-xs bg-blue-600 rounded text-white">Save</button>
          <button onClick={() => setEditing(false)} className="px-2 py-1 text-xs bg-gray-700 rounded text-gray-300">Cancel</button>
        </div>
      ) : (
        <div className="flex items-center justify-between mt-1">
          <span className="text-sm text-gray-200">{channel.name}</span>
          <button onClick={() => { setEditing(true); setName(channel.name) }} className="text-[10px] text-blue-400 hover:text-blue-300">Edit</button>
        </div>
      )}
    </div>
  )
}

// ── Projects ──

function ProjectsSection({
  channel,
  projects,
  onDone,
}: {
  channel: Channel
  projects: { name: string; collection_id?: string }[]
  onDone: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [selectedProject, setSelectedProject] = useState("")

  const linkedNames = channel.project_names || []

  const addProject = async () => {
    if (!selectedProject) return
    // Link project to channel via API
    await POST(`/channels/${channel.id}/projects`, { project_name: selectedProject })
    onDone()
    setAdding(false)
    setSelectedProject("")
  }

  const removeProject = async (projectName: string) => {
    await DEL(`/channels/${channel.id}/projects/${encodeURIComponent(projectName)}`)
    onDone()
  }

  const availableProjects = projects.filter((p) => !linkedNames.includes(p.name))

  return (
    <div>
      <label className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold">Projects</label>
      <div className="mt-1 space-y-1">
        {linkedNames.length === 0 && (
          <span className="text-xs text-gray-600">No project linked (ephemeral channel)</span>
        )}
        {linkedNames.map((name) => (
          <div key={name} className="flex items-center justify-between bg-gray-700/50 rounded px-2 py-1">
            <span className="text-xs text-gray-300">{name}</span>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-gray-600">
                {projects.find((p) => p.name === name)?.collection_id || ""}
              </span>
              <button
                onClick={() => removeProject(name)}
                className="text-[10px] text-red-400 hover:text-red-300"
              >
                Remove
              </button>
            </div>
          </div>
        ))}

        {adding ? (
          <div className="flex gap-1">
            <select
              value={selectedProject}
              onChange={(e) => setSelectedProject(e.target.value)}
              className="flex-1 px-2 py-1 text-xs bg-gray-700 border border-gray-600 rounded text-gray-300 outline-none"
            >
              <option value="">Select project...</option>
              {availableProjects.map((p) => (
                <option key={p.name} value={p.name}>{p.name}</option>
              ))}
            </select>
            <button onClick={addProject} disabled={!selectedProject} className="px-2 py-1 text-xs bg-blue-600 disabled:opacity-50 rounded text-white">Add</button>
            <button onClick={() => setAdding(false)} className="px-2 py-1 text-xs bg-gray-700 rounded text-gray-300">Cancel</button>
          </div>
        ) : (
          <button
            onClick={() => setAdding(true)}
            className="text-[10px] text-blue-400 hover:text-blue-300"
          >
            + Add project
          </button>
        )}
      </div>
    </div>
  )
}

// ── Auto-mode ──

function AutoModeSection({ channel, onToggle }: { channel: Channel; onToggle: () => void }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <label className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold">Auto-mode</label>
        <p className="text-[10px] text-gray-600 mt-0.5">Skip tactical approvals (file edits, commands)</p>
      </div>
      <button
        onClick={onToggle}
        className={`px-3 py-1 text-xs rounded ${
          channel.auto_mode
            ? "bg-yellow-600 text-white"
            : "bg-gray-700 text-gray-400"
        }`}
      >
        {channel.auto_mode ? "On" : "Off"}
      </button>
    </div>
  )
}

// ── Info ──

function InfoSection({ channel }: { channel: Channel }) {
  return (
    <div>
      <label className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold">Info</label>
      <div className="mt-1 grid grid-cols-2 gap-1 text-[10px]">
        <span className="text-gray-500">Type</span>
        <span className="text-gray-300">{channel.type}</span>
        <span className="text-gray-500">Created</span>
        <span className="text-gray-300">{new Date(channel.created_at).toLocaleDateString()}</span>
        <span className="text-gray-500">Tokens spent</span>
        <span className="text-gray-300">{channel.token_spent.toLocaleString()}</span>
        {channel.token_budget && (
          <>
            <span className="text-gray-500">Token budget</span>
            <span className="text-gray-300">{channel.token_budget.toLocaleString()}</span>
          </>
        )}
        {channel.collection_ids?.length > 0 && (
          <>
            <span className="text-gray-500">Collections</span>
            <span className="text-gray-300">{channel.collection_ids.join(", ")}</span>
          </>
        )}
      </div>
    </div>
  )
}

// ── Danger zone ──

function DangerSection({
  channel,
  onArchive,
  onDelete,
}: {
  channel: Channel
  onArchive: () => void
  onDelete: () => void
}) {
  return (
    <div className="border-t border-gray-700 pt-3">
      <label className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold">Danger Zone</label>
      <div className="flex gap-2 mt-2">
        <button
          onClick={() => { if (confirm("Archive this channel? It will be hidden but not deleted.")) onArchive() }}
          className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded text-gray-300"
        >
          Archive
        </button>
        <button
          onClick={() => { if (confirm(`Permanently delete ${channel.name} and all messages?`)) onDelete() }}
          className="px-3 py-1 text-xs bg-red-600/80 hover:bg-red-600 rounded text-white"
        >
          Delete
        </button>
      </div>
    </div>
  )
}
