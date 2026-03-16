import { useStateStore } from "@/stores/state-store"

export function StatsGrid() {
  const queueStats = useStateStore((s) => s.queueStats)
  const agents = useStateStore((s) => s.agents)

  const working = agents.filter(
    (a) => a.status === "working" || a.status === "running"
  ).length

  const stats = [
    { label: "Running", value: working, color: "text-green-400" },
    { label: "Pending", value: queueStats.pending, color: "text-yellow-400" },
    { label: "Completed", value: queueStats.completed, color: "text-green-400" },
    { label: "Failed", value: queueStats.failed, color: "text-red-400" },
  ]

  return (
    <div className="grid grid-cols-2 gap-3">
      {stats.map((s) => (
        <div key={s.label} className="bg-gray-800 rounded-lg p-3 text-center">
          <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
          <div className="text-gray-400 text-xs">{s.label}</div>
        </div>
      ))}
    </div>
  )
}
