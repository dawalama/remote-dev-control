import { useRef, useCallback } from "react"
import { useVoice } from "./use-voice"
import { useOrchestrator } from "./use-orchestrator"
import { useTerminalStore } from "@/stores/terminal-store"
import { useUIStore } from "@/stores/ui-store"
import { useChannelStore } from "@/stores/channel-store"

/**
 * Workstream-aware voice hook — routes speech based on context:
 *
 * - **Terminal focused** (stream mode): dictated text goes to GlobalTextInput
 *   for review before sending to terminal
 * - **Otherwise** (command mode): text goes through the orchestrator and
 *   response is posted to the active workstream's channel
 *
 * Returns the voice state + indicator text for floating UI.
 */
export function useWorkstreamVoice(opts: {
  channel: "desktop" | "mobile"
  terminalSendRef: React.MutableRefObject<((data: string) => void) | null>
  onOpenTerminal?: (project: string) => void
  onCreateTask?: () => void
  onOpenBrowser?: () => void
  onOpenActivity?: () => void
  onOpenMenu?: () => void
  onEditProject?: () => void
  onSystemSettings?: () => void
  toast: (msg: string, level: "info" | "success" | "warning" | "error") => void
}) {
  const { toast, terminalSendRef } = opts
  const openTextInput = useUIStore((s) => s.openTextInput)

  // Voice indicator state managed via refs + callback to avoid re-renders on every interim
  const indicatorRef = useRef("")
  const indicatorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const submitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const orchestrator = useOrchestrator({
    channel: opts.channel,
    onOpenTerminal: opts.onOpenTerminal,
    onCreateTask: opts.onCreateTask,
    onOpenBrowser: opts.onOpenBrowser,
    onOpenActivity: opts.onOpenActivity,
    onOpenMenu: opts.onOpenMenu,
    onEditProject: opts.onEditProject,
    onSystemSettings: opts.onSystemSettings,
  })

  const handleOrchestratorSend = useCallback(async (text: string) => {
    const result = await orchestrator.send(text)

    // Post to active channel
    const { activeChannelId, postMessage } = useChannelStore.getState()
    if (activeChannelId) {
      await postMessage(activeChannelId, text, "user")
      if (result?.response || result?.executed?.length) {
        const metadata: Record<string, unknown> = {}
        if (result?.executed?.length) {
          metadata.type = "action_results"
          metadata.actions = result.executed
          metadata.response = result.response || ""
        }
        if (result?.usage) metadata.usage = result.usage
        await postMessage(
          activeChannelId,
          result?.response || "Actions executed",
          "orchestrator",
          Object.keys(metadata).length > 0 ? metadata : undefined,
        )
      }
    } else if (result?.response) {
      toast(result.response, "info")
    }
  }, [orchestrator, toast])

  const voice = useVoice({
    onFinal: (text) => {
      const isTerminalMode = useTerminalStore.getState().terminalFocused

      if (isTerminalMode) {
        // Stream mode: route to GlobalTextInput for review
        indicatorRef.current = ""
        const uiState = useUIStore.getState()
        if (uiState.textInputOpen) {
          uiState.appendTextInput(text)
        } else {
          openTextInput(
            (confirmed) => terminalSendRef.current?.(confirmed.replace(/\n/g, "\r") + "\r"),
            "Voice \u2192 Terminal",
            text,
            true,
          )
        }
      } else {
        // Command mode: send through orchestrator with debounce
        indicatorRef.current = text
        if (submitTimerRef.current) clearTimeout(submitTimerRef.current)
        submitTimerRef.current = setTimeout(() => {
          handleOrchestratorSend(text)
          if (indicatorTimerRef.current) clearTimeout(indicatorTimerRef.current)
          indicatorTimerRef.current = setTimeout(() => {
            indicatorRef.current = ""
          }, 2000)
        }, 600)
      }
    },
    onInterim: (text) => {
      indicatorRef.current = text
    },
  })

  const cleanup = useCallback(() => {
    if (submitTimerRef.current) clearTimeout(submitTimerRef.current)
    if (indicatorTimerRef.current) clearTimeout(indicatorTimerRef.current)
  }, [])

  return {
    voice,
    indicatorRef,
    cleanup,
  }
}
