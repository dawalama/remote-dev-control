import { useState, useEffect, useCallback } from "react"
import { GET, POST, PATCH, DELETE } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { useTerminalPresetsStore } from "@/stores/terminal-presets-store"
import { useModels, fetchModels, ModelSelector } from "@/features/tasks/create-task-form"

interface TTSConfig {
  provider: string
  voice: string
  fallback_provider: string
  fallback_voice: string
  elevenlabs_stability: number
  elevenlabs_similarity: number
}

interface NanobotConfig {
  model_fast: string
  model_mid: string
  word_threshold: number
  max_tokens: number
  compress_enabled: boolean
  compress_rate: number
  projects_base_path: string
  ollama_model: string
  llm_provider: string
  routing_mode: "auto" | "manual"
  model_overrides: Record<string, string>
}

interface NanobotModel {
  id: string
  name: string
  tier: string
}

interface AdminSettings {
  secrets: string[]
  settings: Record<string, string>
}

interface PairedSession {
  id: string
  name: string
  role: string
  device_name: string | null
  created_at: string
  last_used_at: string | null
}

interface PinchTabConfig {
  enabled: boolean
  port: number
  binary: string
  headless: boolean
}

interface PinchTabStatus {
  available: boolean
  tabs: { id: string; title: string; url: string }[]
}

type Section = "voice" | "terminal" | "nanobot" | "recipes" | "pinchtab" | "phone" | "caddy" | "keys" | "sessions" | "info"

const LAYOUTS = [
  { id: "desktop", label: "Desktop" },
  { id: "mobile", label: "Mobile" },
  { id: "kiosk", label: "Kiosk" },
]

export function SystemSettingsModal({ onClose }: { onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const layout = useUIStore((s) => s.layout)
  const setLayout = useUIStore((s) => s.setLayout)
  const [section, setSection] = useState<Section>("voice")

  const [ttsConfig, setTtsConfig] = useState<TTSConfig | null>(null)
  const [availableVoices, setAvailableVoices] = useState<Record<string, string[]>>({})
  const [nanobotConfig, setNanobotConfig] = useState<NanobotConfig | null>(null)
  const [nanobotModels, setNanobotModels] = useState<NanobotModel[]>([])
  const [adminSettings, setAdminSettings] = useState<AdminSettings | null>(null)
  const [loading, setLoading] = useState(true)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [tts, admin] = await Promise.all([
        GET<{ config: TTSConfig; available_voices: Record<string, string[]> }>("/tts/config"),
        GET<AdminSettings>("/admin/settings"),
      ])
      if (tts) {
        setTtsConfig(tts.config)
        setAvailableVoices(tts.available_voices || {})
      }
      if (admin) setAdminSettings(admin)
    } catch { /* */ }

    // Nanobot loaded separately (may not exist)
    try {
      const nb = await GET<{ config: NanobotConfig; available_models: NanobotModel[] }>("/config/nanobot")
      if (nb) {
        setNanobotConfig(nb.config)
        setNanobotModels(nb.available_models || [])
      }
    } catch { /* */ }

    setLoading(false)
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  const saveTTS = async (key: string, value: unknown) => {
    try {
      await POST("/tts/config", { [key]: value })
      toast("Saved", "success")
      // Reload to get updated voice lists
      const tts = await GET<{ config: TTSConfig; available_voices: Record<string, string[]> }>("/tts/config")
      if (tts) {
        setTtsConfig(tts.config)
        setAvailableVoices(tts.available_voices || {})
      }
    } catch {
      toast("Failed to save", "error")
    }
  }

  const testVoice = async () => {
    toast("Testing voice...", "info")
    try {
      const token = localStorage.getItem("rdc_token")
      const resp = await fetch("/tts/speak", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ text: "Hello! This is a test of the current voice configuration." }),
      })
      if (!resp.ok) throw new Error("TTS failed")
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => URL.revokeObjectURL(url)
      audio.play()
    } catch {
      toast("Voice test failed", "error")
    }
  }

  const sections: { id: Section; label: string }[] = [
    { id: "voice", label: "Voice & TTS" },
    { id: "terminal", label: "Terminal" },
    { id: "nanobot", label: "Agent Config" },
    { id: "recipes", label: "Recipes" },
    { id: "pinchtab", label: "PinchTab" },
    { id: "phone", label: "Phone" },
    { id: "caddy", label: "Caddy Proxy" },
    { id: "keys", label: "API Keys" },
    { id: "sessions", label: "Sessions" },
    { id: "info", label: "Server Info" },
  ]

  return (
    <div className="fixed inset-0 h-app bg-gray-900 z-50 flex flex-col text-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            className="text-sm text-gray-400 hover:text-gray-200"
            onClick={onClose}
          >
            ← Back
          </button>
          <h3 className="text-sm font-semibold">System Settings</h3>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">Layout</span>
          <div className="flex rounded-lg overflow-hidden border border-gray-600">
            {LAYOUTS.map((l) => (
              <button
                key={l.id}
                className={`px-3 py-1 text-xs font-medium ${
                  layout === l.id
                    ? "bg-blue-600 text-white"
                    : "bg-gray-700 text-gray-300"
                }`}
                onClick={() => setLayout(l.id)}
              >
                {l.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Section tabs */}
      <div className="flex border-b border-gray-700 flex-shrink-0 px-2 overflow-x-auto">
        {sections.map((s) => (
          <button
            key={s.id}
            className={`px-3 py-2 text-xs whitespace-nowrap transition-colors ${
              section === s.id
                ? "text-white border-b-2 border-blue-500"
                : "text-gray-400 hover:text-gray-200"
            }`}
            onClick={() => setSection(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-auto p-4 max-w-2xl w-full mx-auto">
        {loading ? (
          <p className="text-xs text-gray-500 animate-pulse">Loading...</p>
        ) : (
          <>
            {section === "voice" && ttsConfig && (
              <VoiceSection
                config={ttsConfig}
                voices={availableVoices}
                onSave={saveTTS}
                onTest={testVoice}
              />
            )}
            {section === "terminal" && (
              <TerminalSectionGlobal />
            )}
            {section === "nanobot" && (
              <NanobotSection
                config={nanobotConfig}
                models={nanobotModels}
                toast={toast}
                onReload={loadAll}
              />
            )}
            {section === "recipes" && (
              <RecipesSection toast={toast} />
            )}
            {section === "pinchtab" && (
              <PinchTabSection toast={toast} />
            )}
            {section === "phone" && adminSettings && (
              <PhoneSection settings={adminSettings} toast={toast} />
            )}
            {section === "caddy" && (
              <CaddySection toast={toast} />
            )}
            {section === "keys" && adminSettings && (
              <APIKeysSection secrets={adminSettings.secrets} />
            )}
            {section === "sessions" && (
              <SessionsSection toast={toast} />
            )}
            {section === "info" && adminSettings && (
              <ServerInfoSection settings={adminSettings.settings} />
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ─── Voice & TTS ──────────────────────────────────────────────────────

function TerminalSectionGlobal() {
  const presets = useTerminalPresetsStore((s) => s.presets)
  const loadPresets = useTerminalPresetsStore((s) => s.load)
  const savePresets = useTerminalPresetsStore((s) => s.save)
  const toast = useUIStore((s) => s.toast)
  const [saving, setSaving] = useState(false)
  const [editList, setEditList] = useState<typeof presets>([])

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  useEffect(() => {
    setEditList(presets)
  }, [presets])

  const updateStarter = (idx: number, patch: Partial<(typeof editList)[0]>) => {
    setEditList((prev) => prev.map((p, i) => (i === idx ? { ...p, ...patch } : p)))
  }

  const removeStarter = (idx: number) => {
    setEditList((prev) => prev.filter((_, i) => i !== idx))
  }

  const moveStarter = (idx: number, dir: -1 | 1) => {
    setEditList((prev) => {
      const next = [...prev]
      const target = idx + dir
      if (target < 0 || target >= next.length) return prev
      ;[next[idx], next[target]] = [next[target], next[idx]]
      return next
    })
  }

  const addStarter = () => {
    setEditList((prev) => [
      ...prev,
      {
        id: `custom-${Date.now()}`,
        label: "New",
        command: "",
        icon: "•",
        description: "",
      },
    ])
  }

  const save = async () => {
    setSaving(true)
    try {
      await savePresets(editList)
      toast("Terminal starters saved", "success")
    } catch {
      toast("Failed to save terminal starters", "error")
    }
    setSaving(false)
  }

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-gray-400">
        Configure the global terminal starters shown in the "+" menus (Cursor, Claude, Gemini, Shell, etc.).
        Projects can still choose their own default starter in project settings.
      </p>
      <p className="text-[11px] text-gray-500">
        Tip: put the full command here (including args). Example: <span className="font-mono">claude --continue</span>
      </p>
      <div className="space-y-2 border border-gray-700 rounded p-2 bg-gray-900/60">
        {editList.map((p, idx) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 text-xs">
            <input
              className="w-10 font-mono bg-gray-800 rounded px-1"
              value={p.icon}
              onChange={(e) => updateStarter(idx, { icon: e.target.value })}
              placeholder="icon"
            />
            <input
              className="w-24 bg-gray-800 rounded px-1"
              value={p.label}
              onChange={(e) => updateStarter(idx, { label: e.target.value })}
              placeholder="Label"
            />
            <input
              className="flex-1 min-w-0 font-mono bg-gray-800 rounded px-1"
              value={p.command}
              onChange={(e) => updateStarter(idx, { command: e.target.value })}
              placeholder="command"
            />
            <button type="button" className="text-gray-500 hover:text-gray-300 text-[10px]" onClick={() => moveStarter(idx, -1)} disabled={idx === 0} title="Move up">▲</button>
            <button type="button" className="text-gray-500 hover:text-gray-300 text-[10px]" onClick={() => moveStarter(idx, 1)} disabled={idx === editList.length - 1} title="Move down">▼</button>
            <button type="button" className="text-red-400 hover:text-red-300" onClick={() => removeStarter(idx)}>×</button>
          </div>
        ))}
        <div className="flex gap-2">
          <button
            type="button"
            className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600"
            onClick={addStarter}
          >
            + Add
          </button>
          <button
            type="button"
            className="px-2 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-50"
            onClick={save}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save starters"}
          </button>
        </div>
      </div>
    </div>
  )
}

function VoiceSection({
  config,
  voices,
  onSave,
  onTest,
}: {
  config: TTSConfig
  voices: Record<string, string[]>
  onSave: (key: string, value: unknown) => void
  onTest: () => void
}) {
  const primaryVoices = voices[config.provider] || []
  const fallbackVoices = voices[config.fallback_provider] || []

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Primary Provider</Label>
          <select className="input-cls" value={config.provider} onChange={(e) => onSave("provider", e.target.value)}>
            <option value="elevenlabs">ElevenLabs (Best Quality)</option>
            <option value="deepgram">Deepgram (Fast)</option>
            <option value="openai">OpenAI</option>
          </select>
        </div>
        <div>
          <Label>Voice</Label>
          <select className="input-cls" value={config.voice} onChange={(e) => onSave("voice", e.target.value)}>
            {primaryVoices.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
        <div>
          <Label>Fallback Provider</Label>
          <select className="input-cls" value={config.fallback_provider} onChange={(e) => onSave("fallback_provider", e.target.value)}>
            <option value="deepgram">Deepgram</option>
            <option value="elevenlabs">ElevenLabs</option>
            <option value="openai">OpenAI</option>
          </select>
          <p className="text-[10px] text-gray-500 mt-1">Used when primary quota is exceeded</p>
        </div>
        <div>
          <Label>Fallback Voice</Label>
          <select className="input-cls" value={config.fallback_voice} onChange={(e) => onSave("fallback_voice", e.target.value)}>
            {fallbackVoices.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
      </div>

      {config.provider === "elevenlabs" && (
        <div className="border-t border-gray-700 pt-3">
          <h4 className="text-xs font-medium text-gray-300 mb-2">ElevenLabs Settings</h4>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Stability: {config.elevenlabs_stability}</Label>
              <input
                type="range" min="0" max="1" step="0.1"
                value={config.elevenlabs_stability}
                onChange={(e) => onSave("elevenlabs_stability", e.target.value)}
                className="w-full"
              />
              <p className="text-[10px] text-gray-500">Higher = more consistent</p>
            </div>
            <div>
              <Label>Similarity: {config.elevenlabs_similarity}</Label>
              <input
                type="range" min="0" max="1" step="0.1"
                value={config.elevenlabs_similarity}
                onChange={(e) => onSave("elevenlabs_similarity", e.target.value)}
                className="w-full"
              />
              <p className="text-[10px] text-gray-500">Higher = closer to original voice</p>
            </div>
          </div>
        </div>
      )}

      <button
        className="px-3 py-1.5 text-xs rounded bg-purple-600 hover:bg-purple-700 text-white"
        onClick={onTest}
      >
        Test Voice
      </button>
    </div>
  )
}

// ─── Nanobot ──────────────────────────────────────────────────────────

function NanobotSection({
  config,
  models,
  toast,
  onReload,
}: {
  config: NanobotConfig | null
  models: NanobotModel[]
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
  onReload: () => void
}) {
  const { models: taskModels } = useModels()
  const [local, setLocal] = useState<NanobotConfig>(
    config || {
      model_fast: "", model_mid: "",
      word_threshold: 12, max_tokens: 400,
      compress_enabled: false, compress_rate: 0.5,
      projects_base_path: "", ollama_model: "qwen3.5", llm_provider: "cloud",
      routing_mode: "auto", model_overrides: {},
    }
  )

  // Sync local state when config prop updates (e.g. after save + reload)
  useEffect(() => {
    if (config) setLocal(config)
  }, [config])

  const save = async () => {
    try {
      await PATCH("/config/nanobot", local)
      toast("Agent config saved", "success")
      onReload()
    } catch {
      toast("Failed to save", "error")
    }
  }

  if (!config && models.length === 0) {
    return <p className="text-xs text-gray-500">Agent configuration not available</p>
  }

  const isOllama = local.llm_provider === "ollama"
  const isAutoRouting = local.routing_mode === "auto"
  const missingFields = isOllama
    ? !local.ollama_model
    : (isAutoRouting ? false : (!local.model_fast || !local.model_mid))

  const setOverride = (key: string, value: string) => {
    setLocal((p) => ({
      ...p,
      model_overrides: { ...p.model_overrides, [key]: value },
    }))
  }

  return (
    <div className="space-y-4">
      {missingFields && (
        <div className="bg-yellow-900/40 border border-yellow-700 rounded px-3 py-2 text-xs text-yellow-300">
          {isOllama
            ? "Please configure the Ollama model below."
            : "Please configure both models below to enable the orchestrator."}
        </div>
      )}

      <div>
        <Label>LLM Provider</Label>
        <div className="flex rounded-lg overflow-hidden border border-gray-600 w-fit">
          <button
            className={`px-4 py-1.5 text-xs font-medium ${
              !isOllama ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300"
            }`}
            onClick={() => setLocal((p) => ({ ...p, llm_provider: "cloud" }))}
          >
            Cloud (OpenRouter)
          </button>
          <button
            className={`px-4 py-1.5 text-xs font-medium ${
              isOllama ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300"
            }`}
            onClick={() => setLocal((p) => ({ ...p, llm_provider: "ollama" }))}
          >
            Local (Ollama)
          </button>
        </div>
        <p className="text-[10px] text-gray-500 mt-1">
          {isOllama
            ? "Uses local Ollama instance — free, private, no API key needed"
            : "Uses OpenRouter/OpenAI — requires API key in API Keys tab"}
        </p>
      </div>

      {!isOllama && (
        <div>
          <Label>Routing Mode</Label>
          <div className="flex rounded-lg overflow-hidden border border-gray-600 w-fit">
            <button
              className={`px-4 py-1.5 text-xs font-medium ${
                isAutoRouting ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300"
              }`}
              onClick={() => setLocal((p) => ({ ...p, routing_mode: "auto" }))}
            >
              Auto
            </button>
            <button
              className={`px-4 py-1.5 text-xs font-medium ${
                !isAutoRouting ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300"
              }`}
              onClick={() => setLocal((p) => ({ ...p, routing_mode: "manual" }))}
            >
              Manual
            </button>
          </div>
          <p className="text-[10px] text-gray-500 mt-1">
            {isAutoRouting
              ? "Auto-routes to the cheapest model that matches message complexity"
              : "Uses fixed fast/mid models based on word count threshold"}
          </p>
        </div>
      )}

      <RefreshModelsButton toast={toast} />

      {!isOllama && isAutoRouting && (
        <div>
          <p className="text-[11px] text-gray-400 mb-2">
            Tier overrides — pin a specific model for a complexity tier (leave empty for auto-selection)
          </p>
          <div className="grid grid-cols-2 gap-3">
            {([
              ["model_trivial", "Trivial", "UI nav, toggles, show tabs"],
              ["model_simple", "Simple", "Select project, stop process"],
              ["model_complex", "Complex", "Create project, spawn agent"],
              ["model_reasoning", "Reasoning", "Explain, debug, compare"],
            ] as const).map(([key, label, hint]) => (
              <div key={key}>
                <Label>{label}</Label>
                <ModelSelector
                  models={taskModels}
                  value={(local.model_overrides || {})[key] || ""}
                  onChange={(v) => setOverride(key, v)}
                  className=""
                />
                <p className="text-[10px] text-gray-500 mt-1">{hint}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {!isOllama && !isAutoRouting && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <ModelSelector
              models={taskModels}
              value={local.model_fast}
              onChange={(v) => setLocal((p) => ({ ...p, model_fast: v }))}
              className=""
            />
            <p className="text-[10px] text-gray-500 mt-1">Fast — commands, short input</p>
          </div>
          <div>
            <ModelSelector
              models={taskModels}
              value={local.model_mid}
              onChange={(v) => setLocal((p) => ({ ...p, model_mid: v }))}
              className=""
            />
            <p className="text-[10px] text-gray-500 mt-1">Mid — conversations, longer</p>
          </div>
          <div>
            <Label>Word Threshold</Label>
            <input
              type="number" className="input-cls" min={1} max={50}
              value={local.word_threshold}
              onChange={(e) => setLocal((p) => ({ ...p, word_threshold: parseInt(e.target.value) || 12 }))}
            />
            <p className="text-[10px] text-gray-500 mt-1">Messages with fewer words use fast model</p>
          </div>
        </div>
      )}

      {!isOllama && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Max Tokens</Label>
            <input
              type="number" className="input-cls" min={100} max={2000} step={50}
              value={local.max_tokens}
              onChange={(e) => setLocal((p) => ({ ...p, max_tokens: parseInt(e.target.value) || 400 }))}
            />
          </div>
        </div>
      )}

      {!isOllama && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
              <input
                type="checkbox"
                checked={local.compress_enabled}
                onChange={(e) => setLocal((p) => ({ ...p, compress_enabled: e.target.checked }))}
              />
              Prompt Compression <span className="text-gray-500">(LLMLingua-2)</span>
            </label>
            <p className="text-[10px] text-gray-500 mt-1">Compress dynamic context to reduce tokens</p>
          </div>
          <div>
            <Label>Compression Rate: {local.compress_rate}</Label>
            <input
              type="range" min={0.1} max={0.9} step={0.1}
              value={local.compress_rate}
              onChange={(e) => setLocal((p) => ({ ...p, compress_rate: parseFloat(e.target.value) }))}
              className="w-full"
              disabled={!local.compress_enabled}
            />
            <p className="text-[10px] text-gray-500">Lower = more aggressive (0.3 = keep 30%)</p>
          </div>
        </div>
      )}

      <div>
        <Label>Projects Base Path</Label>
        <input
          className="input-cls"
          value={local.projects_base_path}
          onChange={(e) => setLocal((p) => ({ ...p, projects_base_path: e.target.value }))}
          placeholder="~/projects"
        />
        <p className="text-[10px] text-gray-500 mt-1">Where new projects are created</p>
      </div>

      <div>
        <Label>Local Model (Ollama)</Label>
        <input
          className="input-cls font-mono"
          value={local.ollama_model}
          onChange={(e) => setLocal((p) => ({ ...p, ollama_model: e.target.value }))}
          placeholder="qwen3.5"
        />
        <p className="text-[10px] text-gray-500 mt-1">
          {isOllama
            ? "Used for all LLM tasks (orchestrator, process discovery, project analysis)"
            : "Used for process discovery and project analysis when no cloud key is set"}
        </p>
      </div>

      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={save}
          disabled={missingFields}
        >
          Save Agent Config
        </button>
      </div>
    </div>
  )
}

// ─── PinchTab ─────────────────────────────────────────────────────────

function PinchTabSection({
  toast,
}: {
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
}) {
  const [config, setConfig] = useState<PinchTabConfig>({
    enabled: true, port: 9867, binary: "", headless: true,
  })
  const [status, setStatus] = useState<PinchTabStatus>({ available: false, tabs: [] })
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await GET<{ config: PinchTabConfig; status: PinchTabStatus }>("/config/pinchtab")
      if (data) {
        setConfig(data.config)
        setStatus(data.status)
      }
    } catch { /* */ }
    setLoaded(true)
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    try {
      await PATCH("/config/pinchtab", config)
      toast("PinchTab settings saved", "success")
      load()
    } catch {
      toast("Failed to save", "error")
    }
  }

  if (!loaded) return <p className="text-xs text-gray-500 animate-pulse">Loading...</p>

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-gray-400">
        PinchTab provides browser automation via REST API. Enable it to use voice commands
        like "go to localhost:3000", "click the login button", "what's on the screen?".
      </p>

      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={config.enabled}
          onChange={(e) => setConfig((p) => ({ ...p, enabled: e.target.checked }))}
        />
        <span>Enabled</span>
        <span className={`text-[10px] ml-auto flex items-center gap-1.5 ${status.available ? "text-green-400" : "text-gray-500"}`}>
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${status.available ? "bg-green-400" : "bg-gray-600"}`} />
          {status.available ? "Running" : "Not running"}
        </span>
      </label>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Port</Label>
          <input
            type="number" className="input-cls" min={1024} max={65535}
            value={config.port}
            onChange={(e) => setConfig((p) => ({ ...p, port: parseInt(e.target.value) || 9867 }))}
          />
        </div>
        <div>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer mt-5">
            <input
              type="checkbox"
              checked={config.headless}
              onChange={(e) => setConfig((p) => ({ ...p, headless: e.target.checked }))}
            />
            Headless mode
          </label>
        </div>
      </div>

      <div>
        <Label>Binary Path <span className="text-gray-500">(optional)</span></Label>
        <input
          className="input-cls"
          value={config.binary}
          onChange={(e) => setConfig((p) => ({ ...p, binary: e.target.value }))}
          placeholder="Auto-detect (pinchtab in PATH)"
        />
        <p className="text-[10px] text-gray-500 mt-1">Leave empty to auto-detect from PATH or /opt/homebrew/bin</p>
      </div>

      {status.available && status.tabs.length > 0 && (
        <div>
          <Label>Open Tabs</Label>
          <div className="space-y-1">
            {status.tabs.map((tab) => (
              <div key={tab.id} className="text-[11px] text-gray-300 bg-gray-700/50 rounded px-2 py-1 truncate">
                {tab.title || "Untitled"} <span className="text-gray-500">— {tab.url}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
          onClick={save}
        >
          Save PinchTab Settings
        </button>
      </div>
    </div>
  )
}

// ─── Recipes ──────────────────────────────────────────────────────────

interface RecipeItem {
  id: string
  name: string
  description: string
  prompt_template: string
  model?: string
  tags: string[]
  builtin: boolean
}

function RecipesSection({
  toast,
}: {
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
}) {
  const [recipes, setRecipes] = useState<RecipeItem[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  const { models } = useModels()

  // Form state
  const [formName, setFormName] = useState("")
  const [formDesc, setFormDesc] = useState("")
  const [formTemplate, setFormTemplate] = useState("")
  const [formModel, setFormModel] = useState("")
  const [formTags, setFormTags] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await GET<RecipeItem[]>("/recipes")
      setRecipes(data || [])
    } catch { /* */ }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const resetForm = () => {
    setFormName("")
    setFormDesc("")
    setFormTemplate("")
    setFormModel("")
    setFormTags("")
    setShowAdd(false)
    setEditingId(null)
  }

  const startEdit = (r: RecipeItem) => {
    setEditingId(r.id)
    setFormName(r.name)
    setFormDesc(r.description)
    setFormTemplate(r.prompt_template)
    setFormModel(r.model || "")
    setFormTags((r.tags || []).join(", "))
    setShowAdd(false)
  }

  const startAdd = () => {
    resetForm()
    setShowAdd(true)
  }

  const saveNew = async () => {
    if (!formName.trim() || !formTemplate.trim()) {
      toast("Name and prompt template are required", "error")
      return
    }
    try {
      await POST("/recipes", {
        name: formName.trim(),
        description: formDesc.trim(),
        prompt_template: formTemplate.trim(),
        model: formModel || undefined,
        tags: formTags.split(",").map((t) => t.trim()).filter(Boolean),
      })
      toast("Recipe created", "success")
      resetForm()
      load()
    } catch {
      toast("Failed to create recipe", "error")
    }
  }

  const saveEdit = async () => {
    if (!editingId || !formName.trim() || !formTemplate.trim()) {
      toast("Name and prompt template are required", "error")
      return
    }
    try {
      await PATCH(`/recipes/${editingId}`, {
        name: formName.trim(),
        description: formDesc.trim(),
        prompt_template: formTemplate.trim(),
        model: formModel || null,
        tags: formTags.split(",").map((t) => t.trim()).filter(Boolean),
      })
      toast("Recipe updated", "success")
      resetForm()
      load()
    } catch {
      toast("Failed to update recipe", "error")
    }
  }

  const deleteRecipe = async (id: string) => {
    try {
      await DELETE(`/recipes/${id}`)
      toast("Recipe deleted", "success")
      if (editingId === id) resetForm()
      load()
    } catch {
      toast("Failed to delete recipe", "error")
    }
  }

  if (loading) return <p className="text-xs text-gray-500 animate-pulse">Loading...</p>

  const isEditing = editingId !== null
  const showForm = showAdd || isEditing

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-gray-400">
        Recipes are reusable task templates. Built-in recipes are read-only. Add your own with custom prompt templates.
      </p>
      <p className="text-[11px] text-gray-500">
        Use placeholders: <code className="bg-gray-700 px-1 rounded">{"{project_name}"}</code>, <code className="bg-gray-700 px-1 rounded">{"{stack}"}</code>, <code className="bg-gray-700 px-1 rounded">{"{project_path}"}</code>
      </p>

      {/* Recipe list */}
      <div className="space-y-1">
        {recipes.map((r) => (
          <div
            key={r.id}
            className={`flex items-center justify-between bg-gray-700/50 rounded px-3 py-2 ${
              editingId === r.id ? "ring-1 ring-blue-500" : ""
            }`}
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-gray-200 truncate">{r.name}</span>
                {r.builtin && (
                  <span className="px-1.5 py-0.5 text-[9px] rounded bg-gray-600 text-gray-300">Built-in</span>
                )}
              </div>
              <div className="text-[10px] text-gray-400 truncate">{r.description}</div>
              {r.model && (
                <span className="text-[9px] text-blue-400">{r.model}</span>
              )}
              {r.tags && r.tags.length > 0 && (
                <div className="flex gap-1 mt-1">
                  {r.tags.map((t) => (
                    <span key={t} className="px-1 py-0.5 text-[9px] rounded bg-gray-600/50 text-gray-400">{t}</span>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-1 ml-2 flex-shrink-0">
              <button
                className="px-2 py-1 text-[10px] rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
                onClick={() => startEdit(r)}
              >
                Edit
              </button>
              {!r.builtin && (
                <button
                  className="px-2 py-1 text-[10px] rounded bg-red-600/80 hover:bg-red-600 text-white"
                  onClick={() => deleteRecipe(r.id)}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Add / Edit form */}
      {showForm && (
        <div className="border border-gray-700 rounded p-3 bg-gray-900/60 space-y-2">
          <h4 className="text-xs font-medium text-gray-300">
            {isEditing ? "Edit Recipe" : "New Recipe"}
          </h4>
          <div>
            <Label>Name</Label>
            <input
              className="input-cls"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="My Recipe"
            />
          </div>
          <div>
            <Label>Description</Label>
            <input
              className="input-cls"
              value={formDesc}
              onChange={(e) => setFormDesc(e.target.value)}
              placeholder="What this recipe does"
            />
          </div>
          <div>
            <Label>Prompt Template</Label>
            <textarea
              className="input-cls min-h-[120px] font-mono text-[11px]"
              value={formTemplate}
              onChange={(e) => setFormTemplate(e.target.value)}
              placeholder="You are a senior engineer reviewing {project_name}..."
            />
          </div>
          <ModelSelector models={models} value={formModel} onChange={setFormModel} />
          <div>
            <Label>Tags <span className="text-gray-500">(comma-separated)</span></Label>
            <input
              className="input-cls"
              value={formTags}
              onChange={(e) => setFormTags(e.target.value)}
              placeholder="review, quality, security"
            />
          </div>
          <div className="flex gap-2 justify-end">
            <button
              className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
              onClick={resetForm}
            >
              Cancel
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
              onClick={isEditing ? saveEdit : saveNew}
            >
              {isEditing ? "Save Changes" : "Create Recipe"}
            </button>
          </div>
        </div>
      )}

      {!showForm && (
        <button
          className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
          onClick={startAdd}
        >
          + Add Recipe
        </button>
      )}
    </div>
  )
}

// ─── Phone ────────────────────────────────────────────────────────────

function PhoneSection({
  settings,
  toast,
}: {
  settings: AdminSettings
  toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void
}) {
  const [userNumber, setUserNumber] = useState(settings.settings.phone_user_number || "")
  const [webhookUrl, setWebhookUrl] = useState(settings.settings.phone_webhook_url || "")

  const save = async () => {
    try {
      await Promise.all([
        POST("/admin/settings", { key: "phone_user_number", value: userNumber.trim() }),
        POST("/admin/settings", { key: "phone_webhook_url", value: webhookUrl.trim() }),
      ])
      toast("Phone settings saved", "success")
    } catch {
      toast("Failed to save", "error")
    }
  }

  const twilioKeys = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"]

  return (
    <div className="space-y-4">
      <p className="text-[10px] text-gray-500">
        Call your phone from the dashboard to interact with the orchestrator via voice
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Your Phone Number</Label>
          <input
            className="input-cls font-mono"
            value={userNumber}
            onChange={(e) => setUserNumber(e.target.value)}
            placeholder="+1234567890"
          />
          <p className="text-[10px] text-gray-500 mt-1">E.164 format</p>
        </div>
        <div>
          <Label>Webhook Base URL</Label>
          <input
            className="input-cls font-mono"
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            placeholder="https://abc123.ngrok.io"
          />
          <p className="text-[10px] text-gray-500 mt-1">Public URL for Twilio callbacks</p>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        {twilioKeys.map((key) => {
          const isSet = settings.secrets.includes(key)
          const label = key.replace("TWILIO_", "").replace(/_/g, " ")
          return (
            <span
              key={key}
              className={`px-2 py-1 rounded text-[10px] ${
                isSet ? "bg-green-900 text-green-400" : "bg-red-900 text-red-400"
              }`}
            >
              {label}: {isSet ? "\u2713" : "\u2717"}
            </span>
          )
        })}
      </div>

      <p className="text-[10px] text-gray-500">
        Set secrets via CLI: <code className="bg-gray-700 px-1 rounded">rdc config set-secret TWILIO_ACCOUNT_SID value</code>
      </p>

      <div className="flex justify-end">
        <button className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white" onClick={save}>
          Save Phone Settings
        </button>
      </div>
    </div>
  )
}

// ─── API Keys ─────────────────────────────────────────────────────────

function APIKeysSection({ secrets }: { secrets: string[] }) {
  const allKeys = [
    "ELEVENLABS_API_KEY",
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "X_BEARER_TOKEN",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
  ]

  return (
    <div className="space-y-2">
      {allKeys.map((key) => {
        const isSet = secrets.includes(key)
        return (
          <div key={key} className="flex items-center justify-between bg-gray-700 rounded px-3 py-2">
            <div>
              <div className="font-mono text-xs">{key}</div>
              <div className={`text-[10px] ${isSet ? "text-green-400" : "text-gray-500"}`}>
                {isSet ? "\u2713 Configured" : "\u2717 Not set"}
              </div>
            </div>
            {isSet ? (
              <span className="text-[10px] text-gray-400">{"\u2022".repeat(8)}</span>
            ) : (
              <span className="text-[10px] text-yellow-400">
                Required for {key.split("_")[0].toLowerCase()}
              </span>
            )}
          </div>
        )
      })}
      <p className="text-[10px] text-gray-500 mt-3">
        To add/update keys: <code className="bg-gray-700 px-1 rounded">rdc config set-secret KEY_NAME value</code>
      </p>
    </div>
  )
}

// ─── Server Info ──────────────────────────────────────────────────────

function ServerInfoSection({ settings }: { settings: Record<string, string> }) {
  const [showRaw, setShowRaw] = useState(false)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-400">Version</div>
          <div className="text-xs font-mono">v2.0</div>
        </div>
        <div className="bg-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-400">Database</div>
          <div className="text-xs font-mono">SQLite</div>
        </div>
        <div className="bg-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-400">Settings</div>
          <div className="text-xs font-mono">{Object.keys(settings).length} keys</div>
        </div>
      </div>

      <button
        className="text-xs text-blue-400 hover:text-blue-300"
        onClick={() => setShowRaw(!showRaw)}
      >
        {showRaw ? "Hide" : "Show"} raw settings
      </button>
      {showRaw && (
        <pre className="bg-gray-900 rounded p-2 text-[10px] text-gray-300 overflow-auto max-h-48 font-mono">
          {JSON.stringify(settings, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ─── Sessions ─────────────────────────────────────────────────────────

function SessionsSection({ toast }: { toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void }) {
  const [sessions, setSessions] = useState<PairedSession[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await GET<PairedSession[]>("/auth/sessions")
      setSessions(data || [])
    } catch { /* */ }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const revoke = async (id: string) => {
    try {
      await DELETE(`/auth/sessions/${id}`)
      toast("Session revoked", "success")
      load()
    } catch {
      toast("Failed to revoke", "error")
    }
  }

  if (loading) return <p className="text-xs text-gray-500 animate-pulse">Loading...</p>

  if (sessions.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-sm text-gray-400">No paired devices</p>
        <p className="text-xs text-gray-500 mt-1">
          Pair a device by scanning the QR code on the login screen
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {sessions.map((s) => (
        <div key={s.id} className="flex items-center justify-between bg-gray-700 rounded px-3 py-2">
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium text-gray-200 truncate">
              {s.device_name || "Unknown Device"}
            </div>
            <div className="text-[10px] text-gray-400">
              {s.role} &middot; paired {new Date(s.created_at).toLocaleDateString()}
              {s.last_used_at && (
                <> &middot; last used {new Date(s.last_used_at).toLocaleDateString()}</>
              )}
            </div>
          </div>
          <button
            className="px-2 py-1 text-[10px] rounded bg-red-600 hover:bg-red-700 text-white flex-shrink-0 ml-2"
            onClick={() => revoke(s.id)}
          >
            Revoke
          </button>
        </div>
      ))}
    </div>
  )
}

// ─── Caddy Proxy ─────────────────────────────────────────────────

interface CaddyStatus {
  enabled: boolean
  running: boolean
  available?: boolean
  base_domain?: string
  listen_port?: number
  routes: { process_id: string; subdomain: string; target_port: number; url: string }[]
}

interface CaddyConfigData {
  enabled: boolean
  base_domain: string
  rdc_domain: string
  admin_port: number
  listen_port: number
}

function CaddySection({ toast }: { toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void }) {
  const [status, setStatus] = useState<CaddyStatus | null>(null)
  const [cfg, setCfg] = useState<CaddyConfigData | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [statusData, cfgData] = await Promise.all([
        GET<CaddyStatus>("/caddy/status"),
        GET<{ config: CaddyConfigData }>("/config/caddy"),
      ])
      setStatus(statusData || null)
      if (cfgData) setCfg(cfgData.config)
    } catch { /* */ }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const save = async (patch: Partial<CaddyConfigData>) => {
    try {
      const result = await PATCH<{ config: CaddyConfigData }>("/config/caddy", patch)
      if (result) setCfg(result.config)
      toast("Saved", "success")
    } catch {
      toast("Failed to save", "error")
    }
  }

  const restartServer = async () => {
    try {
      await POST("/admin/restart")
      toast("Server restarting...", "info")
    } catch {
      toast("Failed to restart server", "error")
    }
  }

  if (loading) return <p className="text-xs text-gray-500 animate-pulse">Loading...</p>
  if (!cfg) return <p className="text-xs text-gray-500">Failed to load Caddy config</p>

  return (
    <div className="space-y-4">
      {/* Enable toggle */}
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={cfg.enabled}
          onChange={(e) => {
            const enabled = e.target.checked
            setCfg((p) => p && ({ ...p, enabled }))
            save({ enabled })
          }}
        />
        <span>Enabled</span>
        <span className={`text-[10px] ml-auto ${cfg.enabled ? "text-green-400" : "text-yellow-400"}`}>
          {cfg.enabled ? (status?.running ? "Running" : "Enabled (not running)") : "Disabled"}
        </span>
      </label>

      {/* Config fields */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Preview Domain</Label>
          <input
            className="input-cls font-mono"
            value={cfg.base_domain}
            onChange={(e) => setCfg((p) => p && ({ ...p, base_domain: e.target.value }))}
            onBlur={() => save({ base_domain: cfg.base_domain })}
            placeholder="preview.example.com"
          />
          <p className="text-[10px] text-gray-500 mt-1">Subdomains: {"{name}"}.{cfg.base_domain}</p>
        </div>
        <div>
          <Label>RDC Domain</Label>
          <input
            className="input-cls font-mono"
            value={cfg.rdc_domain}
            onChange={(e) => setCfg((p) => p && ({ ...p, rdc_domain: e.target.value }))}
            onBlur={() => save({ rdc_domain: cfg.rdc_domain })}
            placeholder="rdc.example.com"
          />
          <p className="text-[10px] text-gray-500 mt-1">Routes to RDC dashboard</p>
        </div>
        <div>
          <Label>Listen Port</Label>
          <input
            type="number" className="input-cls font-mono" min={1} max={65535}
            value={cfg.listen_port}
            onChange={(e) => setCfg((p) => p && ({ ...p, listen_port: parseInt(e.target.value) || 8888 }))}
            onBlur={() => save({ listen_port: cfg.listen_port })}
          />
          <p className="text-[10px] text-gray-500 mt-1">Caddy HTTP listener</p>
        </div>
        <div>
          <Label>Admin Port</Label>
          <input
            type="number" className="input-cls font-mono" min={1} max={65535}
            value={cfg.admin_port}
            onChange={(e) => setCfg((p) => p && ({ ...p, admin_port: parseInt(e.target.value) || 2019 }))}
            onBlur={() => save({ admin_port: cfg.admin_port })}
          />
          <p className="text-[10px] text-gray-500 mt-1">Caddy admin API</p>
        </div>
      </div>

      {/* Runtime status + routes (only when enabled) */}
      {cfg.enabled && status && (
        <>
          <div className="border-t border-gray-700 pt-3">
            <h4 className="text-xs font-medium text-gray-300 mb-2">Active Routes ({status.routes.length})</h4>
            {status.routes.length === 0 ? (
              <p className="text-[10px] text-gray-500">No active routes — start a process with a port</p>
            ) : (
              <div className="space-y-1">
                {status.routes.map((r) => (
                  <div key={r.process_id} className="flex items-center justify-between bg-gray-700 rounded px-3 py-1.5">
                    <div className="min-w-0 flex-1">
                      <a href={r.url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-400 hover:text-blue-300 truncate block">
                        {r.subdomain}.{status.base_domain}
                      </a>
                    </div>
                    <span className="text-[10px] text-gray-400 ml-2 flex-shrink-0">:{r.target_port}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

        </>
      )}

      <p className="text-[10px] text-gray-500">
        {cfg.enabled
          ? "Changes require a server restart to take effect."
          : "Enable Caddy and restart the server to start the reverse proxy."
        } Caddy binary is auto-downloaded to <code className="bg-gray-700 px-1 rounded">~/.rdc/bin/</code> if not in PATH.
      </p>

      <div className="flex justify-end">
        <button
          className="px-3 py-1.5 text-xs rounded bg-yellow-600 hover:bg-yellow-700 text-white"
          onClick={restartServer}
        >
          Restart Server to Apply
        </button>
      </div>
    </div>
  )
}

// ─── Shared ───────────────────────────────────────────────────────────

function RefreshModelsButton({ toast }: { toast: (msg: string, type?: "info" | "success" | "error" | "warning") => void }) {
  const [refreshing, setRefreshing] = useState(false)

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const models = await fetchModels(true)
      toast(`Refreshed ${models.length} models from OpenRouter + Ollama`, "success")
    } catch {
      toast("Failed to refresh models", "error")
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-50"
        onClick={handleRefresh}
        disabled={refreshing}
      >
        {refreshing ? "Refreshing..." : "Refresh Available Models"}
      </button>
      <span className="text-[10px] text-gray-500">
        Fetches latest models from OpenRouter and local Ollama
      </span>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs text-gray-400 mb-1">{children}</label>
}
