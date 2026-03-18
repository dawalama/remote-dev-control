// Core domain types matching the backend API

export interface ProjectProfile {
  purpose?: string
  stack?: string[]
  conventions?: string
  test_command?: string
  source_dir?: string
  test_dir?: string
}

export interface Project {
  name: string
  path: string
  description?: string
  tags?: string[]
  collection_id?: string
  config?: { profile?: ProjectProfile; [key: string]: unknown }
}

export interface Collection {
  id: string
  name: string
  projects?: string[]
  project_count?: number
}

export type ActionKind = "service" | "command"

export interface Action {
  id: string
  name: string
  project: string
  kind: ActionKind
  status: "running" | "stopped" | "error" | "failed" | "completed" | "idle"
  port?: number
  pid?: number
  command?: string
  completed_at?: string
  preview_url?: string
  error?: string
}

/** @deprecated Use Action instead */
export type Process = Action

export interface Task {
  id: string
  project_id?: string
  project?: string
  title?: string
  description: string
  status: "pending" | "running" | "in_progress" | "completed" | "failed" | "needs_review" | "awaiting_review" | "blocked"
  created_at?: string
  completed_at?: string
  output?: string
  metadata?: Record<string, unknown>
}

export interface Recipe {
  id: string
  name: string
  description: string
  tags: string[]
  prompt_template?: string
  model?: string
  builtin?: boolean
}

export interface Agent {
  project: string
  status: "running" | "working" | "stopped" | "error" | "idle"
  provider: string
  pid?: number
  error?: string
}

export interface BrowserSession {
  id: string
  process_id?: string | null
  project_id?: string | null
  target_url?: string
  container_port?: number
  status: string
  viewer_url?: string
  error?: string | null
}

export interface ActivityEvent {
  id: string
  type: string
  message: string
  project?: string
  timestamp: string
}

export interface Screenshot {
  id: string
  project?: string
  filename: string
  timestamp: string
}

export type AgentStepType =
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "text"
  | "error"
  | "status"
  | "approval_request"

export interface AgentStep {
  type: AgentStepType
  content: string
  step_index: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  tool_call_id?: string
  result?: string
  is_error?: boolean
  approval_id?: string
  preview?: string
}

export type TabId = "activity" | "workers" | "processes" | "actions" | "system" | "browser" | "pinchtab" | "tasks" | "attachments" | "dictation" | "project" | "chat"
