import { useState, useMemo } from "react"
import { useProjectStore, getActiveProjectNames } from "@/stores/project-store"
import { useStateStore } from "@/stores/state-store"
import { StackBadge } from "./project-card"
import { Sheet } from "./sheet"
import type { ProjectProfile } from "@/types"

export function ProjectSheet({ onClose }: { onClose: () => void }) {
  const { projects, collections, currentProject, currentCollection, selectProject, selectCollection } =
    useProjectStore()
  const [activeFilter, setActiveFilter] = useState(true)

  // Subscribe to state-store for activity detection
  const terminals = useStateStore((s) => s.terminals)
  const processes = useStateStore((s) => s.actions)
  const tasks = useStateStore((s) => s.tasks)
  const agents = useStateStore((s) => s.agents)

  const activeSet = useMemo(() => {
    void terminals; void processes; void tasks; void agents
    return new Set(getActiveProjectNames())
  }, [terminals, processes, tasks, agents])

  // When activeFilter, show all active projects across collections; otherwise filter by collection
  const filtered = activeFilter
    ? projects.filter((p) => activeSet.has(p.name))
    : projects.filter((p) => currentCollection === "all" || p.collection_id === currentCollection)

  return (
    <Sheet onClose={onClose} title="Select Project">
      {/* Filter pills */}
      <div className="mb-3">
        <div className="flex gap-1 flex-wrap">
          <Pill
            active={activeFilter}
            onClick={() => setActiveFilter(!activeFilter)}
            variant={activeFilter ? "green" : undefined}
          >
            ⚡ Active
          </Pill>
          {collections.length > 0 && (
            <>
              <Pill active={currentCollection === "all" && !activeFilter} onClick={() => { selectCollection("all"); setActiveFilter(false) }}>
                All
              </Pill>
              {collections.map((c) => (
                <Pill key={c.id} active={currentCollection === c.id && !activeFilter} onClick={() => { selectCollection(c.id); setActiveFilter(false) }}>
                  {c.name}
                </Pill>
              ))}
            </>
          )}
        </div>
      </div>

      <div className="space-y-1">
        {!activeFilter && (
          <button
            className={`w-full text-left px-3 py-2 rounded-lg text-sm ${
              currentProject === "all" ? "bg-blue-600 text-white" : "text-gray-300 hover:bg-gray-700"
            }`}
            onClick={() => { selectProject("all"); onClose() }}
          >
            All Projects
          </button>
        )}
        {activeFilter && filtered.length === 0 && (
          <div className="text-center text-sm text-gray-500 py-4 italic">No active projects</div>
        )}
        {filtered.map((p) => (
          <button
            key={p.name}
            className={`w-full text-left px-3 py-2 rounded-lg text-sm relative ${
              currentProject === p.name ? "bg-blue-600 text-white" : "text-gray-300 hover:bg-gray-700"
            }`}
            onClick={() => { selectProject(p.name); onClose() }}
          >
            <span className="flex items-center gap-2">
              {activeSet.has(p.name) && (
                <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
              )}
              {p.name}
            </span>
            {p.description && (
              <span className="text-[10px] text-gray-500 block truncate">{p.description}</span>
            )}
            {(p.config?.profile as ProjectProfile | undefined)?.stack?.length ? (
              <span className="flex flex-wrap gap-0.5 mt-0.5">
                {((p.config?.profile as ProjectProfile).stack ?? []).slice(0, 5).map((tag) => (
                  <StackBadge key={tag} tag={tag} />
                ))}
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </Sheet>
  )
}

function Pill({ children, active, onClick, variant }: { children: React.ReactNode; active: boolean; onClick: () => void; variant?: "green" }) {
  const colors = active
    ? variant === "green"
      ? "bg-green-600/30 text-green-400 border border-green-600/50"
      : "bg-blue-600 text-white"
    : "bg-gray-700 text-gray-400"
  return (
    <button
      className={`px-2.5 py-1 rounded-full text-xs ${colors}`}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
