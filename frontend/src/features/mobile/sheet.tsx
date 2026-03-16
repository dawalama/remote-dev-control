import { useRef, useState, useCallback } from "react"
import { useUIStore } from "@/stores/ui-store"

const DISMISS_THRESHOLD = 80 // px drag distance to trigger close

// Shared bottom sheet wrapper for mobile overlays
export function Sheet({
  children,
  onClose,
  title,
  position = "bottom",
}: {
  children: React.ReactNode
  onClose: () => void
  title: string
  position?: "bottom" | "top"
}) {
  const layout = useUIStore((s) => s.layout)
  const maxW = layout === "kiosk" ? "max-w-6xl" : "max-w-lg"

  const sheetRef = useRef<HTMLDivElement>(null)
  const [dragY, setDragY] = useState(0)
  const [dragging, setDragging] = useState(false)
  const startY = useRef(0)
  const startedOnHandle = useRef(false)

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    // Only start drag from the handle area or sheet header (first 48px)
    const sheetEl = sheetRef.current
    if (!sheetEl) return
    const rect = sheetEl.getBoundingClientRect()
    const touchY = e.touches[0].clientY
    if (touchY - rect.top > 48) return // only drag from top area

    startY.current = touchY
    startedOnHandle.current = true
    setDragging(true)
  }, [])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!startedOnHandle.current) return
    const delta = e.touches[0].clientY - startY.current
    // Only allow dragging downward
    setDragY(Math.max(0, delta))
  }, [])

  const onTouchEnd = useCallback(() => {
    if (!startedOnHandle.current) return
    startedOnHandle.current = false
    setDragging(false)
    if (dragY > DISMISS_THRESHOLD) {
      onClose()
    } else {
      setDragY(0)
    }
  }, [dragY, onClose])

  return (
    <div className="fixed inset-0 z-[150]" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50" />
      {/* Centering wrapper — keeps left-1/2 translateX separate from drag translateY */}
      <div className={`absolute ${position === "top" ? "top-0" : "bottom-0"} left-1/2 -translate-x-1/2 w-full ${maxW} pointer-events-none`}>
        <div
          ref={sheetRef}
          className={`pointer-events-auto bg-gray-800 max-h-[70vh] flex flex-col ${
            position === "top" ? "rounded-b-2xl" : "rounded-t-2xl"
          }`}
          style={{
            transform: `translateY(${dragY}px)`,
            transition: dragging ? "none" : "transform 0.2s ease-out",
          }}
          onClick={(e) => e.stopPropagation()}
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
        >
          <div className="flex items-center justify-between px-4 pt-3 pb-2">
            <h3 className="text-sm font-semibold text-gray-200">{title}</h3>
            <button className="text-gray-400 text-lg" onClick={onClose}>
              ×
            </button>
          </div>
          <div className="flex-1 overflow-auto px-4 pb-4">{children}</div>
          {/* Grab handle — at bottom for top-positioned, at top for bottom-positioned */}
          {position !== "top" && (
            <div className="flex justify-center absolute top-0 left-0 right-0 pt-1.5 pb-1 cursor-grab active:cursor-grabbing">
              <div className="w-10 h-1 rounded-full bg-gray-500" />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
