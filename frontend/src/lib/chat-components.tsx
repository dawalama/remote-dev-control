import { createRenderer } from "@json-render/react"
import { chatCatalog } from "./chat-catalog"

/**
 * json-render renderer for the chat catalog.
 * Maps catalog component types to Tailwind-styled React components.
 *
 * Usage: <ChatSpecRenderer spec={spec} onAction={handleAction} />
 */
export const ChatSpecRenderer = createRenderer(chatCatalog, {
  Message: ({ element }) => {
    const { role, content } = element.props
    return (
      <div className={`flex ${role === "user" ? "justify-end" : "justify-start"}`}>
        <div
          className={`max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${
            role === "user"
              ? "bg-blue-600/80 text-white"
              : "bg-gray-700 text-gray-200"
          }`}
        >
          {content}
        </div>
      </div>
    )
  },

  ActionResult: ({ element }) => {
    const { action, status, detail } = element.props
    const isError = status === "error"
    return (
      <div
        className={`rounded-lg px-3 py-2 text-xs border ${
          isError
            ? "border-red-600/30 bg-red-900/20 text-red-200"
            : "border-green-600/30 bg-green-900/20 text-green-200"
        }`}
      >
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isError ? "bg-red-400" : "bg-green-400"}`} />
          <span className="font-medium">{action}</span>
        </div>
        {detail && <div className="mt-1 text-[10px] opacity-75">{detail}</div>}
      </div>
    )
  },

  QuestionForm: ({ element, children }) => {
    const { question, placeholder: _ } = element.props
    return (
      <div className="rounded-lg border border-purple-600/30 bg-purple-900/20 px-3 py-2">
        <div className="text-sm text-purple-200 mb-2">{question}</div>
        {children}
      </div>
    )
  },

  ChoiceGroup: ({ element, emit }) => {
    const { label, options } = element.props
    return (
      <div className="space-y-1">
        <div className="text-[10px] text-gray-400 uppercase">{label}</div>
        <div className="flex flex-wrap gap-1">
          {options.map((opt) => (
            <button
              key={opt}
              className="px-2.5 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
              onClick={() => emit("press")}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>
    )
  },

  Screenshot: ({ element }) => {
    const { src, alt } = element.props
    return (
      <div className="rounded-lg overflow-hidden border border-gray-700">
        <img src={src} alt={alt || "Screenshot"} className="w-full" />
      </div>
    )
  },

  LinkCard: ({ element }) => {
    const { title, url, description } = element.props
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="block rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 hover:bg-gray-700 transition-colors"
      >
        <div className="text-sm text-blue-400">{title}</div>
        {description && <div className="text-[10px] text-gray-400 mt-0.5">{description}</div>}
        <div className="text-[10px] text-gray-500 mt-1 truncate">{url}</div>
      </a>
    )
  },

  CodeBlock: ({ element }) => {
    const { code, language } = element.props
    return (
      <div className="rounded-lg bg-gray-900 border border-gray-700 overflow-hidden">
        {language && (
          <div className="px-3 py-1 text-[10px] text-gray-500 border-b border-gray-700">{language}</div>
        )}
        <pre className="px-3 py-2 text-xs text-gray-300 overflow-auto">
          <code>{code}</code>
        </pre>
      </div>
    )
  },

  Card: ({ element, children }) => {
    const { title } = element.props
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 overflow-hidden">
        {title && (
          <div className="px-3 py-1.5 text-xs font-medium text-gray-300 border-b border-gray-700">
            {title}
          </div>
        )}
        <div className="px-3 py-2">{children}</div>
      </div>
    )
  },

  Stack: ({ element, children }) => {
    const { direction } = element.props
    const isHorizontal = direction === "horizontal"
    return (
      <div className={`${isHorizontal ? "flex gap-2" : "space-y-2"}`}>
        {children}
      </div>
    )
  },
})
