import { useCallback, useState } from "react"
import { GET } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"

interface EvidenceProvider {
  id: string
  label: string
  installed: boolean
  ready: boolean
  detail: string
  setup_command: string | null
  capabilities: string[]
}

export function EvidenceSetup() {
  const project = useProjectStore((state) => state.currentProject)
  const [providers, setProviders] = useState<EvidenceProvider[] | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [checking, setChecking] = useState(false)
  const toast = useUIStore((state) => state.toast)

  const load = useCallback(async () => {
    setChecking(true)
    try {
      const response = await GET<{ providers: EvidenceProvider[] }>(`/settings/evidence-providers${project ? `?project=${encodeURIComponent(project)}` : ""}`)
      setProviders(response.providers)
    } catch { toast("Could not inspect evidence providers", "error") }
    setChecking(false)
  }, [project, toast])

  const entire = providers?.find((provider) => provider.id === "entire")
  return <section className="rounded-lg border border-gray-700 bg-gray-800/50 p-3"><div className="flex items-center gap-3"><span className={`h-2 w-2 rounded-full ${entire?.ready ? "bg-green-400" : "bg-blue-400"}`} /><div className="min-w-0 flex-1"><div className="text-xs font-medium text-gray-200">Evidence capture</div><div className="truncate text-[10px] text-gray-500">{entire?.ready ? `Entire enabled for ${project}` : "RDC native evidence is active · Entire is optional"}</div></div><button disabled={checking} onClick={() => { if (!providers) void load(); setExpanded((value) => !value) }} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 disabled:opacity-50">{checking ? "Checking…" : expanded ? "Done" : "Manage"}</button></div>{expanded && <div className="mt-3 space-y-2 border-t border-gray-700 pt-3">{!providers && <button onClick={() => { void load() }} className="text-xs text-blue-400">Check available evidence providers</button>}{providers?.map((provider) => <div key={provider.id} className="rounded border border-gray-700 px-3 py-2"><div className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${provider.ready ? "bg-green-400" : provider.installed ? "bg-yellow-400" : "bg-gray-600"}`} /><span className="text-xs text-gray-200">{provider.label}</span><span className="ml-auto text-[10px] text-gray-600">{provider.ready ? "Active" : "Optional"}</span></div><div className="mt-1 text-[10px] text-gray-500">{provider.detail}</div><div className="mt-1 text-[10px] text-gray-600">{provider.capabilities.join(" · ")}</div>{!provider.ready && provider.setup_command && <div className="mt-2 flex items-center gap-2"><code className="rounded bg-gray-950 px-2 py-1 text-[10px] text-blue-300">{provider.setup_command}</code><button onClick={() => { void navigator.clipboard.writeText(provider.setup_command || ""); toast("Setup command copied", "success") }} className="text-[10px] text-gray-400">Copy</button></div>}</div>)}{providers && <button onClick={() => { void load() }} className="text-[10px] text-blue-400">Test again</button>}</div>}</section>
}
