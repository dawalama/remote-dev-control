import { useState, useMemo } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useStateStore } from "@/stores/state-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { Sheet } from "@/features/mobile/sheet"

type FilterMode = "all" | "active"

/**
 * Mobile workstream selector — a sheet with search, collection filter,
 * and active filter for quickly finding workstreams.
 */
export function WorkspaceSelectorSheet({
  onClose,
  onSelect,
  position = "bottom",
}: {
  onClose: () => void
  onSelect: (channelId: string) => void
  position?: "bottom" | "top"
}) {
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const terminals = useStateStore((s) => s.terminals)
  const actions = useStateStore((s) => s.actions)
  const collections = useProjectStore((s) => s.collections)
  const currentCollection = useProjectStore((s) => s.currentCollection)
  const selectCollection = useProjectStore((s) => s.selectCollection)
  const layout = useUIStore((s) => s.layout)

  const [search, setSearch] = useState("")
  const [filterMode, setFilterMode] = useState<FilterMode>("all")

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

  const filteredChannels = useMemo(() => {
    let result = channels.filter((c) => c.type !== "system")

    // Active filter — shows all active workstreams across collections
    if (filterMode === "active") {
      result = result.filter((c) =>
        c.project_names?.some((n) => activeProjectNames.has(n))
      )
    } else if (currentCollection) {
      // Collection filter — only when not in active mode
      result = result.filter((c) => c.collection_ids?.includes(currentCollection))
    }

    // Search filter
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter((c) =>
        c.name.toLowerCase().includes(q) ||
        c.project_names?.some((n) => n.toLowerCase().includes(q))
      )
    }

    return result
  }, [channels, currentCollection, filterMode, search, activeProjectNames])

  return (
    <Sheet title="Workstreams" onClose={onClose} position={position}>
      <div className="space-y-2">
        {/* Search */}
        <input
          autoFocus
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search workstreams..."
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 outline-none focus:border-blue-500"
        />

        {/* Filters */}
        <div className="space-y-2">
          {layout === "kiosk" ? (
            <div className="flex gap-2 overflow-x-auto pb-1">
              <button
                onClick={() => selectCollection(null)}
                className={`px-3 py-1.5 text-xs rounded-full whitespace-nowrap border ${
                  !currentCollection
                    ? "bg-blue-600 text-white border-blue-500"
                    : "bg-gray-800 text-gray-300 border-gray-700"
                }`}
              >
                All
              </button>
              {collections.map((c) => (
                <button
                  key={c.id}
                  onClick={() => selectCollection(c.id)}
                  className={`px-3 py-1.5 text-xs rounded-full whitespace-nowrap border ${
                    currentCollection === c.id
                      ? "bg-blue-600 text-white border-blue-500"
                      : "bg-gray-800 text-gray-300 border-gray-700"
                  }`}
                >
                  {c.name}
                </button>
              ))}
            </div>
          ) : (
            <select
              value={currentCollection ?? ""}
              onChange={(e) => selectCollection(e.target.value || null)}
              className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300 outline-none"
            >
              <option value="">All</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}

          {/* Active toggle */}
          <button
            onClick={() => setFilterMode(filterMode === "all" ? "active" : "all")}
            className={`px-2 py-1 text-xs rounded ${
              filterMode === "active"
                ? "bg-green-700 text-white"
                : "bg-gray-800 text-gray-400 border border-gray-700"
            }`}
          >
            Active
          </button>
        </div>

        {/* List */}
        <div className="space-y-1 max-h-[50vh] overflow-auto">
          {filteredChannels.map((ch) => {
            const isActive = ch.id === activeChannelId
            const hasActivity = ch.project_names?.some((n) => activeProjectNames.has(n))

            return (
              <button
                key={ch.id}
                onClick={() => { onSelect(ch.id); onClose() }}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left ${
                  isActive
                    ? "bg-blue-600/20 border border-blue-500/30"
                    : "bg-gray-800 hover:bg-gray-700"
                }`}
              >
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  hasActivity ? "bg-green-500" : "bg-gray-700"
                }`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-gray-200 truncate">
                    {ch.name.replace(/^#/, "")}
                  </div>
                  {ch.project_names && ch.project_names.length > 0 && (
                    <div className="text-[10px] text-gray-500 truncate">
                      {ch.project_names.join(", ")}
                    </div>
                  )}
                </div>
                {ch.auto_mode && (
                  <span className="text-[9px] text-yellow-500">Auto</span>
                )}
              </button>
            )
          })}
          {filteredChannels.length === 0 && (
            <p className="text-xs text-gray-600 text-center py-4">
              {search ? "No matching workstreams" : filterMode === "active" ? "No active workstreams" : "No workstreams yet"}
            </p>
          )}
        </div>
      </div>
    </Sheet>
  )
}
