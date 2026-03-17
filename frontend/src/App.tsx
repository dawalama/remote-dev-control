import { useState, useEffect, lazy, Suspense } from "react"
import { BrowserRouter, Routes, Route, useLocation } from "react-router"
import { useAuthStore } from "@/stores/auth-store"
import { useUIStore } from "@/stores/ui-store"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ToastContainer } from "@/components/toast"
import { DesktopLayout } from "@/layouts/desktop"
import { PairDeviceQR } from "@/features/modals/pair-device"
import { PairApprovePage } from "@/features/auth/pair-approve"

const MobileLayout = lazy(() =>
  import("@/layouts/mobile").then((m) => ({ default: m.MobileLayout }))
)
const KioskLayout = lazy(() =>
  import("@/layouts/kiosk").then((m) => ({ default: m.KioskLayout }))
)

function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const [showPair, setShowPair] = useState(false)
  const [tokenVisible, setTokenVisible] = useState(false)

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    const token = form.get("token") as string
    if (token.trim()) login(token.trim())
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-900">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-80">
        <h1 className="text-2xl font-bold text-center text-gray-100 tracking-tight">
          REMOTE CTRL
        </h1>
        <div className="relative">
          <input
            name="token"
            type={tokenVisible ? "text" : "password"}
            placeholder="Enter API token"
            autoFocus
            className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500 pr-10"
          />
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs"
            onClick={() => setTokenVisible(!tokenVisible)}
            tabIndex={-1}
          >
            {tokenVisible ? "Hide" : "Show"}
          </button>
        </div>
        <button
          type="submit"
          className="bg-blue-600 text-white rounded px-3 py-2 text-sm font-medium hover:bg-blue-700"
        >
          Connect
        </button>
        <div className="text-center">
          <button
            type="button"
            className="text-xs text-gray-500 hover:text-gray-300"
            onClick={() => setShowPair(true)}
          >
            Pair from another device
          </button>
        </div>
      </form>
      {showPair && <PairDeviceQR onClose={() => setShowPair(false)} />}
    </div>
  )
}

function AuthGuard({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const login = useAuthStore((s) => s.login)

  // Auto-login from ?token= query param (used by direct share links)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const token = params.get("token")
    if (token) {
      login(token)
      // Clean the URL so token isn't visible in browser history
      const url = new URL(window.location.href)
      url.searchParams.delete("token")
      window.history.replaceState({}, "", url.pathname + url.hash)
    }
  }, [login])

  if (!isAuthenticated) return <LoginPage />
  return <>{children}</>
}

function LoadingFallback() {
  return (
    <div className="h-screen flex items-center justify-center bg-gray-900 text-gray-400">
      Loading...
    </div>
  )
}

/** Renders the correct layout based on the ui-store layout setting */
function LayoutSwitch() {
  const layout = useUIStore((s) => s.layout)
  if (layout === "mobile") return <MobileLayout />
  if (layout === "kiosk") return <KioskLayout />
  return <DesktopLayout />
}

/** Router-aware app shell that handles pair-approve outside of AuthGuard */
function AppRoutes() {
  const location = useLocation()
  const normalizedPath = location.pathname.startsWith("/app/")
    ? location.pathname.slice(4)
    : location.pathname

  // /pair-approve is a special route — it handles its own auth state
  if (normalizedPath === "/pair-approve") {
    return <PairApprovePage />
  }

  return (
    <AuthGuard>
      <Suspense fallback={<LoadingFallback />}>
        <Routes>
          <Route path="/*" element={<LayoutSwitch />} />
        </Routes>
      </Suspense>
    </AuthGuard>
  )
}

export default function App() {
  const theme = useUIStore((s) => s.theme)
  const layout = useUIStore((s) => s.layout)

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme)
  }, [theme])

  useEffect(() => {
    document.documentElement.setAttribute("data-layout", layout)
  }, [layout])

  return (
    <TooltipProvider>
      <div className="dark">
        <BrowserRouter basename="/">
          <AppRoutes />
        </BrowserRouter>
        <ToastContainer />
      </div>
    </TooltipProvider>
  )
}
