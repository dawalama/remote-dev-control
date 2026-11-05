import { useState, useCallback } from "react"
import { useBrowserStore } from "@/stores/browser-store"
import { useUIStore } from "@/stores/ui-store"
import { POST } from "@/lib/api"
import type { Spec } from "@json-render/core"

interface AgentAction {
  name?: string
  params?: Record<string, unknown>
}

interface AgentLoopStep {
  step: number
  type: "act" | "done" | "error"
  page?: { url: string; title: string }
  actions?: AgentAction[]
  response?: string
  results?: string[]
  detail?: string
}

interface AgentLoopResponse {
  response?: string
  steps?: AgentLoopStep[]
  actions_taken?: AgentAction[]
  results?: string[]
  done?: boolean
  model?: string
  spec?: Spec
}

/** A single entry in the agent conversation history. */
export interface AgentHistoryEntry {
  role: "user" | "assistant"
  content: string
  spec?: Spec | null
  actions?: { action: string; [key: string]: unknown }[]
}

export function useBrowserAgent(_channel: "desktop" | "mobile" | "kiosk") {
  const [agentInput, setAgentInput] = useState("")
  const [sendingToAgent, setSendingToAgent] = useState(false)
  const [conversationHistory, setConversationHistory] = useState<AgentHistoryEntry[]>([])

  const activeSession = useBrowserStore((s) => s.activeSession)
  const toast = useUIStore((s) => s.toast)

  const addMessage = useCallback((entry: AgentHistoryEntry) => {
    setConversationHistory((prev) => [...prev, entry])
  }, [])

  const clearHistory = useCallback(() => {
    setConversationHistory([])
  }, [])

  const sendToAgent = async (instruction: string) => {
    if (!instruction.trim()) return
    setSendingToAgent(true)

    // Add user message to conversation
    addMessage({ role: "user", content: instruction.trim() })

    try {
      // Prefer CDP path when a browser session is running
      if (activeSession && activeSession.status === "running") {
        // Use the observe->act loop endpoint for multi-step execution
        const result = await POST<AgentLoopResponse>(
          `/browser/sessions/${activeSession.id}/agent/loop`,
          { instruction: instruction.trim(), max_steps: 10 },
        )

        const responseText = result?.response || "Done"
        const steps = result?.steps || []
        const actionsTaken = result?.actions_taken || []
        const allResults = result?.results || []

        // Show intermediate steps if there were multiple
        if (steps.length > 1) {
          for (const step of steps) {
            if (step.type === "act" && step.results && step.results.length > 0) {
              const stepSpec = buildActionResultSpec(
                step.actions || [],
                step.results,
              )
              addMessage({
                role: "assistant",
                content: `Step ${step.step}: ${step.response || step.results.join("; ")}`,
                spec: stepSpec,
              })
            }
          }
        }

        // Final summary message
        const spec = result?.spec ?? buildActionResultSpec(actionsTaken, allResults)

        addMessage({
          role: "assistant",
          content: responseText,
          spec,
          actions: actionsTaken.map((a) => ({ action: a.name || "", ...a.params })),
        })

        const statusType: "success" | "info" = result?.done ? "success" : "info"
        toast(responseText.slice(0, 120) || allResults[0] || "Done", statusType)
        setAgentInput("")
        setSendingToAgent(false)
        return
      }

      addMessage({
        role: "assistant",
        content: "Start a browser session first, then ask again.",
      })
      toast("No browser session running", "error")
      setAgentInput("")
    } catch (err) {
      console.error("[BrowserAgent] error:", err)
      addMessage({ role: "assistant", content: `Error: ${err}` })
      toast("Failed to send", "error")
    }
    setSendingToAgent(false)
  }

  return {
    agentInput,
    setAgentInput,
    sendingToAgent,
    sendToAgent,
    conversationHistory,
    clearHistory,
  }
}

/**
 * Build a json-render Spec from agent action results.
 * Returns null if there are no actions to render.
 */
function buildActionResultSpec(
  actions: AgentAction[],
  results: string[],
): Spec | null {
  if (actions.length === 0 && results.length === 0) return null

  const elements: Record<string, unknown> = {
    stack: { type: "Stack", props: { direction: "vertical" }, children: [] as string[] },
  }
  const children = elements.stack as { children: string[] }

  // Pair actions with results
  const count = Math.max(actions.length, results.length)
  for (let i = 0; i < count; i++) {
    const key = `r${i}`
    const action = actions[i]
    const resultText = results[i] || ""
    const actionName = action?.name || ""
    const isError = resultText.toLowerCase().startsWith("failed") || resultText.toLowerCase().startsWith("error")

    elements[key] = {
      type: "ActionResult",
      props: {
        action: actionName
          ? actionName.replace(/^browser_/, "").replace(/_/g, " ")
          : resultText.split(":")[0] || "action",
        status: isError ? "error" : "success",
        detail: resultText || undefined,
      },
    }
    children.children.push(key)
  }

  if (children.children.length === 0) return null

  return { root: "stack", elements } as Spec
}
