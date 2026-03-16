import { useEffect, useState } from "react"
import { GET, DELETE } from "@/lib/api"
import { useUIStore } from "@/stores/ui-store"
import { useAuthStore } from "@/stores/auth-store"
import { Sheet } from "./sheet"

interface PairedSession {
  id: string
  device_name?: string
  role?: string
  created_at: string
  last_used_at?: string
}

interface AuthMe {
  id: string
  name: string
  role: string
  device_name?: string
  parent_token_id?: string
  is_parent: boolean
}

export function PairedDevicesSheet({ onClose }: { onClose: () => void }) {
  const toast = useUIStore((s) => s.toast)
  const [sessions, setSessions] = useState<PairedSession[]>([])
  const [me, setMe] = useState<AuthMe | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      GET<PairedSession[]>("/auth/sessions").catch(() => []),
      GET<AuthMe>("/auth/me").catch(() => null),
    ]).then(([s, m]) => {
      setSessions(s ?? [])
      setMe(m ?? null)
      setLoading(false)
    })
  }, [])

  const isParent = me?.is_parent ?? true

  const revoke = async (id: string) => {
    try {
      await DELETE(`/auth/sessions/${id}`)
      if (id === me?.id) {
        // Self-disconnect: clear local token and redirect to login
        toast("Logged out", "success")
        useAuthStore.getState().logout()
        return
      }
      toast("Session revoked", "success")
      setSessions((prev) => prev.filter((s) => s.id !== id))
    } catch {
      toast("Failed to revoke", "error")
    }
  }

  return (
    <Sheet onClose={onClose} title="Paired Devices">
      {loading ? (
        <p className="text-xs text-gray-500 text-center py-6">Loading...</p>
      ) : sessions.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-sm text-gray-400">
            {isParent ? "No paired devices" : "Not connected"}
          </p>
          <p className="text-xs text-gray-500 mt-1">
            {isParent
              ? "Pair a device by scanning the QR code on the login screen"
              : "This device is a paired session"}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {sessions.map((s) => {
            const isSelf = s.id === me?.id
            return (
              <div
                key={s.id}
                className="flex items-center justify-between bg-gray-700 rounded-lg px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-gray-200 truncate">
                    {s.device_name || "Unknown Device"}
                    {isSelf && (
                      <span className="ml-1.5 text-xs text-gray-400">(this device)</span>
                    )}
                  </div>
                  <div className="text-xs text-gray-400">
                    {s.role || "paired"} &middot;{" "}
                    {new Date(s.created_at).toLocaleDateString()}
                    {s.last_used_at && (
                      <> &middot; last used {new Date(s.last_used_at).toLocaleDateString()}</>
                    )}
                  </div>
                </div>
                {isParent ? (
                  <button
                    className="ml-2 px-2.5 py-1 text-xs rounded bg-red-600 text-white flex-shrink-0"
                    onClick={() => revoke(s.id)}
                  >
                    Revoke
                  </button>
                ) : isSelf ? (
                  <button
                    className="ml-2 px-2.5 py-1 text-xs rounded bg-yellow-600 text-white flex-shrink-0"
                    onClick={() => revoke(s.id)}
                  >
                    Log Out
                  </button>
                ) : null}
              </div>
            )
          })}
        </div>
      )}
    </Sheet>
  )
}
