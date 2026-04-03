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
  const archiveChannel = useChannelStore((s) => s.archiveChannel)
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

  // Compute which project names are in the current collection
  const collectionProjectNames = useMemo(() => {
    if (currentCollection === "all") return null // no filter
    const col = collections.find((c) => c.id === currentCollection)
    if (!col) return null
    // Projects in this collection
    const names = new Set<string>()
    for (const p of projects) {
      if (p.collection_id === currentCollection) names.add(p.name)
    }
    return names
  }, [currentCollection, collections, projects])

  // Filter channels
  const filteredChannels = useMemo(() => {
    let result = channels

    // Collection filter
    if (collectionProjectNames) {
      result = result.filter((ch) => {
        if (ch.type === "system") return true // always show system
        const name = ch.name.replace(/^#/, "").split("/")[0]
        return collectionProjectNames.has(name)
      })
    }

    // Active filter
    if (filterMode === "active") {
      result = result.filter((ch) => {
        if (ch.type === "system") return false // hide system in active view
        const name = ch.name.replace(/^#/, "").split("/")[0]
        return activeProjectNames.has(name)
      })
    }

    return result
  }, [channels, filterMode, collectionProjectNames, activeProjectNames])

  const handleCreate = useCallback(async () => {
    if (!newName.trim()) return
    await createChannel(newName.trim())
    setNewName("")
    setCreating(false)
  }, [newName, createChannel])

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

      {/* Create channel input */}
      {creating && (
        <div className="px-2 py-1.5 border-b border-gray-800">
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreate()
              if (e.key === "Escape") { setCreating(false); setNewName("") }
            }}
            placeholder="#channel-name"
            className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-200 outline-none focus:border-blue-500"
          />
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
                  onArchive={() => archiveChannel(ch.id)}
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
              onArchive={() => archiveChannel(ch.id)}
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
  onArchive,
  indent = false,
}: {
  channel: Channel
  active: boolean
  hasActivity: boolean
  onSelect: () => void
  onArchive: () => void
  indent?: boolean
}) {
  const [menuOpen, setMenuOpen] = useState(false)

  const typeIcon = {
    project: "#",
    mission: "M",
    ephemeral: "~",
    system: "S",
    event: "E",
  }[channel.type] || "#"

  return (
    <div className="relative">
      <button
        onClick={onSelect}
        onContextMenu={(e) => { e.preventDefault(); setMenuOpen(!menuOpen) }}
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

      {/* Context menu */}
      {menuOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
          <div className="absolute left-full top-0 ml-1 z-50 bg-gray-800 border border-gray-700 rounded shadow-lg py-1 min-w-[120px]">
            {channel.type !== "system" && (
              <button
                onClick={() => { setMenuOpen(false); onArchive() }}
                className="w-full px-3 py-1 text-xs text-left text-red-400 hover:bg-gray-700"
              >
                Archive
              </button>
            )}
            <button
              onClick={() => setMenuOpen(false)}
              className="w-full px-3 py-1 text-xs text-left text-gray-400 hover:bg-gray-700"
            >
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ── Helpers ──

function isChannelActive(ch: Channel, activeProjectNames: Set<string>): boolean {
  if (ch.type === "system") return false
  const name = ch.name.replace(/^#/, "").split("/")[0]
  return activeProjectNames.has(name)
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
