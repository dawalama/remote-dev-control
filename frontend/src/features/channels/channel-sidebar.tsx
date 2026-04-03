import { useState, useCallback } from "react"
import { useChannelStore, type Channel } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useMountEffect } from "@/hooks/use-mount-effect"

type GroupMode = "flat" | "project"

export function ChannelSidebar() {
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const selectChannelRaw = useChannelStore((s) => s.selectChannel)
  const loadChannels = useChannelStore((s) => s.loadChannels)
  const createChannel = useChannelStore((s) => s.createChannel)
  const selectProject = useProjectStore((s) => s.selectProject)
  const projects = useProjectStore((s) => s.projects)

  // When selecting a channel, also set the active project
  const selectChannel = useCallback((channelId: string) => {
    selectChannelRaw(channelId)
    const ch = channels.find((c) => c.id === channelId)
    if (ch && ch.project_ids.length > 0) {
      const proj = projects.find((p) => p.name === ch.name.replace(/^#/, "").split("/")[0])
      if (proj) selectProject(proj.name)
    }
  }, [selectChannelRaw, channels, projects, selectProject])

  const [groupMode, setGroupMode] = useState<GroupMode>("flat")
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")

  useMountEffect(() => {
    loadChannels()
  })

  const handleCreate = useCallback(async () => {
    if (!newName.trim()) return
    await createChannel(newName.trim())
    setNewName("")
    setCreating(false)
  }, [newName, createChannel])

  // Group channels by project
  const grouped = groupMode === "project"
    ? groupByProject(channels)
    : null

  return (
    <div className="flex flex-col h-full bg-gray-900 border-r border-gray-800 w-56 flex-shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Channels</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setGroupMode(groupMode === "flat" ? "project" : "flat")}
            className="text-[10px] text-gray-500 hover:text-gray-300 px-1"
            title={groupMode === "flat" ? "Group by project" : "Flat list"}
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
          // Project-grouped view
          Object.entries(grouped).map(([projectName, projectChannels]) => (
            <div key={projectName} className="mb-1">
              <div className="px-3 py-1 text-[10px] text-gray-600 uppercase tracking-wider font-semibold">
                {projectName}
              </div>
              {projectChannels.map((ch) => (
                <ChannelItem
                  key={ch.id}
                  channel={ch}
                  active={ch.id === activeChannelId}
                  onSelect={() => selectChannel(ch.id)}
                  indent
                />
              ))}
            </div>
          ))
        ) : (
          // Flat view
          channels.map((ch) => (
            <ChannelItem
              key={ch.id}
              channel={ch}
              active={ch.id === activeChannelId}
              onSelect={() => selectChannel(ch.id)}
            />
          ))
        )}

        {channels.length === 0 && (
          <div className="px-3 py-4 text-xs text-gray-600 text-center">
            No channels yet. Add a project to get started.
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
  onSelect,
  indent = false,
}: {
  channel: Channel
  active: boolean
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
      className={`w-full flex items-center gap-1.5 px-3 py-1 text-left text-sm transition-colors group ${
        indent ? "pl-5" : ""
      } ${
        active
          ? "bg-gray-800 text-white"
          : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
      }`}
    >
      <span className="text-gray-600 text-xs w-3 flex-shrink-0">{typeIcon}</span>
      <span className="truncate flex-1">{channel.name.replace(/^#/, "")}</span>
      {channel.auto_mode && (
        <span className="text-[9px] text-yellow-500" title="Auto-mode on">A</span>
      )}
    </button>
  )
}

// ── Helpers ──

function groupByProject(channels: Channel[]): Record<string, Channel[]> {
  const groups: Record<string, Channel[]> = {}

  // System channels first
  const system = channels.filter((c) => c.type === "system")
  if (system.length > 0) groups["System"] = system

  // Group by channel name prefix (e.g. #chilly-snacks/payments → chilly-snacks)
  for (const ch of channels) {
    if (ch.type === "system") continue
    const name = ch.name.replace(/^#/, "")
    const group = name.includes("/") ? name.split("/")[0] : name
    if (!groups[group]) groups[group] = []
    groups[group].push(ch)
  }

  return groups
}
