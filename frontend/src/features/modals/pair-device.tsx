import { useState, useEffect, useRef } from "react"
import { QRCodeSVG } from "qrcode.react"
import { useAuthStore } from "@/stores/auth-store"

/**
 * Desktop login page shows this: creates a pairing session, displays QR code.
 * Phone scans QR → opens /app/pair-approve?id=xxx → phone approves → desktop gets token.
 */
export function PairDeviceQR({ onClose }: { onClose: () => void }) {
  const login = useAuthStore((s) => s.login)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState("")
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const appBase = window.location.pathname.startsWith("/app") ? "/app" : ""

  // Create pairing session on mount
  useEffect(() => {
    const base = `${window.location.protocol}//${window.location.host}`
    fetch(`${base}/auth/pair`, { method: "POST" })
      .then((r) => r.json())
      .then((data) => setSessionId(data.id))
      .catch(() => setError("Failed to create pairing session"))
  }, [])

  // Poll for completion
  useEffect(() => {
    if (!sessionId) return
    const base = `${window.location.protocol}//${window.location.host}`

    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${base}/auth/pair/${sessionId}`)
        const data = await res.json()
        if (data.status === "complete" && data.token) {
          if (pollRef.current) clearInterval(pollRef.current)
          login(data.token)
        }
      } catch {
        // ignore poll errors
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [sessionId, login])

  const pairUrl = sessionId
    ? `${window.location.protocol}//${window.location.host}${appBase}/pair-approve?id=${sessionId}`
    : ""

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-gray-800 rounded-xl w-[340px] p-6 shadow-2xl">
        <h2 className="text-lg font-bold text-gray-100 text-center mb-1">
          Pair Device
        </h2>
        <p className="text-xs text-gray-400 text-center mb-4">
          Scan this QR code from a device that is already logged in
        </p>

        {error ? (
          <p className="text-xs text-red-400 text-center py-8">{error}</p>
        ) : !sessionId ? (
          <p className="text-xs text-gray-500 text-center py-8 animate-pulse">
            Creating pairing session...
          </p>
        ) : (
          <>
            {/* QR Code */}
            <div className="flex justify-center mb-4">
              <div className="bg-white p-3 rounded-lg">
                <QRCodeSVG
                  value={pairUrl}
                  size={200}
                  level="M"
                  bgColor="#ffffff"
                  fgColor="#000000"
                />
              </div>
            </div>

            {/* Waiting indicator */}
            <div className="flex items-center justify-center gap-2 mb-4">
              <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
              <span className="text-xs text-gray-400">
                Waiting for approval...
              </span>
            </div>
          </>
        )}

        <button
          className="w-full py-2 text-sm rounded-lg bg-gray-700 text-gray-300 hover:bg-gray-600"
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

