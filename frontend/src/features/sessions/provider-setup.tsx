import { useCallback, useState } from "react"
import { DELETE, GET, PATCH, PUT } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"

interface ProviderReadiness {
  id: string
  label: string
  driver?: string
  source?: "builtin" | "alias" | "custom"
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
  const [checking, setChecking] = useState(false)
  const [addingCustom, setAddingCustom] = useState(false)
  const [customId, setCustomId] = useState("")
  const [customLabel, setCustomLabel] = useState("")
  const [customDriver, setCustomDriver] = useState("generic")
  const [customExe, setCustomExe] = useState("")
  const [customTemplate, setCustomTemplate] = useState("")
  const toast = useUIStore((state) => state.toast)

  const load = useCallback(async () => {
    setChecking(true)
    try { setSettings(await GET<ProviderSettings>("/settings/agent-providers")) }
    catch { /* The sessions list remains usable if discovery is unavailable. */ }
    setChecking(false)
  }, [])

  if (!settings) return <section className="rounded-lg border border-gray-700 bg-gray-800/50 p-3"><div className="flex items-center gap-3"><span className="h-2 w-2 rounded-full bg-gray-500" /><div className="min-w-0 flex-1"><div className="text-xs font-medium text-gray-200">Agent setup</div><div className="text-[10px] text-gray-500">Discover, authenticate, and choose your preferred coding agents & local shell aliases.</div></div><button disabled={checking} onClick={() => { void load() }} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 disabled:opacity-50">{checking ? "Checking…" : "Open setup"}</button></div></section>
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

  const handleSaveCustom = async () => {
    const id = customId.trim()
    const label = customLabel.trim() || id
    if (!id) {
      toast("Wrapper ID is required (e.g. grok)", "error")
      return
    }
    setSaving(true)
    try {
      const updated = await PUT<ProviderSettings>(`/settings/agent-providers/wrappers/${encodeURIComponent(id)}`, {
        label,
        driver: customDriver,
        executable: customExe.trim() || id,
        terminal_command: customExe.trim() || id,
        session_command_template: customTemplate.trim() || null,
        supports_sessions: true,
        show_in_terminal_menu: true,
      })
      setSettings(updated)
      setAddingCustom(false)
      setCustomId("")
      setCustomLabel("")
      setCustomExe("")
      setCustomTemplate("")
      toast(`Registered ${label}`, "success")
    } catch (e) {
      toast((e as Error)?.message || "Failed to register wrapper", "error")
    }
    setSaving(false)
  }

  const handleRemoveWrapper = async (id: string) => {
    setSaving(true)
    try {
      const updated = await DELETE<ProviderSettings>(`/settings/agent-providers/wrappers/${encodeURIComponent(id)}`)
      setSettings(updated)
      toast(`Removed ${id}`, "info")
    } catch {
      toast("Could not remove wrapper", "error")
    }
    setSaving(false)
  }

  return (
    <section className={`rounded-lg border p-3 ${preferred?.ready ? "border-gray-700 bg-gray-800/50" : "border-yellow-600/40 bg-yellow-950/20"}`}>
      <div className="flex items-center gap-3">
        <span className={`h-2 w-2 rounded-full ${preferred?.ready ? "bg-green-400" : "bg-yellow-400"}`} />
        <div className="min-w-0 flex-1"><div className="text-xs font-medium text-gray-200">Agent setup</div><div className="truncate text-[10px] text-gray-500">{preferred?.ready ? `${preferred.label} preferred · ${readyCount} provider${readyCount === 1 ? "" : "s"} ready` : `${preferred?.label ?? "Preferred agent"} needs setup before orchestrated sessions can run`}</div></div>
        <button onClick={() => setExpanded((value) => !value)} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300">{expanded ? "Done" : preferred?.ready ? "Manage" : "Set up"}</button>
      </div>
      {expanded && (
        <div className="mt-3 space-y-3 border-t border-gray-700 pt-3">
          <label className="block text-[10px] text-gray-400">
            Preferred implementation agent
            <select disabled={saving} value={settings.policy.preferred_provider} onChange={(event) => { void savePreferred(event.target.value) }} className="mt-1 w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-200">
              {settings.providers.filter((provider) => provider.id !== "diagnostic").map((provider) => (
                <option key={provider.id} value={provider.id}>{provider.label}{provider.ready ? " — Ready" : " — Setup required"}</option>
              ))}
            </select>
          </label>
          <div className="space-y-2">
            {settings.providers.map((provider) => (
              <div key={provider.id} className="flex items-start gap-2 rounded border border-gray-700 px-3 py-2">
                <span className={`mt-1 h-2 w-2 rounded-full ${provider.ready ? "bg-green-400" : provider.installed ? "bg-yellow-400" : "bg-gray-600"}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-xs text-gray-200">
                    <span>{provider.label}</span>
                    {provider.source === "alias" && <span className="rounded bg-blue-950/60 px-1.5 py-0.5 text-[9px] text-blue-300">shell alias</span>}
                    {provider.source === "custom" && <span className="rounded bg-purple-950/60 px-1.5 py-0.5 text-[9px] text-purple-300">custom</span>}
                  </div>
                  <div className="text-[10px] text-gray-500">{provider.ready ? `Ready · ${provider.capabilities.join(", ")}` : provider.detail}</div>
                  {!provider.ready && provider.setup_command && (
                    <div className="mt-1 flex items-center gap-2">
                      <code className="rounded bg-gray-950 px-2 py-1 text-[10px] text-blue-300">{provider.setup_command}</code>
                      <button onClick={() => { void navigator.clipboard.writeText(provider.setup_command || ""); toast("Setup command copied", "success") }} className="text-[10px] text-gray-400">Copy</button>
                    </div>
                  )}
                </div>
                {provider.source === "custom" && (
                  <button disabled={saving} onClick={() => { void handleRemoveWrapper(provider.id) }} className="text-[10px] text-red-400 hover:text-red-300">Remove</button>
                )}
              </div>
            ))}
          </div>
          {addingCustom ? (
            <div className="space-y-2 rounded border border-gray-700 bg-gray-900/70 p-2.5 text-xs">
              <div className="font-medium text-gray-200">Register CLI Agent Wrapper</div>
              <div className="grid grid-cols-2 gap-2">
                <input value={customId} onChange={(e) => setCustomId(e.target.value)} placeholder="ID (e.g. grok, aider)" className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200" />
                <input value={customLabel} onChange={(e) => setCustomLabel(e.target.value)} placeholder="Label (e.g. Grok CLI)" className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200" />
                <select value={customDriver} onChange={(e) => setCustomDriver(e.target.value)} className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200">
                  <option value="generic">Generic CLI driver</option>
                  <option value="grok">Grok CLI driver</option>
                  <option value="codex">Codex CLI driver</option>
                  <option value="claude">Claude Code driver</option>
                  <option value="cursor">Cursor Agent driver</option>
                </select>
                <input value={customExe} onChange={(e) => setCustomExe(e.target.value)} placeholder="Executable (default: ID)" className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200" />
              </div>
              <input value={customTemplate} onChange={(e) => setCustomTemplate(e.target.value)} placeholder="Optional session command template (use {prompt})" className="w-full rounded border border-gray-700 bg-gray-950 px-2 py-1 font-mono text-[11px] text-gray-200" />
              <div className="flex justify-end gap-2">
                <button onClick={() => setAddingCustom(false)} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400">Cancel</button>
                <button disabled={saving} onClick={() => { void handleSaveCustom() }} className="rounded bg-blue-600 px-2.5 py-1 text-[10px] font-medium text-white hover:bg-blue-500">Save Wrapper</button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between text-[10px] text-gray-500">
              <button onClick={() => setAddingCustom(true)} className="text-blue-400 hover:text-blue-300">+ Add CLI wrapper (Grok, Aider, custom)</button>
              <button onClick={() => { void load() }} className="text-blue-400 hover:text-blue-300">Test again</button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
