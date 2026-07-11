import { useCallback, useState } from "react"
import { GET, PATCH } from "@/lib/api"
import { useMountEffect } from "@/hooks/use-mount-effect"
import { useUIStore } from "@/stores/ui-store"

interface ProviderReadiness {
  id: string
  label: string
  installed: boolean
  ready: boolean
  status: "ready" | "needs_auth" | "not_installed"
  detail: string | null
  setup_command: string | null
  capabilities: string[]
}

interface ProviderSettings {
  policy: { preferred_provider: string; fallback_policy: "ask" | "disabled" }
  providers: ProviderReadiness[]
}

export function ProviderSetup() {
  const [settings, setSettings] = useState<ProviderSettings | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const toast = useUIStore((state) => state.toast)

  const load = useCallback(async () => {
    try { setSettings(await GET<ProviderSettings>("/settings/agent-providers")) }
    catch { /* The sessions list remains usable if discovery is unavailable. */ }
  }, [])
  useMountEffect(() => { void load() })

  if (!settings) return null
  const preferred = settings.providers.find((provider) => provider.id === settings.policy.preferred_provider)
  const readyCount = settings.providers.filter((provider) => provider.ready && provider.id !== "diagnostic").length

  const savePreferred = async (preferredProvider: string) => {
    setSaving(true)
    try {
      const updated = await PATCH<ProviderSettings>("/settings/agent-providers", { preferred_provider: preferredProvider, fallback_policy: settings.policy.fallback_policy })
      setSettings(updated)
      toast("Preferred agent updated", "success")
    } catch { toast("Could not update agent policy", "error") }
    setSaving(false)
  }

  return (
    <section className={`rounded-lg border p-3 ${preferred?.ready ? "border-gray-700 bg-gray-800/50" : "border-yellow-600/40 bg-yellow-950/20"}`}>
      <div className="flex items-center gap-3">
        <span className={`h-2 w-2 rounded-full ${preferred?.ready ? "bg-green-400" : "bg-yellow-400"}`} />
        <div className="min-w-0 flex-1"><div className="text-xs font-medium text-gray-200">Agent setup</div><div className="truncate text-[10px] text-gray-500">{preferred?.ready ? `${preferred.label} preferred · ${readyCount} provider${readyCount === 1 ? "" : "s"} ready` : `${preferred?.label ?? "Preferred agent"} needs setup before orchestrated sessions can run`}</div></div>
        <button onClick={() => setExpanded((value) => !value)} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300">{expanded ? "Done" : preferred?.ready ? "Manage" : "Set up"}</button>
      </div>
      {expanded && <div className="mt-3 space-y-3 border-t border-gray-700 pt-3"><label className="block text-[10px] text-gray-400">Preferred implementation agent<select disabled={saving} value={settings.policy.preferred_provider} onChange={(event) => { void savePreferred(event.target.value) }} className="mt-1 w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-200">{settings.providers.filter((provider) => provider.id !== "diagnostic").map((provider) => <option key={provider.id} value={provider.id}>{provider.label}{provider.ready ? " — Ready" : " — Setup required"}</option>)}</select></label><div className="space-y-2">{settings.providers.map((provider) => <div key={provider.id} className="flex items-start gap-2 rounded border border-gray-700 px-3 py-2"><span className={`mt-1 h-2 w-2 rounded-full ${provider.ready ? "bg-green-400" : provider.installed ? "bg-yellow-400" : "bg-gray-600"}`} /><div className="min-w-0 flex-1"><div className="text-xs text-gray-200">{provider.label}</div><div className="text-[10px] text-gray-500">{provider.ready ? `Ready · ${provider.capabilities.join(", ")}` : provider.detail}</div>{!provider.ready && provider.setup_command && <div className="mt-1 flex items-center gap-2"><code className="rounded bg-gray-950 px-2 py-1 text-[10px] text-blue-300">{provider.setup_command}</code><button onClick={() => { void navigator.clipboard.writeText(provider.setup_command || ""); toast("Setup command copied", "success") }} className="text-[10px] text-gray-400">Copy</button></div>}</div></div>)}</div><div className="flex items-center justify-between text-[10px] text-gray-500"><span>RDC never silently changes providers.</span><button onClick={() => { void load() }} className="text-blue-400">Test again</button></div></div>}
    </section>
  )
}
