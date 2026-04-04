import { useState, useCallback, useMemo } from "react"
import { useChannelStore, type Channel } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useStateStore } from "@/stores/state-store"
import { useMountEffect } from "@/hooks/use-mount-effect"

type GroupMode = "flat" | "project"
type FilterMode = "all" | "active"

export function ChannelSidebar() {
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const selectChannelRaw = useChannelStore((s) => s.selectChannel)
  const loadChannels = useChannelStore((s) => s.loadChannels)
  const createChannel = useChannelStore((s) => s.createChannel)
  const selectProject = useProjectStore((s) => s.selectProject)
  const projects = useProjectStore((s) => s.projects)
  const collections = useProjectStore((s) => s.collections)
  const currentCollection = useProjectStore((s) => s.currentCollection)
  const selectCollection = useProjectStore((s) => s.selectCollection)

  // Activity state from server
  const terminals = useStateStore((s) => s.terminals)
  const actions = useStateStore((s) => s.actions)

  const [groupMode, setGroupMode] = useState<GroupMode>("flat")
  const [filterMode, setFilterMode] = useState<FilterMode>("all")
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [newProjectId, setNewProjectId] = useState<string>("")

  useMountEffect(() => {
    loadChannels()
  })

  // When selecting a channel, also set the active project
  const selectChannel = useCallback((channelId: string) => {
    selectChannelRaw(channelId)
    const ch = channels.find((c) => c.id === channelId)
    if (ch && ch.project_ids.length > 0) {
      const proj = projects.find((p) => p.name === ch.name.replace(/^#/, "").split("/")[0])
      if (proj) selectProject(proj.name)
    }
  }, [selectChannelRaw, channels, projects, selectProject])

  // Compute which project names are "active" (have running terminals or processes)
  const activeProjectNames = useMemo(() => {
    const active = new Set<string>()
    for (const t of terminals) {
      if (t.status === "running" && t.project) active.add(t.project)
    }
    for (const p of actions) {
      if (p.status === "running" && p.project) active.add(p.project)
    }
    return active
  }, [terminals, actions])

  // Filter channels
  const filteredChannels = useMemo(() => {
    let result = channels

    // Collection filter — use channel's collection_ids from API
    if (currentCollection !== "all") {
      result = result.filter((ch) => {
        if (ch.type === "system") return true // system shows everywhere
        if (ch.collection_ids.length === 0 && ch.project_ids.length === 0) return false // orphaned ephemeral
        return ch.collection_ids.includes(currentCollection)
      })
    }

    // Active filter
    if (filterMode === "active") {
      result = result.filter((ch) => {
        if (ch.type === "system") return false
        return isChannelActive(ch, activeProjectNames)
      })
    }

    return result
  }, [channels, filterMode, currentCollection, activeProjectNames])

  const handleCreate = useCallback(async () => {
    if (!newName.trim()) return
    const projectIds = newProjectId ? [newProjectId] : []
    const type = projectIds.length > 0 ? "project" : "ephemeral"
    await createChannel(newName.trim(), type, projectIds)
    setNewName("")
    setNewProjectId("")
    setCreating(false)
  }, [newName, newProjectId, createChannel])

  const grouped = groupMode === "project" ? groupByProject(filteredChannels) : null

  return (
    <div className="flex flex-col h-full bg-gray-900 border-r border-gray-800 w-56 flex-shrink-0">
      {/* Header with filters */}
      <div className="flex-shrink-0 border-b border-gray-800">
        {/* Collection selector */}
        <div className="px-2 pt-2 pb-1">
          <select
            value={currentCollection}
            onChange={(e) => selectCollection(e.target.value)}
            className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300 outline-none"
          >
            <option value="all">All Collections</option>
            {collections.map((c) => (
              <option key={c.id} value={c.id}>{c.name} ({
                projects.filter((p) => p.collection_id === c.id).length
              })</option>
            ))}
          </select>
        </div>

        {/* Filter + group toggles */}
        <div className="flex items-center justify-between px-2 pb-1.5">
          <div className="flex gap-1">
            <button
              onClick={() => setFilterMode("all")}
              className={`px-1.5 py-0.5 text-[10px] rounded ${
                filterMode === "all" ? "bg-gray-700 text-white" : "text-gray-500 hover:text-gray-300"
              }`}
            >
              All
            </button>
            <button
              onClick={() => setFilterMode("active")}
              className={`px-1.5 py-0.5 text-[10px] rounded ${
                filterMode === "active" ? "bg-green-700 text-white" : "text-gray-500 hover:text-gray-300"
              }`}
            >
              Active
            </button>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setGroupMode(groupMode === "flat" ? "project" : "flat")}
              className="text-[10px] text-gray-500 hover:text-gray-300 px-1"
            >
              {groupMode === "flat" ? "Group" : "Flat"}
            </button>
            <button
              onClick={() => setCreating(true)}
              className="text-gray-500 hover:text-gray-300 text-sm px-1"
              title="New channel"
            >
              +
            </button>
          </div>
        </div>
      </div>

      {/* Create channel form */}
      {creating && (
        <div className="px-2 py-1.5 border-b border-gray-800 space-y-1">
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreate()
              if (e.key === "Escape") { setCreating(false); setNewName(""); setNewProjectId("") }
            }}
            placeholder="#channel-name"
            className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-200 outline-none focus:border-blue-500"
          />
          <select
            value={newProjectId}
            onChange={(e) => setNewProjectId(e.target.value)}
            className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300 outline-none"
          >
            <option value="">No project (ephemeral)</option>
            {projects.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
          <div className="flex gap-1">
            <button
              onClick={handleCreate}
              disabled={!newName.trim()}
              className="flex-1 px-2 py-0.5 text-[10px] bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-white"
            >
              Create
            </button>
            <button
              onClick={() => { setCreating(false); setNewName(""); setNewProjectId("") }}
              className="px-2 py-0.5 text-[10px] bg-gray-700 hover:bg-gray-600 rounded text-gray-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Channel list */}
      <div className="flex-1 overflow-y-auto py-1">
        {grouped ? (
          Object.entries(grouped).map(([group, groupChannels]) => (
            <div key={group} className="mb-1">
              <div className="px-3 py-1 text-[10px] text-gray-600 uppercase tracking-wider font-semibold">
                {group}
              </div>
              {groupChannels.map((ch) => (
                <ChannelItem
                  key={ch.id}
                  channel={ch}
                  active={ch.id === activeChannelId}
                  hasActivity={isChannelActive(ch, activeProjectNames)}
                  onSelect={() => selectChannel(ch.id)}
                  indent
                />
              ))}
            </div>
          ))
        ) : (
          filteredChannels.map((ch) => (
            <ChannelItem
              key={ch.id}
              channel={ch}
              active={ch.id === activeChannelId}
              hasActivity={isChannelActive(ch, activeProjectNames)}
              onSelect={() => selectChannel(ch.id)}
            />
          ))
        )}

        {filteredChannels.length === 0 && (
          <div className="px-3 py-4 text-xs text-gray-600 text-center">
            {filterMode === "active" ? "No active channels" : "No channels yet"}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Channel Item ──

function ChannelItem({
  channel,
  active,
  hasActivity,
  onSelect,
  indent = false,
}: {
  channel: Channel
  active: boolean
  hasActivity: boolean
  onSelect: () => void
  indent?: boolean
}) {
  const typeIcon = {
    project: "#",
    mission: "M",
    ephemeral: "~",
    system: "S",
    event: "E",
  }[channel.type] || "#"

  return (
    <button
      onClick={onSelect}
      className={`w-full flex items-center gap-1.5 px-3 py-1 text-left text-sm transition-colors ${
        indent ? "pl-5" : ""
      } ${
        active
          ? "bg-gray-800 text-white"
          : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
        hasActivity ? "bg-green-500" : "bg-transparent"
      }`} />
      <span className="text-gray-600 text-[10px] w-3 flex-shrink-0">{typeIcon}</span>
      <span className="truncate flex-1 text-xs">{channel.name.replace(/^#/, "")}</span>
      {channel.auto_mode && (
        <span className="text-[9px] text-yellow-500" title="Auto-mode">A</span>
      )}
    </button>
  )
}

// ── Helpers ──

function isChannelActive(ch: Channel, activeProjectNames: Set<string>): boolean {
  if (ch.type === "system") return false
  return ch.project_names.some((n) => activeProjectNames.has(n))
}

function groupByProject(channels: Channel[]): Record<string, Channel[]> {
  const groups: Record<string, Channel[]> = {}

  const system = channels.filter((c) => c.type === "system")
  if (system.length > 0) groups["System"] = system

  for (const ch of channels) {
    if (ch.type === "system") continue
    const name = ch.name.replace(/^#/, "")
    const group = name.includes("/") ? name.split("/")[0] : name
    if (!groups[group]) groups[group] = []
    groups[group].push(ch)
  }

  return groups
}
