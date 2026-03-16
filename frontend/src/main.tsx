import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Migrate legacy adt_* localStorage keys to rdc_*
const LEGACY_KEY_MAP: Record<string, string> = {
  adt_token: "rdc_token",
  adt_tab: "rdc_tab",
  adt_theme: "rdc_theme",
  adt_layout: "rdc_layout",
  adt_current_project: "rdc_current_project",
  adt_current_collection: "rdc_current_collection",
  adt_active_filter: "rdc_active_filter",
  adt_client_id: "rdc_client_id",
  adt_client_name: "rdc_client_name",
}
for (const [oldKey, newKey] of Object.entries(LEGACY_KEY_MAP)) {
  if (localStorage.getItem(oldKey) !== null && localStorage.getItem(newKey) === null) {
    localStorage.setItem(newKey, localStorage.getItem(oldKey)!)
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
