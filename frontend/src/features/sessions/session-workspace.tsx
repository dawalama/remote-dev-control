import { useUIStore } from "@/stores/ui-store"
import { SessionViewer } from "./session-viewer"

export function SessionWorkspace() {
  const sessionId = useUIStore((state) => state.viewingSessionId)
  const closeSessionViewer = useUIStore((state) => state.closeSessionViewer)
  const setViewingSessionId = useUIStore((state) => state.setViewingSessionId)
  if (!sessionId) return null

  return (
    <main className="fixed inset-0 z-[175] flex flex-col bg-gray-900 p-2 text-gray-100 sm:p-4">
      <div className="mb-2 flex justify-end">
        <button onClick={() => setViewingSessionId(null)} className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800 hover:text-white">Open HomeBase</button>
      </div>
      <SessionViewer key={sessionId} sessionId={sessionId} onClose={closeSessionViewer} />
    </main>
  )
}
