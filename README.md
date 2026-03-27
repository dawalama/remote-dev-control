# RDC - Remote Dev Ctrl

**Monitor and control your AI coding agents from anywhere - phone, tablet, or browser.**

RDC is an open-source command center for developers running multiple AI agents (Claude Code, Cursor, Gemini CLI) across multiple projects. One dashboard, all your terminals, accessible from any device.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

<!-- TODO: Add demo GIF here before launch -->
<!-- ![RDC Demo](docs/assets/demo.gif) -->

## Why RDC?

- **See all your agents in one place** - 5 projects with Claude Code running? See them all, switch instantly
- **Check on agents from your phone** - Mobile-first UI with terminal access, virtual keyboard, and touch controls
- **Shared browser preview** - You and the AI agent see the same page. Click, type, navigate together
- **Terminals that survive everything** - PTY sessions persist across server restarts and network drops

## Quick Start

```bash
# Install from PyPI
pip install rdc
rdc setup          # Guided config: API keys, presets, remote access
rdc server start   # Open http://localhost:8420
```

Or install from source:

```bash
# One-line install (clones, installs deps, builds, runs guided setup)
curl -sSL https://raw.githubusercontent.com/dawalama/remote-dev-ctrl/main/install.sh | bash

# Or manual:
git clone https://github.com/dawalama/remote-dev-ctrl.git
cd remote-dev-ctrl
uv sync && cd frontend && pnpm install && pnpm run build && cd ..
rdc setup
rdc server start
```

### Prerequisites

Python 3.11+, Node.js 18+, and Chrome (for browser preview).

```bash
# If you don't have uv/pnpm:
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g pnpm
```

## Features

### Three Layouts, All First-Class

| Desktop | Mobile | Kiosk |
|---------|--------|-------|
| IDE-like split view | Card-based, touch-optimized | Tablet sidebar + terminal |
| Terminal + sidebar tabs | Fullscreen terminal overlay | Collapsible side panels |

### Terminal Management

- Spawn Claude Code, Cursor, Gemini CLI, or plain shell per project
- WebSocket streaming with auto-reconnect and snapshot-based session restore
- Terminal switcher: tap title or long-press Back to switch between terminals
- Virtual keyboard with arrow keys, Ctrl-C, Tab, Enter for mobile
- Waiting-for-input detection with visual alerts

### Browser Automation

- Local Chrome (no Docker required) with CDP screencast viewer
- 5 agent tools: `observe`, `click`, `type`, `navigate`, `screenshot`
- Observe-act loop: agent sees the page, decides actions, executes, verifies
- Same page shared between you and the agent - no separate sessions

### Actions (Services & Commands)

- Auto-discover project scripts from package.json, Makefile, etc.
- Services (dev servers) with port detection and browser preview
- Commands (builds, tests, lints) with output capture
- Start, stop, restart, attach to orphaned processes

### AI Orchestrator

Natural language control via chat:
- "Start a terminal for my-project"
- "Show running tasks"
- "Switch to kiosk mode"

### Remote Access

Access from anywhere via Cloudflare Tunnel + Caddy reverse proxy:

```
Internet -> Cloudflare Tunnel -> Caddy (:8888) -> RDC (:8420)
                                               -> Dev servers (:3000, :5173, etc.)
```

Each dev server gets its own preview subdomain automatically.

## Architecture

```
src/remote_dev_ctrl/server/
  app.py            # FastAPI app, routes, lifespan
  chrome.py         # Local Chrome process lifecycle
  browser.py        # Browser sessions, CDP connections
  browser_use.py    # Agent browser control (observe/act/screenshot)
  terminal.py       # PTY management, WebSocket relay, snapshots
  worker.py         # Task execution engine
  config.py         # Config loading
  agents/tools.py   # Agent tool definitions (file, git, browser)

frontend/src/
  layouts/          # desktop.tsx, mobile.tsx, kiosk.tsx
  features/         # terminal, browser, chat, tasks, processes
  stores/           # Zustand state management
  hooks/            # use-browser-agent, use-mount-effect
```

**Stack:** Python (FastAPI) + React + TypeScript + Tailwind + Zustand + xterm.js + SQLite

## Configuration

Config lives at `~/.rdc/config.yml` (auto-created on first run):

```yaml
server:
  host: 127.0.0.1
  port: 8420

browser:
  backend: chrome     # Local Chrome, no Docker needed
  headless: true

providers:
  cursor:
    type: cursor-agent
    default: true
  ollama:
    type: ollama
    model: qwen3.5
```

API keys go in the vault, not config files:

```bash
rdc config set-secret ANTHROPIC_API_KEY sk-ant-...
```

## CLI

```bash
rdc server start [-d]              # Start server (optionally as daemon)
rdc server stop                    # Stop server
rdc add <path> [-n name]           # Register a project
rdc list                           # List projects
rdc config set-secret KEY VALUE    # Store API key securely
```

## API

Interactive docs at `http://localhost:8420/docs` when the server is running.

Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `WS /ws/state` | Real-time state updates |
| `WS /terminals/{id}/ws` | Terminal I/O stream |
| `POST /terminals` | Spawn terminal session |
| `POST /tasks` | Create task |
| `POST /browser/start` | Start browser session |
| `POST /browser/sessions/{id}/agent/loop` | Multi-step browser agent |
| `POST /orchestrator` | Natural language command |
| `GET /processes` | List actions |

## MCP Integration

For AI assistants that support [Model Context Protocol](https://modelcontextprotocol.io/):

```json
{
  "mcpServers": {
    "remote-dev-ctrl": {
      "command": "python",
      "args": ["-m", "remote_dev_ctrl.mcp.server"],
      "env": { "PYTHONPATH": "/path/to/remote-dev-ctrl/src" }
    }
  }
}
```

## Remote Access Setup

For accessing RDC from your phone or another machine:

1. **Cloudflare Tunnel** - Exposes your local machine to the internet (free tier)
2. **Caddy** - Routes subdomains locally (auto-downloaded by RDC)

```bash
# Install and configure cloudflared
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create rdc

# Add to ~/.rdc/config.yml
caddy:
  enabled: true
  base_domain: yourdomain.com
  rdc_domain: rdc.yourdomain.com
  listen_port: 8888

# Start
cloudflared tunnel run rdc
rdc server start
```

Dashboard at `https://rdc.yourdomain.com`. Enable auth with:

```bash
rdc config set-secret RDC_SECRET_KEY $(openssl rand -hex 32)
```

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
