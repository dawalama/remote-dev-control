# Human Guide

Complete guide for developers using RDC (Remote Dev Ctrl).

## Table of Contents

1. [Installation](#installation)
2. [Starting the Server](#starting-the-server)
3. [Dashboard](#dashboard)
4. [Managing Projects & Workstreams](#managing-projects--workstreams)
5. [Goal Sessions, Supervision & Proof Artifacts](#goal-sessions-supervision--proof-artifacts)
6. [Terminals & Live Fallback](#terminals--live-fallback)
7. [Actions (Services & Commands)](#actions)
8. [Chat, HomeBase Briefs & Voice](#chat--homebase-briefs--voice)
9. [Telegram Bot Integration](#telegram-bot-integration)
10. [AI Agent & Model Wrappers](#ai-agent--model-wrappers)
11. [MCP Integration](#mcp-integration)
12. [Configuration](#configuration)
13. [Troubleshooting](#troubleshooting)

---

## Installation

### Automated

```bash
curl -sSL https://raw.githubusercontent.com/dawalama/remote-dev-control/main/install.sh | bash
```

The script installs all prerequisites (uv, pnpm), clones the repo, builds the frontend, and creates default config.

### Manual

```bash
git clone https://github.com/dawalama/remote-dev-control.git
cd remote-dev-control
uv sync                    # or: pip install -e .
cd frontend && pnpm install && pnpm run build && cd ..
```

### Verify

```bash
rdc --help
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

## Goal Sessions, Supervision & Proof Artifacts

RDC organizes work around **Goal Sessions** within dedicated Workstream channels. Instead of manually watching a terminal, you define what needs to be achieved and let the agent work under supervision.

### Creating a Goal Session

From the dashboard:
1. Open the target workstream channel (e.g. `#tesla-light-show`).
2. Click **"New Session"** in the Sessions Hub or Goals panel.
3. Enter:
   - **Goal** — Clear description of the desired outcome (e.g., "Look for any bugs and performance issues in the codebase, and fix them").
   - **Acceptance Criteria** (optional) — Testable conditions for completion (e.g., "npm run verify passes", "no uncommitted dirty state").
   - **Agent Provider** — Orchestrated (default policy), Codex CLI, Claude Code, Cursor Agent, or custom wrapper.

### Session Supervision Lifecycle

```
[Pending] ──> [Working] ──> [Needs You / Waiting] (if approval needed)
                   │                │
                   ▼                ▼
            [Verifying] ◄───────────┘
                 │
                 ├──> [Accept] ──> [Completed]
                 └──> [Tell Agent Next Step] ──> [Working]
```

1. **Pending** — Queued for execution.
2. **Working** — The AI agent wrapper is executing in an isolated background PTY terminal.
3. **Needs You (`waiting`)** — The agent encountered an interactive question or command execution boundary requiring human approval. Approve via Web UI or Telegram.
4. **Ready for Review (`verifying`)** — Agent execution is finished. RDC automatically runs the project's independent verification command and captures Git diffs.
5. **Accept or Continue** — Review the **Proof Artifact**, accept the outcome, or provide instructions for the next loop.

### Proof Artifact & Evidence Viewer

When a session reaches **Ready for Review**, the Session Viewer displays:
- **Proof Artifact Tab**: Rendered Markdown outcome summary, changed file metrics, and verification status.
- **Details & Evidence Tab**: Interactive Git diff viewer, verification command output (`npm run verify`, `pytest`), and visual media attachments.
- **Timeline Tab**: Chronological audit log of all events and agent state changes.
- **Raw Log Tab**: Complete ANSI terminal output stream for deep debugging.
- **Live Terminal Toggle**: One-click fallback directly into the running PTY terminal whenever you want manual control.

---

## Terminals & Live Fallback

---

## Actions

RDC auto-discovers actions defined in your project (from `package.json` scripts, `Makefile`, `Procfile`, etc.). Actions come in two kinds:

- **Services** — Long-running processes like dev servers, APIs, and workers. They have ports, and support start, stop, restart, and attach.
- **Commands** — One-off operations like builds, tests, lints, and migrations. They have a Run button and show output logs on completion.

You can also add actions manually or use **Ask AI** to suggest the right command for your project.

### Service Controls

- **Start** — Launch the service
- **Stop** — Kill the service
- **Restart** — Stop then start
- **Attach** — Reconnect to an orphaned process (e.g., after server restart when a dev server is still running on its port)
- **View Logs** — See stdout/stderr output

### Command Controls

- **Run** — Execute the command
- **Logs** — View output after completion or failure

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

### HomeBase and Nodes

- **HomeBase** is the machine running the RDC server (repos, agents, terminals).
- **Nodes** are paired phones, tablets, or browsers that supervise HomeBase remotely.
- Say or type **`Brief me`** for a deterministic status summary (running sessions, what needs you, recommended next step) without waiting on an LLM for exact phrases.
- See [Hike Demo](hike-demo.md) for the full away-from-desk loop.

### Voice Control (Kiosk/Mobile)

Tap the microphone button to dictate. Voice input is routed to:
- **Terminal** — If a terminal is focused, voice text is entered into the terminal
- **Orchestrator** — Otherwise, treated as a command

---

## Telegram Bot Integration

RDC includes a native interactive Telegram Bot so you can supervise HomeBase and approve actions directly from your phone chat without opening a browser.

### Setup

1. Create a bot with `@BotFather` on Telegram and copy your bot token.
2. Store the token in the vault:
   ```bash
   rdc config set-secret TELEGRAM_BOT_TOKEN "123456789:ABCdef..."
   ```
3. In `~/.rdc/config.yml`, enable Telegram:
   ```yaml
   channels:
     telegram:
       enabled: true
       allowed_users: [123456789] # Your numeric Telegram User ID
   ```
4. Restart RDC: `rdc server restart`.

### Bot Capabilities & Inline Buttons

- **`/brief`** — Spoken and written briefing of running sessions, what needs your attention, and recommended next steps.
- **`/status`** — Instant health check across agents, queues, and dev servers.
- **`/sessions`** — List active and reviewable goal sessions.
- **`/actions`** — Start or stop dev servers and inspect logs.
- **Interactive Approval Buttons**:
  - `[Approve]` and `[Deny]` for pending agent command execution requests.
  - `[Accept Outcome]` and `[Request Changes]` when a session is in review.

---

## AI Agent & Model Wrappers

RDC supports multiple AI agent CLI tools and model routers:

- **Claude Code** (`claude`)
- **OpenAI Codex CLI** (`codex`)
- **Cursor Agent** (`cursor-agent`)
- **OpenRouter Models** (multi-tier routing: free, cheap, premium)
- **Local Ollama** (e.g., `gemma4`, `llama3`)

### Managing Wrappers

```bash
# List all registered wrappers and their readiness
rdc wrapper list

# View instructions for model wrappers to self-register
rdc wrapper instructions

# Register or update a wrapper specification
rdc wrapper register spec.json
```

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
        "PYTHONPATH": "/path/to/remote-dev-control/src"
      }
    }
  }
}
```

This gives AI assistants access to RDC's browser context, session creation, and state inspection tools.

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
source ~/remote-dev-control/.venv/bin/activate

# Option 2: Add to PATH (in ~/.zshrc or ~/.bashrc)
export PATH="$HOME/remote-dev-control/.venv/bin:$PATH"
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
cd ~/remote-dev-control/frontend && pnpm run build
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
