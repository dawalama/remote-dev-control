import { useState, useEffect } from "react"
import { GET } from "@/lib/api"
import { Sheet } from "./sheet"

interface ActivityEvent {
  id: string
  type: string
  message: string
  project?: string
  timestamp: string
}

export function ActivitySheet({ onClose }: { onClose: () => void }) {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(true)

  const loadMore = async (before?: string) => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: "30" })
      if (before) params.set("before", before)
      const data = await GET<ActivityEvent[]>(`/activity?${params}`)
      if (data.length < 30) setHasMore(false)
      if (data.length > 0) setCursor(data[data.length - 1].timestamp)
      setEvents((prev) => (before ? [...prev, ...data] : data))
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadMore()
  }, [])

  const formatTime = (ts: string) => {
    try {
      const d = new Date(ts)
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    } catch {
      return ts
    }
  }

  return (
    <Sheet onClose={onClose} title="Activity">
      <div className="space-y-1">
        {events.map((ev) => (
          <div key={ev.id} className="flex items-start gap-2 py-1.5">
            <span className="text-[10px] text-gray-500 w-12 flex-shrink-0 pt-0.5">
              {formatTime(ev.timestamp)}
            </span>
            <span className="text-xs text-gray-300 flex-1">{ev.message}</span>
          </div>
        ))}
        {events.length === 0 && !loading && (
          <p className="text-xs text-gray-500 text-center py-4">No activity</p>
        )}
        {loading && (
          <p className="text-xs text-gray-500 text-center py-2 animate-pulse">Loading...</p>
        )}
        {hasMore && !loading && events.length > 0 && (
          <button
            className="w-full py-2 text-xs text-blue-400"
            onClick={() => cursor && loadMore(cursor)}
          >
            Load more
          </button>
        )}
      </div>
    </Sheet>
  )
}
