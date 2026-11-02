import { useState } from "react"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { SessionsCard } from "./sessions-card"
import { ProviderSetup } from "./provider-setup"
import { ProjectBootstrap } from "./project-bootstrap"
import { EvidenceSetup } from "./evidence-setup"
import { GET } from "@/lib/api"

export function SessionsHub() {
  const [showProjectBootstrap, setShowProjectBootstrap] = useState(false)
  const [briefing, setBriefing] = useState(false)
  const [briefText, setBriefText] = useState<string | null>(null)
  const open = useUIStore((state) => state.sessionsHubOpen)
  const setOpen = useUIStore((state) => state.setSessionsHubOpen)
  const toast = useUIStore((state) => state.toast)
  const setVoicePanelOpen = useUIStore((state) => state.setVoicePanelOpen)
  const channels = useChannelStore((state) => state.channels)
  const activeChannelId = useChannelStore((state) => state.activeChannelId)
  const scope = useUIStore((state) => state.sessionsHubScope)
  const setScope = useUIStore((state) => state.setSessionsHubScope)
  const workstreams = channels.filter((channel) => channel.type !== "system" && !channel.archived_at)

  const runBrief = async () => {
    setBriefing(true)
    setBriefText(null)
    try {
      // Always whole HomeBase from Sessions hub (not workstream-filtered)
      const brief = await GET<{ spoken_summary?: string }>("/homebase/brief")
      const text = brief.spoken_summary || "No brief available."
      setBriefText(text)
      toast(text.slice(0, 140), "info")
    } catch {
      toast("Failed to load HomeBase brief", "error")
    } finally {
      setBriefing(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[180] flex bg-black/70 p-2 backdrop-blur-sm sm:p-6" onMouseDown={() => setOpen(false)}>
      <section className="m-auto flex h-full max-h-[900px] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-gray-700 bg-gray-900 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex flex-wrap items-center gap-3 border-b border-gray-800 px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-gray-100">Sessions</h2>
            <div className="mt-1 flex items-center gap-1 text-xs text-gray-500"><span>Workstream ·</span><select value={activeChannelId ?? ""} onChange={(event) => { useChannelStore.getState().selectChannel(event.target.value); setScope("workstream") }} className="max-w-48 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-xs text-gray-300"><option value="" disabled>Select workstream</option>{workstreams.map((channel) => <option key={channel.id} value={channel.id}>{channel.name.replace(/^#+/, "")}</option>)}</select></div>
          </div>
          <div className="flex rounded-lg border border-gray-700 bg-gray-800 p-1">
            <button onClick={() => setScope("workstream")} className={`rounded px-3 py-1.5 text-xs ${scope === "workstream" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"}`}>This workstream</button>
            <button onClick={() => setScope("all")} className={`rounded px-3 py-1.5 text-xs ${scope === "all" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"}`}>All workstreams</button>
          </div>
          <button
            type="button"
            disabled={briefing}
            onClick={() => { void runBrief() }}
            className="rounded border border-blue-500/40 bg-blue-500/10 px-3 py-1.5 text-xs font-medium text-blue-200 hover:bg-blue-500/20 disabled:opacity-50"
            title="Whole HomeBase status (all projects)"
          >
            {briefing ? "Briefing…" : "Brief me"}
          </button>
          <button
            type="button"
            onClick={() => setVoicePanelOpen(true)}
            className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-200 hover:bg-red-500/20"
            title="Connect voice to HomeBase"
          >
            🎤 Voice
          </button>
          <button onClick={() => setShowProjectBootstrap(true)} className="rounded bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500">New project</button>
          <button onClick={() => setOpen(false)} className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800 hover:text-white">HomeBase</button>
        </header>
        {briefText && (
          <div className="border-b border-gray-800 bg-gray-950/80 px-4 py-2 text-xs text-gray-300 sm:px-6">
            <span className="mr-2 font-medium text-blue-300">HomeBase brief</span>
            {briefText}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
          <div className="mb-4 grid gap-3 lg:grid-cols-2"><ProviderSetup /><EvidenceSetup /></div>
          {scope === "workstream" && !activeChannelId ? (
            <div className="rounded-lg border border-dashed border-gray-700 p-8 text-center text-sm text-gray-500">Select a workstream to create and supervise its sessions.</div>
          ) : <SessionsCard key={scope} scope={scope} />}
        </div>
      </section>
      {showProjectBootstrap && <ProjectBootstrap onClose={() => setShowProjectBootstrap(false)} />}
    </div>
  )
}

export function SessionsNavButton({ compact = false }: { compact?: boolean }) {
  const setOpen = useUIStore((state) => state.setSessionsHubOpen)
  const setScope = useUIStore((state) => state.setSessionsHubScope)
  return <button onClick={() => { setScope("workstream"); setOpen(true) }} className="rounded-md border border-blue-500/40 bg-blue-500/10 px-3 py-1.5 text-xs font-medium text-blue-300 hover:bg-blue-500/20">{compact ? "Sessions" : "Work sessions"}</button>
}
