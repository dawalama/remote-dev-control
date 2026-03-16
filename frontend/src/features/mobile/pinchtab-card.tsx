import { useEffect } from "react"
import { usePinchTabStore } from "@/stores/pinchtab-store"

export function PinchTabCard({ onOpen }: { onOpen: () => void }) {
  const { available, tabs, activeTabId, screenshotDataUrl, loading, loadStatus, startPinchTab } =
    usePinchTabStore()

  useEffect(() => {
    loadStatus()
    const interval = setInterval(loadStatus, 30000)
    return () => clearInterval(interval)
  }, [loadStatus])

  const activeTab = tabs.find((t) => t.id === activeTabId)
  const activeUrl = activeTab?.url?.replace(/^https?:\/\//, "").slice(0, 40)

  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            PinchTab
          </h3>
          {available && tabs.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-600 text-white">
              {tabs.length}
            </span>
          )}
        </div>
        {available && (
          <span className="w-2 h-2 rounded-full bg-green-400" />
        )}
      </div>

      {!available ? (
        <button
          className="w-full py-2 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          onClick={startPinchTab}
          disabled={loading}
        >
          {loading ? "Starting..." : "Start"}
        </button>
      ) : (
        <button
          className="w-full flex items-center gap-3 py-1"
          onClick={onOpen}
        >
          {/* Screenshot thumbnail */}
          {screenshotDataUrl ? (
            <img
              src={screenshotDataUrl}
              alt="PinchTab preview"
              className="w-[120px] h-[68px] rounded border border-gray-700 object-cover flex-shrink-0"
            />
          ) : (
            <div className="w-[120px] h-[68px] rounded border border-gray-700 bg-gray-900 flex items-center justify-center flex-shrink-0">
              <span className="text-[10px] text-gray-600">No preview</span>
            </div>
          )}

          <div className="flex-1 min-w-0 text-left">
            {activeUrl && (
              <p className="text-xs text-gray-300 truncate">{activeUrl}</p>
            )}
            <p className="text-[10px] text-gray-500 mt-0.5">Tap to open</p>
          </div>
        </button>
      )}
    </div>
  )
}
