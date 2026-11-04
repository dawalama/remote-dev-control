import { useChannelStore } from "@/stores/channel-store"

/**
 * Compact HomeBase / open-workstream scope chip for the control-room chat surface.
 */
export function ChatScopeChip({ compact = false }: { compact?: boolean }) {
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const selectChannel = useChannelStore((s) => s.selectChannel)

  const isHomebase = !activeChannelId || activeChannelId === "ch-system"
  const active = channels.find((c) => c.id === activeChannelId)
  const label = active?.name?.replace(/^#/, "") || "workstream"

  if (!isHomebase) {
    return (
      <div className={`flex items-center gap-1 ${compact ? "" : ""}`}>
        <span
          className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-200"
          title="Chat scope is the open workstream."
        >
          Workstream · {label}
        </span>
        <button
          type="button"
          onClick={() => selectChannel("ch-system")}
          className="rounded px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-700 hover:text-gray-200"
          title="Back to HomeBase overview"
        >
          HomeBase
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <span
        className="rounded-full border border-blue-500/40 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-200"
        title="Whole-machine HomeBase scope."
      >
        HomeBase
      </span>
    </div>
  )
}
