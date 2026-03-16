import { useProjectStore } from "@/stores/project-store"
import type { ProjectProfile } from "@/types"

const STACK_COLORS: Record<string, string> = {
  python: "bg-yellow-900/60 text-yellow-300 border-yellow-700/40",
  react: "bg-cyan-900/60 text-cyan-300 border-cyan-700/40",
  vue: "bg-green-900/60 text-green-300 border-green-700/40",
  next: "bg-gray-700 text-gray-200 border-gray-600",
  fastapi: "bg-teal-900/60 text-teal-300 border-teal-700/40",
  django: "bg-green-900/60 text-green-300 border-green-700/40",
  flask: "bg-gray-700 text-gray-300 border-gray-600",
  vite: "bg-purple-900/60 text-purple-300 border-purple-700/40",
  tailwind: "bg-sky-900/60 text-sky-300 border-sky-700/40",
  typescript: "bg-blue-900/60 text-blue-300 border-blue-700/40",
  rust: "bg-orange-900/60 text-orange-300 border-orange-700/40",
  go: "bg-cyan-900/60 text-cyan-300 border-cyan-700/40",
}

export function StackBadge({ tag }: { tag: string }) {
  const cls = STACK_COLORS[tag] || "bg-gray-700 text-gray-300 border-gray-600"
  return (
    <span className={`px-1.5 py-0.5 text-[10px] rounded-full border ${cls}`}>
      {tag}
    </span>
  )
}

export function ProjectCard({ onEdit }: { onEdit: () => void }) {
  const currentProject = useProjectStore((s) => s.currentProject)
  const projects = useProjectStore((s) => s.projects)

  if (currentProject === "all") return null

  const project = projects.find((p) => p.name === currentProject)
  const profile: ProjectProfile | undefined = project?.config?.profile as ProjectProfile | undefined
  const hasProfile = profile && (profile.purpose || profile.stack?.length || profile.test_command)

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Project
        </h3>
        <button className="text-[10px] text-blue-400" onClick={onEdit}>
          Edit
        </button>
      </div>

      {!hasProfile ? (
        <p className="text-xs text-gray-500">
          No profile yet.{" "}
          <button className="text-blue-400 underline" onClick={onEdit}>
            Set up project profile
          </button>
        </p>
      ) : (
        <div className="space-y-1.5">
          {profile.purpose && (
            <p className="text-xs text-gray-300 line-clamp-2">{profile.purpose}</p>
          )}
          {profile.stack && profile.stack.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {profile.stack.map((tag) => (
                <StackBadge key={tag} tag={tag} />
              ))}
            </div>
          )}
          {profile.test_command && (
            <p className="text-[10px] text-gray-500">
              Test: <span className="font-mono text-gray-400">{profile.test_command}</span>
            </p>
          )}
        </div>
      )}
    </div>
  )
}
