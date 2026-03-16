import { useUIStore } from "@/stores/ui-store"

const typeStyles: Record<string, string> = {
  info: "bg-blue-600",
  success: "bg-green-600",
  error: "bg-red-600",
  warning: "bg-yellow-600",
}

export function ToastContainer() {
  const toasts = useUIStore((s) => s.toasts)
  const dismiss = useUIStore((s) => s.dismissToast)

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`${typeStyles[t.type] || typeStyles.info} text-white px-4 py-2 rounded-lg shadow-lg text-sm cursor-pointer max-w-sm animate-in fade-in slide-in-from-top-2`}
          onClick={() => dismiss(t.id)}
        >
          {t.message}
        </div>
      ))}
    </div>
  )
}
