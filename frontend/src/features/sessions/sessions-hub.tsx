import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { SessionsCard } from "./sessions-card"

export function SessionsHub() {
  const open = useUIStore((state) => state.sessionsHubOpen)
  const setOpen = useUIStore((state) => state.setSessionsHubOpen)
  const channels = useChannelStore((state) => state.channels)
  const activeChannelId = useChannelStore((state) => state.activeChannelId)
  const scope = useUIStore((state) => state.sessionsHubScope)
  const setScope = useUIStore((state) => state.setSessionsHubScope)
  const activeChannel = channels.find((channel) => channel.id === activeChannelId)

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[180] flex bg-black/70 p-2 backdrop-blur-sm sm:p-6" onMouseDown={() => setOpen(false)}>
      <section className="m-auto flex h-full max-h-[900px] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-gray-700 bg-gray-900 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex flex-wrap items-center gap-3 border-b border-gray-800 px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-gray-100">Sessions</h2>
            <p className="truncate text-xs text-gray-500">
              {scope === "workstream" ? `Workstream · ${activeChannel?.name ?? "No workstream selected"}` : "Supervise work across every workstream"}
            </p>
          </div>
          <div className="flex rounded-lg border border-gray-700 bg-gray-800 p-1">
            <button onClick={() => setScope("workstream")} className={`rounded px-3 py-1.5 text-xs ${scope === "workstream" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"}`}>This workstream</button>
            <button onClick={() => setScope("all")} className={`rounded px-3 py-1.5 text-xs ${scope === "all" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"}`}>All workstreams</button>
          </div>
          <button onClick={() => setOpen(false)} className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800 hover:text-white">Control room</button>
        </header>
        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
          {scope === "workstream" && !activeChannelId ? (
            <div className="rounded-lg border border-dashed border-gray-700 p-8 text-center text-sm text-gray-500">Select a workstream to create and supervise its sessions.</div>
          ) : <SessionsCard key={scope} scope={scope} />}
        </div>
      </section>
    </div>
  )
}

export function SessionsNavButton({ compact = false }: { compact?: boolean }) {
  const setOpen = useUIStore((state) => state.setSessionsHubOpen)
  const setScope = useUIStore((state) => state.setSessionsHubScope)
  return <button onClick={() => { setScope("workstream"); setOpen(true) }} className="rounded-md border border-blue-500/40 bg-blue-500/10 px-3 py-1.5 text-xs font-medium text-blue-300 hover:bg-blue-500/20">{compact ? "Sessions" : "Work sessions"}</button>
}
