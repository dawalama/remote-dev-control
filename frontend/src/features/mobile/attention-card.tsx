interface Terminal {
  id: string
  project: string
  status: string
  waiting_for_input?: boolean
}

export function AttentionCard({
  terminals,
  onOpen,
}: {
  terminals: Terminal[]
  onOpen: (id: string) => void
}) {
  return (
    <div className="bg-gray-800 rounded-lg border-l-4 border-orange-500 p-3">
      <h3 className="text-xs font-semibold text-orange-400 mb-2 flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
        Waiting for Input
      </h3>
      <div className="space-y-2">
        {terminals.map((t) => (
          <div key={t.id} className="flex items-center justify-between">
            <span className="text-sm text-gray-200">{t.project}</span>
            <button
              className="px-3 py-1 text-xs rounded bg-orange-600 text-white"
              onClick={() => onOpen(t.id)}
            >
              Open
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
