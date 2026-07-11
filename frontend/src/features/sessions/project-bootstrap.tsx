import { useState } from "react"
import { POST } from "@/lib/api"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"

interface CreatedProject { id: string; name: string; path: string }
interface CreatedSession { id: string }

export function ProjectBootstrap({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [path, setPath] = useState("")
  const [startSession, setStartSession] = useState(true)
  const [goal, setGoal] = useState("")
  const [creating, setCreating] = useState(false)
  const toast = useUIStore((state) => state.toast)
  const openSession = useUIStore((state) => state.openSessionFromHub)

  const create = async () => {
    if (!name.trim() || !description.trim()) return
    setCreating(true)
    try {
      const project = await POST<CreatedProject>("/projects/bootstrap", { name: name.trim(), description: description.trim(), path: path.trim() || undefined })
      await Promise.all([useProjectStore.getState().loadProjects(), useChannelStore.getState().loadChannels()])
      const channel = useChannelStore.getState().channels.find((item) => item.project_names.includes(project.name))
      if (!channel) throw new Error("Project workstream was not created")
      useChannelStore.getState().selectChannel(channel.id)
      if (startSession) {
        const session = await POST<CreatedSession>("/sessions", { channel_id: channel.id, project: project.name, goal: goal.trim() || `Initialize the project structure for ${project.name}. ${description.trim()}`, acceptance_criteria: [], provider: "orchestrated" })
        try { await POST(`/sessions/${session.id}/retry`, { provider: "orchestrated" }) }
        catch { toast("Project created; the Session needs agent setup before it can run", "warning") }
        openSession(session.id)
      } else {
        onClose()
        toast(`${project.name} and its workstream are ready`, "success")
      }
    } catch (error) {
      toast(error instanceof Error ? error.message : "Could not create project", "error")
    }
    setCreating(false)
  }

  return <div className="fixed inset-0 z-[190] flex bg-black/70 p-3" onMouseDown={onClose}><form onSubmit={(event) => { event.preventDefault(); void create() }} onMouseDown={(event) => event.stopPropagation()} className="m-auto w-full max-w-xl space-y-4 rounded-xl border border-gray-700 bg-gray-900 p-5 shadow-2xl"><div><h2 className="text-lg font-semibold text-gray-100">New project and workstream</h2><p className="text-xs text-gray-500">Create the project container here; let its first Session build the actual structure.</p></div><label className="block text-xs text-gray-300">Project name<input autoFocus required value={name} onChange={(event) => setName(event.target.value)} placeholder="my-new-project" className="mt-1 w-full rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm" /></label><label className="block text-xs text-gray-300">What is this project?<textarea required rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Describe the product, users, and intended outcome…" className="mt-1 w-full resize-y rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm" /></label><label className="block text-xs text-gray-300">Location <span className="text-gray-600">(optional)</span><input value={path} onChange={(event) => setPath(event.target.value)} placeholder="Defaults to your RDC projects directory" className="mt-1 w-full rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm" /></label><label className="flex items-start gap-2 rounded border border-blue-500/30 bg-blue-500/5 p-3"><input type="checkbox" checked={startSession} onChange={(event) => setStartSession(event.target.checked)} className="mt-0.5" /><span><span className="block text-xs font-medium text-gray-200">Start with a Session</span><span className="block text-[10px] text-gray-500">Recommended: the orchestrator creates the initial structure as an auditable goal.</span></span></label>{startSession && <label className="block text-xs text-gray-300">First goal <span className="text-gray-600">(optional)</span><textarea rows={3} value={goal} onChange={(event) => setGoal(event.target.value)} placeholder={`Initialize the project structure, README, tests, and development workflow…`} className="mt-1 w-full resize-y rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm" /></label>}<div className="flex justify-end gap-2"><button type="button" onClick={onClose} className="px-3 py-2 text-xs text-gray-400">Cancel</button><button disabled={creating || !name.trim() || !description.trim()} className="rounded bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:opacity-50">{creating ? "Creating…" : startSession ? "Create and start Session" : "Create project"}</button></div></form></div>
}
