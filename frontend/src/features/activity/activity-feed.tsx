import { useEffect, useState, useCallback } from "react"
import { GET } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { Button } from "@/components/ui/button"
import type { ActivityEvent } from "@/types"

export function ActivityFeed() {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(false)
  const currentProject = useProjectStore((s) => s.currentProject)

  const loadEvents = useCallback(async (before?: string) => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: "30" })
      if (currentProject) params.set("project", currentProject)
      if (before) params.set("before", before)
      const data = await GET<ActivityEvent[]>(`/activity?${params}`)
      if (before) {
        setEvents((prev) => [...prev, ...data])
      } else {
        setEvents(data)
      }
      setHasMore(data.length >= 30)
    } catch {
      // Activity endpoint may not exist
    } finally {
      setLoading(false)
    }
  }, [currentProject])

  useEffect(() => {
    loadEvents()
  }, [loadEvents])

  if (loading && events.length === 0) {
    return <div className="text-muted-foreground text-sm">Loading activity...</div>
  }

  if (events.length === 0) {
    return <div className="text-muted-foreground text-sm">No activity yet.</div>
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs text-muted-foreground">Recent activity</span>
        <Button variant="secondary" size="sm" className="text-xs h-7" onClick={() => loadEvents()}>
          Refresh
        </Button>
      </div>
      <div className="space-y-1">
        {events.map((event) => (
          <div
            key={event.id}
            className="flex items-start gap-2 py-1.5 text-sm border-b border-border/50 last:border-0"
          >
            <span className="text-muted-foreground text-xs whitespace-nowrap mt-0.5">
              {new Date(event.timestamp).toLocaleTimeString()}
            </span>
            <span className="text-foreground text-xs">{event.message}</span>
            {event.project && (
              <span className="text-muted-foreground text-xs ml-auto whitespace-nowrap">
                {event.project}
              </span>
            )}
          </div>
        ))}
      </div>
      {hasMore && (
        <Button
          variant="ghost"
          size="sm"
          className="w-full mt-2 text-xs"
          onClick={() => {
            const last = events[events.length - 1]
            if (last) loadEvents(last.timestamp)
          }}
        >
          Load more
        </Button>
      )}
    </div>
  )
}
