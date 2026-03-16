import { useEffect, useRef, useState } from "react"
import { useAgentRunStore } from "@/stores/agent-run-store"
import type { AgentStep } from "@/types"

export function AgentRunPanel({ taskId }: { taskId: string }) {
  const { steps, status, startWatching, stopWatching, sendApproval, cancelRun } =
    useAgentRunStore()
  const bottomRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    startWatching(taskId)
    return () => stopWatching()
  }, [taskId])

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    }
  }, [steps.length, autoScroll])

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setAutoScroll(atBottom)
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-primary)" }}>
      {/* Header */}
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 13,
        }}
      >
        <span>
          Agent Run{" "}
          <span style={{ opacity: 0.6 }}>
            ({status} — {steps.length} steps)
          </span>
        </span>
        {status === "running" && (
          <button
            onClick={cancelRun}
            style={{
              fontSize: 12,
              padding: "2px 8px",
              background: "var(--error-color, #e74c3c)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        )}
      </div>

      {/* Steps */}
      <div
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflow: "auto",
          padding: "8px 12px",
          fontSize: 13,
          fontFamily: "var(--font-mono, monospace)",
        }}
      >
        {steps.map((step, i) => (
          <StepCard key={i} step={step} onApproval={sendApproval} />
        ))}
        {status === "running" && steps.length > 0 && (
          <div style={{ opacity: 0.5, padding: "4px 0" }}>Thinking...</div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

function StepCard({
  step,
  onApproval,
}: {
  step: AgentStep
  onApproval: (id: string, decision: "approve" | "reject", feedback?: string) => void
}) {
  const [collapsed, setCollapsed] = useState(step.type === "tool_result")

  switch (step.type) {
    case "thinking":
      return (
        <div style={{ opacity: 0.5, padding: "2px 0", fontStyle: "italic" }}>
          {step.content}
        </div>
      )

    case "text":
      return (
        <div style={{ padding: "4px 0", whiteSpace: "pre-wrap" }}>
          {step.content}
        </div>
      )

    case "tool_call":
      return (
        <div
          style={{
            padding: "4px 0",
            borderLeft: "2px solid var(--accent-color, #3498db)",
            paddingLeft: 8,
            marginTop: 4,
          }}
        >
          <span style={{ fontWeight: 600 }}>{step.tool_name}</span>
          {step.tool_args && Object.keys(step.tool_args).length > 0 && (
            <pre
              style={{
                margin: "2px 0",
                fontSize: 12,
                opacity: 0.8,
                maxHeight: 120,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {JSON.stringify(step.tool_args, null, 2)}
            </pre>
          )}
        </div>
      )

    case "tool_result":
      return (
        <div
          style={{
            padding: "2px 0 4px 10px",
            borderLeft: step.is_error
              ? "2px solid var(--error-color, #e74c3c)"
              : "2px solid var(--success-color, #27ae60)",
          }}
        >
          <span
            onClick={() => setCollapsed(!collapsed)}
            style={{ cursor: "pointer", fontSize: 12, opacity: 0.7 }}
          >
            {collapsed ? "▶" : "▼"} Result {step.is_error ? "(error)" : ""}
          </span>
          {!collapsed && (
            <pre
              style={{
                margin: "2px 0",
                fontSize: 12,
                maxHeight: 200,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                color: step.is_error ? "var(--error-color, #e74c3c)" : "inherit",
              }}
            >
              {step.result || step.content}
            </pre>
          )}
        </div>
      )

    case "error":
      return (
        <div
          style={{
            padding: "4px 8px",
            marginTop: 4,
            background: "rgba(231,76,60,0.1)",
            borderRadius: 4,
            color: "var(--error-color, #e74c3c)",
          }}
        >
          {step.content}
        </div>
      )

    case "status":
      return (
        <div style={{ opacity: 0.6, padding: "2px 0", fontSize: 12 }}>
          — {step.content}
        </div>
      )

    case "approval_request":
      return <ApprovalCard step={step} onApproval={onApproval} />

    default:
      return (
        <div style={{ opacity: 0.5, padding: "2px 0" }}>
          [{step.type}] {step.content}
        </div>
      )
  }
}

function ApprovalCard({
  step,
  onApproval,
}: {
  step: AgentStep
  onApproval: (id: string, decision: "approve" | "reject", feedback?: string) => void
}) {
  const [decided, setDecided] = useState(false)

  const approve = () => {
    if (!step.approval_id) return
    setDecided(true)
    onApproval(step.approval_id, "approve")
  }

  const reject = () => {
    if (!step.approval_id) return
    setDecided(true)
    onApproval(step.approval_id, "reject")
  }

  return (
    <div
      style={{
        margin: "6px 0",
        padding: "8px",
        border: "1px solid var(--warning-color, #f39c12)",
        borderRadius: 6,
        background: "rgba(243,156,18,0.06)",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        Approve: {step.tool_name}
      </div>
      {step.preview && (
        <pre
          style={{
            margin: "4px 0",
            fontSize: 12,
            maxHeight: 200,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            background: "rgba(0,0,0,0.1)",
            padding: 6,
            borderRadius: 4,
          }}
        >
          {step.preview}
        </pre>
      )}
      {!decided ? (
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button
            onClick={approve}
            style={{
              padding: "4px 12px",
              fontSize: 12,
              background: "var(--success-color, #27ae60)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Approve
          </button>
          <button
            onClick={reject}
            style={{
              padding: "4px 12px",
              fontSize: 12,
              background: "var(--error-color, #e74c3c)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Reject
          </button>
        </div>
      ) : (
        <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>
          Decision sent
        </div>
      )}
    </div>
  )
}
