import { useCallback } from "react"
import { useChannelStore, type OrchestratorRunStatus } from "@/stores/channel-store"
import { onOrchestratorComplete } from "@/stores/state-store"

const STALL_NO_HEARTBEAT_MS = 35_000
const HARD_TIMEOUT_MS = 90_000

/**
 * Shared send/respond logic for channel panels and the docked CommandBar.
 *
 * Stores live per-channel status in useChannelStore so every surface
 * (CommandBar, FloatingChannelPanel, ChannelPanel, MobileChannelPanel)
 * stays in sync and clearly shows whether the orchestrator is working,
 * stalled, or failed.
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
  const runStatus = useChannelStore((s) =>
    activeChannelId ? s.orchestratorStatus[activeChannelId] : undefined
  )
  const sending = runStatus?.status === "sending" || runStatus?.status === "working"

  /** Post a user-typed message and route through the orchestrator. */
  const handleSend = useCallback(async (userMessage: string) => {
    if (!userMessage.trim() || !activeChannelId) return
    const store = useChannelStore.getState()
    const current = store.orchestratorStatus[activeChannelId]
    if (current?.status === "sending" || current?.status === "working") return

    const text = userMessage.trim()
    const now = Date.now()
    store.setOrchestratorStatus(activeChannelId, {
      status: "sending",
      stage: "Sending…",
      startedAt: now,
      lastHeartbeatAt: now,
      prompt: text,
    })

    try {
      await postMessage(activeChannelId, text, "user")
      const result = await orchestrator.send(text, projectName || undefined)
      await processResult(result, activeChannelId, postMessage, text)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Network error reaching orchestrator"
      useChannelStore.getState().setOrchestratorStatus(activeChannelId, {
        status: "failed",
        stage: "Request failed",
        error: msg,
        prompt: text,
      })
    }
  }, [activeChannelId, projectName, postMessage, orchestrator])

  /** Respond to an A2UI button click (__action: / __confirm: prefixes). */
  const handleRespond = useCallback(async (text: string) => {
    if (!activeChannelId) return
    const store = useChannelStore.getState()
    const current = store.orchestratorStatus[activeChannelId]
    if (current?.status === "sending" || current?.status === "working") return

    const displayText = text.startsWith("__action:")
      ? `Selected: ${text.slice(9).replace(/_/g, " ")}`
      : text.startsWith("__confirm:")
        ? (text.slice(10) === "confirm" ? "Confirmed" : "Cancelled")
        : text

    const now = Date.now()
    store.setOrchestratorStatus(activeChannelId, {
      status: "sending",
      stage: "Sending…",
      startedAt: now,
      lastHeartbeatAt: now,
      prompt: text,
    })

    try {
      await postMessage(activeChannelId, displayText, "user")
      const result = await orchestrator.send(text, projectName || undefined)
      await processResult(result, activeChannelId, postMessage, text)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Network error reaching orchestrator"
      useChannelStore.getState().setOrchestratorStatus(activeChannelId, {
        status: "failed",
        stage: "Request failed",
        error: msg,
        prompt: text,
      })
    }
  }, [activeChannelId, projectName, postMessage, orchestrator])

  const retryLast = useCallback(() => {
    if (!activeChannelId) return
    const st = useChannelStore.getState().orchestratorStatus[activeChannelId]
    const prompt = st?.prompt
    useChannelStore.getState().clearOrchestratorStatus(activeChannelId)
    if (prompt) {
      void handleSend(prompt)
    }
  }, [activeChannelId, handleSend])

  const dismissStatus = useCallback(() => {
    if (!activeChannelId) return
    useChannelStore.getState().clearOrchestratorStatus(activeChannelId)
  }, [activeChannelId])

  return {
    handleSend,
    handleRespond,
    sending,
    runStatus: runStatus as OrchestratorRunStatus | undefined,
    retryLast,
    dismissStatus,
  }
}

// ---------------------------------------------------------------------------
// Shared result handler — async heartbeat-aware polling or sync posting
// ---------------------------------------------------------------------------

async function processResult(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result: any,
  channelId: string,
  postMessage: (channelId: string, content: string, role?: string, metadata?: Record<string, unknown>) => Promise<unknown>,
  promptText: string,
) {
  const isAsync = result?.async === true

  if (isAsync) {
    const store = useChannelStore.getState()
    const existing = store.orchestratorStatus[channelId]
    if (!existing || existing.status === "sending") {
      store.setOrchestratorStatus(channelId, {
        status: "working",
        stage: "Thinking…",
        lastHeartbeatAt: Date.now(),
        prompt: promptText,
      })
    }

    const baselineIds = new Set(useChannelStore.getState().messages.map((m) => m.id))
    let doneEarly = false
    const unsubscribe = onOrchestratorComplete((data) => {
      if (!data.channel_id || data.channel_id === channelId) {
        doneEarly = true
      }
    })

    const startedAt = Date.now()
    let polls = 0
    try {
      while (Date.now() - startedAt < HARD_TIMEOUT_MS) {
        await new Promise((r) => setTimeout(r, 500))
        polls += 1
        const state = useChannelStore.getState()
        const current = state.orchestratorStatus[channelId]
        if (!current) {
          // Cleared by WS orchestrator_complete handler
          return
        }
        if (doneEarly) {
          state.clearOrchestratorStatus(channelId)
          if (state.activeChannelId === channelId) {
            await state.loadMessages(channelId)
          }
          return
        }

        // Periodically refresh messages in case WS push was missed
        if (polls % 8 === 0 && state.activeChannelId === channelId) {
          await state.loadMessages(channelId)
        }

        // Exit cleanly if a new orchestrator reply or orchestrator error landed
        const latestMessages = useChannelStore.getState().messages
        const hasNewReply = latestMessages.some(
          (m) =>
            !baselineIds.has(m.id) &&
            (m.role === "orchestrator" ||
              (m.role === "system" && (m.content || "").startsWith("Orchestrator error"))),
        )
        if (hasNewReply) {
          useChannelStore.getState().clearOrchestratorStatus(channelId)
          return
        }

        // Detect silent stall when no heartbeat arrives for STALL_NO_HEARTBEAT_MS
        const latestStatus = useChannelStore.getState().orchestratorStatus[channelId]
        if (!latestStatus) return
        if (Date.now() - latestStatus.lastHeartbeatAt >= STALL_NO_HEARTBEAT_MS) {
          useChannelStore.getState().setOrchestratorStatus(channelId, {
            status: "stalled",
            stage: "No response from orchestrator",
            error: "No progress update received — the request may have timed out or the server restarted.",
            prompt: promptText,
          })
          return
        }
      }

      // Hard timeout reached
      useChannelStore.getState().setOrchestratorStatus(channelId, {
        status: "stalled",
        stage: "Timed out waiting for reply",
        error: "The request exceeded 90s without finishing. Try again or check server logs.",
        prompt: promptText,
      })
    } finally {
      unsubscribe()
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
  useChannelStore.getState().clearOrchestratorStatus(channelId)
}
