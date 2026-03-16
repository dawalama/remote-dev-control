import { create } from "zustand"
import { setToken, clearToken, setOnUnauthorized } from "@/lib/api"

interface AuthState {
  isAuthenticated: boolean
  login: (token: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => {
  const store = {
    isAuthenticated: !!localStorage.getItem("rdc_token"),

    login: (token: string) => {
      setToken(token)
      set({ isAuthenticated: true })
    },

    logout: () => {
      clearToken()
      set({ isAuthenticated: false })
    },
  }

  // Wire up global 401 handler
  setOnUnauthorized(() => store.logout())

  return store
})
