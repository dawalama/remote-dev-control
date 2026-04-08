import { useState, useCallback } from "react"
import { useChannelStore } from "@/stores/channel-store"

/**
 * Shared send/respond logic for channel panels.
 *
 * Extracts the duplicated handleSend + handleRespond pattern from
 * channel-panel, mobile-channel-panel, and floating-channel-panel.
 */
export function useChannelSend({
  activeChannelId,
  projectName,
  orchestrator,
  postMessage,
}: {
  activeChannelId: string | null
  projectName: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  orchestrator: { send: (message: string, project?: string) => Promise<any> }
  postMessage: (channelId: string, content: string, role?: string, metadata?: Record<string, unknown>) => Promise<unknown>
}) {
  const [sending, setSending] = useState(false)

  /** Post a user-typed message and route through the orchestrator. */
  const handleSend = useCallback(async (userMessage: string) => {
    if (!userMessage.trim() || sending || !activeChannelId) return
    const text = userMessage.trim()
    setSending(true)

    await postMessage(activeChannelId, text, "user")
    const result = await orchestrator.send(text, projectName || undefined)

    await processResult(result, activeChannelId, postMessage)

    setSending(false)
  }, [sending, activeChannelId, projectName, postMessage, orchestrator])

  /** Respond to an A2UI button click (__action: / __confirm: prefixes). */
  const handleRespond = useCallback(async (text: string) => {
    if (!activeChannelId || sending) return
    setSending(true)

    const displayText = text.startsWith("__action:")
      ? `Selected: ${text.slice(9).replace(/_/g, " ")}`
      : text.startsWith("__confirm:")
        ? (text.slice(10) === "confirm" ? "Confirmed" : "Cancelled")
        : text

    await postMessage(activeChannelId, displayText, "user")
    const result = await orchestrator.send(text, projectName || undefined)

    await processResult(result, activeChannelId, postMessage)

    setSending(false)
  }, [sending, activeChannelId, projectName, postMessage, orchestrator])

  return { handleSend, handleRespond, sending }
}

// ---------------------------------------------------------------------------
// Shared result handler — async polling or sync metadata posting
// ---------------------------------------------------------------------------

async function processResult(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result: any,
  channelId: string,
  postMessage: (channelId: string, content: string, role?: string, metadata?: Record<string, unknown>) => Promise<unknown>,
) {
  const isAsync = result?.async === true

  if (isAsync) {
    // Poll for the server-posted response (up to 30s)
    const msgCountBefore = useChannelStore.getState().messages.length
    for (let i = 0; i < 15; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      await useChannelStore.getState().loadMessages(channelId)
      if (useChannelStore.getState().messages.length > msgCountBefore) break
    }
    return
  }

  const executed = result?.executed as Array<unknown> | undefined
  const response = result?.response as string | undefined
  const usage = result?.usage as Record<string, unknown> | undefined

  if (response || executed?.length) {
    const metadata: Record<string, unknown> = {}
    if (executed?.length) {
      metadata.type = "action_results"
      metadata.actions = executed
      metadata.response = response || ""
    }
    if (usage) metadata.usage = usage
    await postMessage(
      channelId,
      response || "Actions executed",
      "orchestrator",
      Object.keys(metadata).length > 0 ? metadata : undefined,
    )
  }
}
