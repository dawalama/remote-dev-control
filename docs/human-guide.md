# Human Guide

Complete guide for developers using RDC (Remote Dev Ctrl).

## Table of Contents

1. [Installation](#installation)
2. [Starting the Server](#starting-the-server)
3. [Dashboard](#dashboard)
4. [Managing Projects](#managing-projects)
5. [Terminals](#terminals)
6. [Tasks & Recipes](#tasks--recipes)
7. [Processes](#processes)
8. [Chat & Orchestrator](#chat--orchestrator)
9. [Knowledge Management](#knowledge-management)
10. [Skills & Tools](#skills--tools)
11. [MCP Integration](#mcp-integration)
12. [Configuration](#configuration)
13. [Troubleshooting](#troubleshooting)

---

## Installation

### Automated

```bash
curl -sSL https://raw.githubusercontent.com/dawalama/remote-dev-ctrl/main/install.sh | bash
```

The script installs all prerequisites (uv, pnpm), clones the repo, builds the frontend, and creates default config.

### Manual

```bash
git clone https://github.com/dawalama/remote-dev-ctrl.git
cd remote-dev-ctrl
uv sync                    # or: pip install -e .
cd frontend && pnpm install && pnpm run build && cd ..
```

### Verify

```bash
rdc--help
rdc server start
open http://localhost:8420
```

---

## Starting the Server

```bash
# Foreground (see logs in terminal)
rdc server start

# Background daemon
rdc server start -d

# Custom port
rdc server start --port 9000

# Check status
rdc server status

# Stop
rdc server stop

# Restart
rdc server restart
```

The server initializes databases and directory structure on first start. No manual setup needed.

---

## Dashboard

Access at `http://localhost:8420` after starting the server.

### Layouts

Switch between layouts using the layout buttons or `?layout=` URL parameter.

**Desktop** — IDE-like layout with terminal on the left, tabbed sidebar on the right. Best for large screens. Keyboard shortcuts: `Cmd+T` (new terminal), `Cmd+K` (project search), `Cmd+/` (chat).

**Kiosk** — Terminal fills most of the screen with a collapsible side panel. Includes voice control, chat tab, and an action bar at the bottom. Best for tablets or dedicated monitors.

**Mobile** — Card-based layout optimized for phones. Scrollable cards for terminals, processes, tasks, chat, and browser sessions. Bottom command bar with voice input.

### Common Features

All layouts support:

- **Project switching** — Tap the project name to open the picker. Arrow buttons cycle through projects with active terminals/processes.
- **Attention alerts** — Orange banner when a terminal is waiting for your input (e.g., MCP approval, y/n prompt).
- **Task review** — Approve or reject tasks that need human review.
- **Global text input** — Floating input bar for typing into terminals when the on-screen keyboard is active (mobile/kiosk).

---

## Managing Projects

### Register a Project

```bash
# From CLI
rdc add ~/code/my-project --name my-project

# With description
rdc add ~/code/api --name api --desc "Main backend API"
```

Or from the dashboard: Menu > Add Project.

### Project Profiles

RDC auto-detects your project's stack (language, framework, package manager) and stores a profile. View and edit in Project Settings (accessible from the dashboard sidebar or menu).

### Collections

Group related projects into collections for quick filtering:

```bash
# Collections are managed from the dashboard
# Menu > System Settings > Collections
```

---

## Terminals

### Spawning Terminals

From the dashboard, click "+ Terminal" and pick a preset:

| Preset | Description |
|--------|-------------|
| Shell | Default system shell (`$SHELL`) |
| Claude | Claude Code CLI agent |
| Cursor | Cursor agent |
| Custom | Any command you configure |

From CLI: `Cmd+T` (desktop layout) opens the preset picker.

### Terminal Features

- **WebSocket streaming** — Real-time PTY output
- **Auto-reconnect** — Survives page refreshes and network blips
- **Scroll controls** — Tap/hold arrows, double-tap for page scroll
- **Virtual keyboard** — Arrow keys, Ctrl+C, Tab, Esc, y/n buttons
- **PID display** — Terminal tabs show process ID for identification
- **Session persistence** — Terminal metadata survives server restarts

### Attention System

When a terminal is waiting for input (e.g., an MCP approval prompt), an orange "Attention" banner appears across all layouts. Click the terminal name to jump directly to it.

---

## Tasks & Recipes

### Creating Tasks

From the dashboard: click "+ Task" or "New Task".

Fill in:
- **Project** — Which project this task is for
- **Recipe** (optional) — Pre-built template with instructions
- **Description** — What the task should do
- **Model** — Which LLM to use (searchable dropdown with tags)

### Recipes

Recipes are reusable task templates. The built-in "Code Audit" recipe runs a security-focused audit with structured scoring.

Custom recipes can be created via the API or database.

### Task Lifecycle

1. **Pending** — Created, waiting to be run
2. **Running** — Actively executing
3. **Needs Review** — Completed but needs human approval
4. **Completed** — Done successfully
5. **Failed** — Error occurred (can retry or edit & retry)

### Task Actions

| Action | When Available | What It Does |
|--------|---------------|--------------|
| Run | Pending | Start execution |
| Cancel/Stop | Pending, Running | Cancel the task |
| View Output | Running, Completed, Failed | See task output |
| Live Log | Running | Stream output in real-time |
| Approve | Needs Review | Accept the result |
| Reject | Needs Review | Reject with reason |
| Retry | Failed | Re-run with same parameters |
| Edit & Retry | Failed | Modify description then re-run |
| Continue | Completed | Create follow-up task with context |
| Fix with AI | Failed | Auto-generate a fix task |
| Delete | Completed, Failed | Remove from list |

---

## Processes

RDC auto-discovers processes defined in your project (from `package.json` scripts, `Makefile`, `Procfile`, etc.).

### Process Actions

- **Start** — Launch the process
- **Stop** — Kill the process
- **Restart** — Stop then start
- **Attach** — Reconnect to an orphaned process (e.g., after server restart when a dev server is still running on its port)
- **View Logs** — See stdout/stderr output

---

## Chat & Orchestrator

### Natural Language Control

Type commands in the chat panel (desktop/kiosk) or command bar (mobile):

```
open terminal for my-project
show tasks
create task
switch to kiosk mode
show activity
project settings
```

### How It Works

1. **Local matching first** — Common commands are matched instantly without an API call
2. **Server fallback** — Complex requests go to the AI orchestrator which can execute actions server-side
3. **Action callbacks** — The orchestrator can trigger UI actions (open terminal, switch tabs, etc.)

### Voice Control (Kiosk/Mobile)

Tap the microphone button to dictate. Voice input is routed to:
- **Terminal** — If a terminal is focused, voice text is entered into the terminal
- **Orchestrator** — Otherwise, treated as a command

---

## Knowledge Management

### Two-Tier System

```
~/.ai/               # Global — applies to all projects
  rules.md           # AI behavior rules
  learnings.md       # Cross-project corrections
  skills/            # Reusable workflows
  tools/             # Python utility functions

project/.ai/         # Per-project
  rules.md           # Project-specific conventions
  learnings.md       # Project-specific lessons
  context.md         # Quick reference
```

### Commands

```bash
rdc init                              # Create ~/.ai/ with defaults
rdc tree                              # View knowledge hierarchy
rdc context --project myproject       # Get AI context
rdc learn "Title" -i "issue" -c "fix" # Record a learning
rdc index --refresh                   # Rebuild knowledge index
```

---

## Skills & Tools

### Skills (High-Level Workflows)

```bash
rdc skill list                   # List available
rdc run skill techdebt           # Find code issues
rdc run skill review             # Review staged changes
rdc run skill commit             # Generate commit message
rdc run skill context            # Dump project context
```

### Tools (Code Functions)

```bash
rdc tool list                    # List available
rdc run tool find_todos path=src
rdc run tool git_status_summary --json
rdc run tool git_log_summary count=10
```

### Custom Skills & Tools

Create custom skills in `~/.ai/skills/` (markdown) and tools in `~/.ai/tools/` (Python). See the main README for examples.

---

## MCP Integration

Add to your AI assistant's MCP config:

```json
{
  "mcpServers": {
    "remote-dev-ctrl": {
      "command": "python",
      "args": ["-m", "remote_dev_ctrl.mcp.server"],
      "env": {
        "PYTHONPATH": "/path/to/remote-dev-ctrl/src"
      }
    }
  }
}
```

This gives AI assistants access to RDC's tools, knowledge, and project context.

---

## Configuration

See [configuration.md](configuration.md) for full reference.

Key files:
- `~/.rdc/config.yml` — Server, providers, channels
- `~/.rdc/secrets.json` — API keys (managed via `rdc config set-secret`)
- `~/.ai/rules.md` — Global AI rules

---

## Troubleshooting

### `rdc` command not found

If you installed with `uv sync`, the binary is in the virtualenv:

```bash
# Option 1: Activate the venv
source ~/remote-dev-ctrl/.venv/bin/activate

# Option 2: Add to PATH (in ~/.zshrc or ~/.bashrc)
export PATH="$HOME/remote-dev-ctrl/.venv/bin:$PATH"
```

### Server won't start

Check if the port is in use:
```bash
lsof -i :8420
```

### Dashboard shows old version

The server sets `no-cache` headers, but you can force-refresh:
```bash
# Rebuild frontend
cd ~/remote-dev-ctrl/frontend && pnpm run build
```

### Terminals disconnecting

Terminal WebSocket connections auto-reconnect with exponential backoff (up to 10 attempts). If a terminal process survives a server restart, RDC auto-rediscovers relay processes.

### Database issues

Databases auto-migrate on server start. If corruption occurs:
```bash
# Remove and let RDC recreate
rm ~/.rdc/data/rdc.db
rdc server restart
```

### Process won't start (port in use)

Use the "Attach" button on a stopped process to reconnect to an orphaned process still running on that port.
