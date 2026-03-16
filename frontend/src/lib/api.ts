const API_BASE = ""

// Called on 401 to force re-login
let onUnauthorized: (() => void) | null = null
export function setOnUnauthorized(fn: () => void) {
  onUnauthorized = fn
}

function getToken(): string | null {
  return localStorage.getItem("rdc_token")
}

export function setToken(token: string) {
  localStorage.setItem("rdc_token", token)
}

export function clearToken() {
  localStorage.removeItem("rdc_token")
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json"
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  if (!res.ok) {
    if (res.status === 401 && onUnauthorized) {
      onUnauthorized()
    }
    const text = await res.text().catch(() => "")
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }

  const contentType = res.headers.get("content-type")
  if (contentType?.includes("application/json")) {
    return res.json() as Promise<T>
  }
  return res.text() as unknown as T
}

// Convenience methods
export const GET = <T = unknown>(path: string) => api<T>(path)
export const POST = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  })
export const PATCH = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "PATCH",
    body: body ? JSON.stringify(body) : undefined,
  })
export const PUT = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "PUT",
    body: body ? JSON.stringify(body) : undefined,
  })
export const DELETE = <T = unknown>(path: string) =>
  api<T>(path, { method: "DELETE" })
