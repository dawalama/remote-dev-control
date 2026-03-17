# AI Agent Guide

Instructions for AI assistants (Claude, Cursor, GPT, etc.) working with RDC (Remote Dev Ctrl).

This guide covers two contexts:
1. **CLI context** — When you have shell access and can run `rdc` commands
2. **Dashboard context** — When the user is interacting via the web dashboard and you're operating as an orchestrated agent

---

## System Overview

RDC is a command center for AI-assisted development:

- **Server** (`rdc server start`) — FastAPI backend on port 8420
- **Dashboard** — React web UI with desktop, mobile, and kiosk layouts
- **CLI** (`rdc`) — Direct terminal access to all features
- **MCP Server** (`rdc-mcp`) — Model Context Protocol for IDE integration
- **SQLite databases** at `~/.rdc/data/` — projects, tasks, logs, auth

---

## CLI Quick Reference

```bash
# Project management
rdc list                              # List registered projects
rdc add <path> [-n name]              # Register a project
rdc context --project <name>          # Get AI context for a project

# Server
rdc server start                      # Start server (foreground)
rdc server start -d                   # Start as daemon
rdc server status                     # Check if running
rdc server stop                       # Stop

# Skills (high-level workflows)
rdc run skill techdebt                # Find code issues
rdc run skill techdebt --path=src     # Scope to directory
rdc run skill review                  # Review staged changes
rdc run skill commit                  # Generate commit message
rdc run skill context                 # Dump project context

# Tools (specific functions)
rdc run tool git_status_summary       # Git status as JSON
rdc run tool find_todos path=src      # Find TODO/FIXME comments
rdc run tool git_staged_files         # List staged files
rdc run tool git_log_summary count=10 # Recent commits

# Knowledge
rdc tree                              # View knowledge structure
rdc learn "Title" -i "issue" -c "correction"  # Record learning
rdc index --refresh                   # Rebuild knowledge index

# Secrets
rdc config set-secret KEY VALUE       # Store API key
```

---

## REST API Reference

When operating as an agent through the dashboard or programmatically:

### Projects

```
GET  /projects                        → [{name, path, description, ...}]
POST /projects                        → Create project
GET  /projects/{name}/profile         → Stack detection, AI context
```

### Terminals

```
POST /terminals?project=X&command=Y   → {id} — Spawn terminal
WS   /terminals/{id}/ws               → Binary PTY I/O stream
DELETE /terminals/{id}                 → Kill terminal
```

### Tasks

```
GET  /tasks                           → [{id, status, description, ...}]
POST /tasks                           → Create task
     body: {project, description, recipe_id?, model?}
POST /tasks/{id}/run                  → Execute pending task
POST /tasks/{id}/cancel               → Cancel running task
POST /tasks/{id}/retry                → Retry failed task
POST /tasks/{id}/review               → Approve/reject
     body: {action: "approve"|"reject", reason?}
GET  /tasks/{id}/output               → Get task output
```

### Actions (Services & Commands)

Actions have a `kind` field: `"service"` (long-running) or `"command"` (one-off).

```
GET  /processes                       → [{id, name, kind, status, port, ...}]
POST /processes/register              → Register a new action
     body: {project, name, command, cwd, port?, kind?}
POST /processes/suggest               → AI-suggest an action
     body: {project, description}
     → {name, command, kind, port, cwd}
POST /processes/{id}/start            → Start/run action
POST /processes/{id}/stop             → Stop action
POST /processes/{id}/restart          → Restart service
POST /processes/{id}/attach?port=N    → Attach to orphaned process
GET  /processes/{id}/logs             → Get action output logs
POST /processes/{id}/create-fix-task  → Create fix task from error
```

### Orchestrator

```
POST /orchestrator                    → Send natural language command
     body: {message, channel, project?, client_id}
     → {response, actions[], executed[]}
```

### Models & Recipes

```
GET  /models                          → [{id, label, provider, tags, ...}]
GET  /recipes                         → [{id, name, description, model, ...}]
```

### State (WebSocket)

```
WS   /ws/state                        → Real-time state updates
     Receives: {type: "state", data: {terminals, processes, tasks, ...}}
     Send:     {type: "register", client_id, client_name}
```

---

## When to Use What

### Use `rdc run skill` for:
- High-level analysis (code review, tech debt, context generation)
- Multi-step workflows
- When the user says `/techdebt`, `/review`, etc.

### Use `rdc run tool` for:
- Single operations returning structured data
- Git operations, file analysis
- When you need JSON output (`--json` flag)

### Use `rdc learn` for:
- Recording corrections from the user
- **Always ask first**: "Should I add this to learnings?"

### Use the REST API for:
- Creating/managing tasks programmatically
- Spawning terminals for specific projects
- Starting/stopping processes
- When operating within the dashboard context

---

## Task System

### Creating Tasks

```bash
# Via CLI (uses the REST API internally)
curl -X POST http://localhost:8420/tasks \
  -H "Content-Type: application/json" \
  -d '{"project": "my-project", "description": "Audit security of auth module", "model": "opus-4.6"}'
```

### Task Statuses

| Status | Meaning | Available Actions |
|--------|---------|-------------------|
| `pending` | Created, not started | run, cancel |
| `running` / `in_progress` | Executing | cancel, view output |
| `needs_review` / `awaiting_review` | Needs human approval | approve, reject |
| `completed` | Done | view output, continue, delete |
| `failed` | Error | retry, edit & retry, fix with AI, delete |
| `blocked` | Waiting on dependency | cancel |

### Recipes

Recipes are task templates with pre-filled prompts. The `recipe_id` field links a task to a recipe. Placeholders like `{project_name}`, `{stack}`, `{project_path}` are auto-filled.

---

## Project Context

Before working on a project, check its context:

```bash
# Get full AI context
rdc context --project myproject

# Check project rules
cat ~/myproject/.ai/rules.md

# Check learnings (past corrections)
cat ~/myproject/.ai/learnings.md
```

The dashboard's Project Settings page shows auto-detected stack info.

---

## Terminal Management

### Spawning

Terminals can run any command. Common presets:

| Preset | Command | Use Case |
|--------|---------|----------|
| Shell | `$SHELL` | General purpose |
| Claude | `claude` | Claude Code agent |
| Cursor | `cursor-agent` | Cursor AI agent |

### Attention Detection

The server monitors terminal output for patterns indicating the terminal needs user input (e.g., MCP approval screens, y/n prompts). When detected, `waiting_for_input` is set to `true` in the state.

### Session Persistence

Terminal metadata (command, project, PID) is persisted to `~/.rdc/terminal_sessions.json`. Relay processes survive server restarts and are auto-rediscovered.

---

## Orchestrator Actions

When sending messages to `/orchestrator`, the server may return `actions` or `executed` arrays. Each action has an `action` field:

| Action | Parameters | Effect |
|--------|-----------|--------|
| `select_project` | `project`, `collection_id?` | Switch active project |
| `open_terminal` | `project` | Open terminal overlay |
| `show_tab` | `tab` | Switch dashboard tab |
| `create_task` | — | Open task creation form |
| `start_process` | `process_id` | Start a process |
| `stop_process` | `process_id` | Stop a process |
| `open_browser` | — | Open browser session dialog |
| `show_activity` | — | Show activity log |
| `set_layout` | `layout` | Switch dashboard layout |
| `set_theme` | `theme` | Switch UI theme |
| `rename_client` | `name` | Rename the connected device |
| `kill_terminal` | `terminal_id` | Kill a terminal |
| `restart_terminal` | `terminal_id` | Restart a terminal |

---

## File Structure

```
~/.rdc/                    # RDC home
  config.yml               # Server configuration
  data/*.db                # SQLite databases
  logs/                    # Runtime logs
  terminal_sessions.json   # Persisted terminal metadata
  secrets.json             # Encrypted API keys

~/.ai/                     # Knowledge base
  rules.md                 # Global AI rules
  learnings.md             # Cross-project corrections
  skills/                  # Skill definitions (markdown)
  tools/                   # Tool definitions (Python)

project/.ai/               # Per-project knowledge
  rules.md                 # Project conventions
  learnings.md             # Project-specific lessons
  context.md               # Quick reference
```

---

## Best Practices

1. **Check context first** — Run `rdc context` or read `.ai/rules.md` before starting work
2. **Record learnings** — Always offer to record corrections with `rdc learn`
3. **Use appropriate scope** — Pass `--path` to limit analysis scope
4. **Prefer JSON output** — Use `--json` when processing tool output
5. **Respect project rules** — Check `.ai/rules.md` for conventions
6. **Use recipes for common tasks** — Don't reinvent the wheel
7. **Check task status** — Before creating duplicate tasks, check existing ones

---

## Command Cheatsheet

| Task | Command |
|------|---------|
| Find code issues | `rdc run skill techdebt` |
| Code review | `rdc run skill review` |
| Generate commit | `rdc run skill commit` |
| Git status (JSON) | `rdc run tool git_status_summary --json` |
| Find TODOs | `rdc run tool find_todos path=src` |
| List skills | `rdc skill list` |
| List tools | `rdc tool list` |
| View knowledge | `rdc tree` |
| Record learning | `rdc learn "Title" -i "..." -c "..."` |
| Server status | `rdc server status` |
| List projects | `rdc list` |
| Project context | `rdc context --project <name>` |
