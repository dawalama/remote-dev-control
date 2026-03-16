import { useState, useRef, useEffect } from "react"
import { useUIStore } from "@/stores/ui-store"

const SELF_ATTR = "data-global-text-input"
const TEXT_TYPES = new Set(["text", "search", "url", "email", "tel", "password", "number", ""])

/**
 * Global floating text input bar — appears at the TOP of the screen.
 *
 * On kiosk/mobile layouts, intercepts taps on ANY <input>/<textarea> in the
 * page so the on-screen keyboard never pushes or hides content.  We use
 * pointerdown + preventDefault to stop focus from ever reaching the native
 * element, then open our own input bar at the top of the screen.
 */
export function GlobalTextInput() {
  const open = useUIStore((s) => s.textInputOpen)
  const callback = useUIStore((s) => s.textInputCallback)
  const openTextInput = useUIStore((s) => s.openTextInput)
  const close = useUIStore((s) => s.closeTextInput)
  const layout = useUIStore((s) => s.layout)
  const initialValue = useUIStore((s) => s.textInputInitialValue)
  const appendSeq = useUIStore((s) => s.textInputAppendSeq)
  const appendText = useUIStore((s) => s.textInputAppendText)
  const [text, setText] = useState("")
  const [fading, setFading] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const barTapRef = useRef(false)

  // ── Auto-intercept taps on inputs for kiosk / mobile ────────────────
  useEffect(() => {
    if (layout !== "kiosk" && layout !== "mobile") return

    const handler = (e: PointerEvent) => {
      // Walk from target up to find an input/textarea
      const el = (e.target as HTMLElement)?.closest?.("input, textarea") as
        | HTMLInputElement
        | HTMLTextAreaElement
        | null
      if (!el) return

      // Don't intercept our own input
      if (el.hasAttribute(SELF_ATTR)) return

      const tag = el.tagName
      if (tag !== "INPUT" && tag !== "TEXTAREA") return

      // Skip non-text inputs (checkboxes, radios, file pickers, etc.)
      if (tag === "INPUT" && !TEXT_TYPES.has((el as HTMLInputElement).type || "")) return

      // Skip if the input is in the top half of the viewport — keyboard won't cover it
      const rect = el.getBoundingClientRect()
      if (rect.top < window.innerHeight * 0.5) return

      // Prevent the native focus + keyboard from appearing
      e.preventDefault()

      // Determine label from placeholder or nearby context
      const hint =
        el.placeholder ||
        el.getAttribute("aria-label") ||
        el.closest("label")?.textContent?.trim() ||
        "Input"

      const currentValue = el.value

      openTextInput((typed) => {
        // Feed value back to the original element using React-compatible setter
        try {
          const proto = tag === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
          const nativeSetter = Object.getOwnPropertyDescriptor(proto, "value")?.set
          if (nativeSetter) {
            nativeSetter.call(el, typed)
            el.dispatchEvent(new Event("input", { bubbles: true }))
          } else {
            el.value = typed
          }
          // Simulate Enter to trigger onKeyDown submit handlers
          el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }))
        } catch {
          // Element may have been unmounted
        }
      }, hint, currentValue, false, el)
    }

    // Capture phase so we intercept before React synthetic events
    document.addEventListener("pointerdown", handler, true)
    return () => document.removeEventListener("pointerdown", handler, true)
  }, [layout, openTextInput])

  // ── Reset text when opened ──────────────────────────────────────────
  useEffect(() => {
    if (open) {
      setText(initialValue || "")
      setTimeout(() => {
        const el = inputRef.current
        if (!el) return
        el.focus()
        const len = el.value.length
        el.setSelectionRange(len, len)
        // Auto-size to content
        el.style.height = "auto"
        el.style.height = el.scrollHeight + "px"
      }, 50)
    }
  }, [open, initialValue])

  // ── Append text from external source (e.g. voice dictation) ─────────
  useEffect(() => {
    if (!open || !appendText || appendSeq === 0) return
    setText((prev) => {
      const sep = prev && !prev.endsWith(" ") && !prev.endsWith("\n") ? " " : ""
      return prev + sep + appendText
    })
    // Auto-resize after append
    setTimeout(() => {
      const el = inputRef.current
      if (el) {
        el.style.height = "auto"
        el.style.height = el.scrollHeight + "px"
        // Move cursor to end
        const len = el.value.length
        el.setSelectionRange(len, len)
      }
    }, 0)
  }, [appendSeq]) // only react to seq changes, not appendText directly

  // ── Fade out then close ──────────────────────────────────────────────
  const fadeClose = () => {
    if (fading) return
    setFading(true)
    setTimeout(() => {
      setFading(false)
      close()
    }, 200)
  }

  // Close when our input loses focus (keyboard dismissed)
  useEffect(() => {
    if (!open) return
    const el = inputRef.current
    if (!el) return
    const onBlur = (e: FocusEvent) => {
      // Don't close if focus moved to Send/Close/Hide buttons within our bar
      const related = e.relatedTarget as HTMLElement | null
      if (related?.closest?.("[data-global-text-input-bar]")) return
      // On mobile, relatedTarget is null for button taps — check our flag
      if (barTapRef.current) {
        barTapRef.current = false
        return
      }
      fadeClose()
    }
    el.addEventListener("blur", onBlur)
    return () => el.removeEventListener("blur", onBlur)
  }, [open, fading])

  if (!open || !callback) return null

  const send = () => {
    if (!text) return
    callback(text)
    fadeClose()
  }

  const isKiosk = layout === "kiosk"

  return (
    <div
      data-global-text-input-bar
      onPointerDown={(e) => {
        // Flag that a tap happened within the bar (for blur handler on mobile)
        if ((e.target as HTMLElement) !== inputRef.current) {
          barTapRef.current = true
        }
      }}
      className={`fixed left-0 right-0 top-0 z-[200] bg-gray-800 border-b border-gray-600 shadow-2xl transition-opacity duration-200 ${fading ? "opacity-0" : "opacity-100"} ${isKiosk ? "px-4 py-3" : "px-3 py-2"}`}
    >
      <div className="flex items-start gap-2 max-w-6xl mx-auto">
        <textarea
          ref={inputRef}
          data-global-text-input
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            // Auto-expand height
            const el = e.target
            el.style.height = "auto"
            el.style.height = el.scrollHeight + "px"
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              fadeClose()
            }
            // On mobile/kiosk: Enter inserts newline (default), send via button only
            // On desktop: Enter sends, Shift+Enter for newline
            if (isKiosk || layout === "mobile") return
            if (e.key === "Enter" && (e.ctrlKey || e.shiftKey)) {
              return // let default insert newline
            }
            if (e.key === "Enter") {
              e.preventDefault()
              send()
            }
          }}
          placeholder={isKiosk || layout === "mobile" ? "Type here, tap Send to submit..." : "Type here, Enter to send, Shift+Enter for newline..."}
          rows={1}
          className={`flex-1 bg-gray-900 border border-gray-600 rounded text-gray-200 outline-none focus:border-blue-500 font-mono resize-none overflow-y-auto ${isKiosk ? "px-4 py-3 text-base" : "px-3 py-2 text-sm"}`}
          style={{ maxHeight: "40vh" }}
          autoFocus
        />
        <button
          className={`rounded bg-blue-600 text-white font-medium ${isKiosk ? "px-5 py-3 text-sm" : "px-3 py-2 text-xs"}`}
          onClick={send}
        >
          Send
        </button>
        <button
          className={`rounded bg-gray-700 text-gray-400 ${isKiosk ? "px-4 py-3 text-sm" : "px-2 py-2 text-xs"}`}
          onClick={fadeClose}
        >
          Close
        </button>
      </div>
    </div>
  )
}
