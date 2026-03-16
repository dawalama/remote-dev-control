import { useState, useEffect } from "react"
import { useNavigate } from "react-router"
import { useAuthStore } from "@/stores/auth-store"
import { POST } from "@/lib/api"

/**
 * Page shown on the authenticated device (phone) after scanning a pairing QR code.
 * URL: /app/pair-approve?id=xxx
 * If authenticated, shows an "Approve" button that sends the token to the pairing session.
 * If not authenticated, shows a message to log in first.
 */
export function PairApprovePage() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const navigate = useNavigate()
  const [status, setStatus] = useState<"idle" | "approving" | "done" | "error">("idle")
  const appRoot = window.location.pathname.startsWith("/app/") ? "/app" : "/"

  // Auto-redirect to app after successful pairing
  useEffect(() => {
    if (status !== "done") return
    const timer = setTimeout(() => navigate(appRoot, { replace: true }), 1500)
    return () => clearTimeout(timer)
  }, [status, navigate, appRoot])

  const params = new URLSearchParams(window.location.search)
  const sessionId = params.get("id")

  if (!sessionId) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900">
        <p className="text-gray-400 text-sm">Invalid pairing link</p>
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900">
        <div className="text-center space-y-3 px-6">
          <h1 className="text-xl font-bold text-gray-100">Pair Device</h1>
          <p className="text-sm text-gray-400">
            You need to be logged in to approve this pairing request.
          </p>
          <a
            href={appRoot}
            className="inline-block px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
          >
            Go to Login
          </a>
        </div>
      </div>
    )
  }

  const handleApprove = async () => {
    setStatus("approving")
    const deviceName = /Mobile|Android|iPhone|iPad/i.test(navigator.userAgent)
      ? "Mobile"
      : "Desktop"
    try {
      await POST(`/auth/pair/${sessionId}/approve`, { device_name: deviceName })
      setStatus("done")
    } catch {
      setStatus("error")
    }
  }

  if (status === "done") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900">
        <div className="text-center space-y-3 px-6">
          <div className="text-4xl">✓</div>
          <h1 className="text-xl font-bold text-green-400">Paired!</h1>
          <p className="text-sm text-gray-400">
            The other device is now logged in. Redirecting...
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-900">
      <div className="text-center space-y-4 px-6">
        <h1 className="text-xl font-bold text-gray-100">Pair Device</h1>
        <p className="text-sm text-gray-400">
          Another device wants to connect to your session.
        </p>
        <button
          className="px-6 py-2.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          onClick={handleApprove}
          disabled={status === "approving"}
        >
          {status === "approving" ? "Approving..." : "Approve"}
        </button>
        {status === "error" && (
          <p className="text-xs text-red-400">Failed to approve. Try again.</p>
        )}
      </div>
    </div>
  )
}
