const STORAGE_KEY = "rdc_client_id"
const NAME_KEY = "rdc_client_name"

function generateId(): string {
  const isMobile = window.innerWidth < 768
  const prefix = isMobile ? "mobile" : "desktop"
  return `${prefix}-${crypto.randomUUID()}`
}

export function getClientId(): string {
  let id = localStorage.getItem(STORAGE_KEY)
  if (!id) {
    id = generateId()
    localStorage.setItem(STORAGE_KEY, id)
  }
  return id
}

export function getClientName(): string {
  return localStorage.getItem(NAME_KEY) || ""
}

export function setClientName(name: string): void {
  localStorage.setItem(NAME_KEY, name.trim())
}
