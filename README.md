# RDC - Remote Dev Ctrl

**Supervised mission control for AI coding agents from anywhere — phone, tablet, browser, or Telegram.**

RDC is an open-source command center for developers orchestrating multiple AI agents (Claude Code, Codex, Cursor, OpenRouter, and custom CLI wrappers) across all your projects.

Your development workstation acts as **HomeBase**; your phones, tablets, and remote browsers connect as lightweight **Nodes**. Brief your agents, supervise goals, review proof artifacts with Git and automated test evidence, and approve critical actions on the go.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/frontend-React%2019-61DAFB.svg)](https://react.dev/)

---

## Why RDC?

- **Goals & Resident Agents**: Two complementary modes in one workspace—**🎯 Goals** for bounded worktree execution with automated verify-retry loops and proof artifacts, and **🤖 Agents** for live, persistent terminal conversations you can promote into bounded Goals in 1 click.
- **Structured Agent Streaming & Semantic Timeline**: Live NDJSON streaming for **Claude Code**, **Codex CLI**, and **Antigravity (`agy`)** renders tool calls, reasoning, file changes, token/cost metrics, and mid-flight operator steers as clean, responsive cards on desktop and mobile—with a live PTY terminal always 1 tap away as an escape hatch.
- **Issue-Type Recipes**: Built-in problem-solving playbooks (`Auto`, `🐞 Bugfix`, `✨ Feature`, `🔧 Refactor`, `🗺 Plan / Spike`, `🔍 Code Review`, `🛡 Code Audit`) that enforce the right approach per task type—plus 1-click **Execute Plan** and **Cross-Model Review** transitions.
- **Multi-Model Cost & Reasoning Routing**: Choose `⚖ Balanced` (reasoning tier plans, reviews, and auto-escalates on loop stall; coding tier implements), `🧠 Max Quality`, or `⚡ Fast` across your installed CLI agents.
- **Zero-Clutter Centralized Config**: Project configs live centrally under `~/.rdc/projects/<name>.yaml` and HTML artifacts under `~/.rdc/sessions/` so RDC never dirties client or open-source repos (while still honoring opt-in repo-local `rdc.yaml` files).
- **Mobile-First & Multi-Channel Control**: Three responsive layouts (Desktop, Mobile, Kiosk), browser Voice input/briefs, Telegram bot with inline approval buttons, and shared CDP browser automation.

---

## Quick Start

### 1. Install

Using **uv** (recommended for isolated, fast installation):

```bash
uv tool install rdc
```

Or with **pip**:

```bash
pip install rdc
```

Or run directly without installing:

```bash
uvx rdc setup
```

### 2. Guided Setup

Run the interactive setup wizard to configure your default project directories, agent providers, API keys, and optional Telegram bot:

```bash
rdc setup
```

### 3. Start Command Center

```bash
# Start server in background (opens on http://localhost:8420)
rdc server start -d

# Check status
rdc server status

# Open web dashboard
open http://localhost:8420
```

---

## Install from Source

If you want to contribute or modify RDC:

```bash
git clone https://github.com/dawalama/remote-dev-control.git
cd remote-dev-control

# Install backend dependencies with uv
uv sync

# Build frontend assets
cd frontend
pnpm install
pnpm run build
cd ..

# Run guided setup and start
.venv/bin/rdc setup
.venv/bin/rdc server start
```

---

## Core Concepts & Workflow

```
                                 ┌──────────────────────────────────────────────┐
                                 │              RDC COMMAND CENTER              │
                                 │  ~/.rdc/data/{rdc.db, tasks.db, logs.db}     │
                                 └──────────────────────┬───────────────────────┘
                                                        │
                      ┌─────────────────────────────────┼─────────────────────────────────┐
                      ▼                                 ▼                                 ▼
         ┌─────────────────────────┐       ┌─────────────────────────┐       ┌─────────────────────────┐
         │     Web / Mobile UI     │       │      Telegram Bot       │       │    MCP / CLI Agents     │
         │ Desktop · Mobile · Kiosk│       │ Inline Approvals & Log  │       │ claude · codex · cursor │
         └─────────────────────────┘       └─────────────────────────┘       └─────────────────────────┘
```

### 1. Workstreams & Channels
Projects are organized into workstream channels (e.g. `#web-app`, `#api-service`, `#system`). Each workstream has a unified bottom scope composer with an expandable history drawer (`⌘/`), active services, Goals, and Agents.

### 2. Goals, Recipes & Multi-Model Supervision Lifecycle
Instead of babysitting raw terminal prompts, create a **Goal** and pick a **Recipe** (`Auto`, `Bugfix`, `Feature`, `Refactor`, `Plan`, `Review`, `Audit`) and **Routing Profile** (`Balanced`, `Max Quality`, `Fast`):
1. **Working (`Timeline`)**: Live structured tool calls, reasoning, and responses stream into a responsive Semantic Timeline. You can **Queue Steer** mid-flight to inject guidance into the next verify round without interrupting the agent.
2. **Verify-and-Retry Loop**: After each turn, RDC runs the project’s verification command (`pytest`, `npm run build`, etc.). In `Balanced` mode, if a coding model stalls on the same failure twice, RDC automatically escalates to a reasoning-tier provider to break the stall.
3. **Needs You (`waiting`)**: When an agent hits a permission or decision gate, approve or steer from Desktop, Mobile, or Telegram.
4. **Ready for Review (`verifying`)**: Inspect the clean markdown outcome, Git diff, and test evidence, then **Accept & Commit** in 1 click, run a **🔍 Cross-Model Review** (using a different provider family than the author), or click **⚡ Execute Plan** on advisory spikes.

### 3. Centralized Configuration (`~/.rdc/projects/`)
RDC keeps all per-project YAML configs in `~/.rdc/projects/<name>.yaml` and session artifacts in `~/.rdc/sessions/` so connecting existing repositories never creates untracked files or pollutes `git status`. Repo-local `rdc.yaml` files are still honored as an opt-in fallback when present.

---

## CLI Reference

```bash
# Server Management
rdc server start [-d] [--port 8420]   # Start server (foreground or daemon)
rdc server status                     # Check server PID, memory, active sessions
rdc server restart                    # Clean restart
rdc server stop                       # Gracefully terminate running server

# Projects & Workstreams
rdc add <path> [-n name]              # Register a project directory
rdc list                              # List registered projects and active services
rdc remove <name>                     # Unregister a project

# Agent Wrappers & Model Providers
rdc wrapper list                      # List available wrappers (claude, codex, cursor, etc.)
rdc wrapper register <spec.json>      # Register custom AI agent wrapper
rdc wrapper instructions              # Print self-registration instructions for agents

# API Tokens & Security
rdc token create <name> [--role admin]# Generate API token for dashboard or remote nodes
rdc token list                        # List active API tokens
rdc token revoke <id>                 # Revoke token

# Configuration & Secrets
rdc config show                       # Display current ~/.rdc/config.yml
rdc config set-secret KEY VALUE       # Store credentials in encrypted/scrubbed vault
```

---

## Remote Access & Multi-Device Control

Control your workstation securely from your smartphone or laptop while away from your desk.

### Option A: Cloudflare Tunnel + Built-in Caddy Proxy

```
Phone / Browser ──HTTPS──> Cloudflare Tunnel ──> Caddy (:8888) ──> RDC (:8420)
                                                               └──> Dev Servers (:3000, :5173)
```

1. Install and authenticate `cloudflared`:
   ```bash
   brew install cloudflared
   cloudflared tunnel login
   cloudflared tunnel create rdc-workstation
   ```
2. Enable Caddy in `~/.rdc/config.yml`:
   ```yaml
   caddy:
     enabled: true
     base_domain: yourdomain.com
     rdc_domain: rdc.yourdomain.com
     listen_port: 8888
   ```
3. Run tunnel:
   ```bash
   cloudflared tunnel run rdc-workstation
   ```

### Option B: Telegram Bot Integration

Set up an interactive bot via `@BotFather` to receive instant push alerts and control missions directly from your phone chat:

```bash
rdc config set-secret TELEGRAM_BOT_TOKEN "123456789:ABCdef..."
```

Enable Telegram in `~/.rdc/config.yml`:
```yaml
channels:
  telegram:
    enabled: true
    allowed_users: [123456789] # Your numeric Telegram User ID
```

**Commands available in Telegram:**
- `/brief` — Generate daily HomeBase overview with voice/text summary
- `/status` — View active sessions, pending approvals, and dev servers
- `/sessions` — List recent goal sessions and their outcome status
- `/actions` — Start, stop, or view logs for project dev servers and commands
- Inline interactive buttons for one-tap **Approve**, **Accept Review**, or **Request Changes**.

---

## MCP (Model Context Protocol) Integration

RDC includes a native Model Context Protocol (MCP) server so external IDE assistants (Claude Desktop, Cursor, Antigravity/Gemini) can inspect HomeBase state, create sessions, and query tasks.

Add to your MCP configuration (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "remote-dev-ctrl": {
      "command": "rdc-mcp",
      "env": {
        "RDC_URL": "http://127.0.0.1:8420"
      }
    }
  }
}
```

---

## REST & WebSocket APIs

Full interactive Swagger documentation is available at `http://localhost:8420/docs`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ws/state` | `WS` | Real-time state synchronization (sessions, actions, agents, terminals) |
| `/terminals/{id}/ws` | `WS` | Full duplex PTY binary and ANSI terminal stream |
| `/homebase/brief` | `GET` | HomeBase overview, active sessions, spoken brief audio URL |
| `/sessions` | `GET`, `POST` | List or create goal-first supervised sessions |
| `/sessions/{id}` | `GET`, `PATCH` | Inspect session details, criteria, and outcomes |
| `/sessions/{id}/evidence` | `GET`, `POST` | Query or attach Git diff, verification logs, and media |
| `/sessions/{id}/criteria` | `GET`, `POST` | Manage acceptance criteria status (`passed`, `failed`, `waived`) |
| `/sessions/{id}/approvals` | `GET`, `POST` | List and approve pending agent command execution requests |
| `/actions` | `GET` | Discover and monitor dev servers and one-shot commands |
| `/actions/{id}/start` | `POST` | Start a dev server or execute a verification command |
| `/settings/agent-providers` | `GET`, `POST` | Query readiness and self-register AI agent wrappers |

---

## Architecture & Codebase Layout

```
src/remote_dev_ctrl/
├── cli.py                  # Typer CLI application (rdc)
├── server/
│   ├── app.py              # FastAPI application & route declarations
│   ├── session_manager.py  # Supervised session lifecycle & terminal monitors
│   ├── channel_manager.py  # Workstream channels, messaging, and events
│   ├── channels/           # Telegram bot, voice runtime (Deepgram Aura), phone
│   ├── agent_registry.py   # Wrapper specifications, provider policies, readiness
│   ├── orchestrator.py     # Task queue and background agent process lifecycle
│   ├── terminal.py         # PTY terminal manager, sessions, and ANSI buffer relay
│   ├── processes.py        # Managed actions (dev servers & one-shot commands)
│   ├── browser.py          # Chrome CDP integration & screencast viewer
│   └── db/                 # SQLite repositories (rdc.db, tasks.db, logs.db, auth.db)
frontend/src/
├── layouts/                # Desktop, Mobile, and Kiosk responsive viewports
├── features/
│   ├── sessions/           # Goal-first sessions hub, session viewer, proof artifact
│   ├── channels/           # Workstream sidebar, chat, and live action panels
│   ├── terminal/           # xterm.js terminal view, virtual keyboard overlay
│   ├── right-tabs/         # Services, commands, ports, and action logs
│   ├── browser/            # CDP screencast browser viewer & PinchTab preview
│   └── voice/              # HomeBase voice brief panel & speech playback
```

---

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests on [GitHub](https://github.com/dawalama/remote-dev-control).

1. Fork the repository
2. Create your feature branch (`git checkout -b feat/my-new-feature`)
3. Run tests (`uv run pytest tests/`)
4. Commit your changes
5. Push to the branch (`git push origin feat/my-new-feature`)
6. Open a Pull Request

---

## License

[MIT](LICENSE) © 2026 Dawa
