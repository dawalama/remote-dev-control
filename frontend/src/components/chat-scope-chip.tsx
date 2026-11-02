import { useChatScopeStore } from "@/stores/chat-scope-store"
import { useChannelStore } from "@/stores/channel-store"

/**
 * Compact HomeBase / pinned-workstream control for the control-room chat surface.
 */
export function ChatScopeChip({ compact = false }: { compact?: boolean }) {
  const mode = useChatScopeStore((s) => s.mode)
  const pinnedWorkstreamId = useChatScopeStore((s) => s.pinnedWorkstreamId)
  const setHomebase = useChatScopeStore((s) => s.setHomebase)
  const pinActive = useChatScopeStore((s) => s.pinActive)
  const channels = useChannelStore((s) => s.channels)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)

  const pinned = channels.find((c) => c.id === pinnedWorkstreamId)
  const active = channels.find((c) => c.id === activeChannelId)
  const pinLabel = (pinned || active)?.name?.replace(/^#/, "") || "workstream"

  if (mode === "pinned" && pinnedWorkstreamId) {
    return (
      <div className={`flex items-center gap-1 ${compact ? "" : ""}`}>
        <span
          className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-200"
          title="Chat scope is pinned to this workstream. Brief me is filtered."
        >
          Pinned · {pinLabel}
        </span>
        <button
          type="button"
          onClick={setHomebase}
          className="rounded px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-700 hover:text-gray-200"
          title="Back to whole HomeBase"
        >
          Unpin
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <span
        className="rounded-full border border-blue-500/40 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-200"
        title="Whole-machine HomeBase scope. Brief me sees all projects."
      >
        HomeBase
      </span>
      {activeChannelId && (
        <button
          type="button"
          onClick={() => {
            if (!pinActive()) return
          }}
          className="rounded px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-700 hover:text-gray-200"
          title={`Pin ${pinLabel}`}
        >
          Pin {compact ? "" : pinLabel}
        </button>
      )}
    </div>
  )
}
