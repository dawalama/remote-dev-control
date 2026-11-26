import type { ChannelMessage } from "@/stores/channel-store"

/**
 * Identify routine background system/session lifecycle posts (task cards,
 * start/complete notices, loop retries, terminal logs) so the Chat thread
 * stays focused on actual conversation while activity logs remain 1-click toggleable.
 */
export function isActivityLogMessage(msg: ChannelMessage): boolean {
  if (msg.role !== "system") return false
  const meta = msg.metadata
  if (meta?.activity === true) return true
  const mtype = typeof meta?.type === "string" ? meta.type : ""
  if (mtype === "session_notify" || mtype === "session_activity") return true
  if (mtype === "a2ui") {
    const comps = Array.isArray(meta?.components) ? meta.components : []
    // Always keep interactive confirmation / human-input prompts visible in Chat
    if (comps.some((c) => typeof c === "object" && c !== null && (c as Record<string, unknown>).type === "confirm")) {
      return false
    }
    if (comps.some((c) => typeof c === "object" && c !== null && (c as Record<string, unknown>).type === "task_card")) {
      return true
    }
  }
  const text = (msg.content || "").trim()
  if (
    text.startsWith("Session started:") ||
    text.startsWith("Session completed") ||
    text.startsWith("Auto-verify loop:") ||
    text.startsWith("HomeBase session ") ||
    text.startsWith("Terminal started") ||
    text.startsWith("Terminal killed")
  ) {
    return true
  }
  return false
}
