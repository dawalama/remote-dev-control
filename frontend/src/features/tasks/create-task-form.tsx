import { useState, useEffect } from "react"
import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { GET, POST } from "@/lib/api"
import type { Recipe } from "@/types"

export interface ModelOption {
  id: string
  label: string
  provider?: string
  tags?: string[]
  cost_tier?: string
  context_length?: number
  has_reasoning?: boolean
  has_tools?: boolean
  has_vision?: boolean
  tag_str?: string
}

// Built-in recipes — always available even if /recipes API fails
const BUILTIN_RECIPES: Recipe[] = [
  {
    id: "code-audit",
    name: "Code Audit",
    description: "Security-focused code audit with structured scoring across 5 categories",
    tags: ["security", "audit", "quality"],
    model: "opus-4.6",
    builtin: true,
  },
]

// Fallback if /models endpoint is unavailable
const FALLBACK_MODELS: ModelOption[] = [
  { id: "", label: "Default" },
  { id: "opus-4.6", label: "Claude 4.6 Opus" },
  { id: "sonnet-4.6", label: "Claude 4.6 Sonnet" },
]

// Shared cache so multiple components don't re-fetch
let _modelsCache: ModelOption[] | null = null
let _modelsFetchPromise: Promise<ModelOption[]> | null = null

export function fetchModels(refresh = false): Promise<ModelOption[]> {
  if (_modelsCache && !refresh) return Promise.resolve(_modelsCache)
  if (_modelsFetchPromise && !refresh) return _modelsFetchPromise

  _modelsFetchPromise = GET<ModelOption[]>(refresh ? "/models?refresh=true" : "/models")
    .then((models) => {
      _modelsCache = models && models.length > 0 ? models : FALLBACK_MODELS
      _modelsFetchPromise = null
      return _modelsCache
    })
    .catch(() => {
      _modelsCache = FALLBACK_MODELS
      _modelsFetchPromise = null
      return _modelsCache
    })

  return _modelsFetchPromise
}

/** Hook to get the available models list. Fetches once from /models. */
export function useModels() {
  const [models, setModels] = useState<ModelOption[]>(_modelsCache || FALLBACK_MODELS)

  useEffect(() => {
    fetchModels().then(setModels)
  }, [])

  const refresh = () => fetchModels(true).then(setModels)

  return { models, refresh }
}

// Backward compat — other files import MODELS
export const MODELS = FALLBACK_MODELS

/**
 * Shared task creation form used by both desktop (modal) and mobile (sheet).
 * When a recipe is selected, its prompt_template fills the description textarea
 * so the user can review/edit before submitting.
 */
export function CreateTaskForm({ onClose }: { onClose: () => void }) {
  const projects = useProjectStore((s) => s.projects)
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)

  const { models, refresh: refreshModels } = useModels()
  const [project, setProject] = useState(currentProject === "all" ? (projects[0]?.name || "") : currentProject)
  const [description, setDescription] = useState("")
  const [model, setModel] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [recipes, setRecipes] = useState<Recipe[]>(BUILTIN_RECIPES)
  const [selectedRecipe, setSelectedRecipe] = useState("")

  useEffect(() => {
    GET<Recipe[]>("/recipes")
      .then((r) => {
        if (Array.isArray(r) && r.length > 0) {
          setRecipes(r)
        }
      })
      .catch(() => {})  // keep built-in fallback
  }, [])

  const handleRecipeChange = (recipeId: string) => {
    setSelectedRecipe(recipeId)
    if (recipeId) {
      const recipe = recipes.find((r) => r.id === recipeId)
      if (recipe?.prompt_template) {
        setDescription(recipe.prompt_template)
      }
      setModel(recipe?.model || "")
    } else {
      setDescription("")
      setModel("")
    }
  }

  const handleSubmit = async () => {
    if (!description.trim()) return
    if (!project) return
    setSubmitting(true)
    try {
      await POST("/tasks", {
        project,
        description: description.trim(),
        recipe_id: selectedRecipe || undefined,
        model: model || undefined,
      })
      toast("Task created", "success")
      onClose()
    } catch {
      toast("Failed to create task", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-3">
      {recipes.length > 0 && (
        <div>
          <label className="block text-sm text-gray-400 mb-1">Recipe</label>
          <select
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
            value={selectedRecipe}
            onChange={(e) => handleRecipeChange(e.target.value)}
          >
            <option value="">None — custom task</option>
            {recipes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}{r.model ? ` (${r.model})` : ""}
              </option>
            ))}
          </select>
        </div>
      )}

      <div>
        <label className="block text-sm text-gray-400 mb-1">Project</label>
        <select
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
          value={project}
          onChange={(e) => setProject(e.target.value)}
        >
          {projects.map((p) => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm text-gray-400 mb-1">Description</label>
        <textarea
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 min-h-[120px] resize-y font-mono text-[12px]"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Describe what the task should do..."
          autoFocus={!selectedRecipe}
        />
        {selectedRecipe && (
          <p className="text-[10px] text-gray-500 mt-1">
            Placeholders like {"{project_name}"}, {"{stack}"}, {"{project_path}"} will be filled automatically.
          </p>
        )}
      </div>

      <ModelSelector
        models={models}
        value={model}
        onChange={setModel}
        onRefresh={refreshModels}
      />

      <div className="flex justify-end gap-2 pt-2">
        <button
          className="px-3 py-1.5 text-sm rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
          onClick={onClose}
        >
          Cancel
        </button>
        <button
          className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={handleSubmit}
          disabled={submitting || !description.trim() || !project}
        >
          {submitting ? "Creating..." : "Create Task"}
        </button>
      </div>
    </div>
  )
}


// Cost tier colors for badges
const tierColors: Record<string, string> = {
  free: "bg-green-700 text-green-200",
  cheap: "bg-blue-700 text-blue-200",
  moderate: "bg-yellow-700 text-yellow-200",
  expensive: "bg-orange-700 text-orange-200",
  premium: "bg-red-700 text-red-200",
  local: "bg-green-700 text-green-200",
}

const tagColors: Record<string, string> = {
  reasoning: "bg-purple-700 text-purple-200",
  tools: "bg-cyan-700 text-cyan-200",
  vision: "bg-pink-700 text-pink-200",
  "long-context": "bg-indigo-700 text-indigo-200",
}

/** Reusable model selector with search and tag badges. */
export function ModelSelector({
  models,
  value,
  onChange,
  onRefresh,
  className,
}: {
  models: ModelOption[]
  value: string
  onChange: (id: string) => void
  onRefresh?: () => void
  className?: string
}) {
  const [search, setSearch] = useState("")
  const [open, setOpen] = useState(false)

  const filtered = search
    ? models.filter((m) => {
        const q = search.toLowerCase()
        return (
          m.label.toLowerCase().includes(q) ||
          m.id.toLowerCase().includes(q) ||
          m.provider?.toLowerCase().includes(q) ||
          m.cost_tier?.toLowerCase().includes(q) ||
          m.tags?.some((t) => t.includes(q))
        )
      })
    : models

  const selected = models.find((m) => m.id === value)

  return (
    <div className={className}>
      <label className="block text-sm text-gray-400 mb-1">
        Model
        {onRefresh && (
          <button
            type="button"
            className="ml-2 text-[10px] text-blue-400 hover:text-blue-300"
            onClick={onRefresh}
          >
            refresh
          </button>
        )}
      </label>

      {/* Selected model display */}
      <button
        type="button"
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 text-left outline-none focus:border-blue-500"
        onClick={() => setOpen(!open)}
      >
        <span>{selected?.label || "Default"}</span>
        {selected?.tags && selected.tags.length > 0 && (
          <span className="ml-2 inline-flex gap-1">
            {selected.tags.slice(0, 3).map((t) => (
              <span
                key={t}
                className={`text-[9px] px-1 rounded ${tierColors[t] || tagColors[t] || "bg-gray-600 text-gray-300"}`}
              >
                {t}
              </span>
            ))}
          </span>
        )}
        <span className="float-right text-gray-500">{open ? "\u25B2" : "\u25BC"}</span>
      </button>

      {/* Dropdown */}
      {open && (
        <div className="relative">
          <div className="absolute z-50 w-full mt-1 bg-gray-900 border border-gray-600 rounded shadow-xl max-h-72 flex flex-col">
            <input
              className="w-full px-3 py-1.5 text-sm bg-gray-800 border-b border-gray-700 text-gray-200 outline-none placeholder-gray-500"
              placeholder="Search models..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
            />
            <div className="overflow-y-auto flex-1">
              {filtered.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={`w-full text-left px-3 py-1.5 text-sm hover:bg-gray-700 flex items-center gap-2 ${
                    m.id === value ? "bg-gray-700" : ""
                  }`}
                  onClick={() => {
                    onChange(m.id)
                    setOpen(false)
                    setSearch("")
                  }}
                >
                  <span className="flex-1 truncate">{m.label}</span>
                  {m.tags && m.tags.length > 0 && (
                    <span className="flex gap-0.5 shrink-0">
                      {m.tags.slice(0, 4).map((t) => (
                        <span
                          key={t}
                          className={`text-[9px] px-1 rounded ${tierColors[t] || tagColors[t] || "bg-gray-600 text-gray-300"}`}
                        >
                          {t}
                        </span>
                      ))}
                    </span>
                  )}
                </button>
              ))}
              {filtered.length === 0 && (
                <div className="px-3 py-2 text-xs text-gray-500">No models match "{search}"</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
