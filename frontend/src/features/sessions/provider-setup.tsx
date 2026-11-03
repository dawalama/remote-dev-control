import { useCallback, useState } from "react"
import { DELETE, GET, PATCH, PUT } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"

interface ProviderReadiness {
  id: string
  label: string
  driver?: string
  source?: "builtin" | "alias" | "custom" | "dropin"
  model_provider?: string
  default_model?: string | null
  model_tier?: "decision" | "fast" | "coding" | "reasoning" | string
  hosting?: "cloud" | "local" | "self_hosted" | "hybrid" | string
  api_base_url?: string | null
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

interface ModelProviderCatalogItem {
  id: string
  label: string
  hosting: string
  base_url?: string
  ready: boolean
  models: Array<{ id: string; name?: string; tier?: string }>
  recommended_models: string[]
  description: string
}

interface ModelTierItem {
  tier: string
  label: string
  purpose: string
  cloud_models: string[]
  open_source_models: string[]
}

interface ModelCatalogResponse {
  providers: ModelProviderCatalogItem[]
  tiers: ModelTierItem[]
}

interface SelfRegInstructions {
  dropin_directory: string
  mcp_tool: string
  agent_prompt: string
}

export function ProviderSetup() {
  const [settings, setSettings] = useState<ProviderSettings | null>(null)
  const [catalog, setCatalog] = useState<ModelCatalogResponse | null>(null)
  const [instructions, setInstructions] = useState<SelfRegInstructions | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [showCatalog, setShowCatalog] = useState(false)
  const [showInstructions, setShowInstructions] = useState(false)
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState(false)

  // Custom wrapper form state
  const [addingCustom, setAddingCustom] = useState(false)
  const [customId, setCustomId] = useState("")
  const [customLabel, setCustomLabel] = useState("")
  const [customDriver, setCustomDriver] = useState("generic")
  const [customModelProvider, setCustomModelProvider] = useState("vendor")
  const [customDefaultModel, setCustomDefaultModel] = useState("")
  const [customModelTier, setCustomModelTier] = useState("coding")
  const [customHosting, setCustomHosting] = useState("cloud")
  const [customBaseUrl, setCustomBaseUrl] = useState("")
  const [customExe, setCustomExe] = useState("")
  const [customTemplate, setCustomTemplate] = useState("")

  const toast = useUIStore((state) => state.toast)

  const load = useCallback(async () => {
    setChecking(true)
    try {
      const [provSettings, catResp, instrResp] = await Promise.all([
        GET<ProviderSettings>("/settings/agent-providers"),
        GET<ModelCatalogResponse>("/settings/model-providers").catch(() => null),
        GET<SelfRegInstructions>("/settings/agent-providers/instructions").catch(() => null),
      ])
      setSettings(provSettings)
      if (catResp) setCatalog(catResp)
      if (instrResp) setInstructions(instrResp)
    } catch {
      /* The sessions list remains usable if discovery is unavailable. */
    }
    setChecking(false)
  }, [])

  if (!settings) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
        <div className="flex items-center gap-3">
          <span className="h-2 w-2 rounded-full bg-gray-500" />
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium text-gray-200">Agent &amp; Model Setup</div>
            <div className="text-[10px] text-gray-500">
              Configure coding wrappers (Claude, Codex, Cursor, Grok, Aider), local Ollama/self-hosted models, and Jev Decision API.
            </div>
          </div>
          <button
            disabled={checking}
            onClick={() => { void load() }}
            className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 disabled:opacity-50"
          >
            {checking ? "Checking…" : "Open setup"}
          </button>
        </div>
      </section>
    )
  }

  const preferred = settings.providers.find((p) => p.id === settings.policy.preferred_provider)
  const readyCount = settings.providers.filter((p) => p.ready && p.id !== "diagnostic").length

  const savePreferred = async (preferredProvider: string) => {
    setSaving(true)
    try {
      const updated = await PATCH<ProviderSettings>("/settings/agent-providers", {
        preferred_provider: preferredProvider,
        fallback_policy: settings.policy.fallback_policy,
      })
      setSettings(updated)
      toast("Preferred agent updated", "success")
    } catch {
      toast("Could not update agent policy", "error")
    }
    setSaving(false)
  }

  const handleSaveCustom = async () => {
    const id = customId.trim()
    const label = customLabel.trim() || id
    if (!id) {
      toast("Wrapper ID is required (e.g. aider-qwen, grok)", "error")
      return
    }
    setSaving(true)
    try {
      const updated = await PUT<ProviderSettings>(`/settings/agent-providers/wrappers/${encodeURIComponent(id)}`, {
        label,
        driver: customDriver,
        model_provider: customModelProvider,
        default_model: customDefaultModel.trim() || null,
        model_tier: customModelTier,
        hosting: customHosting,
        api_base_url: customBaseUrl.trim() || null,
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
      setCustomBaseUrl("")
      setCustomDefaultModel("")
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

  const handleCopyPrompt = () => {
    if (!instructions?.agent_prompt) return
    void navigator.clipboard.writeText(instructions.agent_prompt)
    toast("Copied self-registration instructions for AI agents", "success")
  }

  return (
    <section className={`rounded-lg border p-3 ${preferred?.ready ? "border-gray-700 bg-gray-800/50" : "border-yellow-600/40 bg-yellow-950/20"}`}>
      <div className="flex items-center gap-3">
        <span className={`h-2 w-2 rounded-full ${preferred?.ready ? "bg-green-400" : "bg-yellow-400"}`} />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-gray-200">Agent &amp; Model Setup</div>
          <div className="truncate text-[10px] text-gray-500">
            {preferred?.ready
              ? `${preferred.label} preferred · ${readyCount} provider${readyCount === 1 ? "" : "s"} ready`
              : `${preferred?.label ?? "Preferred agent"} needs setup before orchestrated sessions can run`}
          </div>
        </div>
        <button
          onClick={() => setExpanded((value) => !value)}
          className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:text-white"
        >
          {expanded ? "Done" : preferred?.ready ? "Manage" : "Set up"}
        </button>
      </div>

      {expanded && (
        <div className="mt-3 space-y-3 border-t border-gray-700 pt-3">
          {/* Preferred Selector */}
          <label className="block text-[10px] text-gray-400">
            Preferred implementation agent
            <select
              disabled={saving}
              value={settings.policy.preferred_provider}
              onChange={(event) => { void savePreferred(event.target.value) }}
              className="mt-1 w-full rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-200"
            >
              {settings.providers.filter((p) => p.id !== "diagnostic").map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                  {p.hosting === "local" ? " [Local/OSS]" : p.hosting === "self_hosted" ? " [Self-Hosted]" : ""}
                  {p.ready ? " — Ready" : " — Setup required"}
                </option>
              ))}
            </select>
          </label>

          {/* Quick Helper Tabs: Self-Register via MCP / Tiers */}
          <div className="flex flex-wrap items-center gap-2 border-b border-gray-800 pb-2">
            <button
              onClick={() => { setShowInstructions((v) => !v); setShowCatalog(false) }}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                showInstructions ? "bg-blue-600 text-white" : "border border-gray-700 bg-gray-900 text-gray-300 hover:border-gray-500"
              }`}
            >
              ⚡ Self-Register Wrapper (MCP / Drop-in)
            </button>
            <button
              onClick={() => { setShowCatalog((v) => !v); setShowInstructions(false) }}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                showCatalog ? "bg-purple-600 text-white" : "border border-gray-700 bg-gray-900 text-gray-300 hover:border-gray-500"
              }`}
            >
              🧠 Model Providers &amp; Tiers (Ollama / Jev)
            </button>
          </div>

          {/* Self-Registration Info Banner */}
          {showInstructions && (
            <div className="space-y-2 rounded border border-blue-500/30 bg-blue-950/20 p-2.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-medium text-blue-200">How Model Wrappers Add Themselves</span>
                <button
                  onClick={handleCopyPrompt}
                  className="rounded bg-blue-600 px-2 py-0.5 text-[10px] text-white hover:bg-blue-500"
                >
                  Copy Prompt for Agent
                </button>
              </div>
              <div className="text-[11px] text-gray-300 space-y-1">
                <div>
                  <strong className="text-gray-100">1. Via MCP:</strong> If your agent is connected to the RDC MCP server (<code>remote_dev_ctrl.mcp.server</code>), call <code>register_agent_wrapper</code>.
                </div>
                <div>
                  <strong className="text-gray-100">2. Drop-in JSON:</strong> Write a manifest file to <code className="text-blue-300">~/.rdc/wrappers.d/&lt;id&gt;.json</code> (auto-discovered on boot).
                </div>
                <div>
                  <strong className="text-gray-100">3. CLI Command:</strong> Run <code>rdc wrapper register &lt;id&gt; --label "..." --driver &lt;driver&gt;</code>.
                </div>
              </div>
            </div>
          )}

          {/* Catalog & Tiers Matrix */}
          {showCatalog && catalog && (
            <div className="space-y-3 rounded border border-purple-500/30 bg-purple-950/20 p-2.5 text-xs">
              <div>
                <div className="font-medium text-purple-200 mb-1">Inference Providers &amp; Local Engines</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {catalog.providers.map((p) => (
                    <div key={p.id} className="rounded border border-gray-800 bg-gray-900/80 p-2">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="font-semibold text-gray-200">{p.label}</span>
                        <span className={`text-[9px] font-bold uppercase ${p.ready ? "text-emerald-400" : "text-gray-500"}`}>
                          {p.ready ? "Active" : "Offline"}
                        </span>
                      </div>
                      <div className="text-[10px] text-gray-400 mt-0.5">{p.description}</div>
                      {p.models.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {p.models.slice(0, 4).map((m) => (
                            <span key={m.id} className="rounded bg-gray-800 px-1.5 py-0.2 text-[9px] text-gray-300">
                              {m.name || m.id}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="font-medium text-purple-200 mb-1">4-Tier Cognitive Task Architecture</div>
                <div className="space-y-1.5">
                  {catalog.tiers.map((t) => (
                    <div key={t.tier} className="rounded border border-gray-800 bg-gray-900/60 p-1.5 text-[10.5px]">
                      <div className="font-medium text-gray-200">{t.label}</div>
                      <div className="text-[10px] text-gray-400">{t.purpose}</div>
                      <div className="text-[9.5px] text-purple-300/90 mt-0.5">
                        Cloud: {t.cloud_models.join(", ")} · Local: {t.open_source_models.join(", ")}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Wrappers List */}
          <div className="space-y-2">
            {settings.providers.map((provider) => (
              <div key={provider.id} className="flex items-start gap-2 rounded border border-gray-700 bg-gray-900/40 px-3 py-2">
                <span
                  className={`mt-1 h-2 w-2 rounded-full ${
                    provider.ready ? "bg-green-400" : provider.installed ? "bg-yellow-400" : "bg-gray-600"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-200">
                    <span className="font-medium">{provider.label}</span>
                    {provider.source === "alias" && (
                      <span className="rounded bg-blue-950/60 border border-blue-800/40 px-1.5 py-0.2 text-[9px] text-blue-300">shell alias</span>
                    )}
                    {provider.source === "dropin" && (
                      <span className="rounded bg-emerald-950/60 border border-emerald-800/40 px-1.5 py-0.2 text-[9px] text-emerald-300">drop-in</span>
                    )}
                    {provider.source === "custom" && (
                      <span className="rounded bg-purple-950/60 border border-purple-800/40 px-1.5 py-0.2 text-[9px] text-purple-300">custom</span>
                    )}
                    {provider.hosting && (
                      <span className={`rounded px-1.5 py-0.2 text-[9px] ${
                        provider.hosting === "local" ? "bg-green-950 text-green-300" : provider.hosting === "self_hosted" ? "bg-cyan-950 text-cyan-300" : "bg-gray-800 text-gray-400"
                      }`}>
                        {provider.hosting}
                      </span>
                    )}
                    {provider.model_tier && (
                      <span className="rounded bg-gray-800 px-1.5 py-0.2 text-[9px] text-gray-400">
                        {provider.model_tier}
                      </span>
                    )}
                    {provider.default_model && (
                      <span className="rounded bg-gray-800/80 px-1.5 py-0.2 font-mono text-[9px] text-gray-300">
                        {provider.default_model}
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-gray-500 mt-0.5">
                    {provider.ready ? `Ready · ${provider.capabilities.join(", ")}` : provider.detail}
                  </div>
                  {!provider.ready && provider.setup_command && (
                    <div className="mt-1 flex items-center gap-2">
                      <code className="rounded bg-gray-950 px-2 py-1 text-[10px] text-blue-300">{provider.setup_command}</code>
                      <button
                        onClick={() => {
                          void navigator.clipboard.writeText(provider.setup_command || "")
                          toast("Setup command copied", "success")
                        }}
                        className="text-[10px] text-gray-400 hover:text-white"
                      >
                        Copy
                      </button>
                    </div>
                  )}
                </div>
                {(provider.source === "custom" || provider.source === "dropin") && (
                  <button
                    disabled={saving}
                    onClick={() => { void handleRemoveWrapper(provider.id) }}
                    className="text-[10px] text-red-400 hover:text-red-300 disabled:opacity-50"
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Add Custom / Self-Hosted Wrapper Form */}
          {addingCustom ? (
            <div className="space-y-2 rounded border border-gray-700 bg-gray-900/80 p-2.5 text-xs">
              <div className="font-medium text-gray-200">Register CLI Agent / Model Wrapper</div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  value={customId}
                  onChange={(e) => setCustomId(e.target.value)}
                  placeholder="ID (e.g. aider-qwen, grok)"
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                />
                <input
                  value={customLabel}
                  onChange={(e) => setCustomLabel(e.target.value)}
                  placeholder="Label (e.g. Aider Qwen3-Coder)"
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                />
                <select
                  value={customDriver}
                  onChange={(e) => setCustomDriver(e.target.value)}
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                >
                  <option value="generic">Generic CLI driver</option>
                  <option value="aider">Aider driver</option>
                  <option value="opencode">OpenCode driver</option>
                  <option value="goose">Goose driver</option>
                  <option value="grok">Grok CLI driver</option>
                  <option value="codex">Codex CLI driver</option>
                  <option value="claude">Claude Code driver</option>
                  <option value="cursor">Cursor Agent driver</option>
                </select>
                <select
                  value={customHosting}
                  onChange={(e) => setCustomHosting(e.target.value)}
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                >
                  <option value="cloud">Cloud inference</option>
                  <option value="local">Local open-source (Ollama/MLX)</option>
                  <option value="self_hosted">Self-hosted server (vLLM/LAN)</option>
                  <option value="hybrid">Hybrid</option>
                </select>
                <select
                  value={customModelProvider}
                  onChange={(e) => setCustomModelProvider(e.target.value)}
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                >
                  <option value="vendor">Vendor default</option>
                  <option value="ollama">Ollama</option>
                  <option value="vllm">vLLM / Custom OpenAI</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="openai">OpenAI</option>
                  <option value="xai">xAI</option>
                </select>
                <select
                  value={customModelTier}
                  onChange={(e) => setCustomModelTier(e.target.value)}
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                >
                  <option value="coding">Tier 2 · Coding / Implementation</option>
                  <option value="fast">Tier 1 · Fast Utility</option>
                  <option value="reasoning">Tier 3 · Deep Reasoning</option>
                  <option value="decision">Tier 0 · System 1 Decision</option>
                </select>
                <input
                  value={customDefaultModel}
                  onChange={(e) => setCustomDefaultModel(e.target.value)}
                  placeholder="Default model (e.g. qwen3-coder:32b)"
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                />
                <input
                  value={customExe}
                  onChange={(e) => setCustomExe(e.target.value)}
                  placeholder="Executable (default: ID)"
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-200"
                />
              </div>
              <input
                value={customBaseUrl}
                onChange={(e) => setCustomBaseUrl(e.target.value)}
                placeholder="Optional API Base URL (e.g. http://localhost:11434/v1)"
                className="w-full rounded border border-gray-700 bg-gray-950 px-2 py-1 font-mono text-[11px] text-gray-200"
              />
              <input
                value={customTemplate}
                onChange={(e) => setCustomTemplate(e.target.value)}
                placeholder="Optional session command template (use {prompt})"
                className="w-full rounded border border-gray-700 bg-gray-950 px-2 py-1 font-mono text-[11px] text-gray-200"
              />
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setAddingCustom(false)}
                  className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  disabled={saving}
                  onClick={() => { void handleSaveCustom() }}
                  className="rounded bg-blue-600 px-2.5 py-1 text-[10px] font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  Save Wrapper
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between text-[10px] text-gray-500">
              <button
                onClick={() => setAddingCustom(true)}
                className="text-blue-400 hover:text-blue-300"
              >
                + Add CLI wrapper (Aider, Grok, OpenCode, custom)
              </button>
              <button
                onClick={() => { void load() }}
                className="text-blue-400 hover:text-blue-300"
              >
                Test again
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
