import { useProjectStore } from "@/stores/project-store"
import { useUIStore } from "@/stores/ui-store"
import { POST } from "@/lib/api"
import { Sheet } from "./sheet"

const THEMES = [
  { id: "default", label: "Standard" },
  { id: "modern", label: "Modern" },
  { id: "brutalist", label: "Brutalist" },
]

const LAYOUTS = [
  { id: "desktop", label: "Desktop" },
  { id: "mobile", label: "Mobile" },
  { id: "kiosk", label: "Kiosk" },
]

export function HamburgerSheet({
  onClose,
  onAddProject,
  onCreateTask,
  onActivity,
  onProjectSettings,
  onSystemSettings,
  onPairedDevices,
}: {
  onClose: () => void
  onAddProject: () => void
  onCreateTask: () => void
  onActivity: () => void
  onProjectSettings: () => void
  onSystemSettings: () => void
  onPairedDevices: () => void
}) {
  const currentProject = useProjectStore((s) => s.currentProject)
  const toast = useUIStore((s) => s.toast)
  const theme = useUIStore((s) => s.theme)
  const setTheme = useUIStore((s) => s.setTheme)
  const layout = useUIStore((s) => s.layout)
  const setLayout = useUIStore((s) => s.setLayout)

  const handleRestart = async () => {
    if (!confirm("Restart the server?")) return
    try {
      await POST("/admin/restart")
      toast("Server restarting...", "info")
    } catch { toast("Restart failed", "error") }
  }

  return (
    <Sheet onClose={onClose} title="Menu">
      <div className="space-y-1">
        <MenuItem onClick={onAddProject}>Add Project</MenuItem>
        <MenuItem onClick={onCreateTask}>Create Task</MenuItem>
        <MenuItem onClick={() => { window.location.href = "/kb" }}>Knowledge Base</MenuItem>
        <MenuItem onClick={onActivity}>Activity Log</MenuItem>
        <MenuItem onClick={onPairedDevices}>Paired Devices</MenuItem>
        <hr className="border-gray-700 my-2" />
        {currentProject && (
          <MenuItem onClick={onProjectSettings}>Project Settings</MenuItem>
        )}
        <MenuItem onClick={onSystemSettings}>System Settings</MenuItem>
        <MenuItem onClick={handleRestart} danger>Restart Server</MenuItem>
        <hr className="border-gray-700 my-2" />
        {/* Theme picker */}
        <div className="px-3 py-2">
          <div className="text-xs text-gray-500 mb-2">Theme</div>
          <div className="flex rounded-lg overflow-hidden border border-gray-600">
            {THEMES.map((t) => (
              <button
                key={t.id}
                className={`flex-1 py-2 text-xs font-medium ${
                  theme === t.id
                    ? "bg-blue-600 text-white"
                    : "bg-gray-700 text-gray-300"
                }`}
                onClick={() => setTheme(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        {/* Layout picker */}
        <div className="px-3 py-2">
          <div className="text-xs text-gray-500 mb-2">Layout</div>
          <div className="flex rounded-lg overflow-hidden border border-gray-600">
            {LAYOUTS.map((l) => (
              <button
                key={l.id}
                className={`flex-1 py-2 text-xs font-medium ${
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
        <hr className="border-gray-700 my-2" />
        <MenuItem
          onClick={() => {
            localStorage.removeItem("rdc_token")
            window.location.reload()
          }}
          danger
        >
          Log Out
        </MenuItem>
      </div>
    </Sheet>
  )
}

function MenuItem({
  children,
  onClick,
  danger,
}: {
  children: React.ReactNode
  onClick: () => void
  danger?: boolean
}) {
  return (
    <button
      className={`w-full text-left px-3 py-2.5 rounded-lg text-sm ${
        danger ? "text-red-400 hover:bg-red-900/30" : "text-gray-200 hover:bg-gray-700"
      }`}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
