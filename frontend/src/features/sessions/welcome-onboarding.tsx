import { useState } from "react"
import { GET, POST } from "@/lib/api"
import { useMountEffect } from "@/hooks/use-mount-effect"
import { useProjectStore } from "@/stores/project-store"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"
import { ProjectBootstrap } from "./project-bootstrap"

interface ProviderItem {
  id: string
  label: string
  ready?: boolean
  blocker?: string | null
  setup_command?: string | null
}

interface DirEntry {
  name: string
  path: string
  is_project: boolean
}

interface BrowseResult {
  current: string
  parent: string | null
  dirs: DirEntry[]
}

/**
 * First-run onboarding card shown when the user has 0 projects registered.
 * Guides a brand-new user from blank state -> connected project -> first Goal in 1-2 clicks.
 */
export function WelcomeOnboarding() {
  const [providers, setProviders] = useState<ProviderItem[]>([])
  const [suggestedDirs, setSuggestedDirs] = useState<DirEntry[]>([])
  const [connectingPath, setConnectingPath] = useState<string | null>(null)
  const [modalMode, setModalMode] = useState<"existing" | "new" | null>(null)
  const toast = useUIStore((s) => s.toast)

  useMountEffect(() => {
    void GET<{ providers?: ProviderItem[] }>("/settings/agent-providers")
      .then((res) => {
        if (res?.providers) {
          setProviders(res.providers.filter((p) => p.id !== "diagnostic" && p.id !== "shell"))
        }
      })
      .catch(() => {})

    void GET<BrowseResult>("/browse?path=~")
      .then((res) => {
        const detected = (res?.dirs || []).filter((d) => d.is_project).slice(0, 6)
        setSuggestedDirs(detected)
      })
      .catch(() => {})
  })

  const readyProviders = providers.filter((p) => p.ready)

  const handleQuickConnect = async (dir: DirEntry) => {
    setConnectingPath(dir.path)
    try {
      const created = await POST<{ name?: string }>("/projects", {
        path: dir.path,
        name: dir.name,
      })
      const projName = created?.name || dir.name
      await Promise.all([
        useProjectStore.getState().loadProjects(),
        useChannelStore.getState().loadChannels(),
      ])
      useProjectStore.getState().selectProject(projName)
      const ch = useChannelStore
        .getState()
        .channels.find((c) => c.project_names.includes(projName))
      if (ch) useChannelStore.getState().selectChannel(ch.id)
      toast(`Connected ${projName} — ready for your first goal!`, "success")
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not connect folder", "error")
    } finally {
      setConnectingPath(null)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 py-4">
      <div className="rounded-xl border border-gray-800 bg-gray-900/90 p-5 space-y-5 shadow-lg">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Welcome to Remote Dev Ctrl</h2>
          <p className="mt-1 text-xs text-gray-400 leading-relaxed">
            Run bounded coding, planning, and review goals across your repos—or pair live with
            installed CLI agents from desktop or mobile.
          </p>
        </div>

        {/* Step 1: Detected CLI Agents */}
        <div className="space-y-2 rounded-lg border border-gray-800 bg-gray-950/60 p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
              1. Detected CLI Agents
            </span>
            <span className="text-[11px] text-gray-500">
              {readyProviders.length > 0
                ? `${readyProviders.length} ready`
                : "Sign in to a CLI agent to run goals"}
            </span>
          </div>
          {providers.length === 0 ? (
            <div className="text-xs text-gray-500">Scanning local CLI agents…</div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => {
                    if (!p.ready && p.setup_command) {
                      void navigator.clipboard?.writeText(p.setup_command).catch(() => {})
                      toast(`Copied setup command: ${p.setup_command}`, "info")
                    }
                  }}
                  title={
                    p.ready
                      ? `${p.label} is ready`
                      : p.blocker || (p.setup_command ? `Run: ${p.setup_command}` : "Needs setup")
                  }
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] ${
                    p.ready
                      ? "border-emerald-700/50 bg-emerald-950/40 text-emerald-200"
                      : "border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      p.ready ? "bg-emerald-400" : "bg-gray-600"
                    }`}
                  />
                  <span>{p.label}</span>
                  {!p.ready && p.setup_command && (
                    <span className="text-[10px] text-gray-500 font-mono">
                      · {p.setup_command}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Step 2: Add First Project */}
        <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-950/60 p-3.5">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
            2. Add Your First Project
          </div>
          <div className="grid gap-2.5 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => setModalMode("existing")}
              className="flex flex-col items-start gap-1 rounded-lg border border-blue-500/40 bg-blue-950/30 p-3 text-left transition-colors hover:bg-blue-950/50"
            >
              <span className="text-xs font-semibold text-blue-200">📂 Add Existing Folder</span>
              <span className="text-[11px] text-gray-400">
                Browse or paste a path to an existing local repo.
              </span>
            </button>
            <button
              type="button"
              onClick={() => setModalMode("new")}
              className="flex flex-col items-start gap-1 rounded-lg border border-gray-700 bg-gray-900/80 p-3 text-left transition-colors hover:border-gray-600 hover:bg-gray-800/80"
            >
              <span className="text-xs font-semibold text-gray-200">✨ Create New Project</span>
              <span className="text-[11px] text-gray-400">
                Scaffold a fresh project directory and workstream.
              </span>
            </button>
          </div>

          {suggestedDirs.length > 0 && (
            <div className="space-y-1.5 pt-1">
              <div className="text-[10px] text-gray-500">
                Quick-connect a detected repo in <span className="font-mono">~</span>:
              </div>
              <div className="flex flex-wrap gap-1.5">
                {suggestedDirs.map((dir) => (
                  <button
                    key={dir.path}
                    type="button"
                    disabled={connectingPath !== null}
                    onClick={() => void handleQuickConnect(dir)}
                    className="rounded-full border border-gray-700 bg-gray-900 px-2.5 py-1 font-mono text-[11px] text-gray-200 hover:border-blue-500/50 hover:text-blue-200 disabled:opacity-40"
                  >
                    {connectingPath === dir.path ? "Connecting…" : `+ ${dir.name}`}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Compact Mental Model Legend */}
        <div className="grid gap-2 sm:grid-cols-2 text-[11px] text-gray-400">
          <div className="rounded-lg border border-gray-800/80 bg-gray-950/40 px-3 py-2">
            <span className="font-semibold text-gray-200">🎯 Goals</span> — Give an outcome
            (Bugfix, Feature, Plan, Review) to run in an isolated worktree with auto-verify &amp; diff review.
          </div>
          <div className="rounded-lg border border-gray-800/80 bg-gray-950/40 px-3 py-2">
            <span className="font-semibold text-gray-200">🤖 Agents</span> — Chat directly with
            persistent resident agents in live terminals, then promote tasks to Goals anytime.
          </div>
        </div>
      </div>

      {modalMode && (
        <ProjectBootstrap initialMode={modalMode} onClose={() => setModalMode(null)} />
      )}
    </div>
  )
}
