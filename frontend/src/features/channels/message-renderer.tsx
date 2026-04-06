/**
 * A2UI-inspired message renderer — renders structured agent UI components.
 *
 * The orchestrator returns messages with metadata containing a `components` array.
 * Each component has a `type` and type-specific props. The renderer maps these
 * to React components.
 *
 * Feedback loop: interactive components (actions, confirm, input) call `onRespond`
 * which sends the user's choice back to the orchestrator as a new message.
 *
 * Falls back to plain text with markdown-like formatting when no components present.
 */

import { useState } from "react"
import type { ChannelMessage } from "@/stores/channel-store"

type MessageMetadata = Record<string, unknown>

/**
 * variant controls color adaptation:
 *   "default" — dark background (orchestrator/system bubbles, desktop panel)
 *   "user"    — blue background (user message bubbles)
 */
type Variant = "default" | "user"

// ── A2UI Component Types ──

interface A2UIText { type: "text"; content: string }
interface A2UICode { type: "code"; content: string; language?: string; label?: string }
interface A2UIActions {
  type: "actions"
  items: Array<{ id: string; label: string; style?: "primary" | "danger" | "default"; icon?: string }>
}
interface A2UIConfirm {
  type: "confirm"
  title: string
  description?: string
  confirm_label?: string
  cancel_label?: string
}
interface A2UIInput {
  type: "input"
  placeholder?: string
  label?: string
  multiline?: boolean
}
interface A2UIProgress {
  type: "progress"
  steps: Array<{ label: string; status: "pending" | "running" | "done" | "failed" }>
  current?: number
}
interface A2UIDiff {
  type: "diff"
  files: Array<{ path: string; additions: number; deletions: number; status?: string }>
  summary?: string
}
interface A2UIFileList {
  type: "file_list"
  files: Array<{ path: string; status?: string; description?: string }>
  title?: string
}
interface A2UITaskCard {
  type: "task_card"
  title: string
  status: string
  project?: string
  description?: string
}

type A2UIComponent =
  | A2UIText | A2UICode | A2UIActions | A2UIConfirm
  | A2UIInput | A2UIProgress | A2UIDiff | A2UIFileList | A2UITaskCard

// ── Main Renderer ──

interface RendererProps {
  message: ChannelMessage
  compact?: boolean
  variant?: Variant
  onRespond?: (response: string) => void
}

export function MessageRenderer({ message, compact, variant = "default", onRespond }: RendererProps) {
  const meta = message.metadata as MessageMetadata | null

  // A2UI component rendering
  if (meta?.type === "a2ui" && Array.isArray(meta.components)) {
    return (
      <div className="space-y-2">
        {(meta.components as A2UIComponent[]).map((comp, i) => (
          <A2UIComponentRenderer key={i} component={comp} variant={variant} compact={compact} onRespond={onRespond} />
        ))}
      </div>
    )
  }

  // Legacy structured types (backward compatible)
  if (meta?.type) {
    switch (meta.type) {
      case "action_results":
        return <ActionResults meta={meta} compact={compact} variant={variant} />
      case "mission_started":
        return <MissionStarted meta={meta} variant={variant} />
      case "options":
        return <LegacyOptionButtons meta={meta} onRespond={onRespond} />
      case "code":
        return <A2UICodeBlock content={meta.code as string || ""} language={meta.language as string} label={meta.label as string} variant={variant} />
      case "usage":
        return <UsageBadge meta={meta} variant={variant} />
      default:
        break
    }
  }

  // Default: formatted text
  return <FormattedText content={message.content || ""} compact={compact} variant={variant} />
}

// ── A2UI Component Router ──

function A2UIComponentRenderer({
  component: comp,
  variant = "default",
  compact,
  onRespond,
}: {
  component: A2UIComponent
  variant?: Variant
  compact?: boolean
  onRespond?: (response: string) => void
}) {
  switch (comp.type) {
    case "text":
      return <FormattedText content={comp.content} compact={compact} variant={variant} />
    case "code":
      return <A2UICodeBlock content={comp.content} language={comp.language} label={comp.label} variant={variant} />
    case "actions":
      return <A2UIActionsBar items={comp.items} onRespond={onRespond} />
    case "confirm":
      return <A2UIConfirmCard {...comp} onRespond={onRespond} />
    case "input":
      return <A2UIInputField {...comp} onRespond={onRespond} />
    case "progress":
      return <A2UIProgressSteps steps={comp.steps} current={comp.current} />
    case "diff":
      return <A2UIDiffView files={comp.files} summary={comp.summary} variant={variant} />
    case "file_list":
      return <A2UIFileListView files={comp.files} title={comp.title} variant={variant} />
    case "task_card":
      return <A2UITaskCardView {...comp} variant={variant} />
    default:
      return null
  }
}

// ── A2UI Components ──

function A2UIActionsBar({
  items,
  onRespond,
}: {
  items: A2UIActions["items"]
  onRespond?: (response: string) => void
}) {
  const [clicked, setClicked] = useState<string | null>(null)

  const handleClick = (id: string) => {
    setClicked(id)
    onRespond?.(`__action:${id}`)
  }

  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => {
        const isClicked = clicked === item.id
        const style = item.style || "default"
        const base = "px-3 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
        const colors = style === "primary"
          ? "bg-blue-600 hover:bg-blue-500 text-white"
          : style === "danger"
            ? "bg-red-600/20 hover:bg-red-600/40 text-red-400 border border-red-600/30"
            : "bg-gray-700 hover:bg-gray-600 text-gray-200 border border-gray-600"

        return (
          <button
            key={item.id}
            onClick={() => handleClick(item.id)}
            disabled={clicked !== null}
            className={`${base} ${colors}`}
          >
            {item.icon && <span className="mr-1">{item.icon}</span>}
            {isClicked ? "..." : item.label}
          </button>
        )
      })}
    </div>
  )
}

function A2UIConfirmCard({
  title,
  description,
  confirm_label = "Confirm",
  cancel_label = "Cancel",
  onRespond,
}: A2UIConfirm & { onRespond?: (response: string) => void }) {
  const [responded, setResponded] = useState(false)

  const handle = (choice: "confirm" | "cancel") => {
    setResponded(true)
    onRespond?.(`__confirm:${choice}`)
  }

  return (
    <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-3 space-y-2">
      <div className="text-sm font-medium text-gray-200">{title}</div>
      {description && <div className="text-xs text-gray-400">{description}</div>}
      <div className="flex gap-2">
        <button
          onClick={() => handle("confirm")}
          disabled={responded}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
        >
          {confirm_label}
        </button>
        <button
          onClick={() => handle("cancel")}
          disabled={responded}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 disabled:opacity-50"
        >
          {cancel_label}
        </button>
      </div>
    </div>
  )
}

function A2UIInputField({
  placeholder = "Type your response...",
  label,
  multiline,
  onRespond,
}: A2UIInput & { onRespond?: (response: string) => void }) {
  const [value, setValue] = useState("")
  const [submitted, setSubmitted] = useState(false)

  const handleSubmit = () => {
    if (!value.trim()) return
    setSubmitted(true)
    onRespond?.(value.trim())
  }

  if (submitted) {
    return <div className="text-xs text-gray-500 italic">Sent: {value}</div>
  }

  return (
    <div className="space-y-1.5">
      {label && <div className="text-xs text-gray-400">{label}</div>}
      <div className="flex gap-2">
        {multiline ? (
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={placeholder}
            rows={3}
            className="flex-1 px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 outline-none focus:border-blue-500 resize-y"
          />
        ) : (
          <input
            data-no-global-intercept
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleSubmit() } }}
            placeholder={placeholder}
            className="flex-1 px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 outline-none focus:border-blue-500"
          />
        )}
        <button
          onClick={handleSubmit}
          disabled={!value.trim()}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 flex-shrink-0"
        >
          Send
        </button>
      </div>
    </div>
  )
}

function A2UIProgressSteps({ steps, current }: { steps: A2UIProgress["steps"]; current?: number }) {
  return (
    <div className="space-y-1">
      {steps.map((step, i) => {
        const isCurrent = current !== undefined ? i === current : step.status === "running"
        const icon = step.status === "done" ? "✓" : step.status === "failed" ? "✗" : step.status === "running" ? "..." : "○"
        const color = step.status === "done" ? "text-green-400"
          : step.status === "failed" ? "text-red-400"
          : step.status === "running" ? "text-blue-400"
          : "text-gray-600"

        return (
          <div key={i} className={`flex items-center gap-2 text-xs ${isCurrent ? "font-medium" : ""}`}>
            <span className={`w-4 text-center flex-shrink-0 ${color}`}>{icon}</span>
            <span className={step.status === "pending" ? "text-gray-500" : "text-gray-300"}>
              {step.label}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function A2UIDiffView({ files, summary, variant = "default" }: { files: A2UIDiff["files"]; summary?: string; variant?: Variant }) {
  const isUser = variant === "user"
  const totalAdd = files.reduce((s, f) => s + f.additions, 0)
  const totalDel = files.reduce((s, f) => s + f.deletions, 0)

  return (
    <div className={`rounded-lg overflow-hidden ${isUser ? "bg-blue-800/30 border border-blue-500/30" : "bg-gray-800/50 border border-gray-700"}`}>
      {summary && (
        <div className={`px-3 py-1.5 border-b text-xs ${isUser ? "border-blue-500/30 text-blue-200" : "border-gray-700 text-gray-400"}`}>
          {summary}
        </div>
      )}
      <div className="px-3 py-2 space-y-0.5">
        {files.map((f, i) => (
          <div key={i} className="flex items-center gap-2 text-xs font-mono">
            <span className={`flex-shrink-0 ${f.status === "added" ? "text-green-400" : f.status === "deleted" ? "text-red-400" : "text-yellow-400"}`}>
              {f.status === "added" ? "A" : f.status === "deleted" ? "D" : "M"}
            </span>
            <span className={isUser ? "text-blue-100 flex-1 truncate" : "text-gray-300 flex-1 truncate"}>{f.path}</span>
            {f.additions > 0 && <span className="text-green-400 flex-shrink-0">+{f.additions}</span>}
            {f.deletions > 0 && <span className="text-red-400 flex-shrink-0">-{f.deletions}</span>}
          </div>
        ))}
      </div>
      <div className={`px-3 py-1.5 border-t text-[10px] ${isUser ? "border-blue-500/30 text-blue-300" : "border-gray-700 text-gray-500"}`}>
        {files.length} file{files.length !== 1 ? "s" : ""} changed, +{totalAdd} -{totalDel}
      </div>
    </div>
  )
}

function A2UIFileListView({ files, title, variant = "default" }: { files: A2UIFileList["files"]; title?: string; variant?: Variant }) {
  const isUser = variant === "user"

  return (
    <div className={`rounded-lg overflow-hidden ${isUser ? "bg-blue-800/30 border border-blue-500/30" : "bg-gray-800/50 border border-gray-700"}`}>
      {title && (
        <div className={`px-3 py-1.5 border-b text-xs font-medium ${isUser ? "border-blue-500/30 text-blue-200" : "border-gray-700 text-gray-300"}`}>
          {title}
        </div>
      )}
      <div className="px-3 py-2 space-y-1">
        {files.map((f, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="text-gray-500 font-mono flex-shrink-0">{f.status || "~"}</span>
            <span className={`font-mono truncate ${isUser ? "text-blue-100" : "text-gray-300"}`}>{f.path}</span>
            {f.description && <span className={`truncate ${isUser ? "text-blue-300" : "text-gray-500"}`}>— {f.description}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

function A2UITaskCardView({ title, status, project, description, variant = "default" }: A2UITaskCard & { variant?: Variant }) {
  const isUser = variant === "user"
  const statusColor = status === "running" ? "bg-blue-500" : status === "done" ? "bg-green-500" : status === "failed" ? "bg-red-500" : "bg-yellow-500"

  return (
    <div className={`rounded-lg px-3 py-2 ${isUser ? "bg-blue-800/50 border border-blue-500/30" : "bg-gray-800/50 border border-gray-700"}`}>
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
        <span className={`text-sm font-medium ${isUser ? "text-white" : "text-gray-200"}`}>{title}</span>
        <span className="text-[10px] text-gray-500">{status}</span>
      </div>
      {project && <div className="text-[10px] text-gray-500 mt-0.5 ml-4">{project}</div>}
      {description && <div className={`text-xs mt-1 ml-4 ${isUser ? "text-blue-200" : "text-gray-400"}`}>{description}</div>}
    </div>
  )
}

// ── Legacy Components (backward compat) ──

function ActionResults({ meta, compact, variant = "default" }: { meta: MessageMetadata; compact?: boolean; variant?: Variant }) {
  const actions = (meta.actions || []) as Array<{ action: string; success?: boolean; error?: string; [key: string]: unknown }>
  const response = meta.response as string | undefined

  return (
    <div className="space-y-1.5">
      {response && <FormattedText content={response} compact={compact} variant={variant} />}
      {actions.length > 0 && (
        <div className={`space-y-1 ${compact ? "mt-1" : "mt-2"}`}>
          {actions.map((a, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[11px]">
              <span className={`flex-shrink-0 mt-0.5 ${a.success === false ? "text-red-400" : "text-green-400"}`}>
                {a.success === false ? "✗" : "✓"}
              </span>
              <span className="text-gray-400">
                <span className="text-gray-300 font-medium">{formatActionDetail(a)}</span>
                {a.error ? <span className="text-red-400 ml-1">— {String(a.error)}</span> : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function MissionStarted({ meta, variant = "default" }: { meta: MessageMetadata; variant?: Variant }) {
  const isUser = variant === "user"
  return (
    <div className={`rounded-lg px-3 py-2 ${isUser ? "bg-blue-800/50 border border-blue-500/30" : "bg-gray-800/50 border border-gray-700"}`}>
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse flex-shrink-0" />
        <span className={`text-sm font-medium ${isUser ? "text-white" : "text-gray-200"}`}>Mission started</span>
      </div>
      {meta.project ? <div className="text-[10px] text-gray-500 mt-0.5 ml-4">{String(meta.project)}</div> : null}
      {meta.task_id ? <div className="text-[10px] text-gray-600 mt-0.5 ml-4 font-mono">{String(meta.task_id)}</div> : null}
    </div>
  )
}

function LegacyOptionButtons({ meta, onRespond }: { meta: MessageMetadata; onRespond?: (response: string) => void }) {
  const options = (meta.options || []) as string[]
  const prompt = meta.prompt as string | undefined
  const items = options.map((opt) => ({ id: opt, label: opt, style: "default" as const }))

  return (
    <div className="space-y-2">
      {prompt && <span className="text-sm text-gray-300">{prompt}</span>}
      <A2UIActionsBar items={items} onRespond={onRespond} />
    </div>
  )
}

function A2UICodeBlock({ content, language, label, variant = "default" }: { content: string; language?: string; label?: string; variant?: Variant }) {
  const isUser = variant === "user"

  return (
    <div className={`rounded-lg overflow-hidden ${isUser ? "bg-blue-800/50 border border-blue-500/30" : "bg-gray-950 border border-gray-700"}`}>
      {label && (
        <div className={`flex items-center justify-between px-3 py-1 border-b ${isUser ? "border-blue-500/30" : "border-gray-700 bg-gray-800/50"}`}>
          <span className={`text-[10px] ${isUser ? "text-blue-200" : "text-gray-500"}`}>{label}</span>
          {language && <span className={`text-[10px] ${isUser ? "text-blue-300" : "text-gray-600"}`}>{language}</span>}
        </div>
      )}
      <pre className={`px-3 py-2 text-xs overflow-x-auto font-mono whitespace-pre ${isUser ? "text-blue-100" : "text-gray-300"}`}>
        {content}
      </pre>
    </div>
  )
}

function UsageBadge({ meta, variant = "default" }: { meta: MessageMetadata; variant?: Variant }) {
  const model = meta.model as string | undefined
  const promptTokens = meta.prompt_tokens as number | undefined
  const completionTokens = meta.completion_tokens as number | undefined
  const durationMs = meta.duration_ms as number | undefined

  return (
    <div className={`flex items-center gap-2 text-[10px] ${variant === "user" ? "text-blue-200" : "text-gray-600"}`}>
      {model && <span>{model}</span>}
      {promptTokens != null && <span>{promptTokens}+{completionTokens || 0} tok</span>}
      {durationMs != null && <span>{(durationMs / 1000).toFixed(1)}s</span>}
    </div>
  )
}

// ── Formatted Text (default renderer) ──

function FormattedText({ content, compact, variant = "default" }: { content: string; compact?: boolean; variant?: Variant }) {
  const blocks = parseContent(content)
  const isUser = variant === "user"

  return (
    <div className={`space-y-1.5 ${compact ? "text-xs" : "text-sm"}`}>
      {blocks.map((block, i) => {
        switch (block.type) {
          case "code":
            return (
              <pre key={i} className={`rounded px-2.5 py-1.5 text-xs overflow-x-auto font-mono whitespace-pre ${
                isUser
                  ? "bg-blue-800/50 border border-blue-500/30 text-blue-100"
                  : "bg-gray-950 border border-gray-700 text-gray-300"
              }`}>
                {block.content}
              </pre>
            )
          case "text":
            return (
              <p key={i} className={`whitespace-pre-wrap break-words ${isUser ? "text-white" : "text-gray-300"}`}>
                <InlineFormatted text={block.content} variant={variant} />
              </p>
            )
          default:
            return null
        }
      })}
    </div>
  )
}

// ── Inline formatting: **bold**, `code`, [links](url) ──

function InlineFormatted({ text, variant = "default" }: { text: string; variant?: Variant }) {
  const isUser = variant === "user"
  const parts: Array<{ type: "text" | "bold" | "code" | "link"; content: string; href?: string }> = []
  const regex = /(\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: "text", content: text.slice(lastIndex, match.index) })
    }
    if (match[2]) parts.push({ type: "bold", content: match[2] })
    else if (match[3]) parts.push({ type: "code", content: match[3] })
    else if (match[4] && match[5]) parts.push({ type: "link", content: match[4], href: match[5] })
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) parts.push({ type: "text", content: text.slice(lastIndex) })
  if (parts.length === 0) return <>{text}</>

  return (
    <>
      {parts.map((part, i) => {
        switch (part.type) {
          case "bold":
            return <strong key={i} className={`font-semibold ${isUser ? "text-white" : "text-gray-200"}`}>{part.content}</strong>
          case "code":
            return <code key={i} className={`px-1 py-0.5 rounded text-[0.9em] font-mono ${
              isUser ? "bg-blue-800/50 text-blue-100" : "bg-gray-800 text-blue-300"
            }`}>{part.content}</code>
          case "link":
            return <a key={i} href={part.href} target="_blank" rel="noopener noreferrer" className={`hover:underline ${isUser ? "text-blue-200" : "text-blue-400"}`}>{part.content}</a>
          default:
            return <span key={i}>{part.content}</span>
        }
      })}
    </>
  )
}

// ── Content parser ──

interface ContentBlock { type: "code" | "text"; content: string }

function parseContent(text: string): ContentBlock[] {
  const blocks: ContentBlock[] = []
  const codeBlockRegex = /```(?:\w+)?\n?([\s\S]*?)```/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = codeBlockRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index).trim()
      if (before) blocks.push({ type: "text", content: before })
    }
    blocks.push({ type: "code", content: match[1].trim() })
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    const after = text.slice(lastIndex).trim()
    if (after) blocks.push({ type: "text", content: after })
  }
  if (blocks.length === 0 && text.trim()) blocks.push({ type: "text", content: text })
  return blocks
}

// ── Helpers ──

function formatActionName(action: string): string {
  return action.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatActionDetail(a: { action: string; [key: string]: unknown }): string {
  switch (a.action) {
    case "run_command":
      return `$ ${String(a.command || "...").slice(0, 80)}`
    case "read_file":
      return `Read ${String(a.path || "...")}`
    case "write_file":
      return `Wrote ${String(a.path || "...")}${a.bytes ? ` (${a.bytes} bytes)` : ""}`
    case "edit_file":
      return `Edited ${String(a.path || "...")}`
    case "spawn_agent":
      return `Agent started for ${String(a.project || "...")}`
    case "create_task":
      return `Task: ${String(a.title || a.description || "...").slice(0, 60)}`
    case "switch_workstream":
      return `Switched to ${String(a.name || "...")}`
    case "create_workstream":
      return `Created workstream: ${String(a.name || "...")}`
    case "delete_workstream":
    case "archive_workstream":
      return `${a.action === "delete_workstream" ? "Deleted" : "Archived"} workstream`
    default:
      return formatActionName(a.action)
  }
}
