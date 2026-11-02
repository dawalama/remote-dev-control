import { useState } from "react"
import { GET, POST } from "@/lib/api"
import { useChannelStore } from "@/stores/channel-store"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"

interface CreatedProject {
  id: string
  name: string
  path: string
  scaffold?: { scaffolded?: boolean; intelligence?: string }
}
interface CreatedSession { id: string }
interface ProviderSettings {
  policy?: { preferred_provider?: string }
  providers?: { id: string; ready?: boolean; label?: string }[]
}

/**
 * New project flow:
 * - Structure via RDC general intelligence (OpenRouter/cloud) + templates — NOT Codex/Claude.
 * - Optional coding-agent Session is separate and only starts when a provider is ready.
 */
export function ProjectBootstrap({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [path, setPath] = useState("")
  /** OpenRouter/templates scaffold (default on) */
  const [scaffoldWithIntel, setScaffoldWithIntel] = useState(true)
  /** Coding-agent session (default off — avoids freeze when codex/claude not ready) */
  const [startAgentSession, setStartAgentSession] = useState(false)
  const [goal, setGoal] = useState("")
  const [creating, setCreating] = useState(false)
  const toast = useUIStore((state) => state.toast)
  const openSession = useUIStore((state) => state.openSessionFromHub)

  const create = async () => {
    if (!name.trim() || !description.trim()) return
    setCreating(true)
    try {
      const project = await POST<CreatedProject>("/projects/bootstrap", {
        name: name.trim(),
        description: description.trim(),
        path: path.trim() || undefined,
        scaffold: scaffoldWithIntel,
      })
      await Promise.all([
        useProjectStore.getState().loadProjects(),
        useChannelStore.getState().loadChannels(),
      ])
      const channel = useChannelStore.getState().channels.find((item) =>
        item.project_names.includes(project.name),
      )
      if (!channel) throw new Error("Project workstream was not created")
      useChannelStore.getState().selectChannel(channel.id)

      const scaffoldNote = project.scaffold?.scaffolded
        ? " Scaffolded with RDC intelligence (OpenRouter/cloud + templates)."
        : ""

      if (!startAgentSession) {
        onClose()
        toast(`${project.name} is ready.${scaffoldNote}`, "success")
        return
      }

      // Coding agent session — only if preferred provider is ready (fail fast).
      let provider = "orchestrated"
      try {
        const settings = await GET<ProviderSettings>("/settings/agent-providers")
        const preferred = settings.policy?.preferred_provider || "codex"
        const ready = settings.providers?.find((p) => p.id === preferred && p.ready)
        if (!ready) {
          const labels = (settings.providers || [])
            .filter((p) => p.ready)
            .map((p) => p.label || p.id)
          toast(
            `${project.name} created.${scaffoldNote} Coding agent not ready` +
              (labels.length ? ` (available: ${labels.join(", ")})` : " — open Agent setup") +
              ". Session not started.",
            "warning",
          )
          onClose()
          return
        }
        provider = preferred
      } catch {
        toast(
          `${project.name} created.${scaffoldNote} Could not check agent readiness; session not started.`,
          "warning",
        )
        onClose()
        return
      }

      const session = await POST<CreatedSession>("/sessions", {
        channel_id: channel.id,
        project: project.name,
        goal:
          goal.trim() ||
          `Continue development of ${project.name}. ${description.trim()}`,
        acceptance_criteria: [],
        provider,
      })
      try {
        await POST(`/sessions/${session.id}/retry`, { provider })
        toast(`${project.name} created and agent session started.`, "success")
      } catch {
        toast(
          `${project.name} created; agent session needs setup before it can run.`,
          "warning",
        )
      }
      openSession(session.id)
    } catch (error) {
      toast(error instanceof Error ? error.message : "Could not create project", "error")
    }
    setCreating(false)
  }

  return (
    <div className="fixed inset-0 z-[190] flex bg-black/70 p-3" onMouseDown={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void create()
        }}
        onMouseDown={(event) => event.stopPropagation()}
        className="m-auto w-full max-w-xl space-y-4 rounded-xl border border-gray-700 bg-gray-900 p-5 shadow-2xl"
      >
        <div>
          <h2 className="text-lg font-semibold text-gray-100">New project and workstream</h2>
          <p className="text-xs text-gray-500">
            Structure uses RDC general intelligence (OpenRouter) + templates.
            Coding agents (Codex/Claude) are optional and separate.
          </p>
        </div>

        <label className="block text-xs text-gray-300">
          Project name
          <input
            autoFocus
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="my-new-project"
            className="mt-1 w-full rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm"
          />
        </label>

        <label className="block text-xs text-gray-300">
          What is this project?
          <textarea
            required
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Describe the product, users, and intended outcome…"
            className="mt-1 w-full resize-y rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm"
          />
        </label>

        <label className="block text-xs text-gray-300">
          Location <span className="text-gray-600">(optional)</span>
          <input
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="Defaults to your RDC projects directory"
            className="mt-1 w-full rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm"
          />
        </label>

        <label className="flex items-start gap-2 rounded border border-blue-500/30 bg-blue-500/5 p-3">
          <input
            type="checkbox"
            checked={scaffoldWithIntel}
            onChange={(event) => setScaffoldWithIntel(event.target.checked)}
            className="mt-0.5"
          />
          <span>
            <span className="block text-xs font-medium text-gray-200">
              Scaffold with RDC intelligence
            </span>
            <span className="block text-[10px] text-gray-500">
              Recommended. Uses OpenRouter/cloud to pick a stack, then writes templates
              (README, app skeleton, rdc.yaml). Does not start Codex/Claude.
            </span>
          </span>
        </label>

        <label className="flex items-start gap-2 rounded border border-gray-600 bg-gray-800/40 p-3">
          <input
            type="checkbox"
            checked={startAgentSession}
            onChange={(event) => setStartAgentSession(event.target.checked)}
            className="mt-0.5"
          />
          <span>
            <span className="block text-xs font-medium text-gray-200">
              Also start a coding-agent Session
            </span>
            <span className="block text-[10px] text-gray-500">
              Optional. Only runs if your preferred agent CLI is ready. Otherwise the
              project still creates and you can start a Session later from Sessions.
            </span>
          </span>
        </label>

        {startAgentSession && (
          <label className="block text-xs text-gray-300">
            First agent goal <span className="text-gray-600">(optional)</span>
            <textarea
              rows={3}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="Continue building features after the scaffold…"
              className="mt-1 w-full resize-y rounded border border-gray-600 bg-gray-950 px-3 py-2 text-sm"
            />
          </label>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-2 text-xs text-gray-400">
            Cancel
          </button>
          <button
            disabled={creating || !name.trim() || !description.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:opacity-50"
          >
            {creating
              ? "Creating…"
              : startAgentSession
                ? "Create project + agent session"
                : scaffoldWithIntel
                  ? "Create & scaffold"
                  : "Create project"}
          </button>
        </div>
      </form>
    </div>
  )
}
